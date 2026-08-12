from src.services.trend_discovery_service import recommend_content_angle_details


def _item(
    source_type: str,
    source_name: str,
    title: str,
    *,
    source_url: str = "",
    metadata: dict | None = None,
    published_at: str = "",
) -> dict:
    item_metadata = {"item_title": title}
    item_metadata.update(metadata or {})
    return {
        "source_type": source_type,
        "source_name": source_name,
        "raw_title": title,
        "source_url": source_url,
        "metadata": item_metadata,
        "published_at": published_at,
    }


def _keys(details: list[dict]) -> list[str]:
    return [str(detail["key"]) for detail in details]


def test_schedule_intent_only_offers_schedule_supported_directions() -> None:
    details = recommend_content_angle_details(
        "드라마 최종회 방송 일정",
        [
            _item("naver_news", "뉴스A", "최종회 방송일과 시간 확정"),
            _item("daum_web", "웹B", "방송 일정 변경 여부와 다시보기 안내"),
        ],
    )

    assert details[0]["intent_key"] == "schedule"
    assert details[0]["key"] == "schedule_summary"
    assert "schedule_change" in _keys(details)
    assert not any(key in _keys(details) for key in {"reaction_summary", "comparison_core"})


def test_problem_solving_intent_prioritizes_diagnosis_and_steps() -> None:
    details = recommend_content_angle_details(
        "윈도우 업데이트 후 블루투스 끊김 해결",
        [
            _item("naver_blog", "블로그A", "블루투스 끊김 원인과 드라이버 재설치 방법"),
            _item("daum_web", "웹B", "윈도우 블루투스 오류 점검과 복구 순서"),
        ],
    )

    assert details[0]["intent_key"] == "problem_solving"
    assert _keys(details)[:2] == ["problem_diagnosis", "problem_steps"]
    assert not any(key in _keys(details) for key in {"comparison_core", "reaction_summary"})


def test_comparison_intent_requires_explicit_comparison_purpose() -> None:
    details = recommend_content_angle_details(
        "아이폰과 갤럭시 카메라 비교",
        [
            _item("naver_blog", "블로그A", "아이폰 갤럭시 사진 차이 비교"),
            _item("daum_web", "웹B", "카메라 야간 촬영 성능과 가격 차이"),
        ],
    )

    assert details[0]["intent_key"] == "comparison"
    assert _keys(details)[:2] == ["comparison_core", "comparison_choice"]
    assert "comparison_price" in _keys(details)


def test_single_review_source_does_not_claim_repeated_pros_and_cons() -> None:
    details = recommend_content_angle_details(
        "무선 이어폰 사용 후기",
        [_item("naver_blog", "블로그A", "직접 사용 후기 장점과 불편")],
    )

    assert details[0]["intent_key"] == "review_reaction"
    assert _keys(details) == ["reaction_summary"]
    assert details[0]["needs_verification"] is True


def test_multiple_independent_review_sources_allow_pros_and_cons() -> None:
    details = recommend_content_angle_details(
        "무선 이어폰 사용 후기",
        [
            _item("naver_blog", "블로그A", "직접 사용 후기 장점과 불편"),
            _item("daum_cafe", "카페B", "실사용 리뷰 장점 단점 평가"),
        ],
    )

    assert details[0]["intent_key"] == "review_reaction"
    assert "reaction_pros_cons" in _keys(details)


def test_policy_change_does_not_offer_unrelated_review_or_comparison() -> None:
    details = recommend_content_angle_details(
        "청년 지원 정책 변경",
        [
            _item("naver_news", "뉴스A", "청년 지원 정책 개편 발표와 시행일"),
            _item("daum_web", "웹B", "지원 대상 조건과 신청 방법 변경"),
        ],
    )

    assert details[0]["intent_key"] == "release_update"
    assert "update_summary" in _keys(details)
    assert "update_timeline" in _keys(details)
    assert "update_action" in _keys(details)
    assert not any(key in _keys(details) for key in {"reaction_summary", "comparison_core"})


