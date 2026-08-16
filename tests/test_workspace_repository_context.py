import base64
from types import SimpleNamespace

from app.workspace_intelligence import repository_context


class FakeGitHubReadClient:
    def __init__(self, token):
        assert token == "installation-token"

    def close(self):
        pass

    def get(self, path, *, params=None):
        if path == "/repos/acme/SweetPlus":
            return {
                "description": "A focused planning product",
                "default_branch": "main",
                "language": "TypeScript",
                "topics": ["planning"],
            }
        if path.endswith("/branches/main"):
            return {"commit": {"sha": "a" * 40}}
        if "/git/trees/" in path:
            assert params == {"recursive": "1"}
            return {
                "truncated": False,
                "tree": [
                    {"path": "src/planner.ts", "type": "blob", "size": 100, "sha": "planner"},
                    {"path": ".env", "type": "blob", "size": 20, "sha": "secret"},
                    {"path": "README.md", "type": "blob", "size": 100, "sha": "readme"},
                    {"path": "package.json", "type": "blob", "size": 100, "sha": "package"},
                ],
            }
        contents = {
            "readme": "# SweetPlus\nA focused planning and task application.",
            "package": '{"name":"sweetplus","dependencies":{"next":"16"}}',
            "planner": "export function buildPlan() { return []; }",
        }
        sha = path.rsplit("/", 1)[-1]
        return {
            "encoding": "base64",
            "content": base64.b64encode(contents[sha].encode()).decode(),
        }


def test_repository_context_reads_relevant_source_without_sensitive_files(monkeypatch):
    monkeypatch.setattr(
        repository_context,
        "get_installation_credentials",
        lambda _installation_id: SimpleNamespace(
            token="installation-token", permissions={"contents": "read"}
        ),
    )
    monkeypatch.setattr(repository_context, "GitHubReadClient", FakeGitHubReadClient)

    context = repository_context.build_repository_context(
        [{"full_name": "acme/SweetPlus", "default_branch": "main", "installation_id": 7}],
        "What is SweetPlus and how does the planner work?",
    )

    assert context[0]["status"] == "ready"
    assert context[0]["commit_sha"] == "a" * 40
    paths = [item["path"] for item in context[0]["files"]]
    assert "README.md" in paths
    assert "src/planner.ts" in paths
    assert ".env" not in paths


def test_repository_context_reports_missing_contents_permission(monkeypatch):
    monkeypatch.setattr(
        repository_context,
        "get_installation_credentials",
        lambda _installation_id: SimpleNamespace(token="token", permissions={}),
    )

    context = repository_context.build_repository_context(
        [{"full_name": "acme/SweetPlus", "default_branch": "main", "installation_id": 7}],
        "What is this?",
    )

    assert context[0]["status"] == "metadata_only"
    assert context[0]["reason_code"] == "contents_permission_missing"
    assert "selected for metadata and pull requests" in context[0]["reason"]
    assert context[0]["files"] == []

    report = repository_context.repository_access_report(context)
    assert report == [
        {
            "repository_full_name": "acme/SweetPlus",
            "status": "metadata_only",
            "message": context[0]["reason"],
            "files_inspected": 0,
            "action_href": "/settings/integrations",
        }
    ]
