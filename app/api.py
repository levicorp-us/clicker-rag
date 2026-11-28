import hashlib
import os
import re
from dataclasses import asdict

import httpx
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import (
    Cookie,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field, StringConstraints
from typing_extensions import Annotated
from psycopg.types.json import Jsonb
from . import models
from .report_api import router as report_router
from .core import (
    ROOT,
    U,
    FILES,
    connect,
    migrate,
    profile,
    session_for,
    one,
    enqueue,
    add_tag,
    link,
    question,
    count_chars,
)
from .model_settings import index_lock, load as load_models, using
from .model_management import upload_gguf
from .documents import store_doc
from .pipeline import retrieve
from .imports import EXTENSIONS, IGNORED_DIRS, IMAGES, excluded, relative_name

Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
]
ID = uuid.UUID


@asynccontextmanager
async def lifespan(app):
    migrate()
    with connect() as db:
        db.execute(
            "INSERT INTO users VALUES(%s) ON CONFLICT DO NOTHING", (profile()[0],)
        )
        load_models(db, profile()[0])
    yield


app = FastAPI(title="클리커", lifespan=lifespan, docs_url=None, redoc_url=None)
app.include_router(report_router)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=[h.strip() for h in os.getenv("CLICKER_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD")
        and origin
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return JSONResponse(
            {"detail": "현재 작성 화면에서 요청하세요."}, status_code=403
        )
    return await call_next(request)


@app.get("/")
def home(clicker_session: str | None = Cookie(None)):
    r = FileResponse(ROOT / "web/index.html")
    if not clicker_session:
        r.set_cookie(
            "clicker_session",
            session_for(profile()[0]),
            httponly=True,
            samesite="strict",
        )
    return r


@app.get("/api/health")
def health(owner: U):
    with connect() as db:
        selected = load_models(db, owner)
        pg = one(db, "SELECT extversion FROM pg_extension WHERE extname='vector'")[
            "extversion"
        ]
        worker = db.execute("SELECT seen FROM heartbeat WHERE name='worker'").fetchone()
    try:
        ready = models.runtime()
    except Exception:
        ready = {}
    return {
        "pgvector": pg,
        "worker_seen": worker["seen"] if worker else None,
        "models": {
            role: {"name": name, "digest": ready.get(name.removesuffix(":latest"))}
            for role, name in [
                ("경험 분석", selected.analysis),
                ("문단 생성", selected.generation),
                ("의미 검색", selected.embedding),
            ]
        },
    }


ModelName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200,
    pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*(:[a-zA-Z0-9_][a-zA-Z0-9_.-]*)?$")]


InstalledName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class SelectionInput(BaseModel):
    generation: InstalledName
    analysis: InstalledName
    embedding: InstalledName
    version: int = Field(ge=0)


class InstallInput(BaseModel):
    name: ModelName


@app.get("/api/models")
def model_list(owner: U):
    with connect() as db:
        active = load_models(db, owner)
        pgvector = one(db, "SELECT extversion AS version FROM pg_extension WHERE extname='vector'")["version"]
        jobs = db.execute("""SELECT id,kind,status,error,result,args FROM jobs WHERE owner=%s
            AND kind IN ('configure_models','install_model') AND NOT cleared
            ORDER BY (status IN ('실행','대기','보류')) DESC,created DESC LIMIT 20""", (owner,)).fetchall()
    try:
        installed = sorted(models.runtime())
        error = ""
    except (httpx.HTTPError, KeyError, ValueError):
        installed, error = [], "모델 서버에 연결할 수 없습니다. 서버 주소와 실행 상태를 확인하세요."
    return {"active": asdict(active), "installed": installed, "jobs": jobs, "error": error, "pgvector": pgvector}


@app.post("/api/models/apply")
def select_models(i: SelectionInput, owner: U):
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("apply-models:" + str(owner),))
        active = load_models(db, owner)
        if active.version != i.version:
            raise HTTPException(409, "모델 설정이 변경됐습니다. 목록을 새로고침해 주세요.")
        pending = db.execute("""SELECT id FROM jobs WHERE owner=%s AND kind='configure_models'
            AND status IN ('대기','실행','보류') LIMIT 1""", (owner,)).fetchone()
        if pending:
            raise HTTPException(409, "이미 적용 중인 설정이 있습니다. 작업 상태를 확인해 주세요.")
        return {"job": enqueue(db, owner, "configure_models", i.model_dump())}


@app.post("/api/models/download")
def download_model(i: InstallInput, owner: U):
    # Registry model names only; do not accept arbitrary network addresses.
    if not re.fullmatch(r"(?:[A-Za-z0-9_-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*(?::[A-Za-z0-9_.-]+)?", i.name):
        raise HTTPException(422, "레지스트리 모델 이름을 입력해 주세요. 예: 조직/모델:태그")
    with connect() as db:
        return {"job": enqueue(db, owner, "install_model", {"name": i.name})}


