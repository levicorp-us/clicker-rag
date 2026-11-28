import re
from pathlib import Path

from docx import Document
from pypdf import PdfReader
from psycopg.types.json import Jsonb

from . import models, revision
from .model_settings import current
from .job_control import check_running
from .imports import IMAGES, read_text
from .core import (
    FILES,
    PROMPT_VERSION,
    count_chars,
    enqueue,
    link,
    one,
    question,
)


def segments(path, suffix):
    if suffix == ".pdf":
        for i, page in enumerate(PdfReader(path).pages, 1):
            yield f"{i}쪽", page.extract_text() or ""
    elif suffix == ".docx":
        from docx.table import Table

        for i, item in enumerate(Document(path).iter_inner_content(), 1):
            if isinstance(item, Table):
                yield (
                    f"{i}번째 표",
                    "\n".join(" ".join(c.text for c in r.cells) for r in item.rows),
                )
            else:
                yield f"{i}번째 문단", item.text
    elif suffix == ".pptx":
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        def slide_text(shapes):
            for shape in shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    yield from slide_text(shape.shapes)
                elif shape.has_text_frame:
                    yield shape.text
                elif shape.has_table:
                    for row in shape.table.rows:
                        yield " ".join(cell.text for cell in row.cells)

        for i, slide in enumerate(Presentation(path).slides, 1):
            yield f"{i}번째 슬라이드", "\n".join(slide_text(slide.shapes))
    elif suffix in (".html", ".htm"):
        from lxml import html
        content = read_text(path)
        if content.strip():
            tree = html.fromstring(content)
            for element in tree.xpath("//head | //script | //style | //template | //noscript"):
                element.drop_tree()
            for i, line in enumerate(tree.text_content().splitlines(), 1):
                yield f"{i}번째 줄", line
    else:
        for i, line in enumerate(read_text(path).splitlines(), 1):
            yield f"{i}번째 문단", line


def parse(db, owner, a):
    doc = one(
        db,
        "SELECT * FROM documents WHERE id=%s AND owner=%s FOR UPDATE",
        (a["doc"], owner),
    )
    if doc["version"] != a["version"]:
        return {"status": "이전 버전"}
    suffix = Path(doc["filename"]).suffix.lower()
    path = FILES / str(owner) / doc["digest"]
    if suffix in IMAGES:
        db.execute("UPDATE documents SET status='이미지 보관' WHERE id=%s", (doc["id"],))
        if doc.get("previous"):
            db.execute("UPDATE documents SET active=false WHERE id=%s", (doc["previous"],))
        return {"status": "이미지 보관"}
    auto = doc["metadata"].get("auto_classify", False)
    parts = []
    for location, text in segments(path, suffix):
        check_running()
        text = text.replace("\x00", "")
        for offset in range(0, len(text), 1300):
            chunk = text[offset : offset + 1300].strip()
            if chunk:
                parts.append((location, chunk))
    if not parts:
        state = "텍스트 없음 · 스캔 확인" if suffix == ".pdf" else "빈 문서"
        db.execute("UPDATE documents SET status=%s WHERE id=%s", (state, doc["id"]))
        return {"status": state}
    for i, (location, text) in enumerate(parts):
        check_running()
        p = one(
            db,
            """INSERT INTO passages(doc,pos,location,body) VALUES(%s,%s,%s,%s)
                RETURNING id""",
            (doc["id"], i, location, text),
        )
        if not auto and not doc["app"] and a.get("analyze", True):
            enqueue(
                db,
                owner,
                "extract",
                {"passage": str(p["id"]), "version": doc["version"]},
                f"extract:{p['id']}:{current.get().analysis}",
            )
    db.execute("UPDATE documents SET status='원문 준비' WHERE id=%s", (doc["id"],))
    if doc.get("previous"):
        db.execute("UPDATE documents SET active=false WHERE id=%s", (doc["previous"],))
    enqueue(
        db, owner, "index_doc", a, f"index:{doc['id']}:{doc['version']}:{current.get().embedding}"
    )
    if doc["app"]:
        enqueue(db, owner, "company_tags", a, f"company-tags:{doc['id']}:{doc['version']}")
    if doc["kind"] == "보고서 자료":
        enqueue(db, owner, "report_tags", a, f"report-tags:{doc['id']}:{doc['version']}")
    if auto:
        enqueue(db, owner, "classify", a, f"classify:{doc['id']}:{doc['version']}")
    return {"passages": len(parts)}


