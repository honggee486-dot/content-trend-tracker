from __future__ import annotations

from pathlib import Path

import src.publish_preparation_ui as publish_ui


def test_publish_preparation_connects_chrome_compatibility_report_review() -> None:
    source = Path(publish_ui.__file__).read_text(encoding="utf-8")

    assert (
        "from src.chrome_compatibility_report_ui import (\n"
        "    render_chrome_compatibility_report_review,\n"
        ")"
    ) in source
    assert "render_chrome_compatibility_report_review(" in source
    assert "expected_platform=str(profile.get(\"platform\") or \"\")" in source
    assert "key_scope=scope" in source
    assert source.index("Chrome 편집기 입력 보조") < source.index(
        "render_chrome_compatibility_report_review("
    )
    assert source.index("render_chrome_compatibility_report_review(") < source.index(
        "return package"
    )