@app.post("/api/models/import")
def import_model(owner: U, name: Annotated[ModelName, Form()], file: UploadFile = File(...)):
    if "/" in name or not (file.filename or "").lower().endswith(".gguf"):
        raise HTTPException(422, "등록할 모델 이름과 단일 GGUF 파일을 선택해 주세요.")
    try:
        if name.removesuffix(":latest") in models.runtime():
            raise HTTPException(409, "이미 설치된 이름입니다. 새 모델 이름을 사용해 주세요.")
        digest = upload_gguf(file.file)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "모델 서버에 파일을 전송하지 못했습니다.") from exc
    with connect() as db:
        return {"job": enqueue(db, owner, "install_model", {"name": name, "digest": digest})}


@app.get("/api/state")
def state(owner: U):
    queries = {
        "applications": "SELECT * FROM applications WHERE owner=%s ORDER BY created DESC",
        "questions": "SELECT q.* FROM questions q JOIN applications a ON a.id=q.app WHERE a.owner=%s ORDER BY q.created",
        "documents": "SELECT id,app,name,kind,metadata,status,version,active FROM documents WHERE owner=%s AND active ORDER BY created DESC",
        "experiences": "SELECT id,name FROM experiences WHERE owner=%s ORDER BY name",
        "facts": """SELECT f.id,f.experience,f.title,f.body,f.role,f.style,f.status,f.version,
          ARRAY(SELECT passage FROM citations WHERE fact=f.id) AS refs FROM facts f WHERE owner=%s ORDER BY title""",
        "tags": "SELECT * FROM tags WHERE owner=%s ORDER BY name",
        "links": "SELECT l.* FROM links l JOIN tags t ON t.id=l.tag WHERE t.owner=%s",
        "paragraphs": "SELECT * FROM paragraphs WHERE owner=%s ORDER BY created DESC",
        "jobs": """WITH owned AS (SELECT * FROM jobs WHERE owner=%s AND NOT cleared),
            visible AS (SELECT * FROM owned WHERE status IN ('대기','실행','보류') UNION ALL
                (SELECT * FROM owned WHERE status NOT IN ('대기','실행','보류') ORDER BY created DESC LIMIT 150))
            SELECT j.id,j.kind,j.args,j.status,j.error,j.result,j.created,
                coalesce(d.name,f.title,t.name,q.title) subject,p.location
            FROM visible j LEFT JOIN passages p ON p.id=(j.args->>'passage')::uuid
            LEFT JOIN documents d ON d.id=coalesce(p.doc,(j.args->>'doc')::uuid) AND d.owner=j.owner
            LEFT JOIN facts f ON f.id=(j.args->>'id')::uuid AND f.owner=j.owner
            LEFT JOIN tags t ON t.id=(j.args->>'tag')::uuid AND t.owner=j.owner
            LEFT JOIN questions q ON q.id=(j.args->>'question')::uuid
            ORDER BY j.created DESC""",
    }
    with connect() as db:
        return {
            key: db.execute(sql, (owner,)).fetchall() for key, sql in queries.items()
        }


@app.get("/api/imports/options")
def import_options(owner: U):
    return {"extensions": sorted(EXTENSIONS), "ignored_dirs": sorted(IGNORED_DIRS)}


@app.post("/api/imports")
def import_item(owner: U, file: UploadFile | None = File(None), text: str = Form(""),
                relative_path: str = Form(""), application: ID | None = Form(None)):
    if file and file.filename:
        try:
            path = relative_name(relative_path or file.filename)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if excluded(path):
            return {"status": "제외"}
        filename, name, data = path.name, path.name, file.file.read()
    elif text.strip():
        filename = "붙여넣기.txt"
        name = text.strip().splitlines()[0][:80]
        data = text.encode("utf8")
        path = None
    else:
        raise HTTPException(422, "파일을 선택하거나 본문을 붙여넣어 주세요.")
    digest = hashlib.sha256(data).hexdigest()
    paths = [str(path)] if path else []
    with connect() as db:
        if application:
            one(db, "SELECT id FROM applications WHERE owner=%s AND id=%s", (owner, application))
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (owner + digest,))
        old = db.execute("""SELECT id, metadata FROM documents WHERE owner=%s AND app IS NOT DISTINCT FROM %s
            AND digest=%s AND active AND kind<>'보고서 자료' ORDER BY created,id LIMIT 1 FOR UPDATE""", (owner, application, digest)).fetchone()
        if old:
            all_paths = old["metadata"].get("folder_paths", [])
            if paths and paths[0] not in all_paths:
                db.execute("UPDATE documents SET metadata=metadata || %s WHERE id=%s",
                           (Jsonb({"folder_paths": [*all_paths, *paths]}), old["id"]))
            return {"status": "중복", "id": old["id"]}
        image = Path(filename).suffix.lower() in IMAGES
        result = store_doc(db, owner, name, filename, data, "이미지" if image else "분류 대기", application,
                           {"auto_classify": not image, "folder_paths": paths or [filename]})
        return {"status": "등록", **result}


