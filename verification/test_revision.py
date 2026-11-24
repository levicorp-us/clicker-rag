"""Deterministic controller regressions. Production quality is checked against the real local models separately."""

from unittest.mock import patch
import pytest
from app import models, revision

FACT = "역할별 작업 의존관계를 확인하고 통합 일정을 조정했습니다."
DATA = {
    "기업": "지원 회사",
    "직무": "기술PM",
    "문항": "팀의 공동 목표에 어떻게 기여했나요?",
    "태그": ["일정 조율"],
    "경험 근거": {"F1": ["팀원", FACT]},
    "기업 근거": {},
    "서사 허용": False,
    "글자수": [20, 65],
}


def draft(body):
    return models.Paragraph(body=body, used=["F1"], expanded=[])


def reading(
    status="충족", span="통합 일정을 조정", source="통합 일정을 조정", issues=()
):
    return revision.Reading(
        tags=[
            revision.TagReading(
                tag="일정 조율",
                status=status,
                source="F1",
                draft_quote=span,
                source_quote=source,
                feedback=""
                if status == "충족"
                else "인계 순서를 조정한 행동을 담으세요.",
            )
        ],
        factual_issues=list(issues),
    )


def run(outputs, data=DATA):
    calls = []

    def fake(role, instruction, payload, schema, options):
        calls.append((role, payload.copy()))
        return next(sequence)

    sequence = iter(outputs)
    with patch.object(models, "structured", side_effect=fake):
        result = revision.run(data, "작성", {}, {"F1": {}}, {}, set(), True, "test")
    return result, calls


def test_pass_stops_after_one_draft():
    (w, h, why), calls = run([draft(FACT), reading()])
    assert (
        w["accepted"]
        and len(h) == 1
        and why == "조건 충족"
        and [x[0] for x in calls] == ["generate", "review"]
    )


def test_initial_draft_is_preserved_with_incomplete_ending():
    unfinished = FACT + " 다양한 소스"
    (w, _, why), calls = run([draft(unfinished), reading()])
    assert w["body"] == w["raw_body"] == unfinished and not w["accepted"]
    assert why == "검토 필요" and [role for role, _ in calls] == ["generate", "review"]


def test_length_and_tag_feedback_wait_for_a_user_request():
    other = "짧음"
    (w, h, why), calls = run([draft(other), reading("보완")])
    assert len(h) == 1 and why == "검토 필요" and not w["within"]
    assert any("2자" in line for line in w["feedback"])
    assert any("인계 순서" in line for line in w["feedback"])
    assert [role for role, _ in calls] == ["generate", "review"]


def test_unsupported_tag_needs_human_evidence():
    (w, h, why), _ = run([draft(FACT), reading("근거부족")])
    assert not w["accepted"] and why == "태그 근거 추가 필요" and len(h) == 1


def test_fabricated_source_quote_is_flagged_without_rewriting():
    (w, _, why), calls = run([draft(FACT), reading(source="없는 원문")])
    assert not w["accepted"] and why == "검토 필요" and len(calls) == 2


def test_unauthorized_number_cannot_be_returned():
    with pytest.raises(ValueError):
        run(
            [
                draft("성과를 999% 개선했습니다. " + FACT),
                draft("성과를 999% 개선했습니다. " + FACT),
            ]
        )


def test_unicode_and_space_rules():
    data = {**DATA, "글자수": [3, 3], "태그": ["일정 조율"]}
    with patch.object(
        models, "structured", side_effect=[draft("가 나 다"), reading(span="나 다")]
    ):
        w, _, _ = revision.run(data, "", {}, {"F1": {}}, {}, set(), False, "test")
    assert w["count"] == 3 and w["within"]


def test_future_plan_needs_no_historical_evidence():
    plan = "입사 후 일정 관리에 기여하겠습니다."
    issue = revision.Finding(category="경험 사실", draft_quote=plan, reason="과거 성과로 입증되지 않습니다.")
    (winner, history, _), _ = run([draft(FACT + " " + plan), reading(issues=[issue])])
    assert winner["accepted"] and len(history) == 1


def test_plan_alone_cannot_prove_a_tag():
    plan = "통합 일정을 조정하겠습니다."
    (_, history, _), _ = run([draft(FACT + " " + plan), reading(span=plan)])
    assert history[0]["tags"][0]["status"] == "보완" and len(history) == 1


@pytest.mark.parametrize("plan", [
    "도쿄일렉트론코리아의 장비 인터페이스 점검에도 기여하겠습니다",
    "센서 데이터 기반 기능 시험 시 로그와 데이터를 정밀하게 분석하겠습니다",
    "현장 문제의 원인을 빠르게 찾아 해결하는 역량을 발휘하겠습니다",
])
def test_observed_plans_are_not_past_achievements(plan):
    assert revision.is_simple_intention(plan)


def test_mixed_future_sentence_preserves_a_disputed_past_claim():
    body = "제가 개발한 장비로 일정 관리에 기여하겠습니다. " + FACT
    for quote in ("제가 개발한 장비", "제가 개발한 장비로 일정 관리에 기여하겠습니다."):
        issue = revision.Finding(category="경험 사실", draft_quote=quote, reason="장비 개발 이력이 없습니다.")
        (_, history, _), _ = run([draft(body), reading(issues=[issue])])
        assert quote in history[0]["factual_issues"][0] and len(history) == 1


def test_unknown_critic_quote_cannot_approve_a_draft():
    issue = revision.Finding(category="경험 사실", draft_quote="없는 인용", reason="근거 부족")
    (_, history, _), _ = run([draft(FACT), reading(issues=[issue])])
    assert not history[0]["accepted"] and len(history) == 1
