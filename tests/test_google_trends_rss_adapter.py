from datetime import datetime

from src.adapters.google_trends_rss_adapter import (
    GoogleTrendsRssAdapter,
    parse_approx_traffic,
)


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


def test_google_trends_rss_is_parsed_into_signals() -> None:
    rss = b'''<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
      <channel>
        <item>
          <title>AI search update</title>
          <ht:approx_traffic>20K+</ht:approx_traffic>
          <link>https://trends.google.com/trending?geo=KR</link>
          <pubDate>Wed, 15 Jul 2026 01:00:00 +0000</pubDate>
          <ht:news_item>
            <ht:news_item_title>AI search feature announced</ht:news_item_title>
            <ht:news_item_url>https://example.com/news</ht:news_item_url>
            <ht:news_item_source>Example News</ht:news_item_source>
          </ht:news_item>
        </item>
      </channel>
    </rss>'''

    def opener(request, timeout):
        assert "geo=KR" in request.full_url
        assert timeout == 20.0
        return _Response(rss)

    adapter = GoogleTrendsRssAdapter(
        "KR",
        opener=opener,
        now_provider=lambda: datetime(2026, 7, 15, 2, 0, 0),
    )
    signals = adapter.load_signals(limit=10)

    assert adapter.request_count == 1
    assert len(signals) == 1
    signal = signals[0]
    assert signal["source_type"] == "google_trends"
    assert signal["title"] == "AI search update"
    assert signal["signal_value"] == 20_000
    assert signal["metadata"]["approx_traffic"] == "20K+"
    assert "AI search feature announced" in signal["metadata"]["description"]
    assert signal["source_url"].startswith("https://trends.google.com/trends/explore?")
    assert "q=AI+search+update" in signal["source_url"]
    assert signal["metadata"]["rss_item_url"] == "https://trends.google.com/trending?geo=KR"


def test_google_traffic_buckets_are_converted_to_lower_bounds() -> None:
    assert parse_approx_traffic("5K+") == 5_000
    assert parse_approx_traffic("1.2M+") == 1_200_000
    assert parse_approx_traffic("750+") == 750
    assert parse_approx_traffic("") == 0


def test_google_trends_shared_feed_links_produce_unique_source_urls() -> None:
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0">
      <channel>
        <item>
          <title>First trend</title>
          <ht:approx_traffic>5K+</ht:approx_traffic>
          <link>https://trends.google.com/trending?geo=KR</link>
        </item>
        <item>
          <title>Second trend</title>
          <ht:approx_traffic>10K+</ht:approx_traffic>
          <link>https://trends.google.com/trending?geo=KR</link>
        </item>
      </channel>
    </rss>"""

    adapter = GoogleTrendsRssAdapter(
        "KR",
        opener=lambda request, timeout: _Response(rss),
        now_provider=lambda: datetime(2026, 7, 15, 2, 0, 0),
    )
    signals = adapter.load_signals(limit=10)

    assert len(signals) == 2
    assert signals[0]["source_url"] != signals[1]["source_url"]
    assert signals[0]["metadata"]["rss_item_url"] == signals[1]["metadata"]["rss_item_url"]