@app.get("/api/documents/{doc}/image")
def document_image(doc: ID, owner: U):
    with connect() as db:
        row = one(db, "SELECT filename,digest FROM documents WHERE owner=%s AND id=%s", (owner, doc))
    mime = IMAGES.get(Path(row["filename"]).suffix.lower())
    if not mime:
        raise HTTPException(404, "이미지 자료가 아닙니다.")
    return FileResponse(FILES / owner / row["digest"], media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/documents/{doc}/original")
def original_document(doc: ID, owner: U):
    with connect() as db:
        row = one(db, "SELECT filename,digest FROM documents WHERE owner=%s AND id=%s", (owner, doc))
    return FileResponse(FILES / owner / row["digest"], filename=Path(row["filename"]).name,
                        media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff"})


@app.post("/api/documents")
def upload(
    owner: U,
    name: str = Form(""),
    kind: str = Form("개인 자료"),
    application: ID | None = Form(None),
    source: str = Form(""),
    checked: str = Form(""),
    past_role: str = Form(""),
    past_question: str = Form(""),
    text: str = Form(""),
    file: UploadFile | None = File(None),
):
    filename = file.filename if file and file.filename else "붙여넣기.txt"
    data = file.file.read() if file and file.filename else text.encode("utf8")
    with connect() as db:
        return store_doc(
            db,
            owner,
            name.strip() or filename,
            filename,
            data,
            kind,
            application,
            {
                "source": source,
                "checked": checked,
                "role": past_role,
                "question": past_question,
                "chars": len(text),
            },
        )


@app.get("/api/documents/{doc}/passages")
def passages(doc: ID, owner: U):
    with connect() as db:
        one(db, "SELECT id FROM documents WHERE owner=%s AND id=%s", (owner, doc))
        return db.execute(
            "SELECT id,location,body FROM passages WHERE doc=%s ORDER BY pos", (doc,)
        ).fetchall()


@app.post("/api/documents/{doc}/version")
def replace_doc(doc: ID, owner: U, file: UploadFile = File(...)):
    with connect() as db:
        old = one(
            db,
            "SELECT * FROM documents WHERE id=%s AND owner=%s AND active FOR UPDATE",
            (doc, owner),
        )
        return store_doc(
            db,
            owner,
            old["name"],
            file.filename,
            file.file.read(),
            old["kind"],
            old["app"],
            old["metadata"],
            analyze=old["kind"] != "보고서 자료",
            previous=doc,
            version=old["version"] + 1,
        )


@app.get("/api/passages/{pid}")
def passage(pid: ID, owner: U):
    with connect() as db:
        return one(
            db,
            """SELECT p.id,p.body,p.location,d.name,d.metadata,d.active,d.version FROM passages p JOIN documents d ON d.id=p.doc
            WHERE p.id=%s AND d.owner=%s""",
            (pid, owner),
        )


@app.delete("/api/documents/{doc}")
def delete_doc(doc: ID, owner: U):
    with connect() as db:
        d = one(
            db,
            "SELECT digest,kind FROM documents WHERE id=%s AND owner=%s",
            (doc, owner),
        )
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (owner + d["digest"],),
        )
        one(db, "SELECT id FROM documents WHERE id=%s AND owner=%s FOR UPDATE", (doc, owner))
        db.execute(
            "DELETE FROM links WHERE target_type='passage' AND target IN(SELECT id FROM passages WHERE doc=%s)",
            (doc,),
        )
        db.execute("DELETE FROM documents WHERE id=%s", (doc,))
        if d["kind"] != "보고서 자료":
            db.execute(
                """UPDATE facts f SET status='출처 검토',embedding=NULL WHERE owner=%s AND NOT EXISTS
                (SELECT 1 FROM citations c WHERE c.fact=f.id)""",
                (owner,),
            )
        if not db.execute(
            "SELECT id FROM documents WHERE owner=%s AND digest=%s",
            (owner, d["digest"]),
        ).fetchone():
            # Commit the database change first. Recheck under the same content lock before removing the file.
            db.commit()
            db.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (owner + d["digest"],),
            )
            if not db.execute(
                "SELECT id FROM documents WHERE owner=%s AND digest=%s",
                (owner, d["digest"]),
            ).fetchone():
                (FILES / owner / d["digest"]).unlink(missing_ok=True)
    return {"deleted": True}


