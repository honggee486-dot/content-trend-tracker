"""카카오 Daum 검색 API의 웹문서·카페 결과를 공통 신호 형태로 변환합니다."""

from __future__ import annotations

import hashlib
import html
import json
import re
import socket
from datetime import datetime
from time import perf_counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from src.services.portal_request_ledger_service import record_portal_request_attempt
from src.services.trend_normalization import source_domain

_TAG_PATTERN = re.compile(r"<[^>]+>")
_SEARCHABLE_PATTERN = re.compile(r"[0-9A-Za-z가-힣]")


class DaumSearchError(RuntimeError):
    """Daum 검색 API 요청 또는 응답 처리에 실패했을 때 발생합니다."""


def clean_html_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = _TAG_PATTERN.sub("", text)
    return " ".join(text.split()).strip()


def _parse_iso_datetime(value: str) -> datetime | None:
    clean = str(value or "").strip()
    if not clean:
        return None
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


class DaumSearchAdapter:
    BASE_URL = "https://dapi.kakao.com/v2/search"

    def __init__(
        self,
        rest_api_key: str,
        *,
        opener: Callable[..., Any] = urlopen,
        timeout: int = 15,
    ) -> None:
        self.rest_api_key = str(rest_api_key or "").strip()
        self.opener = opener
        self.timeout = max(1, int(timeout))
        if not self.rest_api_key:
            raise DaumSearchError(
                "카카오 REST API 키가 없습니다. .env에 KAKAO_REST_API_KEY를 설정하세요."
            )

    def search(
        self,
        *,
        search_type: str,
        query: str,
        size: int = 10,
        sort: str = "recency",
        page: int = 1,
    ) -> list[dict[str, Any]]:
        if search_type not in {"web", "cafe"}:
            raise ValueError("search_type은 web 또는 cafe여야 합니다.")
        clean_query = str(query or "").strip()
        if not clean_query:
            return []

        page_number = max(1, min(int(page), 50))
        page_size = max(1, min(int(size), 50))
        started_at = datetime.now()
        started = perf_counter()
        try:
            signals = self._search_once(
                search_type=search_type,
                query=clean_query,
                size=page_size,
                sort=sort,
                page=page_number,
            )
        except Exception as exc:
            finished_at = datetime.now()
            record_portal_request_attempt(
                source_name="daum",
                source_type=f"daum_{search_type}",
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
            source_name="daum",
            source_type=f"daum_{search_type}",
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
        size: int,
        sort: str,
        page: int,
    ) -> list[dict[str, Any]]:
        page_number = max(1, min(int(page), 50))
        page_size = max(1, min(int(size), 50))
        params = urlencode(
            {
                "query": query,
                "sort": sort if sort in {"accuracy", "recency"} else "recency",
                "page": page_number,
                "size": page_size,
            }
        )
        url = f"{self.BASE_URL}/{search_type}?{params}"
        request = Request(
            url,
            headers={
                "Authorization": f"KakaoAK {self.rest_api_key}",
                "User-Agent": "content-trend-tracker/0.9.2",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                raise DaumSearchError(
                    "Daum 검색 API의 무료 호출 한도 또는 요청 제한에 도달했습니다(HTTP 429). "
                    "카카오디벨로퍼스 앱 관리의 쿼터 화면을 확인하세요."
                ) from exc
            if exc.code in {401, 403}:
                raise DaumSearchError(
                    f"Daum {search_type} 검색 인증이 HTTP {exc.code}로 실패했습니다. "
                    "카카오디벨로퍼스의 REST API 키를 확인하세요."
                ) from exc
            raise DaumSearchError(
                f"Daum {search_type} 검색이 HTTP {exc.code}로 실패했습니다."
            ) from exc
        except (URLError, socket.gaierror) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.gaierror) or "getaddrinfo" in str(reason).casefold():
                raise DaumSearchError(
                    "Daum 검색 API 도메인 주소를 찾지 못했습니다. 인터넷 연결, DNS, VPN, "
                    "보안 프로그램을 확인하세요. 다른 출처 데이터는 계속 반영됩니다."
                ) from exc
            raise DaumSearchError(
                f"Daum {search_type} 검색 네트워크 연결에 실패했습니다: {reason}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise DaumSearchError(
                f"Daum {search_type} 검색 응답을 JSON으로 해석하지 못했습니다."
            ) from exc
        except Exception as exc:
            raise DaumSearchError(f"Daum {search_type} 검색에 실패했습니다: {exc}") from exc

        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise DaumSearchError("Daum 검색 API 응답에 documents 목록이 없습니다.")

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        now = datetime.now()
        signals: list[dict[str, Any]] = []
        for item_offset, item in enumerate(documents):
            if not isinstance(item, dict):
                continue
            title = clean_html_text(item.get("title"))
            description = clean_html_text(item.get("contents"))
            source_url = str(item.get("url") or "").strip()
            if not title or not _SEARCHABLE_PATTERN.search(title):
                continue

            if search_type == "cafe":
                source_name = clean_html_text(item.get("cafename")) or "Daum 카페 검색"
            else:
                source_name = source_domain(source_url) or "Daum 웹문서 검색"

            published_at = _parse_iso_datetime(str(item.get("datetime") or ""))
            raw_id = source_url or f"{search_type}|{title}|{published_at}"
            external_id = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()
            signals.append(
                {
                    "source_type": f"daum_{search_type}",
                    "external_id": external_id,
                    "title": title,
                    "source_url": source_url,
                    "source_name": source_name,
                    "published_at": published_at,
                    "observed_at": now,
                    "signal_value": None,
                    "metadata": {
                        "signal_type": f"daum_{search_type}",
                        "item_title": title,
                        "description": description,
                        "discovery_query": query,
                        "daum_total": meta.get("total_count"),
                        "daum_pageable": meta.get("pageable_count"),
                        "thumbnail": item.get("thumbnail"),
                        "page": page_number,
                        "is_end": meta.get("is_end"),
                        "result_rank": ((page_number - 1) * page_size) + item_offset + 1,
                    },
                }
            )
        return signals