def classify(db, owner, a):
    doc = one(db, "SELECT * FROM documents WHERE id=%s AND owner=%s", (a["doc"], owner))
    if not doc["active"] or doc["version"] != a["version"]:
        return {"status": "이전 버전"}
    samples = db.execute("""SELECT body FROM passages WHERE doc=%s
        ORDER BY pos LIMIT 14""", (doc["id"],)).fetchall()
    result = models.structured("classify",
        "취업 자료를 분류한다. 파일명과 발췌를 함께 본다. 지원자 자신의 이력서·작성한 자소서·"
        "직접 수행한 활동 기록만 개인 경험으로 분류한다. 타인 합격사례·기업 분석·외부 논문·"
        "작성 전략은 참고 자료 또는 기업 자료다. 불분명하면 참고 자료다. "
        "company와 role은 근거에 명시된 지원 회사·직무만 쓰고 나머지는 빈 문자열로 둔다.",
        {"파일": doc["metadata"].get("folder_paths", [doc["name"]])[0],
         "발췌": "\n".join(r["body"][:450] for r in samples)[:2200]}, models.DocumentInfo)
    meta = {"classification": "자동 분류", "suggested_company": result.company, "role": result.role}
    if result.kind == "과거 자소서" and result.company and not doc["metadata"].get("source"):
        meta["source"] = result.company
    active = db.execute("""UPDATE documents SET kind=%s,metadata=metadata || %s
        WHERE id=%s AND owner=%s AND active AND version=%s RETURNING id""",
        (result.kind, Jsonb(meta), doc["id"], owner, a["version"])).fetchone()
    if active and not doc["app"] and result.kind in ("이력서", "과거 자소서", "연구·활동 기록"):
        for p in db.execute("SELECT id FROM passages WHERE doc=%s ORDER BY pos", (doc["id"],)).fetchall():
            enqueue(db, owner, "extract", {"passage": str(p["id"]), "version": a["version"]}, f"extract:{p['id']}:{current.get().analysis}")
    return {"kind": result.kind}


def index_doc(db, owner, a):
    rows = db.execute(
        """SELECT p.id,p.body FROM passages p JOIN documents d ON d.id=p.doc
        WHERE d.owner=%s AND d.id=%s AND d.version=%s AND (p.embedding IS NULL OR p.model<>%s)
        ORDER BY p.pos""",
        (owner, a["doc"], a["version"], current.get().embedding),
    ).fetchall()
    for i in range(0, len(rows), 8):
        batch = rows[i : i + 8]
        for row, v in zip(batch, models.vectors([r["body"] for r in batch])):
            db.execute(
                "UPDATE passages SET embedding=%s::vector,model=%s WHERE id=%s",
                (v, current.get().embedding, row["id"]),
            )
    db.execute(
        "UPDATE documents SET status='색인 완료' WHERE id=%s AND owner=%s AND version=%s",
        (a["doc"], owner, a["version"]),
    )
    return {"indexed": len(rows)}


def known_tags(db, owner):
    return db.execute(
        "SELECT id,name,description,aliases FROM tags WHERE owner=%s AND NOT proposed ORDER BY name LIMIT 100",
        (owner,),
    ).fetchall()


def proposed_tag(db, owner, name, existing):
    known = next(
        (t for t in existing if name == t["name"] or name in t["aliases"]), None
    )
    return (
        known["id"]
        if known
        else one(
            db,
            """INSERT INTO tags(owner,name,proposed) VALUES(%s,%s,true)
        ON CONFLICT(owner,name) DO UPDATE SET name=excluded.name RETURNING id""",
            (owner, name),
        )["id"]
    )


