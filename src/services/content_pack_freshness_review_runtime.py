from __future__ import annotations

from datetime import date
from functools import wraps
import importlib
from typing import Callable


LATEST_RESEARCH_REVIEW_MARKER = "[현재 날짜·최신 검색·3중 재검증 필수]"
_LEGACY_RESEARCH_REVIEW_MARKERS = (
    "[현재 날짜·최신 검색·2중 재검증 필수]",
)
_SEO_MARKER = "[SEO 필수 규칙]"


def build_latest_research_review_section(
    *,
    reference_date: date | None = None,
) -> str:
    created_on = (reference_date or date.today()).isoformat()
    return f"""{LATEST_RESEARCH_REVIEW_MARKER}
- 요청서 확인 기준일: {created_on}
- 실제 답변을 작성하기 시작할 때 현재 날짜를 다시 확인하고, 그 날짜를 최종 조사 기준일로 삼습니다. 요청서 확인일과 답변 시점의 날짜가 다르면 실제 답변 시점의 날짜를 우선합니다.
- 주제가 시점 의존으로 분류됐는지와 관계없이 웹 검색을 반드시 수행합니다. 기존 지식이나 자료팩만으로 최신 사실을 확정하지 않습니다.
- 조사 기준일 현재 확인 가능한 가장 최신의 공식 자료와 1차 출처를 우선 검색하고, 게시일뿐 아니라 시행일·적용일·기준일·갱신일도 함께 비교합니다.
- 더 오래된 자료와 최신 자료가 충돌하면 최신 공식 자료의 적용 범위를 우선하되, 변경 전·후 기준이 글에 필요하면 날짜를 명시해 구분합니다.
- 초안을 한 번 완성한 뒤 추가 재검증을 반드시 세 번 수행합니다. 세 재검증 모두 실제 웹 검색을 다시 포함해야 하며, 내부 기억이나 이전 검색 결과 요약만으로 통과 처리하지 않습니다.

[자연스러운 인간 편집 품질 규칙]
- 독자가 가장 궁금해할 답을 불필요한 서론보다 먼저 제시합니다.
- 문장 길이와 문단 길이를 획일적으로 맞추지 않고, 중요한 내용은 충분히 설명하며 단순한 내용은 짧게 끝냅니다. 다만 길이를 무작위로 흔들지는 않습니다.
- 모든 소제목을 같은 `설명 → 목록 → 정리` 구조로 반복하지 않고, 실제 정보의 중요도와 흐름에 맞게 구성합니다.
- `정리하면`, `결론적으로`, `중요한 점은`, `살펴보겠습니다`, `알아보겠습니다` 같은 상투적 전환 표현을 반복하지 않습니다.
- 같은 사실이나 결론을 도입·본문·마무리에서 표현만 바꿔 반복하지 않습니다.
- 목록은 실제로 읽기 쉬워질 때만 사용하고, 지나치게 대칭적인 문단 구성이나 동일한 문장 종결 패턴을 피합니다.
- 독자가 헷갈릴 부분은 실제 질문에 답하듯 구체적인 설명이나 필요한 예시로 풀어냅니다.
- 정보 가치가 없는 감탄문·수사적 질문·과도한 친절 표현·빈말을 추가하지 않습니다.
- 개인 경험, 직접 사용 후기, 전문성, 인터뷰, 감정이나 의견을 실제 근거 없이 만들어 사람인 것처럼 꾸미지 않습니다.
- 자연스러움을 위해 고의적인 오타, 띄어쓰기 오류, 맞춤법 오류, 문장부호 오류나 사실 오류를 만들지 않습니다.
- 숫자·날짜·정책명·기관명·인명·인용 내용은 문체를 자연스럽게 다듬더라도 의미가 달라지지 않도록 보호합니다.

[초안 작성 직후 자연스러운 인간 편집 품질 점검]
1. 초안 전체를 다시 읽고 사람이 실제로 이 정보를 조사해 독자에게 설명한다면 이런 순서와 표현을 사용할 가능성이 높은지 확인합니다.
2. 첫 문단이 상투적인 소개로 시간을 끌지 않고 독자가 궁금한 핵심에 빠르게 접근하는지 확인합니다.
3. 문장 길이·문단 길이·소제목 구조가 지나치게 획일적이거나 기계적으로 반복되는지 확인합니다.
4. 같은 의미의 반복, 상투적인 연결 표현, 불필요한 요약과 정보 가치가 없는 문장을 제거합니다.
5. 사실·수치·출처의 의미는 바꾸지 않은 상태에서 필요한 문장만 자연스럽게 다시 작성합니다.

[초안 작성 후 1차 재검증 — 전체 사실 감사]
1. 초안 전체를 대상으로 웹 검색을 다시 수행해 사실, 수치, 날짜, 정책·제도, 인물 직책, 현재 상태, 출처 URL과 적용 기준이 조사 기준일 현재 맞는지 재확인합니다.
2. 초안 작성 뒤 더 최신 자료가 확인됐는지, 서로 충돌하는 근거가 있는지, 본문이 출처보다 강하게 단정한 부분이 없는지 확인합니다.
3. 잘못됐거나 오래됐거나 불명확한 내용은 즉시 수정하고, 본문·sources·fact_checks에 1차 재검증 결과를 반영합니다.
4. 검증이 충분하지 않은 모든 주장을 needs_verification 후보로 추출하고, 확인된 주장은 verified로 표시한 뒤 실제 근거 source_ids를 연결합니다.
5. 수정할 사항이 없더라도 1차 재검증을 생략하지 않습니다.

[1차 자연스러움 재검토]
- 1차 사실 수정이 반영된 글 전체를 다시 읽고 획일적인 문단 구성, 반복되는 전환어, 과도한 요약, 불필요한 친절 설명, 같은 의미의 반복을 찾아 사실관계를 유지한 채 자연스럽게 다듬습니다.

[1차 수정 후 2차 재검증 — 미확인·변경 항목 집중 검증]
1. 1차에서 needs_verification 후보로 남은 항목을 하나씩 별도의 검색 질문으로 다시 조사합니다.
2. 1차에서 수정한 문장과 핵심 수치·날짜·정책·직책·현재 상태를 다시 확인하고, 수정 때문에 앞뒤 문맥이나 다른 문장과 모순이 생기지 않았는지 확인합니다.
3. 각 미확인 주장에 대해 가능한 한 공식 1차 출처를 추가로 찾고, 같은 내용을 반복 보도한 기사 여러 개만으로 verified라고 판정하지 않습니다.
4. 확인된 항목은 verified로 변경하고 실제 근거 source_ids를 보완합니다.
5. 여전히 확인되지 않는 항목은 무엇을 다시 검색했고 어떤 공식 근거가 부족한지 reason에 명확히 기록합니다.
6. 수정할 사항이 없더라도 2차 재검증을 생략하지 않습니다.

[2차 자연스러움 재검토]
- 2차에서 추가·수정한 문장을 중심으로 주변 문맥과 자연스럽게 이어지는지 확인합니다.
- 공식 자료 문구를 그대로 나열하거나 기사체를 단순 재서술한 느낌이 나는 부분은 사실을 보존한 채 독자 중심 설명으로 다시 정리합니다.
- 사실 하나마다 똑같은 형식의 별도 문단을 만드는 등 기계적인 구조가 생기지 않았는지 확인합니다.

[2차 수정 후 3차 재검증 — 최종 정합성·출처 감사]
1. 2차 수정까지 반영된 최종 수정본 전체를 대상으로 웹 검색을 다시 수행합니다.
2. 아직 needs_verification 후보로 남은 항목을 마지막으로 각각 재검색하고, 확인 가능한 최신 공식 1차 출처가 새로 있는지 확인합니다.
3. title, summary, 본문 blocks, fact_checks, sources 사이에 수치·날짜·정책 조건·적용 대상이 서로 모순되지 않는지 확인합니다.
4. sources의 URL이 실제로 해당 주장에 직접 근거가 되는지 확인하고 게시일과 시행일·적용일·기준일을 혼동하지 않았는지 다시 점검합니다.
5. 삭제하거나 수정한 주장에만 필요했던 불필요한 sources는 제거합니다.
6. 3차까지 확인되지 않은 주장을 추측해서 verified로 바꾸지 않습니다.
7. 확인되지 않은 내용이 글의 핵심에 불필요하면 본문에서 삭제합니다. 반드시 필요한 내용이면 `현재 공개된 자료에서는 확인되지 않았다`, `세부 시행 기준은 추후 확인이 필요하다`처럼 불확실성을 본문에 명확히 표시합니다.
8. 최종 본문의 확정 표현과 fact_checks 상태가 서로 일치하는지 확인합니다.
9. 수정할 사항이 없더라도 3차 재검증을 생략하지 않습니다.

[3차 최종 인간 편집 품질 점검]
- 제목부터 마지막 문단까지 다시 읽고 실제 사람이 조사한 내용을 독자에게 설명한다면 이런 순서와 문장 흐름을 사용할지 확인합니다.
- 핵심을 늦게 말하는 도입, 불필요하게 완벽한 대칭 구조, 같은 주장 반복, 문단마다 억지로 붙인 결론, 정보 가치 없이 매끄럽기만 한 문장, 과도한 소제목·목록·요약을 제거하거나 자연스럽게 다듬습니다.
- 사실 정확성을 위해 필요한 딱딱한 표현과 단순히 기계적으로 보이는 문체를 구분하고, 사실·수치·출처를 바꾸지 않는 범위에서 후자만 수정합니다.

[최종 needs_verification 처리 규칙]
- 최종 JSON의 needs_verification은 최초 조사와 1차·2차·3차 재검증을 모두 수행했음에도 현재 공개된 신뢰할 수 있는 자료만으로 확정할 수 없는 항목에만 사용합니다.
- 검색을 생략했거나 확인 가능한 공식 자료를 충분히 찾지 않은 상태에서 needs_verification으로 넘기지 않습니다.
- 끝까지 확인되지 않은 비핵심 주장은 본문에서 제거하고, 글에 반드시 필요한 주장은 불확실성을 본문에 명확히 표시한 상태로 needs_verification을 유지합니다.
- 확인 근거가 확보된 항목은 verified로 변경하고 실제 source_ids를 연결합니다.

[최종 JSON 인용·출력 정리 규칙]
- 최종 JSON의 어떤 문자열에도 ChatGPT, Gemini 또는 검색 UI가 사용하는 내부 인용 마커를 포함하지 않습니다.
- `contentReference`, `oaicite`, 내부 citation token, 임시 각주 ID, UI 전용 참조 문자열을 title, summary, SEO, blocks, fact_checks, sources의 설명 문자열에 복사하지 않습니다.
- 사실 근거 연결은 sources의 S1, S2, R1, R2 형식 ID와 fact_checks.source_ids만 사용합니다.
- 웹 검색으로 확인한 실제 출처의 title, publisher, url, published_at은 sources에 기록합니다.
- UI 내부 인용 마커를 S/R 출처 ID로 임의 변환하거나 추측하지 않습니다.
- 위 세 번의 추가 재검증과 필요한 사실·문체 수정 반영을 모두 끝내기 전에는 최종 JSON을 출력하지 않습니다.
"""


