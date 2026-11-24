"""Choose the next writing action using measured length, source IDs and an independent reading."""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from . import models
from .job_control import current_run
from .core import connect, count_chars, PROMPT_VERSION
from .model_settings import current

class TagReading(BaseModel):
    tag: str
    status: Literal["충족", "보완", "근거부족"]
    source: str
    draft_quote: str
    source_quote: str
    feedback: str


class Finding(BaseModel):
    category: Literal["경험 사실", "기업 사실", "문항 불일치", "미표시 서사"]
    draft_quote: str = Field(min_length=2)
    reason: str = Field(min_length=2)


class Reading(BaseModel):
    tags: list[TagReading]
    factual_issues: list[Finding] = Field(max_length=4)


def is_simple_intention(quote):
    text = quote.strip().rstrip(".!")
    if not text.endswith(("겠습니다", "고 싶습니다")):
        return False
    # Past forms, role assertions and relative clauses can introduce an
    # independently checkable premise in a sentence ending in the future tense.
    premise = (
        r"[았었였했됐]|(?:으로서|로서)|(?:경험|경력|성과|실적|자격)(?:을|를|이|가|으로|에)"
        r"|\S*(?:한|된|던|낸|운|춘|은|온|준|쓴|든|진|인|본)\s"
        r"|[.!?。！？]\s"
    )
    return re.search(premise, text) is None


def read_tags(body, used, expanded, request, allowed):
    data = {
        "문항": request["문항"],
        "직무": request["직무"],
        "태그": request["태그"],
        "본문": body,
        "경험": {k: v for k, v in request["경험 근거"].items() if k in used},
        "검색한 전체 경험": request["경험 근거"],
        "검토된 태그 연결":request.get("확정된 태그 연결",{}),
        "기업": request["기업 근거"],
        "요청별 서사 허용": request["서사 허용"],
        "표시한 서사": expanded,
        "태그 정의": request.get("태그 정의", {}),
    }
    spec = Reading.model_json_schema()
    spec["properties"]["tags"].update(
        minItems=len(request["태그"]), maxItems=len(request["태그"])
    )
    if request["태그"]:
        spec["$defs"]["TagReading"]["properties"]["tag"]["enum"] = request["태그"]
    audit = models.structured(
        "review",
        "독립적인 한국어 자소서 검수자다. 각 태그를 정확히 한 번 판정한다. "
        "태그에 해당하는 구체적인 활동이나 기술이 본문에 나타나고 원문이 이를 지지하면 충족이다. "
        "개인·팀 역할 구분은 평가 항목에서 제외한다. "
        "충족이면 source에 사용된 F번호, draft_quote와 source_quote에는 각각 본문과 경험에서 연속된 짧은 원문 그대로를 복사한다. "
        "보완은 관련 사실이 있지만 표현에서 빠진 경우이며 feedback에 살릴 행동을 짧게 쓴다. "
        "검색된 사실 자체가 없으면 근거부족이다. 미래 포부만으로 경험 태그를 충족시키지 않는다. "
        "factual_issues는 실제 오류가 없으면 빈 배열이다. 오류마다 draft_quote에 검증할 수 있는 최소 주장 원문을 복사하고 "
        "reason에 어느 근거와 충돌하거나 어떤 근거가 빠졌는지 쓴다. 미래 계획은 자유롭게 쓸 수 있다. "
        "‘현장에서 기여하겠습니다’에는 사실 오류가 없다. 근거 없는 과거 성과는 해당 구절만 인용한다. "
        "단순 소감이나 칭찬도 오류 목록에서 제외한다.",
        data,
        Reading,
        spec,
    )
    observations = []
    for tag in request["태그"]:
        matching = [t for t in audit.tags if t.tag == tag]
        if len(matching) != 1:
            observations.append(
                {
                    "tag": tag,
                    "status": "보완",
                    "feedback": "태그별 독립 판정이 필요합니다.",
                }
            )
            continue
        r = matching[0].model_dump()
        if r["status"] == "충족":
            source = r["source"]
            ok = (
                source in used
                and source in allowed
                and len(r["draft_quote"].strip()) >= 2
                and "".join(r["draft_quote"].split()).casefold()
                != "".join(tag.split()).casefold()
                and r["draft_quote"] in body
                and any(
                    r["draft_quote"].rstrip(".!?") in sentence
                    and not is_simple_intention(sentence)
                    for sentence in re.split(r"(?<=[.!?。！？])\s+|\n", body)
                )
                and len(r["source_quote"].strip()) >= 2
                and any(r["source_quote"] in s for s in request["경험 근거"][source])
            )
            if not ok:
                r.update(
                    status="보완",
                    feedback="본문의 행동과 확인된 경험을 실제 발췌로 연결하세요.",
                )
        observations.append(r)
    issues = []
    for finding in audit.factual_issues:
        quote = finding.draft_quote.strip()
        if quote not in body:
            issues.append("검수자가 지적한 구절이 본문과 일치하지 않아 사실 판정을 확인해야 합니다.")
        elif (finding.category == "경험 사실" and is_simple_intention(quote)
              and re.search(r"미래|포부|계획|의지|과거 성과", finding.reason)):
            continue
        else:
            issues.append(f"{finding.category} · ‘{quote}’ · {finding.reason}")
    return observations, issues


