from __future__ import annotations

from datetime import date
from functools import wraps
import importlib
from typing import Callable


LATEST_RESEARCH_REVIEW_MARKER = "[현재 날짜·최신 검색·2중 재검증 필수]"


def build_latest_research_review_section(
    *,
    reference_date: date | None = None,
) -> str:
    created_on = (reference_date or date.today()).isoformat()
    return f"""{LATEST_RESEARCH_REVIEW_MARKER}
- 요청서 생성 기준일: {created_on}
- 실제 답변을 작성하기 시작할 때 현재 날짜를 다시 확인하고, 그 날짜를 최종 조사 기준일로 삼습니다. 요청서 생성일과 답변 시점의 날짜가 다르면 실제 답변 시점의 날짜를 우선합니다.
- 주제가 시점 의존으로 분류됐는지와 관계없이 웹 검색을 반드시 수행합니다. 기존 지식이나 자료팩만으로 최신 사실을 확정하지 않습니다.
- 조사 기준일 현재 확인 가능한 가장 최신의 공식 자료와 1차 출처를 우선 검색하고, 게시일뿐 아니라 시행일·적용일·기준일·갱신일도 함께 비교합니다.
- 더 오래된 자료와 최신 자료가 충돌하면 최신 공식 자료의 적용 범위를 우선하되, 변경 전·후 기준이 글에 필요하면 날짜를 명시해 구분합니다.
- 초안을 한 번 완성한 뒤 추가 재검증을 반드시 두 번 수행합니다. 두 재검증 모두 실제 웹 검색을 다시 포함해야 하며, 내부 기억이나 첫 검색 결과 요약만으로 통과 처리하지 않습니다.

[초안 작성 후 1차 재검증]
1. 초안 전체를 대상으로 웹 검색을 다시 수행해 사실, 수치, 날짜, 정책·제도, 인물 직책, 현재 상태, 출처 URL과 적용 기준이 조사 기준일 현재 맞는지 재확인합니다.
2. 초안 작성 뒤 더 최신 자료가 확인됐는지, 서로 충돌하는 근거가 있는지, 본문이 출처보다 강하게 단정한 부분이 없는지 확인합니다.
3. 잘못됐거나 오래됐거나 불명확한 내용은 즉시 수정하고, 최종 본문·sources·fact_checks에 1차 재검증 결과를 반영합니다.
4. 수정할 사항이 없더라도 1차 재검증을 생략하지 않습니다.

[1차 수정 후 2차 재검증]
1. 1차 재검증 결과가 반영된 수정본 전체를 대상으로 웹 검색을 다시 수행합니다.
2. 특히 1차에서 수정한 문장과 핵심 수치·날짜·정책·직책·현재 상태를 다시 확인하고, 수정 때문에 앞뒤 문맥이나 다른 문장과 모순이 생기지 않았는지 확인합니다.
3. 최신 공식 자료의 게시일과 실제 적용 기준일을 다시 비교하고, 출처 URL이 실제로 해당 주장을 뒷받침하는지 재확인합니다.
4. 2차 재검증에서 발견한 오류·누락·최신성 문제도 최종 결과에 수정 반영합니다. 확인할 수 없는 내용은 추정하지 말고 fact_checks에 needs_verification으로 남깁니다.
5. 수정할 사항이 없더라도 2차 재검증을 생략하지 않습니다.
- 위 두 번의 추가 재검증과 필요한 수정 반영을 모두 끝내기 전에는 최종 JSON을 출력하지 않습니다.
"""


def install_content_pack_freshness_review_contract() -> None:
    content_pack_module = importlib.import_module("src.services.content_pack_service")
    target: Callable[..., str] = content_pack_module.build_ai_prompt
    if getattr(target, "_latest_research_review_wrapper", False):
        return

    @wraps(target)
    def wrapped(*args, **kwargs) -> str:
        prompt = target(*args, **kwargs)
        if LATEST_RESEARCH_REVIEW_MARKER in prompt:
            return prompt

        section = build_latest_research_review_section()
        seo_marker = "\n[SEO 필수 규칙]"
        if seo_marker in prompt:
            prompt = prompt.replace(
                seo_marker,
                f"\n\n{section}\n[SEO 필수 규칙]",
                1,
            )
        else:
            prompt = f"{prompt.rstrip()}\n\n{section}\n"
        return prompt

    wrapped._latest_research_review_wrapper = True  # type: ignore[attr-defined]
    content_pack_module.build_ai_prompt = wrapped
