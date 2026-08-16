"""Bounded, read-only GitHub App client for recent pull requests."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote

import httpx

from app.github_client import get_installation_credentials

GITHUB_API = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
MAX_GITHUB_RESPONSE_BYTES = 3_000_000
MAX_REPOSITORIES = 50
MAX_FILES = 100
MAX_PATCH_CHARS = 60_000


class GitHubReadError(RuntimeError):
    code = "github_pull_request_read_failed"


def _repository_path(full_name: str) -> str:
    owner, name = full_name.split("/", 1)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


class GitHubReadClient:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=httpx.Timeout(30, connect=5))
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        try:
            with self.client.stream(
                "GET", f"{GITHUB_API}{path}", headers=self.headers, params=params
            ) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_GITHUB_RESPONSE_BYTES:
                        raise GitHubReadError("GitHub response exceeded the size limit")
        except httpx.HTTPError as exc:
            raise GitHubReadError("GitHub pull request request failed") from exc
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise GitHubReadError("GitHub returned invalid JSON") from exc


def _summary(item: dict, repository: str) -> dict:
    return {
        "repository_full_name": repository,
        "number": item.get("number"),
        "title": item.get("title") or "Untitled pull request",
        "body_excerpt": (item.get("body") or "")[:280],
        "state": item.get("state"),
        "draft": bool(item.get("draft")),
        "html_url": item.get("html_url"),
        "author": {
            "login": (item.get("user") or {}).get("login") or "ghost",
            "avatar_url": (item.get("user") or {}).get("avatar_url"),
        },
        "base": {
            "ref": (item.get("base") or {}).get("ref"),
            "sha": (item.get("base") or {}).get("sha"),
        },
        "head": {
            "ref": (item.get("head") or {}).get("ref"),
            "sha": (item.get("head") or {}).get("sha"),
        },
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _installation_tokens(repositories: list[dict]) -> dict[int, str]:
    installation_ids = {
        int(item["installation_id"])
        for item in repositories
        if item.get("installation_id") is not None
    }
    tokens = {}
    for installation_id in installation_ids:
        credentials = get_installation_credentials(installation_id)
        permission = credentials.permissions.get("pull_requests")
        if permission not in {"read", "write"}:
            continue
        tokens[installation_id] = credentials.token
    return tokens


def list_recent_pull_requests(repositories: list[dict], *, limit: int) -> list[dict]:
    scoped = [item for item in repositories[:MAX_REPOSITORIES] if item.get("installation_id")]
    tokens = _installation_tokens(scoped)

    def fetch(repository: dict) -> list[dict]:
        installation_id = int(repository["installation_id"])
        token = tokens.get(installation_id)
        if not token:
            return []
        client = GitHubReadClient(token)
        try:
            payload = client.get(
                f"{_repository_path(repository['full_name'])}/pulls",
                params={
                    "state": "open",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": min(limit, 20),
                },
            )
        finally:
            client.close()
        if not isinstance(payload, list):
            raise GitHubReadError("GitHub returned an invalid pull request list")
        return [_summary(item, repository["full_name"]) for item in payload]

    items: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(scoped)))) as executor:
        futures = [executor.submit(fetch, repository) for repository in scoped]
        for future in as_completed(futures):
            items.extend(future.result())
    items.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return items[:limit]


def fetch_pull_request_snapshot(
    repository: str,
    pull_number: int,
    installation_id: int,
) -> dict:
    credentials = get_installation_credentials(installation_id)
    if credentials.permissions.get("pull_requests") not in {"read", "write"}:
        raise GitHubReadError("GitHub App pull request read permission is missing")
    client = GitHubReadClient(credentials.token)
    prefix = _repository_path(repository)
    try:
        pull = client.get(f"{prefix}/pulls/{pull_number}")
        files = client.get(f"{prefix}/pulls/{pull_number}/files", params={"per_page": MAX_FILES})
        commits = client.get(f"{prefix}/pulls/{pull_number}/commits", params={"per_page": 50})
        reviews = client.get(f"{prefix}/pulls/{pull_number}/reviews", params={"per_page": 50})
        comments = client.get(f"{prefix}/issues/{pull_number}/comments", params={"per_page": 50})
        checks = client.get(
            f"{prefix}/commits/{(pull.get('head') or {}).get('sha')}/check-runs",
            params={"per_page": 100},
        )
    finally:
        client.close()
    if not isinstance(pull, dict) or not isinstance(files, list):
        raise GitHubReadError("GitHub returned invalid pull request detail")

    patch_chars = 0
    bounded_files = []
    for item in files[:MAX_FILES]:
        patch = item.get("patch") if isinstance(item.get("patch"), str) else None
        if patch:
            remaining = MAX_PATCH_CHARS - patch_chars
            patch = patch[: max(0, remaining)] or None
            patch_chars += len(patch or "")
        bounded_files.append(
            {
                "filename": item.get("filename"),
                "status": item.get("status"),
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
                "changes": item.get("changes"),
                "patch": patch,
            }
        )
        if patch_chars >= MAX_PATCH_CHARS:
            break

    return {
        **_summary(pull, repository),
        "body": (pull.get("body") or "")[:12_000],
        "mergeable_state": pull.get("mergeable_state"),
        "changed_files": pull.get("changed_files"),
        "additions": pull.get("additions"),
        "deletions": pull.get("deletions"),
        "commits_count": pull.get("commits"),
        "comments_count": pull.get("comments"),
        "review_comments_count": pull.get("review_comments"),
        "files": bounded_files,
        "commits": [
            {
                "sha": item.get("sha"),
                "message": ((item.get("commit") or {}).get("message") or "")[:500],
                "author": ((item.get("commit") or {}).get("author") or {}).get("name"),
            }
            for item in commits[:50]
        ]
        if isinstance(commits, list)
        else [],
        "reviews": [
            {
                "author": (item.get("user") or {}).get("login"),
                "state": item.get("state"),
                "body": (item.get("body") or "")[:1000],
                "submitted_at": item.get("submitted_at"),
            }
            for item in reviews[:50]
        ]
        if isinstance(reviews, list)
        else [],
        "discussion": [
            {
                "author": (item.get("user") or {}).get("login"),
                "body": (item.get("body") or "")[:1000],
                "created_at": item.get("created_at"),
            }
            for item in comments[:50]
        ]
        if isinstance(comments, list)
        else [],
        "checks": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "conclusion": item.get("conclusion"),
            }
            for item in (checks.get("check_runs") or [])[:100]
            if isinstance(item, dict)
        ]
        if isinstance(checks, dict)
        else [],
    }
