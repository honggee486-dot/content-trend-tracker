import json
from datetime import datetime

from src.adapters.wikimedia_pageviews_adapter import WikimediaPageviewsAdapter


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


def test_wikimedia_top_pages_are_filtered_and_converted() -> None:
    payload = json.dumps(
        {
            "items": [
                {
                    "articles": [
                        {"article": "Main_Page", "views": 999999, "rank": 1},
                        {"article": "AI_%EA%B2%80%EC%83%89", "views": 12345, "rank": 2},
                        {"article": "특수:검색", "views": 5000, "rank": 3},
                    ]
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")

    def opener(request, timeout):
        assert "ko.wikipedia.org" in request.full_url
        return _Response(payload)

    adapter = WikimediaPageviewsAdapter(
        opener=opener,
        now_provider=lambda: datetime(2026, 7, 15, 12, 0, 0),
    )
    signals = adapter.load_signals(limit=10)

    assert adapter.request_count == 1
    assert len(signals) == 1
    signal = signals[0]
    assert signal["source_type"] == "wikipedia_pageviews"
    assert signal["title"] == "AI 검색"
    assert signal["signal_value"] == 12_345
    assert signal["metadata"]["rank"] == 2
    assert "ko.wikipedia.org/wiki/AI_" in signal["source_url"]
