"""Bounded, read-only GitHub source context for Ask Delta."""

import base64
import re
from pathlib import PurePosixPath
from urllib.parse import quote

from app.github_client import get_installation_credentials
from app.pull_request_intelligence.github import GitHubReadClient, GitHubReadError

MAX_REPOSITORIES = 3
MAX_FILES_PER_REPOSITORY = 10
MAX_FILE_CHARS = 10_000
MAX_TOTAL_CHARS = 56_000
MAX_CANDIDATE_FILE_BYTES = 100_000
SOURCE_ACCESS_ACTION = "/settings/integrations"

_TEXT_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".graphql", ".html", ".java",
    ".js", ".jsx", ".json", ".kt", ".md", ".mjs", ".php", ".py", ".rb",
    ".rs", ".scss", ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".vue",
    ".yaml", ".yml",
}
_PRIORITY_NAMES = {
    "readme": 120,
    "readme.md": 130,
    "package.json": 115,
    "pyproject.toml": 115,
    "go.mod": 115,
    "cargo.toml": 115,
    "pom.xml": 110,
    "build.gradle": 110,
    "dockerfile": 95,
    "docker-compose.yml": 95,
    "docker-compose.yaml": 95,
    "next.config.js": 90,
    "next.config.mjs": 90,
    "next.config.ts": 90,
    "vercel.json": 90,
    "railway.json": 90,
    "wrangler.jsonc": 90,
    "wrangler.toml": 90,
}
_IGNORED_PARTS = {
    ".git", ".next", "build", "coverage", "dist", "node_modules", "target", "vendor",
}
_SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", "credentials.json", "secrets.json",
}


def _repo_path(full_name: str) -> str:
    owner, name = full_name.split("/", 1)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


def _question_terms(question: str) -> set[str]:
    ignored = {
        "about", "does", "exactly", "from", "have", "repository", "that", "this",
        "what", "where", "which", "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", question.lower())
        if token not in ignored
    }


def _safe_candidate(item: dict) -> bool:
    path = item.get("path")
    size = item.get("size")
    if item.get("type") != "blob" or not isinstance(path, str):
        return False
    candidate = PurePosixPath(path)
    lower_parts = {part.lower() for part in candidate.parts}
    name = candidate.name.lower()
    if lower_parts & _IGNORED_PARTS or name in _SENSITIVE_NAMES:
        return False
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return False
    if isinstance(size, int) and size > MAX_CANDIDATE_FILE_BYTES:
        return False
    return candidate.suffix.lower() in _TEXT_EXTENSIONS or name in _PRIORITY_NAMES


def _score_path(path: str, terms: set[str]) -> tuple[int, int, str]:
    candidate = PurePosixPath(path)
    lowered = path.lower()
    name = candidate.name.lower()
    score = _PRIORITY_NAMES.get(name, 0)
    if name.startswith("readme"):
        score += 120
    if name in {"index.ts", "index.js", "main.py", "main.go", "app.ts", "app.py"}:
        score += 48
    if any(part in {"src", "app", "server", "api"} for part in candidate.parts):
        score += 18
    score += 35 * sum(term in lowered for term in terms)
    if "test" in lowered and not ({"test", "tests", "testing"} & terms):
        score -= 20
    return (-score, len(candidate.parts), lowered)


def _decode_blob(payload: dict) -> str | None:
    if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        return None
    try:
        raw = base64.b64decode(payload["content"], validate=False)
    except (ValueError, TypeError):
        return None
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")[:MAX_FILE_CHARS]


