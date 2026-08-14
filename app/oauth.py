import hashlib
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.db import get_connection

router = APIRouter(prefix="/auth")

GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID")
CLIENT_ID = os.environ.get("GITHUB_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GITHUB_OAUTH_CLIENT_SECRET")
CALLBACK_URL = os.environ.get("GITHUB_OAUTH_CALLBACK_URL")
FRONTEND_URL = os.environ.get(
    "FRONTEND_URL", "http://localhost:3000"
).rstrip("/")

SESSION_TTL = timedelta(days=7)
COMPLETION_TTL = timedelta(minutes=2)
STATE_COOKIE = "oauth_state"
REDIRECT_COOKIE = "oauth_redirect"
SESSION_COOKIE = "session_id"


def _cookie_kwargs(request: Request) -> dict:
    """Use cross-site cookies in production without breaking local HTTP OAuth."""
    secure = request.url.scheme == "https" or os.environ.get("COOKIE_SECURE", "").lower() == "true"
    return {
        "httponly": True,
        "secure": secure,
        "samesite": "none" if secure else "lax",
        "path": "/",
    }


def _safe_frontend_redirect(value: str | None) -> str:
    """Resolve a post-auth destination without allowing an external redirect."""
    fallback = f"{FRONTEND_URL.rstrip('/')}/migrations"
    if not value:
        return fallback

    if value.startswith("/") and not value.startswith("//"):
        return urljoin(f"{FRONTEND_URL.rstrip('/')}/", value.lstrip("/"))

    candidate = urlparse(value)
    frontend = urlparse(FRONTEND_URL)
    if candidate.scheme in {"http", "https"} and candidate.netloc == frontend.netloc:
        return value
    return fallback


@router.get("/github/login")
def github_login(request: Request, redirect_uri: str | None = None):
    if not CLIENT_ID:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured")

    state = secrets.token_urlsafe(24)
    callback_url = CALLBACK_URL or str(request.url_for("github_callback"))
    authorize_url = "https://github.com/login/oauth/authorize?" + urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": callback_url,
            "state": state,
        }
    )
    response = RedirectResponse(authorize_url)
    cookie_kwargs = _cookie_kwargs(request)
    response.set_cookie(STATE_COOKIE, state, max_age=600, **cookie_kwargs)
    response.set_cookie(
        REDIRECT_COOKIE, _safe_frontend_redirect(redirect_uri), max_age=600, **cookie_kwargs
    )
    return response


def _github_get(url: str, user_token: str, *, page: int) -> dict:
    headers = {"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json"}
    response = httpx.get(
        url,
        headers=headers,
        params={"per_page": 100, "page": page},
        timeout=10.0,
    )
    response.raise_for_status()
    return response.json()


def _fetch_repository_access(user_token: str) -> list[dict]:
    """Return every repository this user can access through this GitHub App."""
    installations: list[dict] = []
    page = 1
    while True:
        payload = _github_get("https://api.github.com/user/installations", user_token, page=page)
        batch = payload.get("installations", [])
        installations.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    repositories: dict[str, dict] = {}
    for installation in installations:
        if str(installation.get("app_id")) != str(GITHUB_APP_ID):
            continue
        page = 1
        while True:
            payload = _github_get(
                f"https://api.github.com/user/installations/{installation['id']}/repositories",
                user_token,
                page=page,
            )
            batch = payload.get("repositories", [])
            for repo in batch:
                full_name = repo["full_name"]
                repositories[full_name] = {
                    "full_name": full_name,
                    "private": bool(repo.get("private")),
                    "visibility": repo.get("visibility")
                    or ("private" if repo.get("private") else "public"),
                    "clone_url": repo.get("clone_url")
                    or f"https://github.com/{full_name}.git",
                    "default_branch": repo.get("default_branch") or "main",
                    "installation_id": installation["id"],
                }
            if len(batch) < 100:
                break
            page += 1
    return sorted(repositories.values(), key=lambda repo: repo["full_name"].lower())


