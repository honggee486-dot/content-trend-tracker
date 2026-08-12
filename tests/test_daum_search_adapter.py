import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import unquote

import pytest

from src.adapters.daum_search_adapter import DaumSearchAdapter, DaumSearchError, clean_html_text


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_clean_html_text_removes_tags_and_entities() -> None:
    assert clean_html_text("<b>AI</b> &amp; 검색") == "AI & 검색"


def test_web_search_maps_official_response_to_signal() -> None:
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {key.casefold(): value for key, value in request.header_items()}
        return FakeResponse(
            {
                "meta": {"total_count": 12, "pageable_count": 12, "is_end": True},
                "documents": [
                    {
                        "title": "<b>AI 검색</b> 변화",
                        "contents": "검색 방식이 <b>변경</b>됩니다.",
                        "url": "https://example.com/ai",
                        "datetime": "2026-07-15T10:00:00.000+09:00",
                    }
                ],
            }
        )

    adapter = DaumSearchAdapter("rest-key", opener=opener)
    signals = adapter.search(search_type="web", query="AI 검색", size=10, page=2)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["source_type"] == "daum_web"
    assert signal["title"] == "AI 검색 변화"
    assert signal["metadata"]["description"] == "검색 방식이 변경됩니다."
    assert signal["metadata"]["discovery_query"] == "AI 검색"
    assert signal["metadata"]["result_rank"] == 11
    assert seen["url"].startswith("https://dapi.kakao.com/v2/search/web?")
    assert "query=AI+%EA%B2%80%EC%83%89" in seen["url"]
    assert "sort=recency" in unquote(seen["url"])
    assert "page=2" in seen["url"]
    assert seen["headers"]["authorization"] == "KakaoAK rest-key"


def test_cafe_search_uses_cafe_name() -> None:
    def opener(request, timeout):
        return FakeResponse(
            {
                "meta": {"total_count": 1},
                "documents": [
                    {
                        "title": "전기요금 후기",
                        "contents": "직접 확인한 내용",
                        "url": "https://cafe.daum.net/item",
                        "cafename": "생활정보 카페",
                        "datetime": "2026-07-15T09:00:00.000+09:00",
                    }
                ],
            }
        )

    signal = DaumSearchAdapter("key", opener=opener).search(
        search_type="cafe", query="전기요금", size=5
    )[0]
    assert signal["source_type"] == "daum_cafe"
    assert signal["source_name"] == "생활정보 카페"


def test_rate_limit_error_has_clear_message() -> None:
    def opener(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    adapter = DaumSearchAdapter("key", opener=opener)
    with pytest.raises(DaumSearchError) as exc_info:
        adapter.search(search_type="web", query="테스트")
    assert "HTTP 429" in str(exc_info.value)
    assert "쿼터" in str(exc_info.value)


def test_domain_resolution_error_isolated_message() -> None:
    def opener(request, timeout):
        raise URLError(socket.gaierror(11001, "getaddrinfo failed"))

    adapter = DaumSearchAdapter("key", opener=opener)
    with pytest.raises(DaumSearchError) as exc_info:
        adapter.search(search_type="cafe", query="테스트")
    assert "도메인 주소를 찾지 못했습니다" in str(exc_info.value)
    assert "다른 출처 데이터는 계속 반영됩니다" in str(exc_info.value)


def test_symbol_only_title_is_ignored() -> None:
    def opener(request, timeout):
        return FakeResponse(
            {
                "meta": {"total_count": 1},
                "documents": [
                    {"title": "---", "contents": "", "url": "https://example.com/symbol"}
                ],
            }
        )

    signals = DaumSearchAdapter("key", opener=opener).search(
        search_type="web", query="테스트"
    )
    assert signals == []
