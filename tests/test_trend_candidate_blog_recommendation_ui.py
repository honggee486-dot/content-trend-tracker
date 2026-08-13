from __future__ import annotations

import pandas as pd

import src.trend_candidate_blog_recommendation_ui as recommendation_ui


def test_recommendation_label_token_round_trip_supports_korean() -> None:
    label = "B:요즘화제"
    token = recommendation_ui.encode_recommendation_label(label)

    assert token
    assert "=" not in token
    assert recommendation_ui.decode_recommendation_label(token) == label


def test_adsense_assessment_token_round_trip_supports_korean() -> None:
    assessment = {
        "label": "A:적합",
        "reason": "초기 심사 우선 후보이며 승인 자체를 보장하지 않습니다.",
    }
    token = recommendation_ui.encode_adsense_assessment(assessment)

    assert token
    assert "=" not in token
    assert recommendation_ui.decode_adsense_assessment(token) == assessment


def test_candidate_status_markdown_becomes_three_line_status_blog_and_adsense_hint() -> None:
    blog_token = recommendation_ui.encode_recommendation_label("B:요즘화제")
    adsense_token = recommendation_ui.encode_adsense_assessment(
        {"label": "A:적합", "reason": "정보성 설명형 우선 후보"}
    )
    original = (
        '<div class="candidate-tbl-cell cell-center status-tag '
        f'status-추천 ai-ready blog-rec-{blog_token} adsense-hint-{adsense_token}">'
        f'추천 ai-ready blog-rec-{blog_token} adsense-hint-{adsense_token}</div>'
    )

    rendered = recommendation_ui.rewrite_candidate_markdown(original)

    assert 'class="trend-blog-judgement">추천</span>' in rendered
    assert 'class="trend-blog-label" title="B:요즘화제">B:요즘화제</span>' in rendered
    assert 'class="trend-adsense-label adsense-fit"' in rendered
    assert '>A:적합</span>' in rendered
    assert 'title="정보성 설명형 우선 후보"' in rendered
    assert "blog-rec-" not in rendered
    assert "adsense-hint-" not in rendered
    assert "status-추천" not in rendered
    assert "ai-ready" in rendered


def test_candidate_header_explains_recommendation_and_adsense_hint() -> None:
    original = '<div class="candidate-tbl-hdr cell-center">판정</div>'
    rendered = recommendation_ui.rewrite_candidate_markdown(original)

    assert "추천·AdSense" in rendered
    assert "A:적합=초기 심사 우선 후보" in rendered
    assert "승인 보장은 아님" in rendered


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
    monkeypatch.setattr(
        recommendation_ui,
        "build_adsense_candidate_assessments",
        lambda _con, _rows: {
            "cluster-1": {"label": "A:검토", "reason": "시점 의존 후보"}
        },
    )

    decorated = recommendation_ui.decorate_rankings_with_blog_recommendations(
        object(), rankings
    )

    assert decorated is not rankings
    assert decorated.loc[0, "주제"] == "프로야구 순위"
    assert decorated.loc[0, "트렌드점수"] == 91.2
    assert "blog-rec-" in decorated.loc[0, "판정"]
    assert "adsense-hint-" in decorated.loc[0, "판정"]

    blog_token = decorated.loc[0, "판정"].split("blog-rec-", 1)[1].split(" ", 1)[0]
    assert recommendation_ui.decode_recommendation_label(blog_token) == "B:요즘화제"

    adsense_token = decorated.loc[0, "판정"].split("adsense-hint-", 1)[1]
    assert recommendation_ui.decode_adsense_assessment(adsense_token) == {
        "label": "A:검토",
        "reason": "시점 의존 후보",
    }