def _github_display_name(user: dict) -> str | None:
    """Return GitHub's optional public profile name in a display-safe form."""
    name = user.get("name")
    if not isinstance(name, str):
        return None
    normalized = name.strip()
    return normalized or None


def _ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


@router.get("/github/callback")
def github_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:
        raise HTTPException(status_code=400, detail="GitHub sign-in was cancelled")
    if state != request.cookies.get(STATE_COOKIE):
        raise HTTPException(status_code=400, detail="invalid oauth state")
    if not code or not CLIENT_ID or not CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="GitHub sign-in is not configured")

    callback_url = CALLBACK_URL or str(request.url_for("github_callback"))
    token_response = httpx.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": callback_url,
        },
        timeout=10.0,
    )
    token_response.raise_for_status()
    token_resp = token_response.json()
    user_token = token_resp.get("access_token")
    if not user_token:
        raise HTTPException(status_code=400, detail=f"oauth exchange failed: {token_resp}")

    user_response = httpx.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json"},
        timeout=10.0,
    )
    user_response.raise_for_status()
    user = user_response.json()
    if not isinstance(user.get("id"), int) or not isinstance(user.get("login"), str):
        raise HTTPException(status_code=502, detail="GitHub returned an invalid user profile")

    repositories = _fetch_repository_access(user_token)
    accessible_repos = [repo["full_name"] for repo in repositories]

    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + SESSION_TTL
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions "
            "(id, github_user_id, github_login, github_name, avatar_url, "
            "accessible_repos, repositories, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                session_id,
                user["id"],
                user["login"],
                _github_display_name(user),
                user.get("avatar_url"),
                json.dumps(accessible_repos),
                json.dumps(repositories),
                expires_at,
            ),
        )

    redirect_to = _safe_frontend_redirect(request.cookies.get(REDIRECT_COOKIE))
    completion_ticket = secrets.token_urlsafe(32)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO oauth_completion_tickets "
            "(token_hash, session_id, redirect_to, expires_at) VALUES (%s, %s, %s, %s)",
            (
                _ticket_hash(completion_ticket),
                session_id,
                redirect_to,
                datetime.now(UTC) + COMPLETION_TTL,
            ),
        )

    completion_url = f"{FRONTEND_URL}/api/auth/github/complete?" + urlencode(
        {"ticket": completion_ticket}
    )
    response = RedirectResponse(completion_url)
    response.delete_cookie(STATE_COOKIE, path="/")
    response.delete_cookie(REDIRECT_COOKIE, path="/")
    return response


@router.get("/github/complete")
def github_complete(request: Request, ticket: str):
    with get_connection() as conn:
        row = conn.execute(
            "DELETE FROM oauth_completion_tickets "
            "WHERE token_hash = %s AND expires_at > now() "
            "RETURNING session_id, redirect_to",
            (_ticket_hash(ticket),),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="sign-in completion expired or invalid")

    session_id, redirect_to = row
    response = RedirectResponse(_safe_frontend_redirect(redirect_to))
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=int(SESSION_TTL.total_seconds()),
        **_cookie_kwargs(request),
    )
    return response


@router.post("/logout")
def logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        with get_connection() as conn:
            conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
    response = RedirectResponse(FRONTEND_URL, status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/me")
def me(request: Request):
    session = get_session(request)
    return {
        "login": session["github_login"],
        "name": session["github_name"],
        "avatar_url": session["avatar_url"],
        "accessible_repos": session["accessible_repos"],
        "repositories": session["repositories"],
    }


def get_session(request: Request) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        raise HTTPException(status_code=401, detail="not signed in")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT github_user_id, github_login, github_name, avatar_url, "
            "accessible_repos, repositories "
            "FROM sessions "
            "WHERE id = %s AND expires_at > now()",
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    repositories = row[5] or [
        {"full_name": repo, "private": None, "visibility": "unknown"} for repo in row[4]
    ]
    return {
        "github_user_id": row[0],
        "github_login": row[1],
        "github_name": row[2],
        "avatar_url": row[3],
        "accessible_repos": row[4],
        "repositories": repositories,
    }
