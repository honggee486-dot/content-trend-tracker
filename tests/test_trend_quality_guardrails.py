from datetime import datetime, timedelta

from src.services.trend_discovery_service import (
    _canonical_title,
    _editorial_identity_tokens,
    _rediscovery_score,
    _score_cluster,
)
from src.services.trend_normalization import (
    compact_title,
    identity_tokens,
    normalize_title,
    normalize_url,
    source_domain,
    strip_collection_scope,
)


def _item(
    source_type: str,
    title: str,
    url: str,
    *,
    source_name: str,
    observation_count: int = 1,
    previous_imported_at: datetime | None = None,
    last_imported_at: datetime | None = None,
) -> dict[str, object]:
    now = datetime.now()
    return {
        "source_item_id": f"{source_type}:{url}",
        "source_type": source_type,
        "raw_title": title,
        "item_title": title,
        "canonical_title": title,
        "normalized_title": normalize_title(title),
        "compact_title": compact_title(title),
        "identity_tokens": identity_tokens(title),
        "editorial_identity_tokens": _editorial_identity_tokens(title),
        "calendar_identity_tokens": set(),
        "tokens": set(),
        "source_url": url,
        "normalized_url": normalize_url(url),
        "domain": source_domain(url),
        "source_name": source_name,
        "published_at": now,
        "observed_at": now,
        "imported_at": now,
        "first_imported_at": now,
        "previous_imported_at": previous_imported_at,
        "last_imported_at": last_imported_at or now,
        "observation_count": observation_count,
        "signal_value": None,
        "metadata": {},
        "query": "",
        "query_supported": False,
    }


def test_regional_popularity_scope_uses_actual_video_title() -> None:
    assert strip_collection_scope("지역 인기: KR / Comedy") == ""
    item = {
        "source_type": "youtube",
        "raw_title": "지역 인기: KR / Comedy",
        "item_title": "실제 코미디 영상 제목",
        "metadata": {
            "item_title": "실제 코미디 영상 제목",
            "signal_type": "recent_video",
        },
    }

    assert _canonical_title(item) == "실제 코미디 영상 제목"


def test_single_entity_title_is_not_recommended() -> None:
    items = [
        _item(
            "naver_news",
            "젠슨 황, SK와 AI 인프라 협력 발표",
            "https://news.example.com/article/1001",
            source_name="뉴스A",
        ),
        _item(
            "daum_web",
            "SK와 엔비디아의 AI 인프라 협력 확대",
            "https://web.example.com/view/2002",
            source_name="웹B",
        ),
        _item(
            "daum_cafe",
            "젠슨 황과 SK 협력 소식",
            "https://cafe.example.com/board/3003",
            source_name="카페C",
        ),
    ]

    result = _score_cluster({"title": "젠슨 황", "items": items})

    assert result["recommendation_status"] == "review"
    assert float(result["quality"]) <= 68.0
    assert any(
        "이름·브랜드·프로그램명만 남아" in reason
        for reason in result["quality_reasons"]
    )


def test_program_name_only_title_is_not_recommended() -> None:
    items = [
        _item(
            "naver_news",
            "킬러들의 쇼핑몰 시즌2 제작 발표",
            "https://news.example.com/article/1001",
            source_name="뉴스A",
        ),
        _item(
            "daum_web",
            "킬러들의 쇼핑몰 시즌2 공개 예정",
            "https://web.example.com/view/2002",
            source_name="웹B",
        ),
        _item(
            "daum_cafe",
            "킬러들의 쇼핑몰 시즌2 반응",
            "https://cafe.example.com/board/3003",
            source_name="카페C",
        ),
    ]

    result = _score_cluster({"title": "킬러들의 쇼핑몰 시즌2", "items": items})

    assert result["recommendation_status"] == "review"
    assert any(
        "이름·브랜드·프로그램명만 남아" in reason
        for reason in result["quality_reasons"]
    )


def test_navigation_page_cluster_is_forced_to_hold() -> None:
    items = [
        _item(
            "daum_web",
            "산업 - 아주경제",
            "https://www.ajunews.com/industry",
            source_name="ajunews.com",
        ),
        _item(
            "daum_web",
            "산업>전자 - 아주경제",
            "https://www.ajunews.com/industry/electronic",
            source_name="ajunews.com",
        ),
        _item(
            "naver_news",
            "산업 뉴스 - Example",
            "https://news.example.com/section",
            source_name="news.example.com",
        ),
    ]

    result = _score_cluster({"title": "산업 - 아주경제", "items": items})

    assert result["recommendation_status"] == "hold"
    assert float(result["quality"]) <= 32.0
    assert any(
        "기사·글이 아닌 섹션·홈페이지형 원문" in reason
        for reason in result["quality_reasons"]
    )


