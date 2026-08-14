from __future__ import annotations


def test_known_gemini_generation_features_share_the_common_gate() -> None:
    import src.services.gemini_service as gemini_service
    import src.services.topic_angle_ai_service as topic_angle
    import src.services.trend_blog_ai_routing_service as blog_routing
    import src.services.trend_candidate_ai_evaluation_service as candidate_evaluation
    import src.services.trend_cluster_sparse_executor as clustering

    common = gemini_service.call_gemini_structured_output
    assert getattr(common, "_gemini_common_rate_limited", False)

    for module in (
        clustering,
        candidate_evaluation,
        blog_routing,
        topic_angle,
    ):
        assert module.call_gemini_structured_output is common


def test_manual_draft_gateway_resolves_the_same_common_gate() -> None:
    import src.services.gemini_service as gemini_service

    assert (
        gemini_service._call_interactions_api.__globals__["call_gemini_structured_output"]
        is gemini_service.call_gemini_structured_output
    )
