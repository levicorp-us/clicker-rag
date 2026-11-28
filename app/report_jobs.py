from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import models
from .core import link, one
from .model_settings import current
from .pipeline import known_tags, proposed_tag
from .report_docx import WordForm


class Slot(BaseModel):
    key: str = Field(max_length=32)
    label: str = Field(min_length=1, max_length=100)
    prefix: str = Field(default="", max_length=1000)
    keep_as_source: bool = True


class Suggestions(BaseModel):
    fields: list[Slot]
    tags: list[str]


class CellText(BaseModel):
    body: str = Field(max_length=16000)
    sources: list[int]


def analyze_template(db, owner, args):
    row = one(
        db,
        "SELECT name,ready,package FROM report_templates WHERE owner=%s AND id=%s",
        (owner, args["template"]),
    )
    if row["ready"]:
        return {"status": "등록 완료"}
    slots = WordForm(row["package"]).inventory()
    fields, tags = [], []
    for start in range(0, len(slots), 12):
        batch = slots[start : start + 12]
        valid = {s["key"]: s for s in batch if not s["fixed_hint"]}
        out = models.structured(
            "classify",
            "보고서의 고정 제목·항목명·안내문은 유지한다. 내용이나 날짜를 교체할 칸과 빈 입력 칸만 fields에 넣는다. "
            "key는 제공된 값, label은 칸의 의미, prefix는 같은 문단 앞에 남길 고정 문구 원문(없으면 빈 문자열)이다. "
            "예시·자리표시·작성 안내는 keep_as_source=false, 실제 기록만 true로 분류한다. "
            "fixed_hint=true인 문단은 고정 문구다. label_hint가 있으면 칸 이름으로 쓴다. "
            "tags는 기존 실적과 무관한 양식 종류·용도 태그 1~3개다.",
            {
                "문서명": row["name"],
                "이전 항목": [x["text"][:60] for x in slots[max(0, start - 2) : start]],
                "문단": [{**s, "text": s["text"][:300]} for s in batch],
            },
            Suggestions,
        )
        for f in out.fields:
            if f.key in valid and valid[f.key]["text"].startswith(f.prefix):
                slot = valid[f.key]
                label = slot["label_hint"] or f.label
                if slot["text"].strip() and slot["text"].strip() in label:
                    label = slot["location"]
                fields.append({**f.model_dump(), "label": label})
                del valid[f.key]
        tags.extend(t.strip()[:80] for t in out.tags if t.strip())
    db.execute(
        "UPDATE report_templates SET fields=%s,tags=%s WHERE owner=%s AND id=%s AND NOT ready",
        (Jsonb(fields), list(dict.fromkeys(tags))[:10], owner, args["template"]),
    )
    return {"fields": len(fields)}


def report_tags(db, owner, args):
    rows = db.execute(
        """SELECT p.id,p.body FROM passages p JOIN documents d ON d.id=p.doc
        WHERE d.owner=%s AND d.id=%s AND d.kind='보고서 자료' AND d.version=%s AND d.active ORDER BY p.pos""",
        (owner, args["doc"], args["version"]),
    ).fetchall()
    existing = known_tags(db, owner)
    for p in rows:
        out = models.structured(
            "tag",
            "보고서 자료의 실제 업무·주제·기간을 나타내는 검색 태그를 1~4개 제안한다.",
            {"자료": p["body"]},
            models.MaterialTags,
        )
        for name in dict.fromkeys(t.strip()[:80] for t in out.tags if t.strip()):
            link(
                db, proposed_tag(db, owner, name, existing), "passage", p["id"], "제안"
            )
    return {"passages": len(rows)}


def report_cell(db, owner, args):
    report, key, version = args["report"], args["key"], args["version"]
    row = one(
        db,
        """SELECT r.title,t.tags,t.fields,c.* FROM reports r JOIN report_templates t ON t.id=r.template
        JOIN report_cells c ON c.report=r.id WHERE r.owner=%s AND r.id=%s AND c.key=%s""",
        (owner, report, key),
    )
    if row["version"] != version:
        raise ValueError("이 칸이 변경됐습니다. 최신 내용으로 다시 요청해 주세요.")
    label = next(f["label"] for f in row["fields"] if f["key"] == key)
    task = {
        "보고서": row["title"],
        "양식 태그": row["tags"],
        "항목": label,
        "지시사항": row["instruction"],
    }
    tags = models.structured(
        "tag",
        "항목을 작성하는 데 필요한 업무·기간·주제 검색 태그를 추출한다.",
        task,
        models.SearchTags,
    ).tags
    query = " ".join([row["title"], label, row["instruction"], *tags])
    vec = models.vectors([query])[0]
    refs = db.execute(
        """SELECT p.id,p.body,d.name,p.location FROM passages p JOIN documents d ON d.id=p.doc
        WHERE d.owner=%s AND d.active AND d.kind='보고서 자료' AND p.model=%s AND p.embedding IS NOT NULL
        ORDER BY (SELECT count(DISTINCT t.id) FROM links l JOIN tags t ON t.id=l.tag WHERE l.target=p.id
            AND l.target_type='passage' AND l.state<>'제외' AND (t.name=ANY(%s) OR t.aliases && %s::text[])) DESC,
            p.embedding <=> %s::vector,p.id LIMIT 6""",
        (owner, current.get().embedding, tags, tags, vec),
    ).fetchall()
    if not refs:
        raise ValueError(
            "검색할 보고서 자료가 없습니다. 자료를 등록하고 색인이 끝난 후 다시 요청하세요."
        )
    out = models.structured(
        "generate",
        "지정한 보고서 항목 하나만 한국어로 작성한다. 지시사항의 기간과 주제를 지켜라. "
        "사실은 제공한 번호가 있는 자료로만 뒷받침하고 사용한 자료 번호를 sources에 넣는다. "
        "이전 문구는 수정 대상이며 근거로 쓰지 않는다. 근거가 부족하면 빈 본문과 빈 sources를 반환한다.",
        {
            **task,
            "수정 대상": row["body"],
            "근거": {i: r["body"] for i, r in enumerate(refs, 1)},
        },
        CellText,
    )
    if (
        not out.body.strip()
        or not out.sources
        or any(i < 1 or i > len(refs) for i in out.sources)
    ):
        raise ValueError(
            "이 항목을 뒷받침하는 근거를 확인할 수 없습니다. 자료나 지시사항을 보완해 주세요."
        )
    used = [
        {
            "id": str(refs[i - 1]["id"]),
            "name": refs[i - 1]["name"],
            "location": refs[i - 1]["location"],
        }
        for i in dict.fromkeys(out.sources)
    ]
    saved = db.execute(
        """UPDATE report_cells SET body=%s,refs=%s,version=version+1
        WHERE report=%s AND key=%s AND version=%s""",
        (out.body.strip(), Jsonb(used), report, key, version),
    )
    if not saved.rowcount:
        raise ValueError(
            "작성 중 사용자가 칸을 수정했습니다. 최신 내용으로 다시 요청해 주세요."
        )
    return {"key": key, "sources": len(used)}
