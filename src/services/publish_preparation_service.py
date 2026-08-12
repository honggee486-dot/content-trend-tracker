from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import duckdb

from src.services.blog_output_service import render_body_for_output


@dataclass(frozen=True)
class PublishOutputPolicy:
    policy_code: str
    label: str
    seo_title_max_length: int
    meta_description_max_length: int
    recommended_tag_count: int


@dataclass(frozen=True)
class PublishCopyPackage:
    seo_title: str
    meta_description: str
    focus_keywords: tuple[str, ...]
    image_slots: tuple[dict[str, Any], ...]
    output_body: str
    output_tags: tuple[str, ...]
    image_guide_text: str
    full_output_text: str
    policy: PublishOutputPolicy
    warnings: tuple[str, ...]


_POLICY_BY_STRATEGY: dict[str, PublishOutputPolicy] = {
    "tistory_life": PublishOutputPolicy(
        "tistory_life", "티스토리 생활·정책", 45, 150, 8
    ),
    "tistory_tech": PublishOutputPolicy(
        "tistory_tech", "티스토리 IT·기술", 45, 150, 8
    ),
    "naver_trend": PublishOutputPolicy(
        "naver_trend", "네이버 생활밀착", 40, 120, 10
    ),
    "blogger_global": PublishOutputPolicy(
        "blogger_global", "Blogger 글로벌", 60, 155, 8
    ),
}

_POLICY_BY_PLATFORM: dict[str, PublishOutputPolicy] = {
    "tistory": PublishOutputPolicy("tistory", "티스토리", 45, 150, 8),
    "naver_blog": PublishOutputPolicy("naver_blog", "네이버 블로그", 40, 120, 10),
    "blogger": PublishOutputPolicy("blogger", "Blogger", 60, 155, 8),
}

_DEFAULT_POLICY = PublishOutputPolicy("default", "사용자 지정", 60, 155, 10)

