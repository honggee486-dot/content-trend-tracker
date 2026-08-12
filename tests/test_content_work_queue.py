from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.content_work_queue_ui import render_content_work_queue
from src.database import connect_database, init_database
from src.services.content_work_queue_service import (
    ABANDONED_DAYS,
    build_content_work_queue,
    get_content_work_queue,
)


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeColumn:
    def __init__(self, owner, index: int) -> None:
        self.owner = owner
        self.index = index

    def markdown(self, value: str, **_kwargs) -> None:
        self.owner.markdowns.append(value)

    def caption(self, value: str) -> None:
        self.owner.captions.append(value)

    def button(self, label: str, **kwargs) -> bool:
        self.owner.buttons.append((label, kwargs))
        return self.owner.click_first and len(self.owner.buttons) == 1


class _FakeStreamlit:
    def __init__(self, *, click_first: bool = False) -> None:
        self.click_first = click_first
        self.subheaders: list[str] = []
        self.metrics: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.captions: list[str] = []
        self.markdowns: list[str] = []
        self.buttons: list[tuple[str, dict[str, object]]] = []
        self.successes: list[str] = []

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def container(self, **_kwargs) -> _Context:
        return _Context()

    def columns(self, spec, **_kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_FakeColumn(self, index) for index in range(count)]

    def metric(self, *args, **kwargs) -> None:
        self.metrics.append((args, kwargs))

    def success(self, value: str) -> None:
        self.successes.append(value)


def _topic_row(
    topic_id: str,
    *,
    status: str = "candidate",
    source_count: int = 0,
    updated_at: datetime,
    content_pack_id: str = "",
    pack_version: int = 0,
    pack_created_at: datetime | None = None,
    draft_id: str = "",
    draft_content_pack_id: str = "",
    draft_updated_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "topic_id": topic_id,
        "topic_title": f"{topic_id} 제목",
        "topic_summary": "",
        "topic_memo": "",
        "topic_status": status,
        "topic_priority": 2,
        "source_count": source_count,
        "topic_updated_at": updated_at,
        "content_pack_id": content_pack_id,
        "pack_version": pack_version,
        "pack_created_at": pack_created_at,
        "draft_id": draft_id,
        "draft_title": f"{topic_id} 초안",
        "draft_content_pack_id": draft_content_pack_id,
        "draft_updated_at": draft_updated_at,
    }


def test_content_work_queue_keeps_one_current_stage_per_topic() -> None:
    now = datetime(2026, 7, 31, 8, 0, 0)
    old = now - timedelta(days=ABANDONED_DAYS + 1)
    recent = now - timedelta(hours=2)
    rows = [
        _topic_row("research", updated_at=recent),
        _topic_row("request", source_count=2, updated_at=old),
        _topic_row(
            "waiting",
            source_count=2,
            updated_at=recent,
            content_pack_id="pack_waiting",
            pack_version=2,
            pack_created_at=recent,
        ),
        _topic_row(
            "editing",
            source_count=2,
            updated_at=recent,
            content_pack_id="pack_editing",
            pack_version=1,
            pack_created_at=recent - timedelta(days=1),
            draft_id="draft_editing",
            draft_content_pack_id="pack_editing",
            draft_updated_at=recent,
        ),
        _topic_row(
            "checking",
            source_count=2,
            updated_at=recent,
            content_pack_id="pack_checking",
            pack_version=1,
            pack_created_at=recent - timedelta(days=1),
            draft_id="draft_checking",
            draft_content_pack_id="pack_checking",
            draft_updated_at=recent,
        ),
        _topic_row(
            "publishing",
            source_count=2,
            updated_at=recent,
            content_pack_id="pack_publishing",
            pack_version=1,
            pack_created_at=recent - timedelta(days=1),
            draft_id="draft_publishing",
            draft_content_pack_id="pack_publishing",
            draft_updated_at=recent,
        ),
        _topic_row(
            "done",
            status="published",
            source_count=2,
            updated_at=recent,
            draft_id="draft_done",
            draft_updated_at=recent,
        ),
        _topic_row("hold", status="on_hold", updated_at=recent),
    ]
    readiness = {
        "draft_editing": {
            "readiness_state": "no_checks",
            "unresolved_count": 0,
        },
        "draft_checking": {
            "readiness_state": "needs_verification",
            "unresolved_count": 2,
        },
        "draft_publishing": {
            "readiness_state": "ready",
            "unresolved_count": 0,
        },
        "draft_done": {
            "readiness_state": "published",
            "unresolved_count": 0,
        },
    }

    queue = build_content_work_queue(
        rows,
        readiness_by_draft=readiness,
        now=now,
        limit=20,
    )

    assert [row["stage"] for row in queue["rows"]] == [
        "needs_research",
        "request_ready",
        "awaiting_ai_result",
        "draft_editing",
        "fact_check",
        "publish_ready",
    ]
    assert queue["total_count"] == 6
    assert queue["stale_count"] == 1
    request_row = next(row for row in queue["rows"] if row["topic_id"] == "request")
    assert request_row["is_stale"] is True
    assert request_row["target_page"] == "AI 요청서"
    assert request_row["action_state"] == {"prefill_topic_id": "request"}


