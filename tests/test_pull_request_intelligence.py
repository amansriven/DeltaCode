from importlib import import_module
from types import SimpleNamespace

from app.openai_responses import OpenAIUsage
from app.pull_request_intelligence import github, service, store
from app.pull_request_intelligence.models import GeneratePullRequestOverviewRequest

router = import_module("app.pull_request_intelligence.router")


class FakeOverviewClient:
    available = True
    model = "gpt-4o"
    usage = OpenAIUsage(input_tokens=300, output_tokens=100, cost_usd=0.002)

    def generate_json(self, **kwargs):
        assert kwargs["schema_name"] == "delta_code_pull_request_overview"
        assert kwargs["max_output_tokens"] == 2800
        return {
            "verdict": "review_needed",
            "headline": "Authentication behavior changed",
            "executive_summary": "The pull request changes the authenticated callback path.",
            "change_summary": ["Updates the OAuth callback handler"],
            "risk_signals": [
                {
                    "severity": "high",
                    "title": "Callback regression risk",
                    "detail": "The supplied patch changes redirect handling.",
                    "evidence": ["app/oauth.py changed"],
                }
            ],
            "review_focus": [
                {
                    "path": "app/oauth.py",
                    "title": "Validate the redirect target",
                    "detail": "Confirm the callback only accepts trusted return paths.",
                    "reviewer_question": "Can an external return path pass validation?",
                }
            ],
            "test_assessment": {
                "status": "gaps",
                "summary": "No callback test was supplied.",
                "missing_coverage": ["External redirect rejection"],
            },
            "recommended_actions": ["Add a callback redirect test"],
            "confidence": {"score": 0.78, "basis": "A bounded patch was supplied."},
        }


def test_pull_request_overview_is_structured_and_reports_usage():
    overview, model, usage = service.generate_pull_request_overview(
        {"repository_full_name": "acme/api", "number": 42, "files": []},
        FakeOverviewClient(),
    )

    assert overview.verdict == "review_needed"
    assert overview.review_focus[0].path == "app/oauth.py"
    assert model == "gpt-4o"
    assert usage.input_tokens == 300


def test_recent_pull_requests_are_sorted_across_repositories(monkeypatch):
    monkeypatch.setattr(github, "_installation_tokens", lambda _repositories: {1: "token"})

    class FakeReadClient:
        def __init__(self, _token):
            pass

        def close(self):
            pass

        def get(self, path, *, params=None):
            repository = "acme/web" if "/web/" in path else "acme/api"
            updated = (
                "2026-08-15T12:00:00Z" if repository.endswith("web") else "2026-08-14T12:00:00Z"
            )
            return [
                {
                    "number": 7,
                    "title": f"Update {repository}",
                    "state": "open",
                    "draft": False,
                    "html_url": f"https://github.com/{repository}/pull/7",
                    "user": {"login": "octocat", "avatar_url": ""},
                    "base": {"ref": "main", "sha": "base"},
                    "head": {"ref": "feature", "sha": "head"},
                    "created_at": updated,
                    "updated_at": updated,
                }
            ]

    monkeypatch.setattr(github, "GitHubReadClient", FakeReadClient)
    repositories = [
        {"full_name": "acme/api", "installation_id": 1},
        {"full_name": "acme/web", "installation_id": 1},
    ]

    items = github.list_recent_pull_requests(repositories, limit=10)

    assert [item["repository_full_name"] for item in items] == ["acme/web", "acme/api"]


def test_generate_pull_request_route_queues_durable_task(monkeypatch):
    queued = []
    monkeypatch.setattr(router, "FRONTEND_URL", "https://delta.example")
    monkeypatch.setattr(router, "_configured", lambda: True)
    monkeypatch.setattr(
        router,
        "queue_overview",
        lambda *_args, **_kwargs: {
            "status": "queued",
            "repository_full_name": "acme/api",
            "pull_number": 42,
        },
    )
    monkeypatch.setattr(
        router.generate_pull_request_ai_overview,
        "defer",
        lambda **kwargs: queued.append(kwargs),
    )
    request = SimpleNamespace(headers={"origin": "https://delta.example"})
    session = {
        "repositories": [{"full_name": "acme/api", "installation_id": 17}],
    }

    response = router.generate_overview(
        "acme",
        "api",
        42,
        GeneratePullRequestOverviewRequest(refresh=False),
        request,
        ("workspace-1", session),
    )

    assert response["status"] == "queued"
    assert queued == [
        {
            "workspace_id": "workspace-1",
            "repository_full_name": "acme/api",
            "pull_number": 42,
        }
    ]


def test_completed_overview_appends_history_version(monkeypatch):
    calls = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, parameters):
            calls.append((query, parameters))

    monkeypatch.setattr(store, "get_connection", FakeConnection)
    snapshot = {
        "head": {"sha": "a" * 40},
        "updated_at": "2026-08-15T12:00:00Z",
    }
    usage = OpenAIUsage(input_tokens=20, output_tokens=8, cost_usd=0.00013)

    store.complete_overview(
        "workspace-1",
        "acme/api",
        42,
        snapshot,
        {"headline": "Review authentication"},
        "gpt-4o",
        usage,
    )

    assert len(calls) == 2
    assert "UPDATE pull_request_ai_overviews" in calls[0][0]
    assert "INSERT INTO pull_request_ai_overview_history" in calls[1][0]
    assert calls[1][1][4] == "a" * 40
