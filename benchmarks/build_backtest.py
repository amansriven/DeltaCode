"""Select and freeze benchmarks/repository-impact-backtest.json from merged public migrations.

Selection is mechanical and runs before any analyzer does: search merged pull requests in
result order, keep the first PER_MIGRATION that pass the rules, and record why others failed.
Cases are never added or removed because of how the analyzer scores on them.

Round 2 was added after round 1 under-filled, before any scores were read. It only appends: the
rules are unchanged, and it searches exact API names because broad phrases mostly found noise.

--holdout selects a separate set under the same rules, excluding every tuning repository, so a
number can be reported from cases no analyzer fix was ever made against. Score it once.

Usage: GITHUB_TOKEN=$(gh auth token) python benchmarks/build_backtest.py [--extend | --holdout]
"""

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import httpx

from app.hardening.backtest import changed_lines
from app.repository_intelligence.workspace import IGNORED_DIRECTORIES

PER_MIGRATION = 10
MAX_CANDIDATES = 150
MAX_REPOSITORY_KB = 200_000
SELENIUM_LOCATORS = (
    "id", "name", "xpath", "css_selector", "class_name", "tag_name", "link_text",
    "partial_link_text",
)


def symbols(names, package, ecosystem):
    return [
        {"kind": "symbol", "name": name, "package": package, "ecosystem": ecosystem}
        for name in names
    ]


MIGRATIONS = [
    {
        "id": "openai-python-v1",
        "description": "openai-python 1.0 removed module-level resource classes.",
        "extensions": [".py"],
        "pattern": r"\b(?:ChatCompletion|Completion|Embedding)\.a?create\s*\(",
        "owners_excluded": ["openai"],
        "queries": ["openai v1 migration", "migrate openai 1.0", "upgrade openai python 1.0",
                    "openai chat.completions.create migrate", "update openai api v1"],
        "targets": symbols(
            [f"{kind}.{verb}" for kind in ("ChatCompletion", "Completion", "Embedding")
             for verb in ("create", "acreate")], "openai", "pypi"),
    },
    {
        "id": "selenium-4-locators",
        "description": "Selenium 4 removed the find_element(s)_by_* driver methods.",
        "extensions": [".py"],
        "pattern": r"\.find_elements?_by_(?:" + "|".join(SELENIUM_LOCATORS) + r")\s*\(",
        "owners_excluded": ["SeleniumHQ"],
        "queries": ["selenium 4 find_element", "find_element_by deprecated",
                    "replace find_element_by", "selenium 4 upgrade", "update selenium locators"],
        "targets": symbols(
            [f"find_element{plural}_by_{locator}" for plural in ("", "s")
             for locator in SELENIUM_LOCATORS], "selenium", "pypi"),
    },
    {
        "id": "numpy-1.24-aliases",
        "description": "NumPy 1.24 removed the np.float style builtin aliases.",
        "extensions": [".py"],
        "pattern": r"\b(?:np|numpy)\.(?:float|int|bool|object|str|complex)\b",
        "owners_excluded": ["numpy"],
        "queries": ["np.float deprecated", "numpy 1.24 np.int", "replace np.float",
                    "np.bool numpy 1.24", "fix numpy deprecated aliases"],
        "targets": symbols(
            [f"numpy.{name}" for name in ("float", "int", "bool", "object", "str", "complex")],
            "numpy", "pypi"),
    },
    {
        "id": "pydantic-v2-validators",
        "description": "Pydantic 2 deprecated @validator and @root_validator.",
        "extensions": [".py"],
        "pattern": r"^\s*@(?:pydantic\.)?(?:validator|root_validator)\s*\(",
        "owners_excluded": ["pydantic"],
        "queries": ["pydantic v2 migration", "migrate to pydantic v2", "upgrade pydantic 2",
                    "pydantic field_validator migrate", "pydantic v2 upgrade validators"],
        "targets": symbols(["pydantic.validator", "pydantic.root_validator"], "pydantic", "pypi"),
    },
    {
        "id": "react-18-root",
        "description": "React 18 replaced ReactDOM.render and hydrate with the root API.",
        "extensions": [".js", ".jsx", ".ts", ".tsx"],
        "pattern": r"\bReactDOM\.(?:render|hydrate)\s*\(",
        "owners_excluded": ["facebook", "reactjs"],
        "queries": ["react 18 createRoot", "upgrade react 18", "migrate createRoot",
                    "react 18 migration", "update to react 18"],
        "targets": symbols(["ReactDOM.render", "ReactDOM.hydrate"], "react-dom", "npm"),
    },
    {
        "id": "openai-node-v4",
        "description": "openai-node v4 removed OpenAIApi and its create* methods.",
        "extensions": [".js", ".jsx", ".mjs", ".ts", ".tsx"],
        "pattern": r"\.create(?:ChatCompletion|Completion|Embedding)\s*\(",
        "owners_excluded": ["openai"],
        "queries": ["openai v4 node", "openai sdk v4 upgrade", "migrate openai node v4",
                    "upgrade openai v4", "openai node library v4 migration"],
        "targets": symbols(
            [f"OpenAIApi.create{name}" for name in ("ChatCompletion", "Completion", "Embedding")],
            "openai", "npm"),
    },
]


