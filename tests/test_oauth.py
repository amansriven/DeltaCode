from app import oauth


class FakeRequest:
    def __init__(self, scheme="https"):
        self.url = type("URL", (), {"scheme": scheme})()

    def url_for(self, _name):
        return "https://api.delta.example/auth/github/callback"


def test_github_display_name_normalizes_optional_profile_name():
    assert oauth._github_display_name({"name": "  The Octocat  "}) == "The Octocat"
    assert oauth._github_display_name({"name": "   "}) is None
    assert oauth._github_display_name({"name": None}) is None
    assert oauth._github_display_name({}) is None


def test_me_returns_public_identity_without_credentials(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "get_session",
        lambda _request: {
            "github_user_id": 1,
            "github_login": "octocat",
            "github_name": "The Octocat",
            "avatar_url": "https://avatars.githubusercontent.com/u/1",
            "accessible_repos": ["acme/example"],
            "repositories": [
                {
                    "full_name": "acme/example",
                    "private": True,
                    "visibility": "private",
                }
            ],
        },
    )

    response = oauth.me(object())

    assert response == {
        "login": "octocat",
        "name": "The Octocat",
        "avatar_url": "https://avatars.githubusercontent.com/u/1",
        "accessible_repos": ["acme/example"],
        "repositories": [
            {
                "full_name": "acme/example",
                "private": True,
                "visibility": "private",
            }
        ],
    }
    assert not {"token", "access_token", "credentials"} & response.keys()


def test_frontend_redirect_accepts_relative_paths(monkeypatch):
    monkeypatch.setattr(oauth, "FRONTEND_URL", "https://delta.example")

    assert (
        oauth._safe_frontend_redirect("/settings/integrations")
        == "https://delta.example/settings/integrations"
    )


def test_frontend_redirect_rejects_external_destinations(monkeypatch):
    monkeypatch.setattr(oauth, "FRONTEND_URL", "https://delta.example")

    assert (
        oauth._safe_frontend_redirect("https://attacker.example/collect")
        == "https://delta.example/runs"
    )
    assert oauth._safe_frontend_redirect("//attacker.example/collect") == "https://delta.example/runs"


def test_login_redirects_to_github_and_preserves_destination(monkeypatch):
    monkeypatch.setattr(oauth, "CLIENT_ID", "client id")
    monkeypatch.setattr(oauth, "CALLBACK_URL", "https://api.delta.example/auth/github/callback")
    monkeypatch.setattr(oauth, "FRONTEND_URL", "https://delta.example")

    response = oauth.github_login(FakeRequest(), "/settings/account")

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=client+id" in response.headers["location"]
    cookies = response.headers.getlist("set-cookie")
    assert any("oauth_state=" in cookie and "Secure" in cookie for cookie in cookies)
    assert any(
        "oauth_redirect=" in cookie and "delta.example/settings/account" in cookie
        for cookie in cookies
    )


def test_local_oauth_cookie_works_over_http(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)

    cookie_kwargs = oauth._cookie_kwargs(FakeRequest("http"))

    assert cookie_kwargs["secure"] is False
    assert cookie_kwargs["samesite"] == "lax"


def test_login_fails_cleanly_when_github_is_not_configured(monkeypatch):
    monkeypatch.setattr(oauth, "CLIENT_ID", None)

    try:
        oauth.github_login(FakeRequest(), "/runs")
    except Exception as exc:
        assert exc.status_code == 503
        assert exc.detail == "GitHub sign-in is not configured"
    else:
        raise AssertionError("Expected unconfigured GitHub sign-in to fail")


def test_repository_access_preserves_visibility(monkeypatch):
    monkeypatch.setattr(oauth, "GITHUB_APP_ID", "42")
    calls = []

    def fake_get(url: str, _token: str, *, page: int):
        calls.append((url, page))
        if url.endswith("/user/installations"):
            return {
                "installations": [
                    {"id": 7, "app_id": 42},
                    {"id": 8, "app_id": 999},
                ]
            }
        return {
            "repositories": [
                {
                    "full_name": "acme/private-api",
                    "private": True,
                    "visibility": "private",
                },
                {
                    "full_name": "acme/public-api",
                    "private": False,
                    "visibility": "public",
                },
            ]
        }

    monkeypatch.setattr(oauth, "_github_get", fake_get)

    assert oauth._fetch_repository_access("user-token") == [
        {
            "full_name": "acme/private-api",
            "private": True,
            "visibility": "private",
            "clone_url": "https://github.com/acme/private-api.git",
            "default_branch": "main",
            "installation_id": 7,
        },
        {
            "full_name": "acme/public-api",
            "private": False,
            "visibility": "public",
            "clone_url": "https://github.com/acme/public-api.git",
            "default_branch": "main",
            "installation_id": 7,
        },
    ]
    assert not any("/8/repositories" in url for url, _page in calls)