def test_newer_content_pack_waits_for_result_instead_of_showing_old_draft() -> None:
    now = datetime(2026, 7, 31, 8, 0, 0)
    row = _topic_row(
        "new-pack",
        source_count=3,
        updated_at=now,
        content_pack_id="pack_new",
        pack_version=3,
        pack_created_at=now - timedelta(minutes=10),
        draft_id="draft_old",
        draft_content_pack_id="pack_old",
        draft_updated_at=now - timedelta(days=1),
    )

    queue = build_content_work_queue(
        [row],
        readiness_by_draft={
            "draft_old": {"readiness_state": "ready", "unresolved_count": 0}
        },
        now=now,
    )

    assert queue["rows"][0]["stage"] == "awaiting_ai_result"
    assert queue["rows"][0]["action_state"] == {
        "prefill_content_pack_id": "pack_new"
    }


def test_content_work_queue_database_query_is_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "content-work-queue.duckdb"
    init_database(db_path)
    now = datetime(2026, 7, 31, 8, 0, 0)

    with connect_database(db_path) as con:
        con.execute(
            """
            INSERT INTO topics(
                topic_id, title, normalized_title, summary, category, status,
                priority, is_interested, memo, source_count,
                first_seen_at, last_seen_at, created_at, updated_at, archived_at
            ) VALUES ('topic_queue', '대기열 주제', '대기열 주제', '', '', 'ai_ready',
                      2, TRUE, '', 2, ?, ?, ?, ?, NULL)
            """,
            [now, now, now, now],
        )
        con.execute(
            """
            INSERT INTO content_packs(
                content_pack_id, topic_id, version, audience, purpose, angle,
                category, target_length, title_rules_json, outline_json,
                forbidden_expressions_json, fact_check_items_json,
                references_json, pack_markdown, prompt_text, created_at
            ) VALUES ('pack_queue', 'topic_queue', 1, '독자', '목적', '방향',
                      '', 2500, '[]', '[]', '[]', '[]', '[]', '# 자료팩', '요청서', ?)
            """,
            [now],
        )
        before = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "topics",
                "content_packs",
                "generation_sessions",
                "drafts",
                "fact_check_items",
                "publish_records",
            )
        }
        queue = get_content_work_queue(con, now=now)
        after = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before
        }

    assert before == after
    assert queue["total_count"] == 1
    assert queue["rows"][0]["stage"] == "awaiting_ai_result"
    assert queue["rows"][0]["content_pack_id"] == "pack_queue"


def test_content_work_queue_ui_navigates_with_prefill(monkeypatch) -> None:
    queue = {
        "total_count": 1,
        "stale_count": 0,
        "abandoned_days": 7,
        "truncated_count": 0,
        "stage_counts": {
            "needs_research": 0,
            "request_ready": 0,
            "awaiting_ai_result": 1,
            "draft_editing": 0,
            "fact_check": 0,
            "publish_ready": 0,
        },
        "rows": [
            {
                "topic_id": "topic_ui",
                "topic_title": "UI 대기열 주제",
                "stage": "awaiting_ai_result",
                "stage_label": "AI 결과 대기",
                "reason": "자료팩 결과를 가져와야 합니다.",
                "target_page": "AI 결과 가져오기",
                "action_label": "AI 결과 가져오기",
                "action_state": {"prefill_content_pack_id": "pack_ui"},
                "last_activity_at": datetime(2026, 7, 31, 8, 0, 0),
                "is_stale": False,
            }
        ],
    }
    monkeypatch.setattr(
        "src.content_work_queue_ui.get_content_work_queue",
        lambda _con, limit=8: queue,
    )
    fake_st = _FakeStreamlit(click_first=True)
    navigations: list[tuple[str, dict[str, object]]] = []

    render_content_work_queue(
        object(),
        st_module=fake_st,
        navigate=lambda page, **state: navigations.append((page, state)),
    )

    assert fake_st.subheaders == ["콘텐츠 작업 대기열"]
    assert [args[0] for args, _kwargs in fake_st.metrics] == [
        "할 작업",
        "자료 보완",
        "AI 요청·결과",
        "편집·사실 확인",
        "발행 준비",
        "7일 이상",
    ]
    assert all(kwargs.get("border") is True for _args, kwargs in fake_st.metrics)
    assert navigations == [
        ("AI 결과 가져오기", {"prefill_content_pack_id": "pack_ui"})
    ]


def test_trend_dashboard_installs_content_work_queue_wrapper() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "ui.py").read_text(
        encoding="utf-8"
    )
    assert "def _install_content_work_queue_ui" in source
    assert 'caller_globals["render_trend_dashboard"] = wrapped' in source
    assert "render_content_work_queue" in source
    assert "if not pending_action" in source
