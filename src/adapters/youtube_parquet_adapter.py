"""YouTube 트래커가 생성한 Parquet 교환 파일을 읽는 어댑터입니다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb


SUPPORTED_SCHEMA_VERSION = "1.0"
REQUIRED_COLUMNS = {
    "schema_version",
    "source_type",
    "signal_type",
    "external_id",
    "topic_title",
    "item_title",
    "keyword",
    "source_url",
    "source_name",
    "published_at",
    "observed_at",
    "signal_value",
    "view_count",
    "view_delta",
    "views_per_hour",
    "topic_score",
    "metadata_json",
    "exported_at",
}


class YouTubeParquetError(RuntimeError):
    """Parquet 교환 파일을 안전하게 사용할 수 없을 때 발생합니다."""


class YouTubeParquetAdapter:
    """검증된 YouTube Parquet 신호를 메인 앱의 공통 신호 형태로 바꿉니다."""

    def __init__(self, parquet_path: str | Path):
        self.parquet_path = Path(parquet_path)

    def _require_file(self) -> None:
        if not self.parquet_path.is_file():
            raise YouTubeParquetError(
                "Parquet 교환 파일이 아직 생성되지 않았습니다. YouTube 트래커의 시간별 스냅샷 작업을 먼저 실행하세요."
            )

    def _read_metadata(self, con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        escaped_path = str(self.parquet_path).replace("'", "''")
        try:
            columns = {
                str(row[0])
                for row in con.execute(
                    f"DESCRIBE SELECT * FROM read_parquet('{escaped_path}')"
                ).fetchall()
            }
            missing = sorted(REQUIRED_COLUMNS - columns)
            if missing:
                raise YouTubeParquetError(
                    "Parquet 교환 파일의 필수 열이 없습니다: " + ", ".join(missing)
                )
            row = con.execute(
                f"""
                SELECT COUNT(*) AS row_count,
                       COUNT(DISTINCT schema_version) AS version_count,
                       MIN(schema_version) AS schema_version,
                       MAX(exported_at) AS exported_at
                FROM read_parquet('{escaped_path}')
                """
            ).fetchone()
        except YouTubeParquetError:
            raise
        except Exception as exc:
            raise YouTubeParquetError(
                "Parquet 교환 파일이 올바르지 않거나 일부 손상되어 읽을 수 없습니다."
            ) from exc

        row_count = int(row[0] or 0)
        version_count = int(row[1] or 0)
        schema_version = str(row[2]) if row[2] is not None else SUPPORTED_SCHEMA_VERSION
        if row_count and (version_count != 1 or schema_version != SUPPORTED_SCHEMA_VERSION):
            raise YouTubeParquetError(
                f"지원하지 않는 Parquet 스키마 버전입니다: {schema_version} "
                f"(지원 버전: {SUPPORTED_SCHEMA_VERSION})"
            )
        return {
            "parquet_path": str(self.parquet_path),
            "exists": True,
            "schema_version": schema_version,
            "exported_at": row[3],
            "row_count": row_count,
        }

    def inspect(self) -> dict[str, Any]:
        self._require_file()
        with duckdb.connect(":memory:") as con:
            return self._read_metadata(con)

    def load_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        self._require_file()
        safe_limit = max(1, min(int(limit), 500))
        escaped_path = str(self.parquet_path).replace("'", "''")
        with duckdb.connect(":memory:") as con:
            self._read_metadata(con)
            try:
                rows = con.execute(
                    f"""
                    SELECT source_type, signal_type, external_id, topic_title,
                           item_title, keyword, source_url, source_name,
                           published_at, observed_at, signal_value, view_count,
                           view_delta, views_per_hour, topic_score, metadata_json
                    FROM read_parquet('{escaped_path}')
                    ORDER BY observed_at DESC NULLS LAST, signal_value DESC NULLS LAST
                    LIMIT ?
                    """,
                    [safe_limit],
                ).fetchall()
            except Exception as exc:
                raise YouTubeParquetError(
                    "Parquet 교환 파일이 올바르지 않거나 일부 손상되어 신호를 읽을 수 없습니다."
                ) from exc
            columns = [item[0] for item in con.description]

        signals: list[dict[str, Any]] = []
        for row in rows:
            data = dict(zip(columns, row))
            external_id = str(data.get("external_id") or "").strip()
            title = str(data.get("topic_title") or data.get("item_title") or "").strip()
            if not external_id or not title:
                continue
            try:
                metadata = json.loads(data.get("metadata_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            metadata.update(
                {
                    "signal_type": data.get("signal_type"),
                    "item_title": data.get("item_title"),
                    "keyword": data.get("keyword"),
                    "view_count": data.get("view_count"),
                    "view_delta": data.get("view_delta"),
                    "views_per_hour": data.get("views_per_hour"),
                    "topic_score": data.get("topic_score"),
                }
            )
            signals.append(
                {
                    "source_type": str(data.get("source_type") or "youtube"),
                    "external_id": external_id,
                    "title": title,
                    "source_url": data.get("source_url"),
                    "source_name": data.get("source_name"),
                    "published_at": data.get("published_at"),
                    "observed_at": data.get("observed_at"),
                    "signal_value": data.get("signal_value"),
                    "metadata": metadata,
                }
            )
        return signals
