from types import SimpleNamespace

from src.services.topic_angle_response_integrity_service import (
    annotate_missing_topic_angle_ids,
)


def test_non_dataclass_execution_is_preserved_for_legacy_callers() -> None:
    execution = SimpleNamespace(preparation=SimpleNamespace(status="ready"))

    annotated = annotate_missing_topic_angle_ids(execution)

    assert annotated is execution