class FactInput(BaseModel):
    title: Text
    body: Text
    role: str = ""
    experience: ID | None = None
    tags: list[str] = []
    version: int = 1


@app.get("/api/facts/{fid}/candidates")
def fact_candidates(fid: ID, owner: U):
    from difflib import SequenceMatcher

    with connect() as db:
        f = one(
            db,
            "SELECT id,title,body,experience FROM facts WHERE id=%s AND owner=%s",
            (fid, owner),
        )
        rows = db.execute(
            """SELECT id,title,body,experience FROM facts WHERE owner=%s AND id<>%s
            AND status='확정'""",
            (owner, fid),
        ).fetchall()
        candidates = []
        for r in rows:
            similar = SequenceMatcher(None, f["title"], r["title"]).ratio()
            if similar < 0.55 and (
                not f["experience"] or f["experience"] != r["experience"]
            ):
                continue
            refs = db.execute(
                "SELECT passage FROM citations WHERE fact=%s", (r["id"],)
            ).fetchall()
            candidates.append(
                {
                    **r,
                    "similarity": similar,
                    "refs": [x["passage"] for x in refs],
                }
            )
        return sorted(candidates, key=lambda item: item["similarity"], reverse=True)[:6]


def experience_id(db, owner, i):
    if i.experience:
        return one(
            db,
            "SELECT id FROM experiences WHERE owner=%s AND id=%s",
            (owner, i.experience),
        )["id"]
    return one(
        db,
        "INSERT INTO experiences(owner,name) VALUES(%s,%s) RETURNING id",
        (owner, i.title),
    )["id"]


def finish_fact(db, owner, fid, version, tags):
    for name in tags:
        if name.strip():
            tid = add_tag(db, owner, name)
            db.execute("UPDATE tags SET proposed=false WHERE id=%s", (tid,))
            link(db, tid, "fact", fid, "확정")
    enqueue(
        db,
        owner,
        "index_fact",
        {"id": str(fid), "version": version},
        f"fact:{fid}:{version}",
    )


@app.post("/api/facts")
def direct(i: FactInput, owner: U):
    with connect() as db:
        index_lock(db, owner)
        raw = (i.role + "\n" + i.body).strip()
        d = store_doc(
            db,
            owner,
            i.title,
            "직접 입력.txt",
            raw.encode(),
            "직접 입력",
            None,
            {},
            False,
        )
        # Direct input is already one reviewed excerpt. Replace the pending parser with the original position.
        db.execute("DELETE FROM jobs WHERE id=%s", (d["job"],))
        pid = one(
            db,
            "INSERT INTO passages(doc,pos,location,body) VALUES(%s,0,'직접 입력',%s) RETURNING id",
            (d["id"], raw),
        )["id"]
        db.execute("UPDATE documents SET status='원문 준비' WHERE id=%s", (d["id"],))
        f = one(
            db,
            """INSERT INTO facts(owner,experience,title,body,role,status) VALUES(%s,%s,%s,%s,%s,'확정')
            RETURNING id""",
            (owner, experience_id(db, owner, i), i.title, i.body, i.role),
        )
        db.execute("INSERT INTO citations VALUES(%s,%s)", (f["id"], pid))
        finish_fact(db, owner, f["id"], 1, i.tags)
        return f


@app.patch("/api/facts/{fid}")
def review(fid: ID, i: FactInput, owner: U):
    with connect() as db:
        index_lock(db, owner)
        f = one(
            db, "SELECT * FROM facts WHERE owner=%s AND id=%s FOR UPDATE", (owner, fid)
        )
        if f["version"] != i.version:
            raise HTTPException(
                409, "다른 창에서 수정한 내용이 있습니다. 다시 열어 주세요."
            )
        eid = experience_id(db, owner, i)
        db.execute(
            """UPDATE facts SET experience=%s,title=%s,body=%s,role=%s,status='확정',version=version+1,
            embedding=NULL WHERE id=%s""",
            (eid, i.title, i.body, i.role if "role" in i.model_fields_set else f["role"], fid),
        )
        db.execute(
            """UPDATE links l SET state='제외' FROM tags t WHERE l.tag=t.id AND l.target_type='fact'
            AND l.target=%s AND NOT(t.name=ANY(%s))""",
            (fid, i.tags),
        )
        finish_fact(db, owner, fid, i.version + 1, i.tags)
        return {"id": fid}


class TagInput(BaseModel):
    name: Text
    description: str = ""
    aliases: list[str] = []
    parent: ID | None = None


