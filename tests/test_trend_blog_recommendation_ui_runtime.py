from __future__ import annotations

import src.services.trend_blog_recommendation_ui_runtime as runtime
import src.trend_candidate_blog_recommendation_ui as recommendation_ui


def test_split_candidate_status_markdown_creates_requested_three_columns() -> None:
    blog_token = recommendation_ui.encode_recommendation_label("B:요즘화제")
    adsense_token = recommendation_ui.encode_adsense_assessment(
        {"label": "A:검토", "reason": "시점 의존 후보"}
    )
    row = (
        '<div class="candidate-tbl-cell cell-center status-tag '
        f'status-검토 ai-ready blog-rec-{blog_token} adsense-hint-{adsense_token}">'
        f'검토 ai-ready blog-rec-{blog_token} adsense-hint-{adsense_token}</div>'
    )

    headers = runtime.split_candidate_status_markdown(
        '<div class="candidate-tbl-hdr cell-center">판정</div>'
    )
    cells = runtime.split_candidate_status_markdown(row)

    assert headers is not None
    assert ">추천</div>" in str(headers[0])
    assert ">블로그</div>" in str(headers[1])
    assert ">애드센스</div>" in str(headers[2])
    assert "○=초기 심사 우선 후보" in str(headers[2])
    assert "△=최신성·독창성 보강 후 사용" in str(headers[2])
    assert "×=초기 심사 전 보수적 회피" in str(headers[2])

    assert cells is not None
    assert "trend-recommendation-column" in str(cells[0])
    assert "status-검토" in str(cells[0])
    assert ">검토</div>" in str(cells[0])
    assert "trend-blog-column" in str(cells[1])
    assert ">B:요즘화제</div>" in str(cells[1])
    assert "trend-adsense-column adsense-review" in str(cells[2])
    assert ">△</div>" in str(cells[2])
    assert 'title="A:검토 · 시점 의존 후보"' in str(cells[2])


def test_adsense_display_symbol_maps_internal_labels() -> None:
    assert runtime.adsense_display_symbol("A:적합") == "○"
    assert runtime.adsense_display_symbol("A:검토") == "△"
    assert runtime.adsense_display_symbol("A:피함") == "×"
    assert runtime.adsense_display_symbol("") == "-"


def test_rewrite_role_header_matches_requested_labels() -> None:
    assert ">기획</div>" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">기회</div>', "opportunity"
    )
    assert "글감 기회 점수" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">기회</div>', "opportunity"
    )
    assert ">DAUM</div>" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">Daum</div>', "daum"
    )
    assert ">YOU<br>TUBE</div>" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">YouTube</div>', "youtube"
    )
    assert ">GOOGLE<br>TRENDS</div>" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">Google Trends</div>', "google"
    )
    assert "trend-source-header" in runtime.rewrite_role_header(
        '<div class="candidate-tbl-hdr cell-right">위키백과</div>', "wikipedia"
    )


def test_candidate_css_keeps_requested_readable_font_sizes_without_drawer() -> None:
    css = runtime._CANDIDATE_LAYOUT_CSS

    assert "font-size: 0.76rem" in css
    assert "font-size: 0.81rem" in css
    assert "font-size: 0.84rem" in css
    assert "font-size: 0.94rem" in css
    assert "trend_candidate_detail_drawer" not in css
    assert "position: fixed" not in css
    assert "translateX" not in css


def test_candidate_css_fits_equal_source_columns_inside_list_panel() -> None:
    css = runtime._CANDIDATE_LAYOUT_CSS

    assert (
        "36px 48px 108px 50px 48px minmax(270px, 1fr) "
        "48px 42px 42px 42px 42px 42px"
        in css
    )
    assert "min-width: 818px" in css
    assert "width: 100%" in css
    assert "max-width: 100%" in css
    assert "width: calc(100% + 0.5rem)" not in css
    assert "margin-right: -0.5rem" not in css


class _FakeBlock:
    def __init__(self, *, button_result: bool = False) -> None:
        self.markdowns: list[object] = []
        self.button_result = button_result

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def markdown(self, body, *args, **kwargs):
        self.markdowns.append(body)
        return body

    def button(self, *args, **kwargs):
        return self.button_result


class _FakeStreamlit:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.column_calls: list[object] = []
        self.column_batches: list[list[_FakeBlock]] = []
        self.container_calls: list[dict[str, object]] = []
        self.markdowns: list[str] = []
        self.rerun_count = 0

    def columns(self, spec, *args, **kwargs):
        self.column_calls.append(spec)
        count = int(spec) if isinstance(spec, int) else len(spec)
        batch = [_FakeBlock() for _ in range(count)]
        self.column_batches.append(batch)
        return batch

    def container(self, *args, **kwargs):
        self.container_calls.append(dict(kwargs))
        return _FakeBlock()

    def markdown(self, body, *args, **kwargs):
        self.markdowns.append(str(body))
        return body

    def rerun(self):
        self.rerun_count += 1
        raise RuntimeError("rerun")


