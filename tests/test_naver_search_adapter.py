import json
import socket
from urllib.error import HTTPError, URLError

import pytest
from urllib.parse import unquote

from src.adapters.naver_search_adapter import NaverSearchAdapter, NaverSearchError, clean_html_text


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


def test_news_search_maps_official_response_to_signal() -> None:
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        return FakeResponse(
            {
                "total": 12,
                "items": [
                    {
                        "title": "<b>AI 검색</b> 변화",
                        "originallink": "https://news.example/item",
                        "link": "https://n.news.naver.com/item",
                        "description": "검색 방식이 <b>변경</b>됩니다.",
                        "pubDate": "Tue, 14 Jul 2026 10:00:00 +0900",
                    }
                ],
            }
        )

    adapter = NaverSearchAdapter("client", "secret", opener=opener)
    signals = adapter.search(search_type="news", query="AI 검색", display=10, page=3)

    assert len(signals) == 1
    signal = signals[0]
    assert signal["source_type"] == "naver_news"
    assert signal["title"] == "AI 검색 변화"
    assert signal["source_url"] == "https://news.example/item"
    assert signal["metadata"]["description"] == "검색 방식이 변경됩니다."
    assert signal["metadata"]["discovery_query"] == "AI 검색"
    assert signal["metadata"]["result_rank"] == 21
    assert seen["url"].startswith("https://naverapihub.apigw.ntruss.com/search/v1/news?")
    assert "query=AI+%EA%B2%80%EC%83%89" in seen["url"]
    assert "start=21" in seen["url"]
    assert unquote(seen["url"]).endswith("query=AI+검색&display=10&start=21&sort=date")
    normalized_headers = {key.casefold(): value for key, value in seen["headers"].items()}
    assert normalized_headers["x-ncp-apigw-api-key-id"] == "client"
    assert normalized_headers["x-ncp-apigw-api-key"] == "secret"


def test_blog_search_uses_api_hub_endpoint_and_headers() -> None:
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = {key.casefold(): value for key, value in request.header_items()}
        return FakeResponse({"total": 0, "items": []})

    adapter = NaverSearchAdapter("hub-id", "hub-secret", opener=opener)
    assert adapter.search(search_type="blog", query="트렌드", display=5) == []
    assert seen["url"].startswith("https://naverapihub.apigw.ntruss.com/search/v1/blog?")
    assert seen["headers"]["x-ncp-apigw-api-key-id"] == "hub-id"
    assert seen["headers"]["x-ncp-apigw-api-key"] == "hub-secret"


def test_domain_resolution_error_has_clear_api_hub_message() -> None:
    def opener(request, timeout):
        raise URLError(socket.gaierror(11001, "getaddrinfo failed"))

    adapter = NaverSearchAdapter("client", "secret", opener=opener)
    with pytest.raises(NaverSearchError) as exc_info:
        adapter.search(search_type="news", query="테스트")

    message = str(exc_info.value)
    assert "도메인 주소를 찾지 못했습니다" in message
    assert "YouTube 데이터는 별도로 계속 반영됩니다" in message


def test_rate_limit_error_has_clear_message() -> None:
    def opener(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", {}, None)

    adapter = NaverSearchAdapter("client", "secret", opener=opener)
    with pytest.raises(NaverSearchError) as exc_info:
        adapter.search(search_type="news", query="테스트")

    message = str(exc_info.value)
    assert "HTTP 429" in message
    assert "이번 달 사용량" in message