@app.post("/api/tags")
def tag(i: TagInput, owner: U):
    with connect() as db:
        if i.parent:
            one(db, "SELECT id FROM tags WHERE id=%s AND owner=%s", (i.parent, owner))
        tid = add_tag(db, owner, i.name)
        if (
            i.parent
            and db.execute(
                """WITH RECURSIVE descendants AS (SELECT id FROM tags WHERE id=%s
            UNION SELECT t.id FROM tags t JOIN descendants d ON d.id=t.parent) SELECT 1 FROM descendants WHERE id=%s""",
                (tid, i.parent),
            ).fetchone()
        ):
            raise HTTPException(422, "하위 태그를 상위 태그로 지정할 수 없습니다.")
        db.execute(
            "UPDATE tags SET description=%s,parent=%s,aliases=%s,proposed=false WHERE id=%s",
            (i.description, i.parent, i.aliases, tid),
        )
        return {"id": tid, "job": enqueue(db, owner, "retag", {"tag": str(tid)})}


class LinkInput(BaseModel):
    tag: ID
    kind: Literal["fact", "passage", "paragraph"]
    target: ID
    state: Literal["확정", "제외"]


@app.patch("/api/tags/{tid}")
def rename_tag(tid: ID, i: TagInput, owner: U):
    with connect() as db:
        one(db, "SELECT id FROM tags WHERE owner=%s AND id=%s FOR UPDATE", (owner, tid))
        exists = db.execute(
            "SELECT id FROM tags WHERE owner=%s AND name=%s AND id<>%s",
            (owner, i.name, tid),
        ).fetchone()
        if exists:
            raise HTTPException(
                409, "같은 이름의 태그가 있습니다. 병합할 태그를 선택하세요."
            )
        if i.parent:
            one(db, "SELECT id FROM tags WHERE id=%s AND owner=%s", (i.parent, owner))
            if db.execute(
                """WITH RECURSIVE tree AS (SELECT id FROM tags WHERE id=%s UNION SELECT t.id FROM tags t
                JOIN tree ON t.parent=tree.id) SELECT id FROM tree WHERE id=%s""",
                (tid, i.parent),
            ).fetchone():
                raise HTTPException(422, "태그 계층이 순환합니다.")
        db.execute(
            "UPDATE tags SET name=%s,description=%s,aliases=%s,parent=%s,proposed=false WHERE id=%s",
            (i.name, i.description, i.aliases, i.parent, tid),
        )
        return {"id": tid, "job": enqueue(db, owner, "retag", {"tag": str(tid)})}


@app.delete("/api/tags/{tid}")
def remove_tag(tid: ID, owner: U, merge_into: ID | None = None):
    with connect() as db:
        one(db, "SELECT id FROM tags WHERE owner=%s AND id=%s FOR UPDATE", (owner, tid))
        if merge_into:
            if merge_into == tid:
                raise HTTPException(422, "다른 태그를 선택하세요.")
            one(
                db,
                "SELECT id FROM tags WHERE owner=%s AND id=%s FOR UPDATE",
                (owner, merge_into),
            )
            db.execute(
                """INSERT INTO links SELECT %s,target_type,target,state FROM links WHERE tag=%s
                ON CONFLICT(tag,target_type,target) DO UPDATE SET state=CASE
                WHEN '제외' IN (links.state,excluded.state) THEN '제외' WHEN '확정' IN (links.state,excluded.state)
                THEN '확정' ELSE '제안' END""",
                (merge_into, tid),
            )
        db.execute("DELETE FROM tags WHERE id=%s", (tid,))
        return {"deleted": tid}


@app.post("/api/links")
def tag_link(i: LinkInput, owner: U):
    with connect() as db:
        one(db, "SELECT id FROM tags WHERE id=%s AND owner=%s", (i.tag, owner))
        sql = {
            "fact": "SELECT id FROM facts WHERE id=%s AND owner=%s",
            "paragraph": "SELECT id FROM paragraphs WHERE id=%s AND owner=%s",
            "passage": "SELECT p.id FROM passages p JOIN documents d ON d.id=p.doc WHERE p.id=%s AND d.owner=%s",
        }
        one(db, sql[i.kind], (i.target, owner))
        link(db, i.tag, i.kind, i.target, i.state)
        return {"state": i.state}


