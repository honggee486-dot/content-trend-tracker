from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

import duckdb
import markdown


BLOGGER_SCOPE = "https://www.googleapis.com/auth/blogger"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLIENT_SECRET_PATH = PROJECT_ROOT / "data" / "blogger_oauth_client.json"
DEFAULT_TOKEN_PATH = PROJECT_ROOT / "data" / "blogger_oauth_token.json"


@dataclass(frozen=True)
class BloggerConnectionStatus:
    dependency_ready: bool
    client_secret_ready: bool
    token_ready: bool
    client_secret_path: str
    token_path: str
    message: str


@dataclass(frozen=True)
class BloggerDraftUploadResult:
    upload_id: str
    blogger_blog_id: str
    blogger_post_id: str
    title: str
    status: str
    content_hash: str
    reused: bool
    updated_at: str


def ensure_blogger_draft_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS blogger_profile_bindings(
            blog_profile_id VARCHAR PRIMARY KEY,
            blogger_blog_id VARCHAR NOT NULL,
            blogger_blog_name VARCHAR NOT NULL DEFAULT '',
            blogger_blog_url VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS blogger_draft_uploads(
            upload_id VARCHAR PRIMARY KEY,
            draft_id VARCHAR NOT NULL,
            blog_profile_id VARCHAR NOT NULL,
            blogger_blog_id VARCHAR NOT NULL,
            blogger_post_id VARCHAR NOT NULL,
            content_hash VARCHAR NOT NULL,
            title_snapshot VARCHAR NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'draft_created',
            blogger_url VARCHAR NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            UNIQUE(draft_id, blog_profile_id, content_hash)
        )
        """
    )


def _load_google_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Blogger API 의존성이 없습니다. requirements.txt를 다시 설치하세요."
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def get_blogger_connection_status(
    *,
    client_secret_path: Path = DEFAULT_CLIENT_SECRET_PATH,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> BloggerConnectionStatus:
    try:
        _load_google_dependencies()
    except RuntimeError:
        dependency_ready = False
    else:
        dependency_ready = True
    client_ready = client_secret_path.is_file()
    token_ready = token_path.is_file()
    if not dependency_ready:
        message = "Blogger API Python 의존성을 설치해야 합니다."
    elif not client_ready:
        message = "Google Cloud의 데스크톱 OAuth 클라이언트 JSON을 지정 경로에 넣으세요."
    elif not token_ready:
        message = "Google 계정 연결이 필요합니다."
    else:
        message = "로컬 OAuth 토큰이 준비되어 있습니다."
    return BloggerConnectionStatus(
        dependency_ready=dependency_ready,
        client_secret_ready=client_ready,
        token_ready=token_ready,
        client_secret_path=str(client_secret_path),
        token_path=str(token_path),
        message=message,
    )


def _write_token_atomic(token_path: Path, token_json: str) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{token_path.name}.",
        suffix=".tmp",
        dir=str(token_path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token_json)
        os.replace(temp_name, token_path)
        try:
            os.chmod(token_path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def authorize_blogger_account(
    *,
    client_secret_path: Path = DEFAULT_CLIENT_SECRET_PATH,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> None:
    if not client_secret_path.is_file():
        raise ValueError(f"OAuth 클라이언트 JSON을 찾을 수 없습니다: {client_secret_path}")
    _Request, _Credentials, InstalledAppFlow, _build = _load_google_dependencies()
    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=[BLOGGER_SCOPE],
    )
    credentials = flow.run_local_server(
        host="127.0.0.1",
        port=0,
        open_browser=True,
        authorization_prompt_message=(
            "브라우저에서 Blogger 권한을 승인한 뒤 이 화면으로 돌아오세요."
        ),
        success_message=(
            "Blogger 계정 연결이 완료되었습니다. 이 창을 닫고 프로그램으로 돌아가세요."
        ),
    )
    _write_token_atomic(token_path, credentials.to_json())


def disconnect_blogger_account(
    *,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> bool:
    if not token_path.exists():
        return False
    token_path.unlink()
    return True


def _load_credentials(
    *,
    token_path: Path = DEFAULT_TOKEN_PATH,
) -> Any:
    Request, Credentials, _InstalledAppFlow, _build = _load_google_dependencies()
    if not token_path.is_file():
        raise ValueError("Blogger 계정 연결 토큰이 없습니다.")
    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_path),
            scopes=[BLOGGER_SCOPE],
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("Blogger OAuth 토큰을 읽을 수 없습니다. 다시 연결하세요.") from exc
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _write_token_atomic(token_path, credentials.to_json())
    if not credentials.valid:
        raise ValueError("Blogger OAuth 토큰이 유효하지 않습니다. 다시 연결하세요.")
    return credentials


def build_blogger_api_client(*, token_path: Path = DEFAULT_TOKEN_PATH) -> Any:
    _Request, _Credentials, _InstalledAppFlow, build = _load_google_dependencies()
    credentials = _load_credentials(token_path=token_path)
    return build(
        "blogger",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


def list_blogger_blogs(*, api_client: Any | None = None) -> list[dict[str, str]]:
    client = api_client or build_blogger_api_client()
    response = (
        client.blogs()
        .listByUser(userId="self", fetchUserInfo=True, view="ADMIN")
        .execute()
    )
    blogs: list[dict[str, str]] = []
    for item in response.get("items") or []:
        blog_id = str(item.get("id") or "").strip()
        if not blog_id:
            continue
        blogs.append(
            {
                "id": blog_id,
                "name": str(item.get("name") or blog_id).strip(),
                "url": str(item.get("url") or "").strip(),
                "status": str(item.get("status") or "").strip(),
            }
        )
    return sorted(blogs, key=lambda item: (item["name"].casefold(), item["id"]))


def save_blogger_profile_binding(
    con: duckdb.DuckDBPyConnection,
    *,
    blog_profile_id: str,
    blogger_blog_id: str,
    blogger_blog_name: str,
    blogger_blog_url: str = "",
) -> None:
    profile_id = str(blog_profile_id or "").strip()
    blog_id = str(blogger_blog_id or "").strip()
    if not profile_id or not blog_id:
        raise ValueError("블로그 프로필과 Blogger 블로그를 선택하세요.")
    ensure_blogger_draft_schema(con)
    now = datetime.now()
    con.execute(
        """
        INSERT INTO blogger_profile_bindings(
            blog_profile_id, blogger_blog_id, blogger_blog_name,
            blogger_blog_url, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(blog_profile_id) DO UPDATE SET
            blogger_blog_id = EXCLUDED.blogger_blog_id,
            blogger_blog_name = EXCLUDED.blogger_blog_name,
            blogger_blog_url = EXCLUDED.blogger_blog_url,
            updated_at = EXCLUDED.updated_at
        """,
        [
            profile_id,
            blog_id,
            str(blogger_blog_name or "").strip(),
            str(blogger_blog_url or "").strip(),
            now,
            now,
        ],
    )


def get_blogger_profile_binding(
    con: duckdb.DuckDBPyConnection,
    *,
    blog_profile_id: str,
) -> dict[str, Any] | None:
    ensure_blogger_draft_schema(con)
    row = con.execute(
        """
        SELECT blog_profile_id, blogger_blog_id, blogger_blog_name,
               blogger_blog_url, created_at, updated_at
        FROM blogger_profile_bindings
        WHERE blog_profile_id = ?
        """,
        [str(blog_profile_id or "").strip()],
    ).fetchone()
    if row is None:
        return None
    columns = [item[0] for item in con.description]
    return dict(zip(columns, row, strict=True))


def build_blogger_post_content(body_text: str) -> str:
    body = str(body_text or "").strip()
    if not body:
        raise ValueError("Blogger 초안으로 전송할 본문이 없습니다.")
    return markdown.markdown(
        body,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )


def _normalize_labels(values: Any) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = str(value or "").strip().lstrip("#")
        if not label or label.casefold() in seen:
            continue
        seen.add(label.casefold())
        labels.append(label[:200])
    return labels[:20]


def _content_hash(*, blog_id: str, title: str, content: str, labels: list[str]) -> str:
    canonical = json.dumps(
        {
            "blog_id": blog_id,
            "title": title,
            "content": content,
            "labels": labels,
            "is_draft": True,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _existing_upload(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    blog_profile_id: str,
    content_hash: str,
) -> BloggerDraftUploadResult | None:
    row = con.execute(
        """
        SELECT upload_id, blogger_blog_id, blogger_post_id, title_snapshot,
               status, content_hash, updated_at
        FROM blogger_draft_uploads
        WHERE draft_id = ? AND blog_profile_id = ? AND content_hash = ?
          AND status = 'draft_created'
        LIMIT 1
        """,
        [draft_id, blog_profile_id, content_hash],
    ).fetchone()
    if row is None:
        return None
    return BloggerDraftUploadResult(
        upload_id=str(row[0]),
        blogger_blog_id=str(row[1]),
        blogger_post_id=str(row[2]),
        title=str(row[3]),
        status=str(row[4]),
        content_hash=str(row[5]),
        reused=True,
        updated_at=str(row[6]),
    )


def upload_blogger_draft(
    con: duckdb.DuckDBPyConnection,
    *,
    draft: Mapping[str, Any],
    profile: Mapping[str, Any],
    package: Any,
    blogger_blog_id: str,
    api_client: Any | None = None,
) -> BloggerDraftUploadResult:
    if str(profile.get("platform") or "") != "blogger":
        raise ValueError("Blogger 프로필에서만 공식 API 초안을 만들 수 있습니다.")
    draft_id = str(draft.get("draft_id") or "").strip()
    profile_id = str(profile.get("blog_profile_id") or "").strip()
    blog_id = str(blogger_blog_id or "").strip()
    title = str(getattr(package, "seo_title", "") or "").strip()
    if not draft_id or not profile_id or not blog_id:
        raise ValueError("초안·블로그 프로필·Blogger 블로그 정보가 필요합니다.")
    if not title:
        raise ValueError("Blogger 초안 제목이 없습니다.")
    content = build_blogger_post_content(str(getattr(package, "output_body", "") or ""))
    labels = _normalize_labels(getattr(package, "output_tags", ()))
    digest = _content_hash(blog_id=blog_id, title=title, content=content, labels=labels)

    ensure_blogger_draft_schema(con)
    existing = _existing_upload(
        con,
        draft_id=draft_id,
        blog_profile_id=profile_id,
        content_hash=digest,
    )
    if existing is not None:
        return existing

    client = api_client or build_blogger_api_client()
    response = (
        client.posts()
        .insert(
            blogId=blog_id,
            isDraft=True,
            body={
                "kind": "blogger#post",
                "title": title,
                "content": content,
                "labels": labels,
            },
        )
        .execute()
    )
    post_id = str(response.get("id") or "").strip()
    if not post_id:
        raise RuntimeError("Blogger API가 생성된 초안 ID를 반환하지 않았습니다.")
    status = str(response.get("status") or "DRAFT").strip().upper()
    if status not in {"DRAFT", "LIVE"}:
        status = "DRAFT"
    if status == "LIVE":
        raise RuntimeError("Blogger API가 초안이 아닌 공개 상태를 반환해 기록하지 않았습니다.")

    upload_id = f"blogger_upload_{uuid4().hex}"
    now = datetime.now()
    con.execute(
        """
        INSERT INTO blogger_draft_uploads(
            upload_id, draft_id, blog_profile_id, blogger_blog_id,
            blogger_post_id, content_hash, title_snapshot, status,
            blogger_url, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft_created', ?, ?, ?)
        """,
        [
            upload_id,
            draft_id,
            profile_id,
            blog_id,
            post_id,
            digest,
            title,
            str(response.get("url") or "").strip(),
            now,
            now,
        ],
    )
    return BloggerDraftUploadResult(
        upload_id=upload_id,
        blogger_blog_id=blog_id,
        blogger_post_id=post_id,
        title=title,
        status="draft_created",
        content_hash=digest,
        reused=False,
        updated_at=str(now),
    )


def list_blogger_draft_uploads(
    con: duckdb.DuckDBPyConnection,
    *,
    draft_id: str,
    blog_profile_id: str,
) -> list[dict[str, Any]]:
    ensure_blogger_draft_schema(con)
    rows = con.execute(
        """
        SELECT upload_id, blogger_blog_id, blogger_post_id, title_snapshot,
               status, blogger_url, created_at, updated_at
        FROM blogger_draft_uploads
        WHERE draft_id = ? AND blog_profile_id = ?
        ORDER BY updated_at DESC
        """,
        [str(draft_id or "").strip(), str(blog_profile_id or "").strip()],
    ).fetchall()
    columns = [item[0] for item in con.description]
    return [dict(zip(columns, row, strict=True)) for row in rows]