def company_tags(db, owner, a):
    doc = one(db, """SELECT id, active, version FROM documents
        WHERE id=%s AND owner=%s AND app IS NOT NULL""", (a["doc"], owner))
    if not doc["active"] or doc["version"] != a["version"]:
        return {"status": "이전 버전"}
    existing = known_tags(db, owner)
    passages = db.execute("SELECT id,body FROM passages WHERE doc=%s ORDER BY pos", (doc["id"],)).fetchall()
    for passage in passages:
        check_running()
        output = models.structured("tag", "기업 자료의 직무·기술·사업·가치를 나타내는 짧은 태그를 1~4개 제안한다. "
            "기존 이름과 별칭을 우선한다.", {"원문": passage["body"], "기존 태그": [{"이름": t["name"], "별칭": t["aliases"]} for t in existing]},
            models.MaterialTags)
        for name in dict.fromkeys(t.strip()[:80] for t in output.tags if t.strip()):
            tag = proposed_tag(db, owner, name, existing)
            link(db, tag, "passage", passage["id"], "제안")
    return {"passages": len(passages)}


def extract(db, owner, a):
    p = one(
        db,
        """SELECT p.*,d.version FROM passages p JOIN documents d ON d.id=p.doc
                WHERE p.id=%s AND d.owner=%s AND d.app IS NULL""",
        (a["passage"], owner),
    )
    existing = known_tags(db, owner)
    output = models.structured(
        "extract",
        "자료에 명시된 활동·방법·결과를 추출한다. 제목은 활동명이다. 개선 제안은 계획으로 기록한다. "
        "각 사실에 행동이나 기술을 설명하는 짧은 한국어 태그를 1~4개 제안한다. 담당자나 기여 주체는 분류하지 않는다. "
        "기존 이름·별칭을 우선하고 목록이 비어 있으면 새 이름을 만든다. 경험이 없으면 facts를 비운다.",
        {"원문": p["body"], "기존 태그": existing},
        models.Extraction,
    )
    for item in output.facts:
        row = one(
            db,
            "INSERT INTO facts(owner,title,body,style) VALUES(%s,%s,%s,%s) RETURNING id",
            (owner, item.title, item.action_result, item.style),
        )
        db.execute("INSERT INTO citations VALUES(%s,%s)", (row["id"], p["id"]))
        for name in set(t.strip() for t in item.tags if t.strip()):
            tag = proposed_tag(db, owner, name, existing)
            link(db, tag, "fact", row["id"], "제안")
            link(db, tag, "passage", p["id"], "제안")
    return {"facts": len(output.facts), "model": current.get().analysis, "prompt": PROMPT_VERSION}


def index_fact(db, owner, a):
    f = one(db, "SELECT * FROM facts WHERE id=%s AND owner=%s", (a["id"], owner))
    if f["version"] != a["version"]:
        return {"status": "이전 버전"}
    v = models.vectors([f["title"] + " " + f["body"]])[0]
    db.execute(
        "UPDATE facts SET embedding=%s::vector,model=%s WHERE id=%s AND version=%s",
        (v, current.get().embedding, f["id"], a["version"]),
    )
    return {"version": a["version"]}


def retag(db, owner, a):
    tag = one(db, "SELECT * FROM tags WHERE owner=%s AND id=%s", (owner, a["tag"]))
    rows = db.execute(
        """SELECT 'fact' AS kind,id,body,version FROM facts WHERE owner=%s AND status='확정'
        UNION ALL SELECT 'passage',p.id,p.body,d.version FROM passages p JOIN documents d ON d.id=p.doc
        WHERE d.owner=%s AND d.app IS NULL AND d.active
        AND (NOT (d.metadata @> '{"auto_classify": true}'::jsonb)
             OR d.kind IN ('이력서','과거 자소서','연구·활동 기록'))""",
        (owner, owner),
    ).fetchall()
    for i in range(0, len(rows), 6):
        batch = rows[i : i + 6]
        result = models.structured(
            "tag",
            "태그의 의미가 실제로 드러난 자료 번호만 ids에 문자열로 반환한다.",
            {
                "태그": [tag["name"], tag["description"], tag["aliases"]],
                "자료": {str(j): r["body"][:600] for j, r in enumerate(batch)},
            },
            models.Matches,
        )
        current = db.execute(
            "SELECT * FROM tags WHERE id=%s AND owner=%s", (tag["id"], owner)
        ).fetchone()
        if not current or any(
            current[k] != tag[k] for k in ("name", "description", "aliases", "parent")
        ):
            return {"status": "태그 변경 · 이전 분석 종료"}
        for j, row in enumerate(batch):
            query = (
                "SELECT id FROM facts WHERE id=%s AND version=%s AND owner=%s AND status='확정' FOR SHARE"
                if row["kind"] == "fact"
                else """SELECT p.id FROM passages p JOIN documents d ON d.id=p.doc
                   WHERE p.id=%s AND d.version=%s AND d.owner=%s AND d.active FOR SHARE"""
            )
            if not db.execute(query, (row["id"], row["version"], owner)).fetchone():
                continue
            if str(j) in result.ids:
                link(db, tag["id"], row["kind"], row["id"], "제안")
            else:
                db.execute(
                    "DELETE FROM links WHERE tag=%s AND target_type=%s AND target=%s AND state='제안'",
                    (tag["id"], row["kind"], row["id"]),
                )
    return {"reviewed": len(rows)}