class _ExistingProxy:
    def __init__(self, target) -> None:
        self._target = target


def test_patched_columns_expands_candidate_table_to_twelve_visual_columns() -> None:
    fake = _FakeStreamlit()
    proxy = _ExistingProxy(fake)

    logical = runtime._patched_columns(
        proxy,
        10,
        gap=None,
        vertical_alignment="center",
    )

    assert fake.column_calls == [12]
    assert len(fake.column_batches[0]) == 12
    assert len(logical) == 10

    logical[1].markdown(
        '<div class="candidate-tbl-hdr cell-center">판정</div>',
        unsafe_allow_html=True,
    )
    actual = fake.column_batches[0]
    assert ">추천</div>" in str(actual[1].markdowns[-1])
    assert ">블로그</div>" in str(actual[2].markdowns[-1])
    assert ">애드센스</div>" in str(actual[3].markdowns[-1])

    logical[4].markdown(
        '<div class="candidate-tbl-hdr cell-right">기회</div>',
        unsafe_allow_html=True,
    )
    logical[6].markdown(
        '<div class="candidate-tbl-hdr cell-right">Daum</div>',
        unsafe_allow_html=True,
    )
    logical[7].markdown(
        '<div class="candidate-tbl-hdr cell-right">YouTube</div>',
        unsafe_allow_html=True,
    )
    logical[8].markdown(
        '<div class="candidate-tbl-hdr cell-right">Google Trends</div>',
        unsafe_allow_html=True,
    )
    logical[9].markdown(
        '<div class="candidate-tbl-hdr cell-right">위키백과</div>',
        unsafe_allow_html=True,
    )
    assert ">기획</div>" in str(actual[6].markdowns[-1])
    assert ">DAUM</div>" in str(actual[8].markdowns[-1])
    assert ">YOU<br>TUBE</div>" in str(actual[9].markdowns[-1])
    assert ">GOOGLE<br>TRENDS</div>" in str(actual[10].markdowns[-1])
    assert "trend-source-header" in str(actual[11].markdowns[-1])


def test_master_detail_columns_give_candidate_list_more_width() -> None:
    fake = _FakeStreamlit()
    proxy = _ExistingProxy(fake)

    layout = runtime._patched_columns(proxy, [1.55, 1.75], gap="medium")

    assert len(layout) == 2
    assert fake.column_calls == [[1.90, 1.40]]
    assert len(fake.column_batches[0]) == 2
    assert fake.markdowns == []


def test_candidate_list_container_is_tall_enough_for_thirteen_rows() -> None:
    fake = _FakeStreamlit()
    proxy = _ExistingProxy(fake)

    block = runtime._patched_container(
        proxy,
        key="trend_candidate_list",
        height=620,
        border=True,
        gap=None,
    )

    assert isinstance(block, _FakeBlock)
    assert fake.container_calls == [
        {
            "key": "trend_candidate_list",
            "height": 760,
            "border": True,
            "gap": None,
        }
    ]


def test_other_containers_keep_their_original_height() -> None:
    fake = _FakeStreamlit()
    proxy = _ExistingProxy(fake)

    runtime._patched_container(proxy, key="other", height=620, border=True)

    assert fake.container_calls == [
        {"key": "other", "height": 620, "border": True}
    ]


def test_clicking_title_does_not_create_drawer_state_or_extra_rerun() -> None:
    fake = _FakeStreamlit()
    proxy = _ExistingProxy(fake)
    logical = runtime._patched_columns(proxy, 10, gap=None)
    fake.column_batches[0][5].button_result = True

    clicked = logical[3].button("후보 제목", key="candidate")

    assert clicked is True
    assert fake.session_state == {}
    assert fake.rerun_count == 0


def test_installer_patches_existing_candidate_proxy_once_without_rendering_early() -> None:
    class Proxy:
        pass

    original_proxy = recommendation_ui._CandidateStreamlitProxy
    original_css = recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS
    fake = _FakeStreamlit()
    try:
        recommendation_ui._CandidateStreamlitProxy = Proxy
        recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS = "base"

        runtime.install_trend_blog_recommendation_ui_runtime(st_module=fake)
        runtime.install_trend_blog_recommendation_ui_runtime(st_module=fake)

        assert Proxy.columns is runtime._patched_columns
        assert Proxy.container is runtime._patched_container
        assert getattr(Proxy, "_trend_candidate_table_runtime") is True
        assert recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS.count(
            "minmax(270px, 1fr)"
        ) == 1
        assert "trend_candidate_detail_drawer" not in (
            recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS
        )
        assert fake.markdowns == []
    finally:
        recommendation_ui._CandidateStreamlitProxy = original_proxy
        recommendation_ui._CANDIDATE_BLOG_RECOMMENDATION_CSS = original_css
