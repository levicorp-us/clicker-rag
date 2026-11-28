import uuid
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .core import U, connect, enqueue, one
from .documents import store_doc
from .imports import EXTENSIONS, IMAGES
from .report_docx import MAX_FILE, MIME, WordForm
from .report_jobs import Slot

router = APIRouter(prefix="/api/reports")
ID = uuid.UUID


def upload_bytes(file):
    data = file.file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise HTTPException(413, "25MB 이하의 파일을 선택하세요.")
    return data


def document(data):
    try:
        return WordForm(data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def download(form, name):
    return Response(
        form.pack(),
        media_type=MIME,
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''"
            + quote(name + ".docx", safe=""),
            "Cache-Control": "no-store",
        },
    )


def template(db, owner, tid, lock=False):
    return one(
        db,
        "SELECT * FROM report_templates WHERE owner=%s AND id=%s"
        + (" FOR UPDATE" if lock else ""),
        (owner, tid),
    )


@router.get("")
def catalog(owner: U):
    with connect() as db:
        templates = db.execute(
            """SELECT id,name,ready,tags,source_doc FROM report_templates
            WHERE owner=%s ORDER BY created DESC""",
            (owner,),
        ).fetchall()
        reports = db.execute(
            """SELECT r.id,r.title,t.name,t.tags FROM reports r JOIN report_templates t ON t.id=r.template
            WHERE r.owner=%s ORDER BY r.created DESC""",
            (owner,),
        ).fetchall()
        return {"templates": templates, "reports": reports}


@router.post("/templates")
def import_template(owner: U, file: Annotated[UploadFile, File()]):
    name = Path(file.filename or "").name
    if not name.lower().endswith(".docx"):
        raise HTTPException(422, "양식은 DOCX 파일로 등록하세요.")
    data = upload_bytes(file)
    document(data)
    with connect() as db:
        tid = one(
            db,
            "INSERT INTO report_templates(owner,name,package) VALUES(%s,%s,%s) RETURNING id",
            (owner, Path(name).stem[:200], data),
        )["id"]
        return {
            "id": tid,
            "job": enqueue(
                db, owner, "analyze_template", {"template": str(tid), "subject": name}
            ),
        }


@router.get("/templates/{tid}")
def review_template(tid: ID, owner: U):
    with connect() as db:
        row = template(db, owner, tid)
        job = db.execute(
            "SELECT id,status,error FROM jobs WHERE owner=%s AND kind='analyze_template' AND args->>'template'=%s ORDER BY created DESC LIMIT 1",
            (owner, str(tid)),
        ).fetchone()
    return {
        k: row[k] for k in ("id", "name", "ready", "fields", "tags", "source_doc")
    } | {"slots": document(row["package"]).inventory(), "analysis": job}


@router.get("/templates/{tid}/file")
def template_file(tid: ID, owner: U, marked: bool = False):
    with connect() as db:
        row = template(db, owner, tid)
    form = document(row["package"])
    if marked:
        if row["ready"]:
            for f in row["fields"]:
                form.replace(
                    f["key"], f["prefix"], "⟦CLICKER:" + f["key"] + "⟧", f["anchor"]
                )
        else:
            form.mark(form.slots)
    return download(form, row["name"])