def _find_review_marker_index(text: str) -> int:
    indexes = [
        index
        for marker in (LATEST_RESEARCH_REVIEW_MARKER, *_LEGACY_RESEARCH_REVIEW_MARKERS)
        if (index := text.find(marker)) >= 0
    ]
    return min(indexes) if indexes else -1


def ensure_latest_research_review_prompt(
    prompt: object,
    *,
    reference_date: date | None = None,
) -> str:
    text = str(prompt or "")
    section = build_latest_research_review_section(reference_date=reference_date)
    marker_index = _find_review_marker_index(text)
    if marker_index >= 0:
        seo_index = text.find(_SEO_MARKER, marker_index)
        if seo_index >= 0:
            before = text[:marker_index].rstrip()
            after = text[seo_index:].lstrip()
            return f"{before}\n\n{section}\n{after}"
        return f"{text[:marker_index].rstrip()}\n\n{section}\n"

    seo_index = text.find(_SEO_MARKER)
    if seo_index >= 0:
        before = text[:seo_index].rstrip()
        after = text[seo_index:].lstrip()
        return f"{before}\n\n{section}\n{after}"
    return f"{text.rstrip()}\n\n{section}\n"


def _refresh_prompt_record(record):
    if not isinstance(record, dict) or "prompt_text" not in record:
        return record
    refreshed = dict(record)
    refreshed["prompt_text"] = ensure_latest_research_review_prompt(
        refreshed.get("prompt_text")
    )
    return refreshed