def retrieve(db, owner, q, experience=None, tags=(), instruction=None):
    query = " ".join([q["company"], q["role"], q["title"], *tags, instruction or ""]).strip()
    # A newly confirmed experience can be used while its first embedding is queued.
    indexed = db.execute(
        """SELECT EXISTS(SELECT 1 FROM passages p JOIN documents d ON d.id=p.doc
               WHERE d.owner=%s AND d.active AND (d.app=%s OR (%s AND d.app IS NULL
                   AND d.kind IN ('연구·활동 기록','이력서','과거 자소서')))
               AND p.model=%s AND p.embedding IS NOT NULL)
           OR EXISTS(SELECT 1 FROM facts WHERE owner=%s AND status='확정' AND model=%s
               AND embedding IS NOT NULL AND (%s::uuid IS NULL OR experience=%s)) AS available""",
        (owner, q["app"], instruction is not None, current.get().embedding, owner, current.get().embedding, experience, experience),
    ).fetchone()["available"]
    v = models.vectors([query])[0] if indexed else None
    company = db.execute(
        """SELECT * FROM (SELECT DISTINCT ON(d.kind) p.id,p.body,p.location,d.name,d.kind,d.version,
        p.embedding <=> %s::vector AS distance FROM passages p JOIN documents d ON p.doc=d.id
        WHERE d.owner=%s AND d.app=%s AND d.active AND p.model=%s ORDER BY d.kind,distance) sources
        ORDER BY distance LIMIT 5""",
        (v, owner, q["app"], current.get().embedding),
    ).fetchall()
    if company and instruction is None:
        v = models.vectors([query + " " + " ".join(s["body"][:220] for s in company)])[
            0
        ]
    facts = db.execute(
        """WITH RECURSIVE chosen(id,label) AS (
        SELECT t.id,x.label FROM tags t CROSS JOIN unnest(%s::text[]) AS x(label)
        WHERE t.owner=%s AND (t.name=x.label OR x.label=ANY(t.aliases))
        UNION SELECT t.id,c.label FROM tags t JOIN chosen c ON t.parent=c.id)
        SELECT f.id,f.body,f.title,f.style,f.version,f.experience,e.name AS experience_name,
        ARRAY(SELECT DISTINCT c.label FROM links l JOIN chosen c ON l.tag=c.id WHERE l.target=f.id AND l.target_type='fact' AND l.state='확정') AS matched,
        (SELECT count(DISTINCT c.label) FROM links l JOIN chosen c ON l.tag=c.id WHERE l.target=f.id AND l.target_type='fact'
        AND l.state='확정') AS hits, coalesce(1-(f.embedding <=> %s::vector),0) AS similarity
        FROM facts f JOIN experiences e ON e.id=f.experience WHERE f.owner=%s AND f.status='확정'
        AND (%s::uuid IS NULL OR f.experience=%s) AND (f.model=%s OR f.embedding IS NULL)
        AND EXISTS(SELECT 1 FROM citations c JOIN passages p ON c.passage=p.id JOIN documents d ON d.id=p.doc
        WHERE c.fact=f.id AND d.active) ORDER BY hits DESC,similarity DESC,f.id LIMIT 6""",
        (list(tags), owner, v, owner, experience, experience, current.get().embedding),
    ).fetchall()
    if instruction is not None:
        # Registered personal documents can be cited directly while experience review is pending.
        raw = db.execute("""SELECT p.id,p.body,d.name AS title, d.name, p.location, d.version,
                '' AS style, NULL AS experience, true AS raw, hits.matched, cardinality(hits.matched) AS hits,
                coalesce(1-(p.embedding <=> %s::vector),0) AS similarity
            FROM passages p JOIN documents d ON d.id=p.doc
            CROSS JOIN LATERAL (SELECT ARRAY(SELECT DISTINCT x FROM unnest(%s::text[]) AS x
                WHERE strpos(lower(p.body),lower(x))>0 OR EXISTS(SELECT 1 FROM links l JOIN tags t ON t.id=l.tag
                WHERE l.target_type='passage' AND l.target=p.id AND l.state IN ('확정','제안')
                AND (t.name=x OR x=ANY(t.aliases)))) AS matched) hits
            WHERE d.owner=%s AND d.app IS NULL AND d.active AND d.kind IN ('연구·활동 기록','이력서','과거 자소서')
            AND (p.model=%s OR p.embedding IS NULL)
            AND NOT EXISTS(SELECT 1 FROM citations c JOIN facts f ON f.id=c.fact
                WHERE c.passage=p.id AND f.status='확정')
            ORDER BY hits DESC,similarity DESC,p.id LIMIT 6""", (v, list(tags), owner, current.get().embedding)).fetchall()
        facts = sorted([*facts, *raw], key=lambda f: (f["hits"], f["similarity"], not f.get("raw")), reverse=True)[:6]
    return facts, company