class Confirmation(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    fields: list[Slot] = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(min_length=1, max_length=20)


def blank_fields(form, selected):
    fields, contents, seen = [], [], set()
    for f in selected:
        if f.key not in form.slots or f.key in seen or not f.label.strip():
            raise HTTPException(422, "칸의 위치와 이름을 확인하세요.")
        seen.add(f.key)
        old = form.slots[f.key][2]
        if not old.startswith(f.prefix):
            raise HTTPException(422, "고정 문구가 원문 시작 부분과 다릅니다.")
        anchor = form.replace(f.key, f.prefix, "")
        fields.append({**f.model_dump(), "anchor": anchor})
        if f.keep_as_source and old[len(f.prefix) :].strip():
            contents.append(f.label + " · " + old[len(f.prefix) :].strip())
    return fields, contents


@router.post("/templates/{tid}/preview")
def preview_blank(tid: ID, fields: list[Slot], owner: U):
    with connect() as db:
        row = template(db, owner, tid)
    form = document(row["package"])
    blank_fields(form, fields)
    form.mark(form.slots)
    return download(form, row["name"])


@router.post("/templates/{tid}/confirm")
def confirm(tid: ID, i: Confirmation, owner: U):
    with connect() as db:
        row = template(db, owner, tid, True)
        if row["ready"]:
            raise HTTPException(409, "이미 등록한 양식입니다.")
        form = document(row["package"])
        tags = list(dict.fromkeys(t.strip()[:80] for t in i.tags if t.strip()))
        if not tags or not i.name.strip():
            raise HTTPException(422, "양식 이름과 태그를 입력하세요.")
        fields, contents = blank_fields(form, i.fields)
        doc = None
        if contents:
            doc = store_doc(
                db,
                owner,
                i.name + " · 기존 내용",
                i.name + ".txt",
                "\n".join(contents).encode(),
                "보고서 자료",
                None,
                {"template": str(tid)},
                analyze=False,
            )["id"]
        db.execute(
            "UPDATE report_templates SET name=%s,fields=%s,tags=%s,package=%s,source_doc=%s,ready=true WHERE id=%s",
            (i.name.strip(), Jsonb(fields), tags, form.pack(), doc, tid),
        )
        return {"id": tid, "source_doc": doc}


@router.post("/materials")
def material(
    owner: U,
    file: Annotated[UploadFile | None, File()] = None,
    text: Annotated[str, Form()] = "",
):
    if file and file.filename:
        name = Path(file.filename).name
        if Path(name).suffix.lower() not in EXTENSIONS - IMAGES.keys():
            raise HTTPException(422, "텍스트를 추출할 수 있는 문서 파일을 선택하세요.")
        data = upload_bytes(file)
    else:
        if not text.strip() or len(text) > 100000:
            raise HTTPException(422, "업무 자료를 입력하세요 (최대 10만 자).")
        name = text.strip().splitlines()[0][:80] + ".txt"
        data = text.encode()
    with connect() as db:
        return store_doc(
            db, owner, name, name, data, "보고서 자료", None, {}, analyze=False
        )


@router.get("/materials")
def materials(owner: U):
    with connect() as db:
        return db.execute(
            """SELECT d.id,d.name,d.status,ARRAY(SELECT DISTINCT t.name FROM passages p
            JOIN links l ON l.target=p.id AND l.target_type='passage' AND l.state<>'제외'
            JOIN tags t ON t.id=l.tag WHERE p.doc=d.id ORDER BY t.name) tags
            FROM documents d WHERE d.owner=%s AND d.kind='보고서 자료' AND d.active ORDER BY d.created DESC""",
            (owner,),
        ).fetchall()


class NewReport(BaseModel):
    template: ID
    title: str = Field(min_length=1, max_length=200)


@router.post("")
def create(i: NewReport, owner: U):
    with connect() as db:
        t = one(
            db,
            "SELECT fields FROM report_templates WHERE id=%s AND owner=%s AND ready",
            (i.template, owner),
        )
        rid = one(
            db,
            "INSERT INTO reports(owner,template,title) VALUES(%s,%s,%s) RETURNING id",
            (owner, i.template, i.title),
        )["id"]
        with db.cursor() as cur:
            cur.executemany(
                "INSERT INTO report_cells(report,key) VALUES(%s,%s)",
                [(rid, f["key"]) for f in t["fields"]],
            )
        return {"id": rid}


def detail(db, owner, rid):
    r = one(
        db,
        """SELECT r.id,r.title,r.template,t.name,t.fields,t.tags FROM reports r JOIN report_templates t ON t.id=r.template
        WHERE r.id=%s AND r.owner=%s""",
        (rid, owner),
    )
    r["cells"] = {
        c["key"]: c
        for c in db.execute(
            "SELECT key,body,instruction,refs,version FROM report_cells WHERE report=%s",
            (rid,),
        )
    }
    r["jobs"] = db.execute(
        """SELECT DISTINCT ON (args->>'key') id,args->>'key' AS key,status,error FROM jobs
        WHERE owner=%s AND kind='report_cell' AND args->>'report'=%s ORDER BY args->>'key',created DESC""",
        (owner, str(rid)),
    ).fetchall()
    return r


@router.get("/{rid}")
def read(rid: ID, owner: U):
    with connect() as db:
        return detail(db, owner, rid)


@router.get("/{rid}/file")
def export(rid: ID, owner: U):
    with connect() as db:
        r = detail(db, owner, rid)
        form = document(template(db, owner, r["template"])["package"])
    for f in r["fields"]:
        form.replace(f["key"], f["prefix"], r["cells"][f["key"]]["body"], f["anchor"])
    return download(form, r["title"])


class CellEdit(BaseModel):
    body: str = Field(max_length=16000)
    instruction: str = Field(max_length=4000)
    version: int = Field(ge=1)
    generate: bool = False


@router.put("/{rid}/cells/{key}")
def edit(rid: ID, key: str, i: CellEdit, owner: U):
    with connect() as db:
        c = one(
            db,
            """SELECT c.*,r.title FROM report_cells c JOIN reports r ON c.report=r.id
            WHERE r.id=%s AND r.owner=%s AND c.key=%s FOR UPDATE OF c""",
            (rid, owner, key),
        )
        if c["version"] != i.version:
            raise HTTPException(
                409, "다른 변경 사항이 있습니다. 최신 값을 확인하고 다시 적용하세요."
            )
        version = c["version"]
        if (c["body"], c["instruction"]) != (i.body, i.instruction):
            version += 1
            db.execute(
                """UPDATE report_cells SET body=%s,instruction=%s,version=%s,
                refs=CASE WHEN body=%s THEN refs ELSE '[]'::jsonb END WHERE report=%s AND key=%s""",
                (i.body, i.instruction, version, i.body, rid, key),
            )
        job = None
        if i.generate:
            pending = db.execute(
                """SELECT id FROM jobs WHERE owner=%s AND kind='report_cell' AND args->>'report'=%s
                AND args->>'key'=%s AND args->>'version'=%s AND status IN ('대기','실행','보류') LIMIT 1""",
                (owner, str(rid), key, str(version)),
            ).fetchone()
            job = (
                pending["id"]
                if pending
                else enqueue(
                    db,
                    owner,
                    "report_cell",
                    {
                        "report": str(rid),
                        "key": key,
                        "version": version,
                        "subject": c["title"],
                    },
                )
            )
        return {"version": version, "job": job}