@app.post("/api/facts/{fid}/merge/{target}")
def combine_sources(fid: ID, target: ID, owner: U):
    if fid == target:
        raise HTTPException(422, "서로 다른 사실을 선택하세요.")
    with connect() as db:
        rows = db.execute(
            "SELECT id,experience FROM facts WHERE owner=%s AND id=ANY(%s) ORDER BY id FOR UPDATE",
            (owner, [fid, target]),
        ).fetchall()
        if len(rows) != 2:
            raise HTTPException(404, "사실을 찾을 수 없습니다.")
        db.execute(
            "INSERT INTO citations SELECT %s,passage FROM citations WHERE fact=%s ON CONFLICT DO NOTHING",
            (target, fid),
        )
        db.execute(
            """INSERT INTO links SELECT tag,'fact',%s,state FROM links WHERE target_type='fact' AND target=%s
            ON CONFLICT DO NOTHING""",
            (target, fid),
        )
        db.execute(
            "UPDATE facts SET status='통합됨',embedding=NULL WHERE id=%s", (fid,)
        )
        return {"id": target}


class QuestionInput(BaseModel):
    title: Text
    max_chars: int = Field(default=700, gt=0, le=100000)
    spaces: bool = True


class ApplicationInput(QuestionInput):
    company: Text
    role: Text


def create_q(db, aid, i):
    return one(
        db,
        "INSERT INTO questions(app,title,max_chars,spaces) VALUES(%s,%s,%s,%s) RETURNING id",
        (aid, i.title, i.max_chars, i.spaces),
    )["id"]


@app.post("/api/applications")
def application(i: ApplicationInput, owner: U):
    with connect() as db:
        aid = one(
            db,
            "INSERT INTO applications(owner,company,role) VALUES(%s,%s,%s) RETURNING id",
            (owner, i.company, i.role),
        )["id"]
        return {"id": aid, "question": create_q(db, aid, i)}


@app.post("/api/applications/{aid}/questions")
def new_q(aid: ID, i: QuestionInput, owner: U):
    with connect() as db:
        one(db, "SELECT id FROM applications WHERE id=%s AND owner=%s", (aid, owner))
        return {"id": create_q(db, aid, i)}


class Block(BaseModel):
    block_id: ID = Field(default_factory=uuid.uuid4)
    body: str = Field(max_length=100000)
    paragraph: ID | None = None
    minimum: int | None = Field(default=None, ge=0, le=100000)
    maximum: int | None = Field(default=None, ge=1, le=100000)


class DraftInput(BaseModel):
    version: int
    blocks: list[Block]


@app.put("/api/questions/{qid}")
def save(qid: ID, i: DraftInput, owner: U):
    if len({b.block_id for b in i.blocks}) != len(i.blocks):
        raise HTTPException(422, "같은 문단이 두 번 포함되어 있습니다.")
    with connect() as db:
        q = question(db, owner, qid)
        for b in i.blocks:
            if b.maximum is not None and (b.minimum or 0) > b.maximum:
                raise HTTPException(422, "문단의 최소 글자 수가 최대 글자 수보다 큽니다.")
            if b.paragraph:
                one(
                    db,
                    "SELECT id FROM paragraphs WHERE id=%s AND owner=%s",
                    (b.paragraph, owner),
                )
        row = db.execute(
            "UPDATE questions SET blocks=%s,rev=rev+1 WHERE id=%s AND rev=%s RETURNING rev",
            (Jsonb([b.model_dump(mode="json") for b in i.blocks]), qid, i.version),
        ).fetchone()
        if not row:
            raise HTTPException(
                409, "최신 저장본과 충돌했습니다. 입력을 복사하고 다시 열어 주세요."
            )
        return {
            **row,
            "count": count_chars("\n\n".join(b.body for b in i.blocks), q["spaces"]),
        }


class WritingInput(BaseModel):
    question: ID
    target_block: ID | None = None
    minimum: int = Field(default=240, ge=1)
    maximum: int = Field(default=300, le=6000)
    instruction: str = Field(default="", max_length=2000)


class GenerateInput(WritingInput):
    experience: ID
    tags: list[Text] = Field(min_length=1, max_length=12)
    narrative: bool = False
    previous: ID | None = None


class InstructionInput(WritingInput):
    instruction: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    narrative: bool = False
    previous: ID | None = None


@app.post("/api/generate/instructions")
def instruction_generation(i: InstructionInput, owner: U):
    if i.minimum > i.maximum:
        raise HTTPException(422, "최소 글자 수가 최대값보다 큽니다.")
    with connect() as db:
        q = question(db, owner, i.question)
        if i.previous:
            one(db, "SELECT id FROM paragraphs WHERE id=%s AND owner=%s", (i.previous, owner))
        args = {**i.model_dump(mode="json"), **writing_position(q, i.target_block), "mode": "instructions"}
        return {"job": enqueue(db, owner, "generate", args)}


