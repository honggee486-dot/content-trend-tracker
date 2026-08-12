from __future__ import annotations

import pandas as pd

import src.trend_candidate_blog_recommendation_ui as recommendation_ui


def test_recommendation_label_token_round_trip_supports_korean() -> None:
    label = "B:요즘화제"
    token = recommendation_ui.encode_recommendation_label(label)

    assert token
    assert "=" not in token
    assert recommendation_ui.decode_recommendation_label(token) == label


def test_candidate_status_markdown_becomes_two_line_status_and_blog_label() -> None:
    token = recommendation_ui.encode_recommendation_label("B:요즘화제")
    original = (
        '<div class="candidate-tbl-cell cell-center status-tag '
        f'status-추천 ai-ready blog-rec-{token}">추천 ai-ready blog-rec-{token}</div>'
    )

    rendered = recommendation_ui.rewrite_candidate_markdown(original)

    assert 'class="trend-blog-judgement">추천</span>' in rendered
    assert 'class="trend-blog-label" title="B:요즘화제">B:요즘화제</span>' in rendered
    assert "blog-rec-" not in rendered
    assert "status-추천" not in rendered
    assert "ai-ready" in rendered


def test_candidate_header_uses_recommend_review_label() -> None:
    original = '<div class="candidate-tbl-hdr cell-center">판정</div>'
    assert recommendation_ui.rewrite_candidate_markdown(original) == (
        '<div class="candidate-tbl-hdr cell-center">추천·검토</div>'
    )


def test_unrelated_markdown_is_not_changed() -> None:
    value = "<div>다른 화면</div>"
    assert recommendation_ui.rewrite_candidate_markdown(value) == value


def test_rankings_are_decorated_without_changing_other_columns(monkeypatch) -> None:
    rankings = pd.DataFrame(
        [
            {
                "cluster_id": "cluster-1",
                "판정": "추천 ai-ready",
                "주제": "프로야구 순위",
                "트렌드점수": 91.2,
            }
        ]
    )
    monkeypatch.setattr(
        recommendation_ui,
        "build_trend_blog_recommendation_labels",
        lambda _con, _rows: {"cluster-1": "B:요즘화제"},
    )

    decorated = recommendation_ui.decorate_rankings_with_blog_recommendations(
        object(), rankings
    )

    assert decorated is not rankings
    assert decorated.loc[0, "주제"] == "프로야구 순위"
    assert decorated.loc[0, "트렌드점수"] == 91.2
    assert "blog-rec-" in decorated.loc[0, "판정"]
    token = decorated.loc[0, "판정"].split("blog-rec-", 1)[1]
    assert recommendation_ui.decode_recommendation_label(token) == "B:요즘화제"
