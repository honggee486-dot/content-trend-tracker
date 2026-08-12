from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb


class YouTubeDatabaseError(RuntimeError):
    """YouTube 트래커 DB를 안전하게 읽지 못했을 때 발생합니다."""


def _hash_external_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.casefold().encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _first_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for separator in ["\n", ",", "|"]:
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    return text or None


class YouTubeDuckDBAdapter:
    """기존 YouTube 트래커 DB를 수정하지 않고 트렌드 신호만 읽습니다."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        if not self.db_path.is_file():
            raise YouTubeDatabaseError(f"YouTube DB 파일을 찾을 수 없습니다: {self.db_path}")
        try:
            return duckdb.connect(str(self.db_path), read_only=True)
        except Exception as exc:
            raise YouTubeDatabaseError(
                "YouTube DB를 읽기 전용으로 열지 못했습니다. 수집 작업 중이면 잠시 후 다시 시도하세요."
            ) from exc

    @staticmethod
    def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
        return {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}

    def inspect(self) -> dict[str, Any]:
        with self._connect() as con:
            tables = sorted(self._tables(con))
            return {"db_path": str(self.db_path), "tables": tables, "table_count": len(tables)}

    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            tables = self._tables(con)
            if "emerging_topic_snapshots" in tables:
                rows = self._load_emerging_topics(con, safe_limit)
                if rows:
                    return rows
            if {"recent_trend_candidates", "videos"}.issubset(tables):
                rows = self._load_recent_videos(con, safe_limit, tables)
                if rows:
                    return rows
            if "content_ideas" in tables:
                rows = self._load_content_ideas(con, safe_limit)
                if rows:
                    return rows
        return []

    def _load_emerging_topics(
        self,
        con: duckdb.DuckDBPyConnection,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = con.execute(
            """
            WITH latest_run AS (
                SELECT MAX(snapshot_at) AS snapshot_at
                FROM emerging_topic_snapshots
            )
            SELECT
                topic,
                snapshot_at,
                topic_rank,
                topic_score,
                topic_stage,
                video_count,
                channel_count,
                confirmed_video_count,
                total_recent_views_per_hour,
                total_view_growth,
                representative_titles,
                representative_video_urls,
                source_labels
            FROM emerging_topic_snapshots
            WHERE snapshot_at = (SELECT snapshot_at FROM latest_run)
            ORDER BY topic_rank ASC, topic_score DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        columns = [item[0] for item in con.description]
        signals: list[dict[str, Any]] = []
        for row in rows:
            data = dict(zip(columns, row))
            topic = str(data.get("topic") or "").strip()
            if not topic:
                continue
            metadata = {
                "topic_rank": data.get("topic_rank"),
                "topic_stage": data.get("topic_stage"),
                "video_count": data.get("video_count"),
                "channel_count": data.get("channel_count"),
                "confirmed_video_count": data.get("confirmed_video_count"),
                "views_per_hour": data.get("total_recent_views_per_hour"),
                "view_growth": data.get("total_view_growth"),
                "representative_titles": data.get("representative_titles"),
                "source_labels": data.get("source_labels"),
            }
            signals.append(
                {
                    "source_type": "youtube",
                    "external_id": _hash_external_id("emerging", topic),
                    "title": topic,
                    "source_url": _first_url(data.get("representative_video_urls")),
                    "source_name": "기존 YouTube 트렌드 트래커 · 떠오르는 주제",
                    "published_at": None,
                    "observed_at": data.get("snapshot_at"),
                    "signal_value": data.get("topic_score"),
                    "metadata": metadata,
                }
            )
        return signals

    def _load_recent_videos(
        self,
        con: duckdb.DuckDBPyConnection,
        limit: int,
        tables: set[str],
    ) -> list[dict[str, Any]]:
        snapshot_join = ""
        snapshot_columns = "NULL AS view_count, NULL AS collected_at"
        if "video_snapshots" in tables:
            snapshot_join = """
                LEFT JOIN (
                    SELECT video_id,
                           arg_max(view_count, collected_at) AS view_count,
                           MAX(collected_at) AS collected_at
                    FROM video_snapshots
                    GROUP BY video_id
                ) s ON s.video_id = r.video_id
            """
            snapshot_columns = "s.view_count, s.collected_at"

        query = f"""
            SELECT
                r.video_id,
                r.keyword,
                r.first_discovered_at,
                r.last_discovered_at,
                r.initial_view_count,
                v.title,
                v.channel_title,
                v.published_at,
                {snapshot_columns}
            FROM recent_trend_candidates r
            JOIN videos v ON v.video_id = r.video_id
            {snapshot_join}
            ORDER BY COALESCE(s.view_count, r.initial_view_count, 0) DESC NULLS LAST,
                     r.last_discovered_at DESC
            LIMIT ?
        """ if "video_snapshots" in tables else f"""
            SELECT
                r.video_id,
                r.keyword,
                r.first_discovered_at,
                r.last_discovered_at,
                r.initial_view_count,
                v.title,
                v.channel_title,
                v.published_at,
                {snapshot_columns}
            FROM recent_trend_candidates r
            JOIN videos v ON v.video_id = r.video_id
            ORDER BY COALESCE(r.initial_view_count, 0) DESC NULLS LAST,
                     r.last_discovered_at DESC
            LIMIT ?
        """

        rows = con.execute(query, [limit]).fetchall()
        columns = [item[0] for item in con.description]
        signals: list[dict[str, Any]] = []
        for row in rows:
            data = dict(zip(columns, row))
            title = str(data.get("title") or "").strip()
            video_id = str(data.get("video_id") or "").strip()
            if not title or not video_id:
                continue
            current_views = data.get("view_count") or data.get("initial_view_count")
            signals.append(
                {
                    "source_type": "youtube",
                    "external_id": f"video:{video_id}",
                    "title": title,
                    "source_url": f"https://www.youtube.com/watch?v={video_id}",
                    "source_name": "기존 YouTube 트렌드 트래커 · 최근 영상",
                    "published_at": data.get("published_at"),
                    "observed_at": data.get("collected_at") or data.get("last_discovered_at"),
                    "signal_value": float(current_views or 0),
                    "metadata": {
                        "keyword": data.get("keyword"),
                        "channel_title": data.get("channel_title"),
                        "initial_view_count": data.get("initial_view_count"),
                        "current_view_count": current_views,
                        "first_discovered_at": str(data.get("first_discovered_at") or ""),
                    },
                }
            )
        return signals

    def _load_content_ideas(
        self,
        con: duckdb.DuckDBPyConnection,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = con.execute(
            """
            SELECT idea_id, title, keyword, content_type, status, priority,
                   score, view_growth, views_per_hour_growth, updated_at
            FROM content_ideas
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        columns = [item[0] for item in con.description]
        signals: list[dict[str, Any]] = []
        for row in rows:
            data = dict(zip(columns, row))
            title = str(data.get("title") or "").strip()
            if not title:
                continue
            signals.append(
                {
                    "source_type": "youtube",
                    "external_id": f"idea:{data.get('idea_id')}",
                    "title": title,
                    "source_url": None,
                    "source_name": "기존 YouTube 트렌드 트래커 · 콘텐츠 아이디어",
                    "published_at": None,
                    "observed_at": data.get("updated_at"),
                    "signal_value": data.get("score"),
                    "metadata": {
                        "keyword": data.get("keyword"),
                        "content_type": data.get("content_type"),
                        "status": data.get("status"),
                        "priority": data.get("priority"),
                        "view_growth": data.get("view_growth"),
                        "views_per_hour_growth": data.get("views_per_hour_growth"),
                    },
                }
            )
        return signals