def test_privacy_policy_page_cluster_is_forced_to_hold() -> None:
    items = [
        _item(
            "daum_web",
            "개인정보취급방침 - 뉴스1",
            "https://www.news1.kr/privacy",
            source_name="news1.kr",
        ),
        _item(
            "naver_news",
            "개인정보처리방침 - 뉴스1",
            "https://www.news1.kr/privacy-policy",
            source_name="news1.kr",
        ),
    ]

    result = _score_cluster(
        {
            "title": "개인정보취급방침 - 뉴스1",
            "items": items,
        }
    )

    assert result["recommendation_status"] == "hold"


def test_navigation_page_cluster_has_no_rediscovery_bonus() -> None:
    now = datetime.now()
    repeated = _item(
        "daum_web",
        "기상청 날씨누리",
        "https://www.weather.go.kr/w/index.do",
        source_name="weather.go.kr",
        observation_count=20,
        previous_imported_at=now - timedelta(minutes=30),
        last_imported_at=now,
    )

    result = _score_cluster(
        {
            "title": "기상청 날씨누리",
            "items": [repeated],
        }
    )

    assert float(result["rediscovery"]) == 0.0
    assert result["recommendation_status"] == "hold"


def test_two_repeated_evidence_groups_can_receive_bonus() -> None:
    now = datetime.now()
    first = _item(
        "naver_news",
        "정책 변경 발표",
        "https://news.example.com/article/1",
        source_name="뉴스A",
        observation_count=3,
        previous_imported_at=now - timedelta(hours=1),
        last_imported_at=now,
    )
    second = _item(
        "daum_web",
        "정책 변경 후속 보도",
        "https://web.example.com/view/2",
        source_name="웹B",
        observation_count=3,
        previous_imported_at=now - timedelta(hours=1),
        last_imported_at=now,
    )

    score, repeated_groups, median_gap = _rediscovery_score([[first], [second]])

    assert score > 0
    assert repeated_groups == 2
    assert median_gap is not None


def test_single_repeated_url_has_a_rediscovery_score_ceiling() -> None:
    now = datetime.now()
    repeated = _item(
        "naver_news",
        "갤럭시 S26 공개",
        "https://news.example.com/article/1",
        source_name="뉴스A",
        observation_count=200,
        previous_imported_at=now - timedelta(minutes=30),
        last_imported_at=now,
    )

    score, repeated_groups, median_gap = _rediscovery_score([[repeated]])

    assert 0 < score <= 4.0
    assert repeated_groups == 1
    assert median_gap is not None

def test_time_sensitive_numeric_topic_has_fact_risk_with_weak_evidence() -> None:
    item = _item(
        "daum_cafe",
        "2026년 7월 26일 프로야구 순위",
        "https://cafe.example.com/board/standings",
        source_name="카페A",
    )

    result = _score_cluster(
        {
            "title": "2026년 7월 26일 프로야구 순위",
            "items": [item],
        }
    )

    assert float(result["risk"]) >= 10.0
    assert any(
        "시점 의존" in reason and "뉴스·웹 사실 근거 없음" in reason
        for reason in result["reasons"]
    )


def test_product_model_number_is_not_treated_as_numeric_claim() -> None:
    items = [
        _item(
            "naver_news",
            "갤럭시 S26 공개",
            "https://news.example.com/article/1",
            source_name="뉴스A",
        ),
        _item(
            "daum_web",
            "삼성 갤럭시 S26 공개",
            "https://web.example.com/view/2",
            source_name="웹B",
        ),
    ]

    result = _score_cluster({"title": "갤럭시 S26 공개", "items": items})

    assert float(result["risk"]) == 0.0


def test_independent_factual_sources_reduce_time_sensitive_risk() -> None:
    weak = _score_cluster(
        {
            "title": "전기요금 5% 인상 일정",
            "items": [
                _item(
                    "naver_blog",
                    "전기요금 5% 인상 일정",
                    "https://blog.example.com/post/1",
                    source_name="블로그A",
                )
            ],
        }
    )
    supported = _score_cluster(
        {
            "title": "전기요금 5% 인상 일정",
            "items": [
                _item(
                    "naver_news",
                    "전기요금 5% 인상 일정",
                    "https://news-a.example.com/article/1",
                    source_name="뉴스A",
                ),
                _item(
                    "daum_web",
                    "전기요금 5% 인상 일정",
                    "https://news-b.example.com/view/2",
                    source_name="뉴스B",
                ),
            ],
        }
    )

    assert float(weak["risk"]) > float(supported["risk"])