def install_content_pack_freshness_review_contract() -> None:
    content_pack_module = importlib.import_module("src.services.content_pack_service")
    build_target: Callable[..., str] = content_pack_module.build_ai_prompt
    if getattr(build_target, "_latest_research_review_wrapper", False):
        return

    @wraps(build_target)
    def wrapped_build(*args, **kwargs) -> str:
        return ensure_latest_research_review_prompt(build_target(*args, **kwargs))

    get_target = content_pack_module.get_content_pack

    @wraps(get_target)
    def wrapped_get(*args, **kwargs):
        return _refresh_prompt_record(get_target(*args, **kwargs))

    list_target = content_pack_module.list_content_packs

    @wraps(list_target)
    def wrapped_list(*args, **kwargs):
        return [
            _refresh_prompt_record(item)
            for item in list_target(*args, **kwargs)
        ]

    wrapped_build._latest_research_review_wrapper = True  # type: ignore[attr-defined]
    wrapped_get._latest_research_review_wrapper = True  # type: ignore[attr-defined]
    wrapped_list._latest_research_review_wrapper = True  # type: ignore[attr-defined]
    content_pack_module.build_ai_prompt = wrapped_build
    content_pack_module.get_content_pack = wrapped_get
    content_pack_module.list_content_packs = wrapped_list
