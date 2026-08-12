from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.database import connect_database, init_database
from src.fact_check_readiness_ui import render_fact_check_readiness
from src.services.fact_check_readiness_service import (
    FAST_RECHECK_HOURS,
    SLOW_RECHECK_HOURS,
    build_fact_check_readiness,
    get_fact_check_readiness,
)


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.subheaders: list[str] = []
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.warnings: list[str] = []
        self.dataframes: list[object] = []

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def dataframe(self, value, **_kwargs) -> None:
        self.dataframes.append(value)


def _draft(
    draft_id: str,
    *,
    title: str,
    updated_at: datetime,
    body: str = "일반 정보 본문",
) -> dict[str, object]:
    return {
        "draft_id": draft_id,
        "topic_id": f"topic_{draft_id}",
        "title": title,
        "summary": "요약",
        "body_markdown": body,
        "current_revision": 1,
        "updated_at": updated_at,
        "created_at": updated_at,
        "topic_title": title,
        "topic_status": "editing",
    }


def _check(
    draft_id: str,
    *,
    fact_check_id: str,
    claim: str,
    status: str,
    checked_at: datetime | None,
    source_url: str = "https://example.com/source",
    evidence: str = "확인 메모",
) -> dict[str, object]:
    return {
        "draft_id": draft_id,
        "fact_check_id": fact_check_id,
        "claim_text": claim,
        "check_status": status,
        "reason": "사실 확인 필요",
        "evidence": evidence,
        "source_url": source_url,
        "checked_at": checked_at,
    }


def test_fact_check_readiness_classifies_workflow_and_recheck_states() -> None:
    now = datetime(2026, 7, 31, 7, 0, 0)
    drafts = [
        _draft("revision", title="수정할 글", updated_at=now - timedelta(days=8)),
        _draft("pending", title="확인할 글", updated_at=now - timedelta(days=1)),
        _draft("stale", title="오늘 환율", updated_at=now - timedelta(hours=2)),
        _draft("ready", title="확인 완료 글", updated_at=now - timedelta(hours=1)),
        _draft("gap", title="오늘 날씨", updated_at=now - timedelta(hours=1)),
        _draft("published", title="발행한 글", updated_at=now - timedelta(days=2)),
    ]
    checks = [
        _check(
            "revision",
            fact_check_id="fc_revision",
            claim="정책 설명",
            status="needs_revision",
            checked_at=now - timedelta(days=8),
        ),
        _check(
            "pending",
            fact_check_id="fc_pending",
            claim="확인이 필요한 주장",
            status="needs_verification",
            checked_at=None,
            source_url="",
            evidence="",
        ),
        _check(
            "stale",
            fact_check_id="fc_stale",
            claim="오늘 환율은 1,400원이다",
            status="verified",
            checked_at=now - timedelta(hours=FAST_RECHECK_HOURS + 1),
        ),
        _check(
            "ready",
            fact_check_id="fc_ready",
            claim="공식 발표 내용",
            status="verified",
            checked_at=now - timedelta(hours=2),
        ),
        _check(
            "published",
            fact_check_id="fc_published",
            claim="경기 결과",
            status="needs_verification",
            checked_at=None,
            source_url="",
            evidence="",
        ),
    ]
    publishes = [
        {
            "draft_id": "published",
            "publish_id": "pub_1",
            "publish_status": "published",
            "created_at": now - timedelta(days=1),
            "published_at": now - timedelta(days=1),
        }
    ]

    diagnostics = build_fact_check_readiness(drafts, checks, publishes, now=now)
    states = {row["draft_id"]: row for row in diagnostics["rows"]}

    assert states["revision"]["readiness_state"] == "needs_revision"
    assert states["revision"]["is_abandoned"] is True
    assert states["pending"]["readiness_state"] == "needs_verification"
    assert states["stale"]["readiness_state"] == "recheck_due"
    assert states["stale"]["recheck_due_count"] == 1
    assert states["ready"]["readiness_state"] == "ready"
    assert states["gap"]["readiness_state"] == "no_checks"
    assert states["gap"]["has_time_sensitive_gap"] is True
    assert states["published"]["readiness_state"] == "published_attention"
    assert diagnostics["published_attention_count"] == 1
    assert diagnostics["abandoned_count"] == 1
    assert diagnostics["fast_recheck_hours"] == 24
    assert diagnostics["slow_recheck_hours"] == 24 * 7