def test_general_intent_returns_small_safe_set_instead_of_filling_five_angles() -> None:
    details = recommend_content_angle_details(
        "새로운 지역 축제 화제",
        [_item("youtube", "채널A", "새로운 지역 축제가 검색과 영상에서 화제")],
    )

    assert details[0]["intent_key"] == "general"
    assert 1 <= len(details) <= 2
    assert details[0]["key"] == "general_core"


def test_angle_details_expose_intent_and_excluded_direction_metadata() -> None:
    details = recommend_content_angle_details(
        "정부 지원금 신청 방법",
        [
            _item("daum_web", "공공안내", "정부 지원금 신청 대상 조건과 필요 서류"),
            _item("naver_news", "뉴스A", "지원금 접수 일정과 신청 절차"),
        ],
    )

    first = details[0]
    assert first["intent_key"] == "how_to"
    assert first["intent_label"] == "사용 방법형"
    assert 0 < float(first["intent_confidence"]) <= 1
    assert first["intent_reason"]
    assert isinstance(first["excluded_labels"], list)


def test_price_benefit_intent_prioritizes_cost_and_eligibility() -> None:
    details = recommend_content_angle_details(
        "전기차 보조금 지원 대상과 가격 혜택",
        [
            _item("naver_news", "뉴스A", "전기차 보조금 지원 대상과 소득 조건"),
            _item("daum_web", "웹B", "차종별 가격 혜택과 신청 기간"),
        ],
    )

    assert details[0]["intent_key"] == "price_benefit"
    assert _keys(details)[:2] == ["price_summary", "price_eligibility"]


def test_fact_check_intent_separates_claims_and_sources() -> None:
    details = recommend_content_angle_details(
        "신제품 배터리 폭발 루머 사실 여부",
        [
            _item("naver_news", "뉴스A", "제조사 공식 발표와 사고 사실 확인"),
            _item("daum_web", "웹B", "온라인 루머 검증과 확인되지 않은 주장"),
        ],
    )

    assert details[0]["intent_key"] == "fact_check"
    assert _keys(details)[:2] == ["fact_claims", "fact_sources"]


def test_person_work_intent_uses_information_not_generic_reaction() -> None:
    details = recommend_content_angle_details(
        "신작 드라마 출연진과 인물관계도",
        [
            _item("naver_news", "뉴스A", "신작 드라마 출연 배우와 등장인물 공개"),
            _item("daum_web", "웹B", "인물관계도와 원작 줄거리 정리"),
        ],
    )

    assert details[0]["intent_key"] == "person_work_info"
    assert details[0]["key"] == "work_core_info"
    assert not any(key in _keys(details) for key in {"reaction_summary", "comparison_core"})


def test_event_summary_intent_prioritizes_confirmed_sequence() -> None:
    details = recommend_content_angle_details(
        "프로야구 결승 경기 결과",
        [
            _item("naver_news", "뉴스A", "결승 경기 결과와 우승팀 확정"),
            _item("daum_web", "웹B", "경기 시간별 주요 상황과 결과"),
        ],
    )

    assert details[0]["intent_key"] == "event_summary"
    assert details[0]["key"] == "event_key_facts"


def test_single_recent_story_cannot_override_broad_cluster_direction() -> None:
    details = recommend_content_angle_details(
        "gpt-5.6",
        [
            _item("daum_web", "웹A", "일반모델 GPT-5.6만 믿고 PSP게임 준한글화 성공 및 삽질기"),
            _item("naver_news", "뉴스B", "GPT-5.6 사용자 파일 삭제 논란"),
            _item("naver_blog", "블로그C", "GPT-5.6와 클로드 교육 현장 비교"),
            _item("daum_web", "웹D", "GPT-5.6 출시 기능 정리"),
            _item("naver_blog", "블로그E", "GPT-5.6 파일을 지웠다는 사용자 제보"),
            _item("naver_news", "뉴스F", "GPT-5.6 성능과 안전성 평가"),
        ],
    )

    assert details[0]["intent_key"] == "general"
    assert details[0]["key"] == "general_issue_map"
    assert details[0]["mixed_evidence"] is True
    assert all("PSP" not in detail["text"] for detail in details)
    comparison = next(detail for detail in details if detail["key"] == "comparison_core")
    problem_case = next(detail for detail in details if detail["key"] == "problem_case_study")
    assert comparison["recommendation_tier"] == "reference"
    assert problem_case["recommendation_tier"] == "reference"


