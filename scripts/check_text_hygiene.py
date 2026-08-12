from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRACKED_ROOT_PREFIXES = ("src/", "tests/", "scripts/", "chrome_extension/")
TRACKED_SUFFIXES = {".py", ".js", ".json"}
TRACKED_ROOT_FILES = {"app.py"}


@dataclass(frozen=True)
class TextHygieneIssue:
    path: str
    code: str
    message: str
    line_number: int | None = None

    def display(self) -> str:
        location = self.path
        if self.line_number is not None:
            location = f"{location}:{self.line_number}"
        return f"{location}: {self.code}: {self.message}"


def _relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def check_text_bytes(data: bytes, *, display_path: str) -> list[TextHygieneIssue]:
    issues: list[TextHygieneIssue] = []
    if b"\x00" in data:
        return [
            TextHygieneIssue(
                display_path,
                "binary-content",
                "NUL 바이트가 있어 텍스트 파일로 검사할 수 없습니다.",
            )
        ]

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        return [
            TextHygieneIssue(
                display_path,
                "invalid-utf8",
                f"UTF-8로 읽을 수 없습니다: byte {exc.start}",
            )
        ]

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    content_lines = lines[:-1] if normalized.endswith("\n") else lines

    for line_number, line in enumerate(content_lines, start=1):
        if line.endswith((" ", "\t")):
            issues.append(
                TextHygieneIssue(
                    display_path,
                    "trailing-whitespace",
                    "줄 끝에 공백 또는 탭이 있습니다.",
                    line_number,
                )
            )

    if not normalized.endswith("\n"):
        issues.append(
            TextHygieneIssue(
                display_path,
                "missing-final-newline",
                "파일 끝에 개행이 정확히 1개 있어야 합니다.",
            )
        )
    elif normalized.endswith("\n\n"):
        issues.append(
            TextHygieneIssue(
                display_path,
                "extra-blank-line-at-eof",
                "파일 끝에 불필요한 빈 줄이 있습니다.",
            )
        )

    return issues


def check_text_files(paths: Iterable[Path], *, root: Path = PROJECT_ROOT) -> list[TextHygieneIssue]:
    issues: list[TextHygieneIssue] = []
    for path in paths:
        display_path = _relative_display(path, root)
        if not path.is_file():
            issues.append(
                TextHygieneIssue(
                    display_path,
                    "missing-file",
                    "추적 중인 파일을 찾을 수 없습니다.",
                )
            )
            continue
        issues.extend(check_text_bytes(path.read_bytes(), display_path=display_path))
    return issues


def _is_managed_text_path(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    if normalized in TRACKED_ROOT_FILES:
        return True
    if not normalized.startswith(TRACKED_ROOT_PREFIXES):
        return False
    return Path(normalized).suffix.casefold() in TRACKED_SUFFIXES


def tracked_text_files(root: Path = PROJECT_ROOT) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files 실행 실패: {stderr}")

    relative_paths = [
        item.decode("utf-8", errors="strict")
        for item in completed.stdout.split(b"\x00")
        if item
    ]
    return [
        root / relative_path
        for relative_path in sorted(relative_paths)
        if _is_managed_text_path(relative_path)
    ]


def main() -> int:
    try:
        paths = tracked_text_files(PROJECT_ROOT)
        issues = check_text_files(paths, root=PROJECT_ROOT)
    except (OSError, RuntimeError, UnicodeDecodeError) as exc:
        print(f"텍스트 위생 검사 실행 실패: {exc}", file=sys.stderr)
        return 2

    if issues:
        print("저장소 텍스트 위생 검사에서 문제가 발견됐습니다:", file=sys.stderr)
        for issue in issues:
            print(f"- {issue.display()}", file=sys.stderr)
        return 1

    print(f"저장소 텍스트 위생 검사 통과: {len(paths)}개 파일")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
