"""Manage Ollama models and switch searchable vectors atomically."""

import hashlib
import json
from dataclasses import replace

import httpx

from . import models
from .job_control import check_running
from .model_settings import Choices, index_lock, load, using


def upload_gguf(file):
    if file.read(4) != b"GGUF":
        raise ValueError("GGUF 파일 헤더를 확인할 수 없습니다.")
    file.seek(0)
    digest = "sha256:" + hashlib.file_digest(file, "sha256").hexdigest()
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    url = models.URL + "blobs/" + digest
    with httpx.Client(
        trust_env=False, timeout=httpx.Timeout(600, connect=10)
    ) as client:
        existing = client.head(url)
        if existing.status_code == 404:
            response = client.post(
                url,
                content=iter(lambda: file.read(1024 * 1024), b""),
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                },
            )
            response.raise_for_status()
        else:
            existing.raise_for_status()
    return digest


def install_model(db, owner, args):
    name = args["name"]
    key = name.removesuffix(":latest")
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("install-model:" + key,),
    )
    if key in models.runtime():
        if args.get("digest"):
            raise ValueError("이미 등록된 모델 이름입니다. 다른 이름을 사용해 주세요.")
        return {"name": name, "status": "이미 설치됨"}
    if args.get("digest"):
        result = models.call(
            "create",
            {"model": name, "files": {"model.gguf": args["digest"]}, "stream": False},
        )
    else:
        result = models.call("pull", {"model": name, "stream": False})
    if result.get("status") != "success" or key not in models.runtime():
        raise ValueError("모델 서버에서 설치 완료를 확인할 수 없습니다.")
    return {"name": name, "status": "설치 완료"}


def probe_llm():
    tag = models.structured(
        "tag",
        "요청에서 강조한 역량 태그를 뽑는다.",
        {"지시사항": "시스템 통합 경험을 강조해 주세요."},
        models.SearchTags,
    )
    if not any(t.strip() for t in tag.tags):
        raise ValueError("분석 모델이 검증 요청의 태그를 반환하지 않았습니다.")
    out = models.structured(
        "generate",
        "주어진 근거만 써서 한국어 한 문장을 작성한다. used에 F1을 넣고 expanded는 비운다.",
        {"F1": "서로 다른 센서의 측정 기준을 맞추었다."},
        models.Paragraph,
    )
    if not out.body.strip() or set(out.used) != {"F1"} or out.expanded:
        raise ValueError(
            "작성 모델이 본문과 근거 번호를 올바른 형식으로 반환하지 않았습니다."
        )


def probe_embedding(db, choices):
    sample = ["서로 다른 센서의 측정 기준을 통일했다.", "행사 예산과 지출을 정리했다."]
    with using(choices):
        corpus = models.vectors(sample)
        size = len(json.loads(corpus[0]))
    with using(replace(choices, dimensions=size)):
        query = models.vectors(["여러 센서의 측정 규격을 맞춘 경험을 찾습니다."])[0]
    hits = db.execute(
        """SELECT id,v <=> %s::vector AS distance FROM (VALUES (1,%s::vector),(2,%s::vector)) AS docs(id,v)
        ORDER BY distance,id""",
        (query, *corpus),
    ).fetchall()
    if hits[0]["id"] != 1 or not hits[0]["distance"] < hits[1]["distance"]:
        raise ValueError("검색 모델의 샘플 문서 검색을 검증하지 못했습니다.")
    return size


def stage_vectors(db, owner):
    with db.cursor(name="source_for_model_change") as sources:
        sources.execute(
            """WITH candidates AS (SELECT 'passage' AS kind,p.id,d.version,p.body FROM passages p JOIN documents d ON p.doc=d.id
            WHERE d.owner=%s AND d.active UNION ALL SELECT 'fact',id,version,title || ' ' || body FROM facts
            WHERE owner=%s AND status='확정')
            SELECT c.* FROM candidates c WHERE NOT EXISTS(SELECT 1 FROM next_embeddings n
                WHERE n.kind=c.kind AND n.id=c.id AND n.version=c.version)""",
            (owner, owner),
        )
        total = 0
        while batch := sources.fetchmany(8):
            check_running()
            vectors = models.vectors([row["body"] for row in batch])
            with db.cursor() as writer:
                writer.executemany(
                    """INSERT INTO next_embeddings VALUES(%s,%s,%s,%s::vector)
                    ON CONFLICT(kind,id) DO UPDATE SET version=excluded.version,v=excluded.v""",
                    [
                        (row["kind"], row["id"], row["version"], v)
                        for row, v in zip(batch, vectors, strict=True)
                    ],
                )
            total += len(batch)
    return total