def _repository_context(repository: dict, question: str, budget: int) -> dict:
    full_name = repository["full_name"]
    credentials = get_installation_credentials(int(repository["installation_id"]))
    if credentials.permissions.get("contents") not in {"read", "write"}:
        return {
            "repository_full_name": full_name,
            "status": "metadata_only",
            "reason_code": "contents_permission_missing",
            "reason": (
                "This repository is selected for metadata and pull requests, but the "
                "Delta Code GitHub App is missing Contents: Read permission."
            ),
            "action_href": SOURCE_ACCESS_ACTION,
            "files": [],
        }
    client = GitHubReadClient(credentials.token)
    prefix = _repo_path(full_name)
    try:
        metadata = client.get(prefix)
        if not isinstance(metadata, dict):
            raise GitHubReadError("GitHub returned invalid repository metadata")
        default_branch = (
            metadata.get("default_branch")
            or repository.get("default_branch")
            or "main"
        )
        branch = client.get(f"{prefix}/branches/{quote(default_branch, safe='')}")
        commit_sha = ((branch.get("commit") or {}).get("sha")) if isinstance(branch, dict) else None
        tree = client.get(
            f"{prefix}/git/trees/{quote(commit_sha or default_branch, safe='')}",
            params={"recursive": "1"},
        )
        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        terms = _question_terms(question)
        candidates = [item for item in entries if isinstance(item, dict) and _safe_candidate(item)]
        candidates.sort(key=lambda item: _score_path(item["path"], terms))
        files = []
        used = 0
        for item in candidates[: MAX_FILES_PER_REPOSITORY * 3]:
            if len(files) >= MAX_FILES_PER_REPOSITORY or used >= budget:
                break
            blob = client.get(f"{prefix}/git/blobs/{quote(item['sha'], safe='')}")
            content = _decode_blob(blob) if isinstance(blob, dict) else None
            if not content:
                continue
            content = content[: max(0, budget - used)]
            if not content:
                break
            files.append({"path": item["path"], "content": content})
            used += len(content)
        return {
            "repository_full_name": full_name,
            "status": "ready",
            "description": metadata.get("description") if isinstance(metadata, dict) else None,
            "homepage": metadata.get("homepage") if isinstance(metadata, dict) else None,
            "topics": metadata.get("topics", []) if isinstance(metadata, dict) else [],
            "primary_language": metadata.get("language") if isinstance(metadata, dict) else None,
            "default_branch": default_branch,
            "commit_sha": commit_sha,
            "tree_truncated": bool(tree.get("truncated")) if isinstance(tree, dict) else True,
            "files": files,
        }
    finally:
        client.close()


def build_repository_context(repositories: list[dict], question: str) -> list[dict]:
    """Return source excerpts for selected repositories under a global character cap."""

    results = []
    remaining = MAX_TOTAL_CHARS
    for repository in repositories[:MAX_REPOSITORIES]:
        if remaining <= 0:
            break
        try:
            result = _repository_context(repository, question, remaining)
        except Exception:
            result = {
                "repository_full_name": repository.get("full_name", "unknown"),
                "status": "unavailable",
                "reason_code": "github_source_read_failed",
                "reason": "GitHub could not provide repository source for this answer.",
                "action_href": SOURCE_ACCESS_ACTION,
                "files": [],
            }
        results.append(result)
        remaining -= sum(len(item["content"]) for item in result.get("files", []))
    return results


def repository_access_report(repository_context: list[dict]) -> list[dict]:
    """Return deterministic, browser-safe source access status for a chat response."""

    report = []
    for repository in repository_context:
        files = repository.get("files", [])
        status = repository.get("status")
        if status == "ready":
            report.append(
                {
                    "repository_full_name": repository["repository_full_name"],
                    "status": "source_ready",
                    "message": (
                        f"GitHub source access verified; {len(files)} relevant "
                        f"file{'s' if len(files) != 1 else ''} inspected."
                    ),
                    "files_inspected": len(files),
                    "action_href": None,
                }
            )
            continue
        report.append(
            {
                "repository_full_name": repository.get("repository_full_name", "unknown"),
                "status": "metadata_only" if status == "metadata_only" else "unavailable",
                "message": repository.get("reason")
                or "Repository source was unavailable for this answer.",
                "files_inspected": 0,
                "action_href": repository.get("action_href", SOURCE_ACCESS_ACTION),
            }
        )
    return report