def fit_free_paragraph(body, minimum, maximum, spaces):
    if count_chars(body, spaces) <= maximum:
        return body
    # Keep the longest complete opening that meets both bounds.
    for ending in reversed(list(re.finditer(r"""[.!?。！？](?:["'”’」])?(?=\s|$)""", body))):
        opening = body[:ending.end()]
        if minimum <= count_chars(opening, spaces) <= maximum:
            return opening
    return body


def paragraph_context(data, args):
    instruction = ""
    if args.get("target_text") and not args.get("manual_revision"):
        data["선택한 문단의 기존 원고"] = args["target_text"]
        instruction = " 선택한 문단의 기존 원고에서 주제를 살려 해당 위치에 들어갈 한 문단을 쓴다."
    if not args.get("preceding_text"):
        return instruction
    data["작성할 문단 번호"] = args["block_number"]
    data["같은 문항의 앞 문단"] = args["preceding_text"]
    return instruction + (" 같은 문항의 앞 문단에서 이어지는 문단 하나만 작성한다. 도입·사례 소개를 반복하지 않고 "
            "앞서 설명한 내용에서 다음 행동·결과·의미로 전개한다. 앞 문단은 글의 흐름을 파악하는 용도로만 사용한다.")


def generate_free(db, owner, q, a):
    data = {
        "지원 기업": q["company"], "지원 직무": q["role"], "문항": q["title"],
        "목표 글자 수": [a["minimum"], a["maximum"]], "공백 포함": q["spaces"],
        "추가 작성 지시": a["instruction"],
    }
    if a.get("previous"):
        data["재작성할 문단"] = one(db, """SELECT body FROM paragraphs
            WHERE id=%s AND owner=%s AND question=%s AND details->>'mode'='free'""",
            (a["previous"], owner, q["id"]))["body"]
    out = models.structured("free",
        "지원 기업과 직무에 어울리는 한국어 자기소개서 초안을 한 문단으로 자유롭게 쓴다. "
        f"약 {(a['minimum'] + a['maximum']) // 2}자를 목표로 문항에 직접 답하고 추가 작성 지시를 따른다. "
        "경험형 문항에는 상황과 행동을 담은 예시를 구성한다. "
        "주어지지 않은 회사의 구체적 사업이나 지원자의 실제 이력·수치 성과를 단정하지 않는다. "
        "재작성할 문단이 있으면 같은 문항에 맞춰 한 번 다듬는다." + paragraph_context(data, a),
        data, models.FreeParagraph)
    body = out.body.strip()
    if not body:
        raise ValueError("모델이 빈 문단을 반환했습니다.")
    body = fit_free_paragraph(body, a["minimum"], a["maximum"], q["spaces"])
    length = count_chars(body, q["spaces"])
    within = a["minimum"] <= length <= a["maximum"]
    details = {**a, "tags": [], "references": [], "expanded": [], "count": length,
               "within": within, "model": current.get().generation, "prompt": "free-1"}
    row = one(db, """INSERT INTO paragraphs(owner,question,previous,body,details)
        VALUES(%s,%s,%s,%s,%s) RETURNING id""",
        (owner, q["id"], a.get("previous"), body, Jsonb(details)))
    return {"paragraph": str(row["id"]), "count": length, "within": within}


