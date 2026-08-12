"""Google Trends 'Trending now' 공식 RSS를 로컬 신호 형식으로 변환합니다."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

GOOGLE_TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo={geo}"
GOOGLE_TRENDS_EXPLORE_URL = "https://trends.google.com/trends/explore"
_TRAFFIC_PATTERN = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KMB]?)", re.IGNORECASE)


class GoogleTrendsRssError(RuntimeError):
    """RSS 다운로드 또는 해석에 실패했을 때 발생합니다."""


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def _child_text(element: ElementTree.Element, name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == name:
            return " ".join("".join(child.itertext()).split()).strip()
    return ""


def _parse_datetime(value: str) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        parsed = parsedate_to_datetime(clean)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def parse_approx_traffic(value: str) -> int:
    """RSS의 '5K+', '1M+' 같은 구간형 검색량을 정수 하한값으로 변환합니다."""
    clean = str(value or "").replace("+", "").strip()
    match = _TRAFFIC_PATTERN.search(clean)
    if not match:
        return 0
    number = float(match.group(1).replace(",", ""))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(
        match.group(2).upper(),
        1,
    )
    return max(0, int(number * multiplier))


def _explore_url(title: str, geo: str) -> str:
    """RSS 항목마다 고유한 Google Trends 탐색 URL을 만듭니다."""
    query = urlencode({"geo": str(geo or "KR").upper(), "q": str(title or "").strip()})
    return f"{GOOGLE_TRENDS_EXPLORE_URL}?{query}"


class GoogleTrendsRssAdapter:
    def __init__(
        self,
        geo: str = "KR",
        *,
        timeout: float = 20.0,
        opener: Callable[..., Any] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.geo = str(geo or "KR").strip().upper()
        self.timeout = float(timeout)
        self._opener = opener or urlopen
        self._now_provider = now_provider or datetime.now
        self.request_count = 0

    @property
    def feed_url(self) -> str:
        return GOOGLE_TRENDS_RSS_URL.format(geo=self.geo)

    def _download(self) -> bytes:
        request = Request(
            self.feed_url,
            headers={
                "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8",
                "User-Agent": "content-trend-tracker/0.9.2 (personal local trend reader)",
            },
        )
        self.request_count += 1
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as exc:
            raise GoogleTrendsRssError(
                f"Google Trends RSS 요청이 실패했습니다. HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise GoogleTrendsRssError(
                f"Google Trends RSS에 연결하지 못했습니다: {exc.reason}"
            ) from exc
        except OSError as exc:
            raise GoogleTrendsRssError(f"Google Trends RSS 읽기 실패: {exc}") from exc

    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            root = ElementTree.fromstring(self._download())
        except ElementTree.ParseError as exc:
            raise GoogleTrendsRssError("Google Trends RSS XML 형식을 해석하지 못했습니다.") from exc

        now = self._now_provider()
        signals: list[dict[str, Any]] = []
        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue
            title = _child_text(item, "title")
            if not title:
                continue
            approx_traffic = _child_text(item, "approx_traffic")
            traffic_count = parse_approx_traffic(approx_traffic)
            published_at = _parse_datetime(_child_text(item, "pubDate")) or now
            rss_item_url = _child_text(item, "link") or self.feed_url
            link = _explore_url(title, self.geo)
            description = _child_text(item, "description")
            picture_source = _child_text(item, "picture_source")

            news_items: list[dict[str, str]] = []
            for child in list(item):
                if _local_name(child.tag) != "news_item":
                    continue
                news_items.append(
                    {
                        "title": _child_text(child, "news_item_title"),
                        "url": _child_text(child, "news_item_url"),
                        "source": _child_text(child, "news_item_source"),
                    }
                )
            news_items = [entry for entry in news_items if entry.get("title") or entry.get("url")]
            related_titles = [entry["title"] for entry in news_items if entry.get("title")]
            evidence_description = " ".join(
                part for part in [description, *related_titles] if str(part or "").strip()
            )
            external_id = hashlib.sha1(title.casefold().encode("utf-8")).hexdigest()
            signals.append(
                {
                    "source_type": "google_trends",
                    "external_id": external_id,
                    "title": title,
                    "source_name": "Google Trends 한국",
                    "source_url": link,
                    "published_at": published_at,
                    "observed_at": now,
                    "signal_value": traffic_count,
                    "metadata": {
                        "signal_type": "google_trend",
                        "item_title": title,
                        "description": evidence_description,
                        "approx_traffic": approx_traffic,
                        "traffic_count": traffic_count,
                        "geo": self.geo,
                        "picture_source": picture_source,
                        "rss_item_url": rss_item_url,
                        "news_items": news_items,
                    },
                }
            )
            if len(signals) >= max(1, int(limit)):
                break
        return signals