def _insert_topic_and_draft(
    con,
    *,
    topic_id: str,
    draft_id: str,
    title: str,
    body: str,
    now: datetime,
) -> None:
    con.execute(
        """
        INSERT INTO topics(
            topic_id, title, normalized_title, summary, category, status,
            priority, is_interested, memo, source_count, first_seen_at,
            last_seen_at, created_at, updated_at
        ) VALUES (?, ?, ?, '', '', 'editing', 3, FALSE, '', 0, ?, ?, ?, ?)
        """,
        [topic_id, title, title, now, now, now, now],
    )
    con.execute(
        """
        INSERT INTO drafts(
            draft_id, topic_id, generation_id, title, summary, category,
            tags_json, body_markdown, body_html, sources_json,
            image_prompts_json, current_revision, created_at, updated_at
        ) VALUES (?, ?, NULL, ?, '', '', '[]', ?, '', '[]', '[]', 1, ?, ?)
        """,
        [draft_id, topic_id, title, body, now, now],
    )


def test_fact_check_readiness_reads_database_without_modifying_records(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fact-check-readiness.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 7, 0, 0)

    with connect_database(db_path) as con:
        _insert_topic_and_draft(
            con,
            topic_id="topic_ready",
            draft_id="draft_ready",
            title="확인 완료 초안",
            body="공식 자료를 바탕으로 작성한 글",
            now=now,
        )
        _insert_topic_and_draft(
            con,
            topic_id="topic_price",
            draft_id="draft_price",
            title="오늘 가격 정리",
            body="현재 가격과 재고 현황을 설명한다.",
            now=now,
        )
        con.execute(
            """
            INSERT INTO fact_check_items(
                fact_check_id, draft_id, claim_text, check_status, reason,
                evidence, source_ids_json, source_url, checked_at
            ) VALUES ('fc_ready', 'draft_ready', '공식 발표 내용', 'verified',
                      '공식 확인', '보도자료 확인', '[]', NULL, ?)
            """,
            [now],
        )

        before = con.execute(
            "SELECT fact_check_id, check_status, evidence, source_url, checked_at "
            "FROM fact_check_items ORDER BY fact_check_id"
        ).fetchall()
        diagnostics = get_fact_check_readiness(con, now=now)
        fake_st = _FakeStreamlit()
        render_fact_check_readiness(con, st_module=fake_st)
        after = con.execute(
            "SELECT fact_check_id, check_status, evidence, source_url, checked_at "
            "FROM fact_check_items ORDER BY fact_check_id"
        ).fetchall()

    assert before == after
    assert diagnostics["draft_count"] == 2
    assert diagnostics["ready_count"] == 1
    assert diagnostics["no_checks_count"] == 1
    assert diagnostics["time_sensitive_gap_count"] == 1
    assert diagnostics["verified_without_url_count"] == 1

    assert fake_st.subheaders == ["사실 확인 준비도"]
    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "전체 초안",
        "확인 대기",
        "수정 필요",
        "재확인 필요",
        "발행 준비",
        "확인 항목 없음",
    ]
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert len(fake_st.dataframes) == 1
    assert {
        "상태",
        "초안",
        "미확인",
        "수정 필요",
        "재확인",
        "완료·URL 없음",
        "다음 행동",
    }.issubset(set(fake_st.dataframes[0].columns))
    assert any("자동 변경하지 않습니다" in value for value in fake_st.captions)


def test_source_freshness_screen_includes_fact_check_readiness() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "src" / "source_freshness_ui.py"
    ).read_text(encoding="utf-8")

    assert "render_fact_check_readiness(con, st_module=st_module)" in source