EXACT_API_QUERIES = {
    "openai-python-v1": ['"ChatCompletion.create"', '"openai.ChatCompletion"', '"openai.Embedding"',
                         '"openai.Completion"', '"ChatCompletion.acreate"'],
    "selenium-4-locators": ['"find_element_by"', '"find_elements_by"', '"find_element_by_id"',
                            '"find_element_by_xpath"', '"find_element(By"'],
    "pydantic-v2-validators": ['"root_validator"', '"field_validator"', '"@validator"',
                               '"model_validator"'],
    "react-18-root": ['"ReactDOM.render"', '"createRoot"', '"ReactDOM.hydrate"', '"hydrateRoot"'],
    "openai-node-v4": ['"createChatCompletion"', '"OpenAIApi"', '"createCompletion"',
                       '"createEmbedding"'],
}


HOLDOUT_EXTRA_QUERIES = {
    "numpy-1.24-aliases": ['"np.float"', '"np.int"', '"np.bool"', '"np.object"'],
}


class GitHub:
    def __init__(self, token: str) -> None:
        self.client = httpx.Client(
            base_url="https://api.github.com",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=httpx.Timeout(60, connect=15),
        )

    def get(self, path: str, **params):
        for _attempt in range(4):
            response = self.client.get(path, params=params)
            if response.status_code in {403, 429} and "rate limit" in response.text.lower():
                reset = int(response.headers.get("x-ratelimit-reset", time.time() + 60))
                time.sleep(max(reset - time.time(), 1) + 1)
                continue
            if response.status_code >= 500:
                time.sleep(5)
                continue
            return response
        return response

    def search(self, query: str):
        for page in (1, 2):
            time.sleep(2.2)  # The search API allows 30 requests a minute.
            response = self.get(
                "/search/issues",
                q=f"{query} is:pr is:merged is:public",
                per_page=100,
                page=page,
            )
            if response.status_code != 200:
                return
            yield from response.json().get("items", [])