def writing_position(q, target):
    """Freeze this question's preceding text when the user clicks Generate."""
    if not target:
        return {}
    preceding = []
    for index, block in enumerate(q["blocks"]):
        if block.get("block_id") == str(target):
            return {"target_block": str(target), "block_number": index + 1,
                    "preceding_text": "\n\n".join(preceding)[-1800:],
                    "target_text": block["body"].strip()[:800],
                    "target_digest": hashlib.sha256(block["body"].encode()).hexdigest()}
        if block["body"].strip():
            preceding.append(block["body"].strip())
    raise HTTPException(409, "선택한 문단이 원고에 없습니다. 문단을 다시 선택해 주세요.")


@app.post("/api/generate/free")
def free_generation(i: WritingInput, owner: U):
    if i.minimum > i.maximum:
        raise HTTPException(422, "최소 글자 수가 최대값보다 큽니다.")
    with connect() as db:
        q = question(db, owner, i.question)
        args = {**i.model_dump(mode="json"), **writing_position(q, i.target_block), "mode": "free"}
        return {"job": enqueue(db, owner, "generate", args)}


@app.post("/api/generate")
def generation(i: GenerateInput, owner: U):
    if i.minimum > i.maximum:
        raise HTTPException(422, "최소 글자 수가 최대값보다 큽니다.")
    with connect() as db:
        q = question(db, owner, i.question)
        one(
            db,
            "SELECT id FROM experiences WHERE id=%s AND owner=%s",
            (i.experience, owner),
        )
        if i.previous:
            one(
                db,
                "SELECT id FROM paragraphs WHERE id=%s AND owner=%s",
                (i.previous, owner),
            )
        args = i.model_dump(mode="json")
        args.update(writing_position(q, i.target_block))
        args["tags"] = list(dict.fromkeys(args["tags"]))
        return {"job": enqueue(db, owner, "generate", args)}


@app.get("/api/questions/{qid}/recommendations")
def recommendations(qid: ID, owner: U):
    with connect() as db:
        q = question(db, owner, qid)
        with using(load_models(db, owner, " FOR SHARE")):
            facts, sources = retrieve(db, owner, q)
        for f in facts:
            f["tags"] = [
                r["name"]
                for r in db.execute(
                    """SELECT t.name FROM links l JOIN tags t ON t.id=l.tag
                WHERE l.target=%s AND l.target_type='fact' AND l.state='확정'""",
                    (f["id"],),
                )
            ]
            f["reason"] = f"{q['role']} 지원 문항과 의미가 가까운 경험입니다. " + (
                "검토한 관점은 " + ", ".join(f["tags"]) + "입니다."
                if f["tags"]
                else "아래의 원문 근거를 확인하세요."
            )
        return {
            "facts": facts,
            "sources": sources,
            "question": q["title"],
            "role": q["role"],
        }


class LibraryInput(BaseModel):
    body: Text
    saved: bool = True


@app.post("/api/paragraphs/{pid}")
def keep(pid: ID, i: LibraryInput, owner: U):
    with connect() as db:
        p = one(db, "SELECT * FROM paragraphs WHERE owner=%s AND id=%s", (owner, pid))
        if p["body"] == i.body:
            db.execute("UPDATE paragraphs SET saved=%s WHERE id=%s", (i.saved, pid))
            return {"id": pid}
        return edited_version(db, p, i.body, i.saved)