_DEFAULT_IMAGE_LAYOUT: tuple[tuple[str, str, str], ...] = (
    ("대표 이미지", "도입부 다음", "글의 핵심 대상이 한눈에 보이는 대표 이미지"),
    ("핵심 설명 이미지", "핵심 내용 중간", "절차·비교·구조를 설명하는 보조 이미지"),
    ("요약 이미지", "마무리 전", "핵심 내용을 다시 확인하는 요약 이미지"),
)


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return name in {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _dump(values: Any) -> str:
    return json.dumps(values, ensure_ascii=False)


def _load_list(value: Any) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _dedupe(values: Iterable[Any], *, limit: int | None = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip().lstrip("#")
        folded = clean.casefold()
        if not clean or folded in seen:
            continue
        seen.add(folded)
        result.append(clean)
        if limit is not None and len(result) >= limit:
            break
    return result


def _plain_excerpt(value: Any, *, max_length: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_length:
        return text
    candidate = text[: max_length + 1]
    cut = candidate.rfind(" ")
    if cut < max_length // 2:
        cut = max_length
    return candidate[:cut].rstrip(" ,.;:-") + "…"


def get_publish_output_policy(
    *,
    platform: str,
    strategy_code: str = "",
) -> PublishOutputPolicy:
    clean_strategy = str(strategy_code or "").strip()
    if clean_strategy in _POLICY_BY_STRATEGY:
        return _POLICY_BY_STRATEGY[clean_strategy]
    return _POLICY_BY_PLATFORM.get(str(platform or "").strip(), _DEFAULT_POLICY)


def ensure_publish_preparation_schema(con: duckdb.DuckDBPyConnection) -> None:
    """초안과 발행 기록을 바꾸지 않고 발행 준비 정보만 별도 저장합니다."""
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_publish_preparations (
            draft_id VARCHAR NOT NULL,
            blog_profile_id VARCHAR NOT NULL,
            seo_title VARCHAR NOT NULL DEFAULT '',
            meta_description VARCHAR NOT NULL DEFAULT '',
            focus_keywords_json VARCHAR NOT NULL DEFAULT '[]',
            image_slots_json VARCHAR NOT NULL DEFAULT '[]',
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            PRIMARY KEY (draft_id, blog_profile_id)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_draft_publish_preparations_profile
        ON draft_publish_preparations(blog_profile_id)
        """
    )


def build_default_focus_keywords(
    draft: Mapping[str, Any],
    strategy: Mapping[str, Any] | None,
    *,
    limit: int,
) -> list[str]:
    tags = draft.get("tags") or []
    tag_values = [tags] if isinstance(tags, str) else list(tags)
    category = str(draft.get("category") or "").strip()
    title = str(draft.get("title") or "")
    summary = str(draft.get("summary") or "")
    body = str(draft.get("body_markdown") or "")[:2000]
    haystack = " ".join((title, category, summary, body)).casefold()
    routing_terms = list((strategy or {}).get("routing_terms") or [])
    matched_terms = [term for term in routing_terms if str(term).casefold() in haystack]
    return _dedupe([*tag_values, category, *matched_terms], limit=limit)


def build_default_image_slots(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    title = str(draft.get("title") or "글 주제").strip() or "글 주제"
    slots: list[dict[str, Any]] = []
    for index, (role, position, note) in enumerate(_DEFAULT_IMAGE_LAYOUT, start=1):
        alt_suffix = "" if index == 1 else " 핵심 설명" if index == 2 else " 요약"
        slots.append(
            {
                "slot_number": index,
                "role": role,
                "position": position,
                "alt_text": f"{title}{alt_suffix}",
                "note": note,
            }
        )
    return slots


def normalize_image_slots(
    slots: Sequence[Mapping[str, Any]] | None,
    *,
    draft: Mapping[str, Any],
) -> list[dict[str, Any]]:
    defaults = build_default_image_slots(draft)
    supplied = list(slots or [])
    normalized: list[dict[str, Any]] = []
    for index, default in enumerate(defaults):
        current = supplied[index] if index < len(supplied) else {}
        normalized.append(
            {
                "slot_number": index + 1,
                "role": str(current.get("role") or default["role"]).strip(),
                "position": str(current.get("position") or default["position"]).strip(),
                "alt_text": str(current.get("alt_text") or default["alt_text"]).strip(),
                "note": str(current.get("note") or default["note"]).strip(),
            }
        )
    return normalized


def build_default_publish_preparation(
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    strategy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    policy = get_publish_output_policy(
        platform=str(profile.get("platform") or ""),
        strategy_code=str((strategy or {}).get("strategy_code") or ""),
    )
    title = str(draft.get("title") or "").strip()
    summary_source = str(draft.get("summary") or "").strip()
    if not summary_source:
        summary_source = re.sub(
            r"[#*_>`~\[\]()]",
            " ",
            str(draft.get("body_markdown") or "")[:1000],
        )
    return {
        "draft_id": str(draft.get("draft_id") or "").strip(),
        "blog_profile_id": str(profile.get("blog_profile_id") or "").strip(),
        "seo_title": title,
        "meta_description": _plain_excerpt(
            summary_source,
            max_length=policy.meta_description_max_length,
        ),
        "focus_keywords": build_default_focus_keywords(
            draft,
            strategy,
            limit=policy.recommended_tag_count,
        ),
        "image_slots": build_default_image_slots(draft),
        "policy": policy,
    }


def save_publish_preparation(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    seo_title: str,
    meta_description: str,
    focus_keywords: Iterable[Any],
    image_slots: Sequence[Mapping[str, Any]] | None,
) -> None:
    ensure_publish_preparation_schema(con)
    draft_id = str(draft.get("draft_id") or "").strip()
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    if not draft_id:
        raise ValueError("발행 준비를 저장할 초안 ID가 없습니다.")
    if not profile_id:
        raise ValueError("발행 준비를 저장할 블로그 프로필 ID가 없습니다.")
    if _table_exists(con, "drafts") and not con.execute(
        "SELECT 1 FROM drafts WHERE draft_id = ?", [draft_id]
    ).fetchone():
        raise ValueError("발행 준비를 저장할 초안을 찾을 수 없습니다.")
    if _table_exists(con, "blog_profiles") and not con.execute(
        "SELECT 1 FROM blog_profiles WHERE blog_profile_id = ? AND is_active = TRUE",
        [profile_id],
    ).fetchone():
        raise ValueError("발행 준비를 저장할 활성 블로그 프로필을 찾을 수 없습니다.")

    clean_title = str(seo_title or "").strip()
    if not clean_title:
        raise ValueError("SEO 제목을 입력하세요.")
    clean_description = re.sub(r"\s+", " ", str(meta_description or "")).strip()
    keywords = _dedupe(focus_keywords, limit=20)
    normalized_slots = normalize_image_slots(image_slots, draft=draft)
    now = datetime.now()
    con.execute(
        """
        INSERT INTO draft_publish_preparations(
            draft_id, blog_profile_id, seo_title, meta_description,
            focus_keywords_json, image_slots_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(draft_id, blog_profile_id) DO UPDATE SET
            seo_title = EXCLUDED.seo_title,
            meta_description = EXCLUDED.meta_description,
            focus_keywords_json = EXCLUDED.focus_keywords_json,
            image_slots_json = EXCLUDED.image_slots_json,
            updated_at = EXCLUDED.updated_at
        """,
        [
            draft_id,
            profile_id,
            clean_title,
            clean_description,
            _dump(keywords),
            _dump(normalized_slots),
            now,
            now,
        ],
    )


def get_publish_preparation(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    blog_profile_id: str,
) -> dict[str, Any] | None:
    ensure_publish_preparation_schema(con)
    cursor = con.execute(
        """
        SELECT draft_id, blog_profile_id, seo_title, meta_description,
               focus_keywords_json, image_slots_json, created_at, updated_at
        FROM draft_publish_preparations
        WHERE draft_id = ? AND blog_profile_id = ?
        """,
        [str(draft_id or "").strip(), str(blog_profile_id or "").strip()],
    )
    row = cursor.fetchone()
    if row is None:
        return None
    result = dict(zip([str(item[0]) for item in cursor.description], row, strict=True))
    result["focus_keywords"] = _dedupe(_load_list(result.pop("focus_keywords_json", "[]")))
    raw_slots = [item for item in _load_list(result.pop("image_slots_json", "[]")) if isinstance(item, dict)]
    result["image_slots"] = raw_slots
    return result


def _image_guide_text(slots: Sequence[Mapping[str, Any]]) -> str:
    lines: list[str] = []
    for slot in slots:
        number = int(slot.get("slot_number") or len(lines) + 1)
        role = str(slot.get("role") or f"이미지 {number}").strip()
        position = str(slot.get("position") or "본문 중간").strip()
        alt_text = str(slot.get("alt_text") or "").strip()
        note = str(slot.get("note") or "").strip()
        lines.append(f"[이미지 {number} · {role}]")
        lines.append(f"위치: {position}")
        lines.append(f"대체텍스트: {alt_text or '-'}")
        lines.append(f"메모: {note or '-'}")
        if number != len(slots):
            lines.append("")
    return "\n".join(lines).strip()


def build_publish_copy_package(
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    strategy: Mapping[str, Any] | None,
    preparation: Mapping[str, Any] | None = None,
) -> PublishCopyPackage:
    defaults = build_default_publish_preparation(draft, profile, strategy)
    source = dict(defaults)
    if preparation:
        source.update({key: value for key, value in preparation.items() if value is not None})

    policy = defaults["policy"]
    seo_title = str(source.get("seo_title") or defaults["seo_title"]).strip()
    meta_description = re.sub(
        r"\s+", " ", str(source.get("meta_description") or "")
    ).strip()
    focus_keywords = _dedupe(
        source.get("focus_keywords") or defaults["focus_keywords"],
        limit=20,
    )
    image_slots = normalize_image_slots(
        source.get("image_slots"),
        draft=draft,
    )

    draft_tags = draft.get("tags") or []
    profile_tags = profile.get("default_tags") or []
    output_tags = _dedupe(
        [*list(draft_tags), *list(profile_tags), *focus_keywords],
        limit=policy.recommended_tag_count,
    )
    output_format = str(profile.get("output_format") or "plain_text")
    output_body = render_body_for_output(
        title=str(draft.get("title") or ""),
        body_markdown=str(draft.get("body_markdown") or ""),
        output_format=output_format,
    )
    image_guide = _image_guide_text(image_slots)
    tag_text = " ".join(f"#{tag}" for tag in output_tags)
    keyword_text = ", ".join(focus_keywords)
    sections = [
        "[SEO 제목]",
        seo_title,
        "",
        "[메타 설명]",
        meta_description or "-",
        "",
        "[핵심 키워드]",
        keyword_text or "-",
        "",
        "[이미지 3개 배치 안내]",
        image_guide,
        "",
        "[본문]",
        output_body,
    ]
    if tag_text:
        sections.extend(["", "[태그]", tag_text])

    warnings: list[str] = []
    if len(seo_title) > policy.seo_title_max_length:
        warnings.append(
            f"SEO 제목이 프로그램 권장 {policy.seo_title_max_length}자를 {len(seo_title) - policy.seo_title_max_length}자 초과했습니다."
        )
    if len(meta_description) > policy.meta_description_max_length:
        warnings.append(
            f"메타 설명이 프로그램 권장 {policy.meta_description_max_length}자를 {len(meta_description) - policy.meta_description_max_length}자 초과했습니다."
        )
    if len(output_tags) > policy.recommended_tag_count:
        warnings.append(
            f"태그가 프로그램 권장 {policy.recommended_tag_count}개를 초과했습니다."
        )

    return PublishCopyPackage(
        seo_title=seo_title,
        meta_description=meta_description,
        focus_keywords=tuple(focus_keywords),
        image_slots=tuple(image_slots),
        output_body=output_body,
        output_tags=tuple(output_tags),
        image_guide_text=image_guide,
        full_output_text="\n".join(sections).strip(),
        policy=policy,
        warnings=tuple(warnings),
    )