def configure_models(db, owner, args):
    db.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("switch-models:" + str(owner),),
    )
    active = load(db, owner)
    if active.version != args["version"]:
        raise ValueError(
            "모델 설정이 이미 변경됐습니다. 최신 설정에서 다시 적용해 주세요."
        )
    installed = models.runtime()
    names = [args[k] for k in ("generation", "analysis", "embedding")]
    if any(n.removesuffix(":latest") not in installed for n in names):
        raise ValueError("선택한 모델이 서버에 설치되어 있지 않습니다.")
    embedding = names[2]
    if embedding.removesuffix(":latest") == active.embedding.removesuffix(":latest"):
        embedding = active.embedding
    digest = installed[embedding.removesuffix(":latest")]
    candidate = Choices(names[0], names[1], embedding, embed_digest=digest)
    with using(candidate):
        probe_llm()
    size = probe_embedding(db, candidate)
    candidate = replace(candidate, dimensions=size)
    rebuild = (
        embedding != active.embedding
        or active.dimensions not in (None, size)
        or active.embed_digest not in (None, digest)
    )
    if not rebuild and active.embed_digest is None:
        # A pre-settings database may have been started with a different environment default.
        rebuild = db.execute(
            """SELECT EXISTS(SELECT 1 FROM passages p JOIN documents d ON d.id=p.doc
                WHERE d.owner=%s AND d.active AND p.embedding IS NOT NULL AND p.model IS DISTINCT FROM %s)
            OR EXISTS(SELECT 1 FROM facts WHERE owner=%s AND status='확정' AND embedding IS NOT NULL
                AND model IS DISTINCT FROM %s) AS needed""",
            (owner, embedding, owner, embedding),
        ).fetchone()["needed"]
    count = 0
    if rebuild:
        db.execute("""CREATE TEMP TABLE next_embeddings(kind text,id uuid,version integer,v vector,
            PRIMARY KEY(kind,id)) ON COMMIT DROP""")
        with using(candidate):
            count = stage_vectors(db, owner)
            # Include sources added or edited while the first pass ran against a stable cursor snapshot.
            index_lock(db, owner, exclusive=True)
            count += stage_vectors(db, owner)
    after = models.runtime()
    if any(
        after.get(n.removesuffix(":latest")) != installed[n.removesuffix(":latest")]
        for n in names
    ):
        raise ValueError("검증 도중 설치된 모델이 변경되었습니다. 다시 적용해 주세요.")
    # Take the exclusive row lock only for publication. Readers pin this row while searching.
    db.execute("SELECT owner FROM model_settings WHERE owner=%s FOR UPDATE", (owner,))
    if rebuild:
        db.execute(
            """UPDATE passages p SET embedding=n.v,model=%s FROM next_embeddings n
            WHERE n.kind='passage' AND p.id=n.id""",
            (embedding,),
        )
        db.execute(
            """UPDATE facts f SET embedding=n.v,model=%s FROM next_embeddings n WHERE n.kind='fact'
            AND f.id=n.id AND f.version=n.version AND f.status='확정'""",
            (embedding,),
        )
    db.execute(
        """UPDATE model_settings SET generation=%s,analysis=%s,embedding=%s,dimensions=%s,
        embed_digest=%s,version=version+1 WHERE owner=%s""",
        (names[0], names[1], embedding, size, digest, owner),
    )
    return {"dimensions": size, "reindexed": count, "status": "검증 및 적용 완료"}
