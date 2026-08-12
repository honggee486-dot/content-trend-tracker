from src.services.trend_normalization import (
    identity_tokens,
    is_specific_topic,
    normalize_title,
    normalize_url,
    strip_collection_scope,
)


def test_title_normalization_handles_html_urls_hashtags_and_versions() -> None:
    title = "<b>갤럭시&nbsp;S26</b>!!! #공식 https://example.com/path?utm_source=x"

    assert normalize_title(title) == "갤럭시 s26 공식"
    assert identity_tokens(title) == {"갤럭시", "s26"}


def test_collection_scope_and_generic_terms_are_not_specific_topics() -> None:
    assert strip_collection_scope("카테고리: Travel & Events / fresh") == ""
    assert strip_collection_scope("자동 주제: GrannyGame") == "GrannyGame"
    assert not is_specific_topic("horror")
    assert not is_specific_topic("VTuber")
    assert not is_specific_topic("moments")
    assert is_specific_topic("GPT-5.6")
    assert is_specific_topic("모태솔로지만 연애는 하고 싶어 시즌2")


def test_daily_digest_dates_and_korean_particles_are_not_topic_identity() -> None:
    assert identity_tokens("2026년 7월 15일 수요일 오늘의 뉴스") == set()
    assert identity_tokens("2026.7.15.(수) 뉴스") == set()
    assert identity_tokens("7.15 HeadlineNews") == set()
    assert identity_tokens("2026년 7월 25일 토요일 간추린 숏뉴스") == set()
    assert not is_specific_topic("7월 15일 수요일 HeadlineNews")
    assert not is_specific_topic("7월 15일 정기 업데이트 안내")
    assert identity_tokens("GPT 5.6 공개") == {"gpt", "5.6"}

    # 조사를 제거해도 제품명과 버전 번호는 그대로 보존합니다.
    assert identity_tokens("아크전자에서 플렉스 티타늄 S27에 적용") == {
        "아크전자",
        "플렉스",
        "티타늄",
        "s27",
        "적용",
    }
    assert identity_tokens("삼성전자는 브로드컴과 협력") == {
        "삼성전자",
        "브로드컴",
        "협력",
    }


def test_episode_round_and_duration_tokens_keep_their_numeric_identity() -> None:
    assert identity_tokens("프로그램 1회") == {"프로그램", "1회"}
    assert normalize_title("제1235회 로또") == "1235회 로또"
    assert identity_tokens("제1235회 로또") == {"1235회", "로또"}
    assert identity_tokens("프로그램 2회") == {"프로그램", "2회"}
    assert identity_tokens("대회 1차") == {"대회", "1차"}
    assert identity_tokens("대회 2차") == {"대회", "2차"}
    assert identity_tokens("3주 계획") == {"3주", "계획"}
    assert identity_tokens("4주 계획") == {"4주", "계획"}
    assert identity_tokens("2026년 행사 일정") == {"2026년", "행사", "일정"}
    assert identity_tokens("아이폰 17 공개") == {"아이폰", "17"}
    assert identity_tokens("7월 15일 삼성전자 실적 뉴스") == {
        "7월",
        "15일",
        "삼성전자",
        "실적",
    }


def test_url_normalization_removes_tracking_and_keeps_content_identity() -> None:
    first = normalize_url(
        "https://www.example.com/article/42/?utm_source=newsletter&fbclid=abc&id=7#top"
    )
    second = normalize_url("http://example.com/article/42?id=7")

    assert first == second == "https://example.com/article/42?id=7"
    assert normalize_url("https://youtu.be/abc123?si=tracking") == (
        "https://youtube.com/watch?v=abc123"
    )


def test_youtube_url_normalization_variants_resolve_to_same_canonical_url() -> None:
    canonical = "https://youtube.com/watch?v=VIDEO_123"
    variants = [
        "https://www.youtube.com/watch?v=VIDEO_123",
        "https://youtu.be/VIDEO_123",
        "https://www.youtube.com/shorts/VIDEO_123",
        "https://www.youtube.com/live/VIDEO_123",
        "https://www.youtube.com/embed/VIDEO_123",
        "https://m.youtube.com/watch?v=VIDEO_123",
        "https://youtube.com/watch?v=VIDEO_123&utm_source=twitter&si=tracking123",
        "https://youtu.be/VIDEO_123?t=90s&si=abc",
        "https://www.youtube.com/shorts/VIDEO_123?feature=share&t=15",
        "https://www.youtube.com/watch?v=VIDEO_123&list=PL12345&index=2",
    ]
    for url in variants:
        assert normalize_url(url) == canonical, f"Failed for {url}"


def test_different_youtube_video_ids_remain_distinct() -> None:
    url1 = normalize_url("https://www.youtube.com/watch?v=VIDEO_AAA")
    url2 = normalize_url("https://www.youtube.com/shorts/VIDEO_BBB")

    assert url1 == "https://youtube.com/watch?v=VIDEO_AAA"
    assert url2 == "https://youtube.com/watch?v=VIDEO_BBB"
    assert url1 != url2


def test_compact_daily_fortune_date_is_not_a_specific_identity() -> None:
    assert identity_tokens("오늘의 운세 2026년7월16일") == set()
    assert identity_tokens("오늘의 운세 2026년7월16일목요일") == set()
    assert not is_specific_topic("오늘의 운세 2026년7월16일")