def edited_version(db, p, body, saved):
    details = dict(p["details"])
    q = question(db, p["owner"], p["question"])
    details["count"] = count_chars(body, q["spaces"])
    details["within"] = details["minimum"] <= details["count"] <= details["maximum"]
    details["expanded"] = [s for s in details["expanded"] if s in body]
    details["review"] = "사용자 편집본" if details.get("mode") == "free" else "사용자 편집본 · 근거 재검토"
    for name in ("attempts", "tag_checks", "selected_round", "stop_reason", "factual_issues", "terms_to_review"):
        details.pop(name, None)
    details["accepted"] = False
    return one(
        db,
        """INSERT INTO paragraphs(owner,question,experience,previous,body,details,saved)
         VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (
            p["owner"],
            p["question"],
            p["experience"],
            p["id"],
            body,
            Jsonb(details),
            saved,
        ),
    )


class ReviseInput(BaseModel):
    body: Text
    target_block: ID | None = None
    request_id: ID
    narrative: bool = False


@app.post("/api/paragraphs/{pid}/revise")
def revise(pid: ID, i: ReviseInput, owner: U):
    with connect() as db:
        p = one(db, "SELECT * FROM paragraphs WHERE id=%s AND owner=%s FOR UPDATE", (pid, owner))
        key = f"revise:{pid}:{i.request_id}"
        target = p["details"].get("target_block") or i.target_block
        position = writing_position(question(db, owner, p["question"]), target)
        signature = hashlib.sha256(f"{pid}:{position}:{i.narrative}:{i.body}".encode()).hexdigest()
        existing = db.execute(
            """SELECT id FROM jobs WHERE owner=%s AND (key=%s OR
               (status IN ('대기','실행') AND args->>'revision_signature'=%s)) LIMIT 1""",
            (owner, key, signature),
        ).fetchone()
        if existing:
            return {"job": existing["id"]}
        parent = pid if p["body"] == i.body else edited_version(db, p, i.body, False)["id"]
        details = p["details"]
        args = WritingInput(
            question=p["question"],
            minimum=details["minimum"], maximum=details["maximum"],
            instruction=details.get("instruction", ""),
        ).model_dump(mode="json")
        if details.get("mode") == "free":
            args.update(mode="free")
        elif details.get("mode") == "instructions":
            args.update(mode="instructions", tags=details["tags"], narrative=i.narrative)
        else:
            args.update(experience=str(p["experience"]), tags=details["tags"], narrative=i.narrative)
        args["previous"] = str(parent)
        args.update(position)
        args.update(manual_revision=True, revise_from=str(pid), revision_signature=signature)
        return {"job": enqueue(db, owner, "generate", args, key)}


@app.post("/api/jobs/clear-completed")
def clear_completed(owner: U):
    with connect() as db:
        cleared = db.execute("UPDATE jobs SET cleared=true WHERE owner=%s AND status='완료' AND NOT cleared", (owner,))
        return {"cleared": cleared.rowcount}


@app.post("/api/applications/{aid}/research")
def research(aid: ID, owner: U):
    with connect() as db:
        one(db, "SELECT id FROM applications WHERE id=%s AND owner=%s FOR UPDATE", (aid, owner))
        pending = db.execute("""SELECT id FROM jobs WHERE owner=%s AND kind='research_company'
            AND args->>'application'=%s AND status IN ('대기','실행','보류') ORDER BY created LIMIT 1""",
            (owner, str(aid))).fetchone()
        return {"job": pending["id"] if pending else enqueue(db, owner, "research_company", {"application": str(aid)})}


@app.get("/api/jobs/{jid}")
def job_status(jid: ID, owner: U):
    with connect() as db:
        row = one(
            db,
            "SELECT id,status,result,error,args FROM jobs WHERE id=%s AND owner=%s",
            (jid, owner),
        )
        row["rounds"] = db.execute(
            """SELECT round,created,assessment FROM generation_rounds
            WHERE job=%s AND owner=%s ORDER BY created,round""",
            (jid, owner),
        ).fetchall()
        return row


@app.post("/api/jobs/{jid}/retry")
def retry(jid: ID, owner: U):
    with connect() as db:
        job = one(
            db,
            "SELECT status,kind,args FROM jobs WHERE id=%s AND owner=%s FOR UPDATE",
            (jid, owner),
        )
        if job["status"] != "실패":
            raise HTTPException(409, "실패한 작업만 재시도할 수 있습니다.")
        args = job["args"]
        if job["kind"] == "generate":
            last = db.execute(
                """SELECT draft,assessment FROM generation_rounds
                   WHERE job=%s AND owner=%s ORDER BY created DESC LIMIT 1""", (jid, owner),
            ).fetchone()
            if last and last["assessment"] and last["assessment"].get("valid") is False:
                args["rejected_draft"] = last["draft"]["body"]
                args["validation_feedback"] = last["assessment"]["feedback"]
                args["validation_retry"] = args.get("validation_retry", 0) + 1
        db.execute(
            "UPDATE jobs SET status='대기',attempts=0,error=NULL,args=%s WHERE id=%s", (Jsonb(args), jid)
        )
        return {"job": jid}


@app.post("/api/jobs/{jid}/{action}")
def control_job(jid: ID, action: Literal["pause", "resume", "cancel"], owner: U):
    target, allowed = {
        "pause": ("보류", ("대기", "실행")),
        "resume": ("대기", ("보류",)),
        "cancel": ("취소", ("대기", "실행", "보류")),
    }[action]
    with connect() as db:
        job = one(db, "SELECT status FROM jobs WHERE id=%s AND owner=%s FOR UPDATE", (jid, owner))
        status = job["status"]
        if status == target or (action == "resume" and status == "실행"):
            return {"job": jid, "status": status}
        if status not in allowed:
            raise HTTPException(409, f"현재 작업 상태({status})에서는 이 동작을 수행할 수 없습니다.")
        db.execute("""UPDATE jobs SET status=%s,lease=NULL,error=NULL,updated=clock_timestamp()
                      WHERE id=%s""", (target, jid))
        return {"job": jid, "status": target}