def test_copied_comparison_title_counts_once_for_intent_detection() -> None:
    copied = "GPT-5.6와 클로드 비교 사용기"
    details = recommend_content_angle_details(
        "gpt-5.6",
        [
            _item("naver_blog", "블로그A", copied),
            _item("daum_cafe", "카페B", copied),
            _item("naver_news", "뉴스C", "GPT-5.6 파일 삭제 논란"),
            _item("daum_web", "웹D", "GPT-5.6 출시 기능 정리"),
        ],
    )

    assert details[0]["intent_key"] == "general"
    comparison = next(detail for detail in details if detail["key"] == "comparison_core")
    assert comparison["recommendation_tier"] == "reference"
    assert int(comparison["support_item_count"]) == 1
    assert int(details[0]["evidence_item_count"]) == 3



def test_one_troubleshooting_story_in_ten_items_is_reference_only() -> None:
    items = [
        _item("daum_web", "개인블로그", "GPT-5.6 PSP 준한글화 실패와 삽질 해결기"),
        _item("naver_news", "뉴스1", "GPT-5.6 출시 기능 공개"),
        _item("naver_news", "뉴스2", "GPT-5.6 파일 삭제 논란"),
        _item("daum_web", "웹3", "GPT-5.6 안전성 발표"),
        _item("naver_blog", "블로그4", "GPT-5.6 교육 현장 활용"),
        _item("naver_news", "뉴스5", "GPT-5.6 기업 도입 소식"),
        _item("daum_web", "웹6", "GPT-5.6 성능 평가"),
        _item("naver_blog", "블로그7", "GPT-5.6 업무 활용 후기"),
        _item("naver_news", "뉴스8", "GPT-5.6 공개 이후 반응"),
        _item("daum_web", "웹9", "GPT-5.6 사용자 영향 분석"),
    ]

    details = recommend_content_angle_details("GPT-5.6", items)
    problem_case = next(detail for detail in details if detail["key"] == "problem_case_study")

    assert problem_case["recommendation_tier"] == "reference"
    assert problem_case["support_item_count"] == 1
    assert details[0]["key"] == "general_issue_map"
    assert details[0]["recommendation_tier"] == "primary"


def test_three_independent_troubleshooting_stories_allow_problem_direction() -> None:
    details = recommend_content_angle_details(
        "GPT-5.6",
        [
            _item("naver_blog", "블로그A", "GPT-5.6 코딩 파일 오류 삽질 해결기"),
            _item("daum_cafe", "카페B", "GPT-5.6 프롬프트 실패 원인 분석과 해결 과정"),
            _item("daum_web", "웹C", "GPT-5.6 설정 충돌 트러블슈팅과 수정 성공기"),
            _item("naver_news", "뉴스D", "GPT-5.6 출시 기능 공개"),
        ],
    )

    problem_case = next(detail for detail in details if detail["key"] == "problem_case_study")
    assert problem_case["support_item_count"] == 3
    assert problem_case["support_publisher_count"] == 3
    assert problem_case["recommendation_tier"] in {"primary", "secondary"}


def test_comparison_direction_is_not_returned_without_comparison_source() -> None:
    details = recommend_content_angle_details(
        "GPT-5.6 모델 비교",
        [
            _item("naver_news", "뉴스A", "GPT-5.6 출시 기능 공개"),
            _item("daum_web", "웹B", "GPT-5.6 파일 삭제 논란"),
        ],
    )

    assert all(not detail["key"].startswith("comparison_") for detail in details)
    assert details[0]["key"] == "verify_scope"


