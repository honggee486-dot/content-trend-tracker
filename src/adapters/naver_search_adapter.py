"""네이버 검색 API의 뉴스·블로그 결과를 공통 신호 형태로 변환합니다."""

from __future__ import annotations

import hashlib
import html
import json
import re
import socket
from datetime import datetime
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.services.portal_request_ledger_service import record_portal_request_attempt
from src.services.trend_normalization import source_domain

_TAG_PATTERN = re.compile(r"<[^>]+>")
_SEARCHABLE_PATTERN = re.compile(r"[0-9A-Za-z가-힣]")


class NaverSearchError(RuntimeError):
    """네이버 검색 API 요청 또는 응답 처리에 실패했을 때 발생합니다."""


def clean_html_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_PATTERN.sub("", text)
    return " ".join(text.split()).strip()


def _parse_news_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_blog_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y%m%d")
    except ValueError:
        return None


# email.utils import는 날짜 파서 가까이에 두되 기존 공개 동작은 유지합니다.
from email.utils import parsedate_to_datetime


class NaverSearchAdapter:
    BASE_URL = "https://naverapihub.apigw.ntruss.com/search/v1"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        opener: Callable[..., Any] = urlopen,
        timeout: int = 15,
    ) -> None:
        self.client_id = str(client_id or "").strip()
        self.client_secret = str(client_secret or "").strip()
        self.opener = opener
        self.timeout = max(1, int(timeout))
        if not self.client_id or not self.client_secret:
            raise NaverSearchError(
                "네이버 검색 API 키가 없습니다. .env에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정하세요."
            )

    def search(
        self,
        *,
        search_type: str,
        query: str,
        display: int = 10,
        sort: str = "date",
        page: int = 1,
    ) -> list[dict[str, Any]]:
        if search_type not in {"news", "blog"}:
            raise ValueError("search_type은 news 또는 blog여야 합니다.")
        clean_query = str(query or "").strip()
        if not clean_query:
            return []

        page_size = max(1, min(int(display), 100))
        page_number = max(1, int(page))
        started_at = datetime.now()
        started = perf_counter()
        try:
            signals = self._search_once(
                search_type=search_type,
                query=clean_query,
                display=page_size,
                sort=sort,
                page=page_number,
            )
        except Exception as exc:
            finished_at = datetime.now()
            record_portal_request_attempt(
                source_name="naver",
                source_type=f"naver_{search_type}",
                discovery_query=clean_query,
                request_page=page_number,
                requested_result_count=page_size,
                result_count=0,
                duration_ms=int(round((perf_counter() - started) * 1000)),
                started_at=started_at,
                finished_at=finished_at,
                error=exc,
            )
            raise

        finished_at = datetime.now()
        record_portal_request_attempt(
            source_name="naver",
            source_type=f"naver_{search_type}",
            discovery_query=clean_query,
            request_page=page_number,
            requested_result_count=page_size,
            result_count=len(signals),
            duration_ms=int(round((perf_counter() - started) * 1000)),
            started_at=started_at,
            finished_at=finished_at,
        )
        return signals

    def _search_once(
        self,
        *,
        search_type: str,
        query: str,
        display: int,
        sort: str,
        page: int,
    ) -> list[dict[str, Any]]:
        page_size = max(1, min(int(display), 100))
        page_number = max(1, int(page))
        start = min(1000, ((page_number - 1) * page_size) + 1)
        params = urlencode(
            {
                "query": query,
                "display": page_size,
                "start": start,
                "sort": sort if sort in {"date", "sim"} else "date",
            }
        )
        url = f"{self.BASE_URL}/{search_type}?{params}"
        auth_headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
        }
        request = Request(
            url,
            headers={**auth_headers, "User-Agent": "content-trend-tracker/0.9.2"},
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                raise NaverSearchError(
                    "NAVER API HUB 요청 속도 또는 호출 한도를 초과했습니다(HTTP 429). "
                    "잠시 후 다시 시도하고, 계속되면 NAVER Cloud 콘솔의 이번 달 사용량을 확인하세요."
                ) from exc
            raise NaverSearchError(
                f"NAVER API HUB {search_type} 검색이 HTTP {exc.code}로 실패했습니다. "
                "API 사용 권한과 Client ID·Client Secret을 확인하세요."
            ) from exc
        except (URLError, socket.gaierror) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.gaierror) or "getaddrinfo" in str(reason).casefold():
                raise NaverSearchError(
                    "NAVER API HUB 도메인 주소를 찾지 못했습니다. 인터넷 연결, DNS, VPN, "
                    "보안 프로그램을 확인하세요. YouTube 데이터는 별도로 계속 반영됩니다."
                ) from exc
            raise NaverSearchError(
                f"NAVER API HUB {search_type} 검색 네트워크 연결에 실패했습니다: {reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise NaverSearchError(
                f"NAVER API HUB {search_type} 응답을 JSON으로 해석하지 못했습니다."
            ) from exc
        except Exception as exc:
            raise NaverSearchError(f"네이버 {search_type} 검색에 실패했습니다: {exc}") from exc

        items = payload.get("items")
        if not isinstance(items, list):
            raise NaverSearchError("네이버 검색 API 응답에 items 목록이 없습니다.")

        signals: list[dict[str, Any]] = []
        now = datetime.now()
        for item_offset, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            title = clean_html_text(item.get("title"))
            description = clean_html_text(item.get("description"))
            if not title or not _SEARCHABLE_PATTERN.search(title):
                continue
            if search_type == "news":
                source_url = str(item.get("originallink") or item.get("link") or "").strip()
                source_name = source_domain(source_url) or "네이버 뉴스 검색"
                published_at = _parse_news_date(str(item.get("pubDate") or ""))
            else:
                source_url = str(item.get("link") or "").strip()
                source_name = clean_html_text(item.get("bloggername")) or "네이버 블로그 검색"
                published_at = _parse_blog_date(str(item.get("postdate") or ""))
            raw_id = source_url or f"{search_type}|{title}|{published_at}"
            external_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()
            signals.append(
                {
                    "source_type": f"naver_{search_type}",
                    "external_id": external_id,
                    "title": title,
                    "source_url": source_url,
                    "source_name": source_name,
                    "published_at": published_at,
                    "observed_at": now,
                    "signal_value": None,
                    "metadata": {
                        "signal_type": f"naver_{search_type}",
                        "item_title": title,
                        "description": description,
                        "discovery_query": query,
                        "naver_total": payload.get("total"),
                        "bloggerlink": item.get("bloggerlink"),
                        "page": page_number,
                        "start": start,
                        "result_rank": start + item_offset,
                    },
                }
            )
        return signals