def source_excerpt(body, tags, limit):
    if len(body) <= limit:
        return body
    # Keep the requested term in view when the matching passage exceeds the prompt budget.
    offsets = [body.casefold().find(tag.casefold()) for tag in tags]
    first = min((offset for offset in offsets if offset >= 0), default=0)
    start = min(max(0, first - limit // 3), len(body) - limit)
    return body[start:start + limit]


def generate(db, owner, a, job_id=None):
    q = question(db, owner, a["question"])
    if a.get("mode") == "free":
        return generate_free(db, owner, q, a)
    automatic = a.get("mode") == "instructions"
    if automatic:
        if "tags" not in a:
            output = models.structured("tag", "지시사항에서 사용자가 강조하려는 경험·역량·기술 태그를 뽑는다. "
                "명시한 태그는 원래 표현을 보존하고 글자 수나 문체 지시는 제외한다. 강조 주제가 없으면 빈 배열이다.",
                {"지시사항": a["instruction"]}, models.SearchTags)
            a = {**a, "tags": list(dict.fromkeys(t.strip()[:80] for t in output.tags if t.strip()))}
        facts, company = retrieve(db, owner, q, tags=a["tags"], instruction=a["instruction"])
    else:
        facts, company = retrieve(db, owner, q, a["experience"], a["tags"])
    if not facts:
        if automatic:
            raise ValueError("검색할 경험 근거가 없습니다. 개인 자료의 등록·분류 상태를 확인하거나 경험을 직접 입력해 주세요.")
        raise ValueError("선택한 경험에 출처가 연결된 확정 사실이 필요합니다.")
    matching = [f for f in facts if f.get("hits", 0) > 0]
    covered = {t for f in matching for t in f.get("matched", [])}
    if a["tags"] and set(a["tags"]) <= covered:
        facts = matching
    else:
        facts = matching + [f for f in facts if not f.get("hits", 0)]
    evidence = {f"F{i + 1}": f for i, f in enumerate(facts)}
    sources = {f"C{i + 1}": s for i, s in enumerate(company)}
    data = {
        "기업": q["company"],
        "직무": q["role"],
        "문항": q["title"],
        "태그": a["tags"],
        "경험 근거": {k: [source_excerpt(f["body"], a["tags"], 450) if automatic else f["body"]]
                   for k, f in evidence.items()},
        "확정된 태그 연결": {k: f.get("matched", []) for k, f in evidence.items() if not f.get("raw")},
        "기업 근거": {k: source_excerpt(s["body"], a["tags"], 240) if automatic else s["body"][:550]
                   for k, s in sources.items()},
        "글자수": [a["minimum"], a["maximum"]],
        "공백 포함": q["spaces"],
        "문체 참고": [f["style"][:100] for f in facts if f["style"]],
        "서사 허용": a["narrative"],
        "작성 지시": a["instruction"],
    }
    definitions = db.execute(
        "SELECT name,description,aliases FROM tags WHERE owner=%s AND name=ANY(%s)",
        (owner, a["tags"]),
    ).fetchall()
    data["태그 정의"] = {
        r["name"]: r["description"] for r in definitions if r["description"]
    }
    if a.get("previous"):
        past = one(
            db,
            "SELECT body,details,question FROM paragraphs WHERE id=%s AND owner=%s",
            (a["previous"], owner),
        )
        if a.get("manual_revision"):
            if str(past["question"]) != str(q["id"]):
                raise ValueError("같은 문항의 문단만 재작성할 수 있습니다.")
            data["수정할 초안"] = past["body"]
            data["수정 전 실제 글자수"] = count_chars(past["body"], q["spaces"])
            checks = past["details"]
            selected = next((r for r in checks.get("attempts", [])
                             if r.get("round") == checks.get("selected_round")), {})
            data["검토 의견"] = selected.get("feedback", [])
            data["유지할 관점"] = [t["tag"] for t in checks.get("tag_checks", []) if t["status"] == "충족"]
        else:
            data["이전 문단 · 전개만 참고"] = past["body"]
    if a.get("validation_retry"):
        data["수정할 초안"] = a["rejected_draft"]
        data["검토 의견"] = a["validation_feedback"]
    instruction = (
        "한국어 입사지원 자기소개서 한 문단을 1인칭으로 쓴다. 문항에 바로 답하고 선택 태그를 하나의 구체 행동으로 연결한다. "
        "활동·기술·성과·수치는 경험 근거로 서술한다. 기업 자료는 강조점을 고르는 데만 쓴다. "
        "used에는 실제 사용한 F번호와 C번호를 넣고 본문에는 번호를 쓰지 않는다. 서사 허용 시에만 장면을 확장하고 "
        "그 구간을 expanded에 본문 그대로 넣는다. 기본 모드는 빈 배열이다. 목표 글자수를 준수한다."
    )
    if automatic:
        instruction += " 작성 지시의 강조점과 서술 순서를 반영한다. 서로 다른 활동을 하나의 사건으로 합치지 않는다."
    instruction += paragraph_context(data, a)
    prior_companies = {
        r["name"]
        for r in db.execute(
            """SELECT DISTINCT metadata->>'source' AS name FROM documents
        WHERE owner=%s AND kind='과거 자소서' AND metadata->>'source'<>'' UNION
        SELECT company FROM applications WHERE owner=%s""",
            (owner, owner),
        )
    }
    prior_companies.discard(q["company"])
    known_text = " ".join(
        [
            q["title"],
            q["role"],
            q["company"],
            *(f["body"] for f in facts),
            *(s["body"] for s in company),
        ]
    )
    expected_terms = set(re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}", known_text.lower()))
    expected_terms.update(t.lower() for t in a["tags"])
    response_schema = models.Paragraph.model_json_schema()
    fields = response_schema["properties"]
    fields["used"]["items"] = {"type": "string", "enum": [*evidence, *sources]}
    if q["spaces"]:
        fields["body"]["minLength"] = a["minimum"]
    target = (a["minimum"] + a["maximum"]) // 2
    instruction += f" 본문은 약 {target}자를 목표로 쓰고 마지막 문장을 완결한다."
    if a.get("manual_revision"):
        instruction += (" 사용자가 재작성을 요청했다. 이전 문단도 검토 대상이다. 과거의 행동이나 역량은 "
                        "현재 경험 근거에서 확인되는 것만 유지한다. 지원 직무의 요구 사항은 미래 계획으로 연결한다. "
                        "검토 의견을 참고해 사용자 태그와 목표 분량에 맞게 한 번 고쳐 쓴다.")
    if not a["narrative"]:
        fields["expanded"]["maxItems"] = 0
    instruction += (
        " 문장마다 정보 하나만 담고 완전한 문장으로 마무리한다. 상세 업무가 미확인이면 "
        "회사의 업무를 단정하지 않는다. 직무에 어떻게 기여할지는 지원자의 계획으로 쓴다. "
        "확인되지 않은 기술·수치·성과를 추가하지 않는다."
    )
    if a["narrative"]:
        instruction+=" 요청한 조율 장면이나 대화가 있으면 가정한 서사로 짧게 확장하고 본문과 일치하는 구간을 expanded에 표시한다."
    else:
        instruction+=" 경험 원문에 없는 대화나 장면을 추가하지 않는다."
    versions = {
        "facts": {str(f["id"]): f["version"] for f in facts},
        "sources": {str(s["id"]): s["version"] for s in company},
        "digest": models.runtime().get(current.get().generation.removesuffix(":latest")),
    }
    if a.get("validation_retry"):
        versions["validation_retry"] = a["validation_retry"]
    winner, attempts, reason = revision.run(
        data,
        instruction,
        response_schema,
        evidence,
        sources,
        prior_companies,
        q["spaces"],
        owner,
        job_id,
        versions,
    )
    out = models.Paragraph.model_validate(winner)
    for key in set(out.used) & evidence.keys():
        fact=evidence[key]
        if fact.get("raw"):
            live = db.execute("""SELECT d.version FROM passages p JOIN documents d ON p.doc=d.id
                WHERE p.id=%s AND d.owner=%s AND d.app IS NULL AND d.active FOR SHARE""",
                (fact["id"], owner)).fetchone()
            if not live or live["version"] != fact["version"]:
                raise ValueError("작성 중 경험 원문이 변경되었습니다. 다시 작성해 주세요.")
            continue
        live=db.execute("SELECT version,status FROM facts WHERE id=%s AND owner=%s FOR SHARE",
                        (fact["id"],owner)).fetchone()
        if not live or live["version"]!=fact["version"] or live["status"]!="확정":
            raise ValueError("검증 도중 경험 근거가 변경되었습니다. 최신 근거로 다시 생성해 주세요.")
    for key in set(out.used) & sources.keys():
        src=sources[key]
        live=db.execute("""SELECT d.version FROM documents d JOIN passages p ON p.doc=d.id
            WHERE p.id=%s AND d.owner=%s AND d.app=%s AND d.active FOR SHARE""",(src["id"],owner,q["app"])).fetchone()
        if not live or live["version"]!=src["version"]:
            raise ValueError("검증 도중 기업 자료가 변경되었습니다. 최신 자료로 다시 생성해 주세요.")
    body = out.body.strip()
    length, within = winner["count"], winner["within"]
    new_terms = (
        set(re.findall(r"[A-Za-z][A-Za-z0-9+#-]{1,}", body.lower())) - expected_terms
    )
    refs = []
    for key in dict.fromkeys(out.used):
        if key in evidence:
            f = evidence[key]
            if f.get("raw"):
                refs.append({"id": str(f["id"]), "name": f["name"], "location": f["location"]})
                continue
            rows = db.execute(
                """SELECT p.id,p.location,d.name FROM citations c JOIN passages p ON c.passage=p.id
                JOIN documents d ON d.id=p.doc WHERE c.fact=%s AND d.owner=%s AND d.active""",
                (f["id"], owner),
            ).fetchall()
            if not rows:
                raise ValueError("원문 출처가 변경되었습니다. 근거를 확인해 주세요.")
            refs.extend(
                {
                    "id": str(r["id"]),
                    "name": r["name"],
                    "location": r["location"],
                    "fact": f["body"],
                }
                for r in rows
            )
        else:
            s = sources[key]
            refs.append(
                {"id": str(s["id"]), "name": s["name"], "location": s["location"]}
            )
    used_experiences = {str(evidence[k]["experience"]) for k in out.used if k in evidence and evidence[k]["experience"]}
    details = {
        **a,
        "experience_ids": sorted(used_experiences),
        "references": refs,
        "expanded": out.expanded,
        "count": length,
        "within": within,
        "model": current.get().generation,
        "prompt": PROMPT_VERSION,
        "used": out.used,
        "review": "원문 대조 필요",
        "model_digest": versions["digest"],
        "analysis_model": current.get().analysis,
        "embedding_model": current.get().embedding,
        "fact_versions": {str(f["id"]): f["version"] for f in facts},
        "attempts": attempts,
        "accepted": winner["accepted"],
        "tag_checks": winner["tags"],
        "factual_issues": winner["factual_issues"],
        "selected_round": winner["round"],
        "stop_reason": reason,
        "terms_to_review": sorted(new_terms),
        "company_versions": {str(s["id"]): s["version"] for s in company},
    }
    row = one(
        db,
        """INSERT INTO paragraphs(owner,question,experience,previous,body,details)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
        (owner, q["id"], a.get("experience") or (next(iter(used_experiences)) if len(used_experiences) == 1 else None),
         a.get("previous"), body, Jsonb(details)),
    )
    return {
        "paragraph": str(row["id"]),
        "count": length,
        "within": within,
        "accepted": winner["accepted"],
        "stop_reason": reason,
    }