def test_comparison_with_two_targets_and_independent_sources_is_allowed() -> None:
    details = recommend_content_angle_details(
        "GPT-5.6과 Claude 비교",
        [
            _item("naver_blog", "블로그A", "GPT-5.6과 Claude 코딩 성능 비교"),
            _item("daum_web", "웹B", "Claude와 GPT-5.6 실제 차이와 장단점"),
        ],
    )

    comparison = next(detail for detail in details if detail["key"] == "comparison_core")
    assert comparison["support_item_count"] == 2
    assert comparison["support_publisher_count"] == 2
    assert comparison["recommendation_tier"] == "primary"
    assert {"gpt-5.6", "claude"}.issubset(set(comparison["comparison_targets"]))


def test_official_announcement_and_copied_articles_count_as_one_evidence() -> None:
    title = "GPT-5.6 공식 출시 발표와 주요 기능"
    details = recommend_content_angle_details(
        "GPT-5.6 출시",
        [
            _item(
                "daum_web",
                "OpenAI 공식",
                title,
                source_url="https://openai.example/releases/gpt-5-6",
                metadata={"is_official": True},
            ),
            _item("naver_news", "뉴스A", title, source_url="https://news-a.example/1"),
            _item("naver_news", "뉴스B", title, source_url="https://news-b.example/2"),
            _item("daum_web", "웹C", title, source_url="https://web-c.example/3"),
        ],
    )

    update = next(detail for detail in details if detail["key"] == "update_summary")
    assert update["support_item_count"] == 1
    assert update["support_publisher_count"] == 1
    assert update["official_support_count"] == 1
    assert update["recommendation_tier"] == "reference"


def test_latest_single_comparison_does_not_override_older_update_majority() -> None:
    details = recommend_content_angle_details(
        "GPT-5.6 출시",
        [
            _item("naver_news", "뉴스A", "GPT-5.6 출시 발표", published_at="2026-07-10T10:00:00"),
            _item("daum_web", "웹B", "GPT-5.6 새 기능 공개", published_at="2026-07-10T11:00:00"),
            _item("naver_news", "뉴스C", "GPT-5.6 업데이트 변경 내용", published_at="2026-07-11T10:00:00"),
            _item("daum_web", "웹D", "GPT-5.6 출시 일정 발표", published_at="2026-07-11T11:00:00"),
            _item("naver_news", "뉴스E", "GPT-5.6 기능 추가 발표", published_at="2026-07-12T10:00:00"),
            _item("naver_blog", "블로그F", "GPT-5.6과 Claude 비교", published_at="2026-07-16T09:00:00"),
        ],
    )

    update = next(detail for detail in details if detail["key"] == "update_summary")
    comparison = next(detail for detail in details if detail["key"] == "comparison_core")
    assert update["recommendation_tier"] == "primary"
    assert update["support_item_count"] == 5
    assert comparison["recommendation_tier"] == "reference"
    assert comparison["support_item_count"] == 1


def test_tracking_parameter_url_variants_count_once() -> None:
    details = recommend_content_angle_details(
        "GPT-5.6",
        [
            _item(
                "naver_blog",
                "블로그A",
                "GPT-5.6과 Claude 실제 비교",
                source_url="https://example.com/post?id=10&utm_source=naver",
            ),
            _item(
                "daum_cafe",
                "카페B",
                "GPT-5.6과 Claude 실제 비교 재게시",
                source_url="https://example.com/post?utm_medium=cafe&id=10",
            ),
            _item("naver_news", "뉴스C", "GPT-5.6 출시 기능 공개"),
        ],
    )

    comparison = next(detail for detail in details if detail["key"] == "comparison_core")
    assert comparison["support_item_count"] == 1
    assert comparison["recommendation_tier"] == "reference"
    assert details[0]["evidence_item_count"] == 2


def test_direction_metadata_exposes_evidence_and_official_confirmation() -> None:
    details = recommend_content_angle_details(
        "청년 지원 정책 변경",
        [
            _item("naver_news", "뉴스A", "청년 지원 정책 개편 발표"),
            _item("daum_web", "웹B", "청년 지원 정책 대상 조건 변경"),
        ],
    )

    selected = details[0]
    assert selected["support_item_count"] >= 2
    assert selected["support_publisher_count"] >= 2
    assert selected["representative_evidence"]
    assert "official_confirmation_required" in selected
    assert selected["confidence_reason"]