def evaluate(github: GitHub, item: dict, migration: dict, pattern: re.Pattern[str]):
    """Return (case, None) when a pull request qualifies, otherwise (None, reason)."""
    repository = item["repository_url"].removeprefix("https://api.github.com/repos/")
    owner = repository.split("/")[0]
    if owner in migration["owners_excluded"]:
        return None, "library_owner"
    number = item["number"]
    pull = github.get(f"/repos/{repository}/pulls/{number}")
    if pull.status_code != 200:
        return None, "pull_unavailable"
    merge_sha = pull.json().get("merge_commit_sha")
    if not merge_sha:
        return None, "no_merge_commit"
    commit = github.get(f"/repos/{repository}/commits/{merge_sha}")
    if commit.status_code != 200 or not commit.json().get("parents"):
        return None, "commit_unavailable"
    parent_sha = commit.json()["parents"][0]["sha"]
    compare = github.get(f"/repos/{repository}/compare/{parent_sha}...{merge_sha}")
    if compare.status_code != 200:
        return None, "compare_unavailable"
    ground_truth = []
    total_added = 0
    for changed in compare.json().get("files", []):
        path = changed.get("previous_filename") or changed["filename"]
        relative = PurePosixPath(path)
        if relative.suffix.lower() not in migration["extensions"]:
            continue
        if IGNORED_DIRECTORIES.intersection(relative.parts) or "patch" not in changed:
            continue
        removed, added = changed_lines(changed["patch"], pattern)
        total_added += added
        ground_truth.extend({"path": path, "line": line} for line in removed)
    if not ground_truth:
        return None, "no_removed_usage"
    if len(ground_truth) <= total_added:
        return None, "usage_not_reduced"
    metadata = github.get(f"/repos/{repository}")
    if metadata.status_code != 200:
        return None, "repository_unavailable"
    if metadata.json().get("fork"):
        return None, "fork"
    if metadata.json().get("size", 0) > MAX_REPOSITORY_KB:
        return None, "repository_too_large"
    return {
        "id": f"{migration['id']}:{repository}#{number}",
        "migration": migration["id"],
        "repository": repository,
        "pull_request": number,
        "parent_sha": parent_sha,
        "migration_sha": merge_sha,
        "ground_truth": ground_truth,
    }, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extend", action="store_true", help="append round 2 to the frozen file")
    parser.add_argument("--holdout", action="store_true", help="select a separate untuned set")
    arguments = parser.parse_args()
    tuning_path = Path(__file__).with_name("repository-impact-backtest.json")
    output = (
        Path(__file__).with_name("repository-impact-backtest-holdout.json")
        if arguments.holdout
        else tuning_path
    )
    frozen = json.loads(tuning_path.read_text(encoding="utf-8")) if arguments.extend else None
    tuning_repositories = (
        {case["repository"] for case in json.loads(tuning_path.read_text("utf-8"))["cases"]}
        if arguments.holdout
        else set()
    )
    github = GitHub(os.environ["GITHUB_TOKEN"])
    cases = list(frozen["cases"]) if frozen else []
    rejections = {}
    for migration in MIGRATIONS:
        pattern = re.compile(migration["pattern"])
        reasons: Counter[str] = Counter()
        selected = [case for case in cases if case["migration"] == migration["id"]]
        seen_repositories = {case["repository"] for case in selected} | tuning_repositories
        seen_pulls: set[str] = set()
        examined = 0
        queries = EXACT_API_QUERIES.get(migration["id"], []) if frozen else migration["queries"]
        if arguments.holdout:
            queries = [
                *EXACT_API_QUERIES.get(migration["id"], []),
                *HOLDOUT_EXTRA_QUERIES.get(migration["id"], []),
                *migration["queries"],
            ]
        for query in queries:
            for item in github.search(query):
                if len(selected) >= PER_MIGRATION or examined >= MAX_CANDIDATES:
                    break
                repository = item["repository_url"].rsplit("/repos/", 1)[-1]
                key = f"{repository}#{item['number']}"
                if key in seen_pulls:
                    continue
                seen_pulls.add(key)
                if repository in seen_repositories:
                    reasons["repository_already_selected"] += 1
                    continue
                examined += 1
                case, reason = evaluate(github, item, migration, pattern)
                if case:
                    selected.append(case)
                    cases.append(case)
                    seen_repositories.add(repository)
                else:
                    reasons[reason] += 1
        print(migration["id"], len(selected), "selected of", examined, dict(reasons), flush=True)
        rejections[migration["id"]] = {"examined": examined, "rejected": dict(reasons)}
    document = {
        "schema_version": "1.0",
        "description": "Real merged migrations in public repositories, frozen before scoring. "
        "Ground truth is the set of lines developers removed that use the changed API.",
        "selected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "selection_rules": [
            "Merged public pull requests, in GitHub search result order for the listed queries.",
            f"First {PER_MIGRATION} qualifying per migration, one per repository, "
            f"at most {MAX_CANDIDATES} examined.",
            "Must remove more non-comment lines using the changed API than it adds.",
            "Excludes forks, the library's own organization, repositories over 200 MB, "
            "and paths the analyzer ignores by policy.",
        ],
        "migrations": [
            {key: migration[key] for key in
             ("id", "description", "extensions", "pattern", "queries", "targets")}
            for migration in MIGRATIONS
        ],
        "rejections": rejections,
        "cases": cases,
    }
    if arguments.holdout:
        document["selection_rules"].append(
            "Excludes every repository in the tuning set; selected before any fix was scored on it."
        )
    if frozen:
        document["selected_at"] = frozen["selected_at"]
        document["extended_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        document["round_1_rejections"] = frozen["rejections"]
        document["selection_rules"].append(
            "Round 2 appended cases using exact API-name queries, before any scores were read."
        )
        for migration in document["migrations"]:
            migration["queries"] = [
                *migration["queries"],
                *EXACT_API_QUERIES.get(migration["id"], []),
            ]
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print("wrote", output, "cases", len(cases))


if __name__ == "__main__":
    main()
