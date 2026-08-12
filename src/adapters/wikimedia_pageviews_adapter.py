"""Wikimedia Analytics API의 한국어 위키백과 인기 문서를 신호로 변환합니다."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

WIKIMEDIA_TOP_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
    "{project}/all-access/{year:04d}/{month:02d}/{day:02d}"
)
_SEARCHABLE_PATTERN = re.compile(r"[0-9A-Za-z가-힣]")
_IGNORED_EXACT = {
    "-",
    "Main_Page",
    "대문",
    "위키백과:대문",
    "Wikipedia:Main_Page",
}
_IGNORED_PREFIXES = (
    "Special:",
    "특수:",
    "File:",
    "파일:",
    "Template:",
    "틀:",
    "Category:",
    "분류:",
    "Help:",
    "도움말:",
    "Wikipedia:",
    "위키백과:",
)


class WikimediaPageviewsError(RuntimeError):
    """페이지뷰 API 다운로드 또는 해석에 실패했을 때 발생합니다."""


class WikimediaPageviewsAdapter:
    def __init__(
        self,
        project: str = "ko.wikipedia.org",
        *,
        timeout: float = 20.0,
        fallback_days: int = 7,
        opener: Callable[..., Any] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.project = str(project or "ko.wikipedia.org").strip()
        self.timeout = float(timeout)
        self.fallback_days = max(1, int(fallback_days))
        self._opener = opener or urlopen
        self._now_provider = now_provider or datetime.now
        self.request_count = 0

    def _download_for_date(self, target_date: datetime) -> dict[str, Any] | None:
        url = WIKIMEDIA_TOP_URL.format(
            project=self.project,
            year=target_date.year,
            month=target_date.month,
            day=target_date.day,
        )
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "content-trend-tracker/0.9.2 (personal local trend reader)",
            },
        )
        self.request_count += 1
        try:
            with self._opener(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {400, 404}:
                return None
            raise WikimediaPageviewsError(
                f"Wikimedia Pageviews 요청이 실패했습니다. HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            raise WikimediaPageviewsError(
                f"Wikimedia Pageviews에 연결하지 못했습니다: {exc.reason}"
            ) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikimediaPageviewsError(f"Wikimedia Pageviews 읽기 실패: {exc}") from exc

    @staticmethod
    def _is_content_article(article: str) -> bool:
        clean = str(article or "").strip()
        normalized = clean.replace(" ", "_")
        if not clean or clean in _IGNORED_EXACT or normalized in _IGNORED_EXACT:
            return False
        return not clean.startswith(_IGNORED_PREFIXES)

    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        now = self._now_provider()
        payload: dict[str, Any] | None = None
        selected_date: datetime | None = None
        # 일별 집계는 즉시 완성되지 않을 수 있으므로 어제부터 며칠 전까지 순차 확인합니다.
        for days_ago in range(1, self.fallback_days + 1):
            candidate = now - timedelta(days=days_ago)
            payload = self._download_for_date(candidate)
            if payload:
                selected_date = candidate
                break
        if not payload or selected_date is None:
            raise WikimediaPageviewsError(
                f"최근 {self.fallback_days}일 범위에서 한국어 위키백과 인기 문서 데이터를 찾지 못했습니다."
            )

        items = payload.get("items") or []
        articles = items[0].get("articles") if items and isinstance(items[0], dict) else []
        if not isinstance(articles, list):
            raise WikimediaPageviewsError("Wikimedia Pageviews 응답에 인기 문서 목록이 없습니다.")

        signals: list[dict[str, Any]] = []
        published_at = selected_date.replace(hour=23, minute=59, second=0, microsecond=0)
        for entry in articles:
            if not isinstance(entry, dict):
                continue
            raw_article = unquote(str(entry.get("article") or "")).replace("_", " ").strip()
            if not self._is_content_article(raw_article) or not _SEARCHABLE_PATTERN.search(raw_article):
                continue
            views = max(0, int(entry.get("views") or 0))
            rank = max(1, int(entry.get("rank") or len(signals) + 1))
            external_id = hashlib.sha1(raw_article.casefold().encode("utf-8")).hexdigest()
            article_path = quote(raw_article.replace(" ", "_"), safe="()'!~*,-._")
            signals.append(
                {
                    "source_type": "wikipedia_pageviews",
                    "external_id": external_id,
                    "title": raw_article,
                    "source_name": "한국어 위키백과 조회수",
                    "source_url": f"https://ko.wikipedia.org/wiki/{article_path}",
                    "published_at": published_at,
                    "observed_at": now,
                    "signal_value": views,
                    "metadata": {
                        "signal_type": "wikipedia_pageview",
                        "item_title": raw_article,
                        "description": f"한국어 위키백과 일간 조회수 {views:,}회, {rank}위",
                        "pageview_date": selected_date.strftime("%Y-%m-%d"),
                        "rank": rank,
                        "views": views,
                        "project": self.project,
                    },
                }
            )
            if len(signals) >= max(1, int(limit)):
                break
        return signals