def fingerprint(request, context):
    raw = json.dumps(
        [PROMPT_VERSION, current.get().generation, request, context],
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def checkpoint(job, owner, key, n, draft, assessment=None):
    if job is None:
        return
    # A separate short transaction preserves progress if a subsequent model call or the worker stops.
    with connect() as db:
        run = current_run.get()
        if run:
            run.lock_current(db)
        db.execute(
            """INSERT INTO generation_rounds(job,owner,input_hash,round,draft,assessment)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(job,input_hash,round) DO UPDATE SET assessment=excluded.assessment
            WHERE generation_rounds.owner=excluded.owner""",
            (
                job,
                owner,
                key,
                n,
                Jsonb(draft),
                Jsonb(assessment) if assessment else None,
            ),
        )


def run(
    request,
    instruction,
    spec,
    facts,
    sources,
    prior_companies,
    spaces,
    owner,
    job=None,
    context=None,
):
    key = fingerprint(request, [instruction, spec, context])
    prior = None
    if job:
        with connect() as db:
            prior = db.execute(
                """SELECT draft,assessment FROM generation_rounds
                   WHERE owner=%s AND job=%s AND input_hash=%s AND round=1""",
                (owner, job, key),
            ).fetchone()
    tags = request["태그"]
    lo, hi = request["글자수"]
    target = (lo + hi) // 2
    evidence = " ".join(
        [
            request["기업"],
            request["문항"],
            request["직무"],
            *(s for v in request["경험 근거"].values() for s in v),
            *request["기업 근거"].values(),
        ]
    )
    out = (
        models.Paragraph.model_validate(prior["draft"])
        if prior
        else models.structured(
            "generate", instruction, request, models.Paragraph, spec
        )
    )
    draft = out.model_dump()
    body = out.body.strip()
    if not prior:
        checkpoint(job, owner, key, 1, draft)
    if prior and prior["assessment"]:
        check = prior["assessment"]
    else:
        length = count_chars(body, spaces)
        errors = []
        cited = set(out.used)
        if not body or len(body.splitlines()) != 1:
            errors.append("한 문단으로 작성하세요.")
        if not cited <= facts.keys() | sources.keys() or not cited & facts.keys():
            errors.append("사용한 실제 경험 F번호를 확인하세요.")
        if any(name in body for name in prior_companies):
            errors.append("이전 지원 기업의 정보를 제거하세요.")
        if not all(
            num in evidence
            for num in re.findall(
                r"\d+(?:[.,]\d+)*(?:%|배|명|개월|년|회|건|점)?", body
            )
        ):
            errors.append("출처에 없는 수치를 제거하세요.")
        if (not request["서사 허용"] and out.expanded) or any(
            not x or x not in body for x in out.expanded
        ):
            errors.append("서사 허용 조건과 표시 구간을 확인하세요.")
        if not request["서사 허용"] and any(
            s not in evidence for s in re.findall(r'[“‘"]([^”’"]{3,})[”’"]', body)
        ):
            errors.append("원문에서 확인할 수 없는 인용을 제거하세요.")
        delta = max(lo - length, length - hi, 0)
        feedback = errors.copy()
        if not re.search(r'[.!?。！？](?:[”’"\)])?$|[다요]$', body):
            feedback.append("마지막 문장의 끝을 확인하세요.")
        if delta:
            direction = "줄이세요" if length > hi else "늘리세요"
            feedback.append(
                f"현재 {length}자. 약 {abs(length - target)}자를 {direction}. 목표 {lo}~{hi}자."
            )
        audit = []
        issues = []
        if not errors:
            audit, issues = read_tags(body, cited, out.expanded, request, facts)
            feedback.extend(issues)
            feedback.extend(
                f"{t['tag']} · {t['feedback'] or t['status']}"
                for t in audit
                if t["status"] != "충족"
            )
        check = {
            "count": length,
            "within": not delta,
            "delta": delta,
            "valid": not errors,
            "feedback": feedback,
            "tags": audit,
            "factual_issues": issues,
            "accepted": not feedback and len(audit) == len(tags),
        }
        checkpoint(job, owner, key, 1, draft, check)
    if not check["valid"]:
        raise ValueError(" ".join(check["feedback"]))
    result = {**draft, "raw_body": out.body, "body": body, **check, "round": 1}
    if check["accepted"]:
        reason = "조건 충족"
    elif any(t["status"] == "근거부족" for t in check["tags"]):
        reason = "태그 근거 추가 필요"
    else:
        reason = "검토 필요"
    return result, [result], reason
