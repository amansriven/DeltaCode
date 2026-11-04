"""A free stand-in for the OpenAI endpoint, so a run can be rehearsed before it is paid for.

Everything except the network call is real: the client, its budget and limits, the strict schema,
parsing, the sandbox, the checks, and the repair loop. Answers are mechanical migrations, with the
kinds of mistakes real runs made, chosen by a hash of the repository so a run can be repeated.
Costs come from size estimates, and scores say nothing about how good the model is.
"""

import hashlib
import json
import re

import httpx

CHARS_PER_INPUT_TOKEN = 3.5
CHARS_PER_OUTPUT_TOKEN = 3.3
# Plans and summaries a model writes that a mechanical answer doesn't.
EXTRA_OUTPUT_TOKENS = 300
NUMPY_ALIAS = re.compile(r"\bnp\.(float|int|bool|object|str|complex)\b(?![\w])")
SELENIUM_LOCATOR = re.compile(
    r"\.find_element(s?)_by_(id|name|xpath|css_selector|class_name|tag_name|link_text"
    r"|partial_link_text)\("
)
SELENIUM_IMPORT = "from selenium.webdriver.common.by import By"
OPENAI_CALLS = {
    "ChatCompletion.create(": "chat.completions.create(",
    "Completion.create(": "completions.create(",
    "Embedding.create(": "embeddings.create(",
}
PYDANTIC_VALIDATOR = re.compile(r"^(\s*)@validator\((.*?)\)\s*$")
PREVIOUS_ATTEMPT = "A previous attempt at this patch failed"


def _first_line_index(lines: list[str], prefix: tuple[str, ...]) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)


def _line_replacements(content: str, transform) -> list[dict]:
    replacements = []
    seen = set()
    for line in content.split("\n"):
        new = transform(line)
        if new != line and line not in seen:
            seen.add(line)
            old = line.rstrip("\r")
            replacements.append(
                {
                    "old_text": old,
                    "new_text": new.rstrip("\r"),
                    "replace_all": content.split("\n").count(line) > 1,
                }
            )
    return replacements


def _add_after_import(content: str, added: str) -> list[dict]:
    lines = content.split("\n")
    index = _first_line_index(lines, ("import ", "from "))
    if index is None:
        return []
    anchor = lines[index].rstrip("\r")
    return [{"old_text": anchor, "new_text": f"{added}\n{anchor}", "replace_all": False}]


def _numpy(content: str, **_faults) -> list[dict]:
    return _line_replacements(content, lambda line: NUMPY_ALIAS.sub(r"\1", line))


def _selenium(content: str, **_faults) -> list[dict]:
    def convert(line: str) -> str:
        return SELENIUM_LOCATOR.sub(
            lambda m: f".find_element{m.group(1)}(By.{m.group(2).upper()}, ", line
        )

    replacements = _line_replacements(content, convert)
    if replacements and SELENIUM_IMPORT not in content:
        replacements += _add_after_import(content, SELENIUM_IMPORT)
    return replacements


def _openai(content: str, repairing: bool = False, **_faults) -> list[dict]:
    """The first answer builds its client at import, the mistake real runs made most often."""
    prefix = "_client()" if repairing else "client"

    def convert(line: str) -> str:
        for old, new in OPENAI_CALLS.items():
            line = line.replace(f"openai.{old}", f"{prefix}.{new}")
        return re.sub(
            r"\[['\"]choices['\"]\]\[0\]\[['\"]message['\"]\]\[['\"]content['\"]\]",
            ".choices[0].message.content",
            line,
        )

    replacements = _line_replacements(content, convert)
    if replacements:
        setup = (
            "from openai import OpenAI\n\n\ndef _client():\n    return OpenAI()\n\n"
            if repairing
            else "from openai import OpenAI\n\nclient = OpenAI()\n"
        )
        replacements += _add_after_import(content, setup)
    return replacements


def _pydantic(content: str, **_faults) -> list[dict]:
    def convert(line: str) -> str:
        match = PYDANTIC_VALIDATOR.match(line.rstrip("\r"))
        if not match:
            return line
        indent, arguments = match.groups()
        arguments = arguments.replace("pre=True", "mode='before'")
        arguments = re.sub(r",\s*always=True", "", arguments)
        return f"{indent}@field_validator({arguments})\n{indent}@classmethod"

    replacements = _line_replacements(content, convert)
    if replacements:
        replacements += _add_after_import(content, "from pydantic import field_validator")
    return replacements


def _react_call(content: str, start: int) -> tuple[int, list[str]] | None:
    depth = 0
    arguments = []
    piece = start
    for index in range(start, len(content)):
        char = content[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                arguments.append(content[piece:index])
                return index, arguments
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append(content[piece:index])
            piece = index + 1
    return None


def _react(content: str, **_faults) -> list[dict]:
    replacements = []
    for match in re.finditer(r"ReactDOM\.(render|hydrate)\(", content):
        parsed = _react_call(content, match.end())
        if parsed is None:
            continue
        end, arguments = parsed
        arguments = [item.strip() for item in arguments if item.strip()]
        if len(arguments) < 2:
            continue
        element, container = arguments[0], arguments[1]
        new_call = (
            f"createRoot({container}).render({element})"
            if match.group(1) == "render"
            else f"hydrateRoot({container}, {element})"
        )
        replacements.append(
            {"old_text": content[match.start() : end + 1], "new_text": new_call,
             "replace_all": False}
        )
    if replacements:
        replacements += _add_after_import(
            content, "import { createRoot, hydrateRoot } from 'react-dom/client';"
        )
    return replacements


TRANSFORMS = {
    "numpy-1.24-aliases": _numpy,
    "selenium-4-locators": _selenium,
    "openai-python-v1": _openai,
    "pydantic-v2-validators": _pydantic,
    "react-18-root": _react,
}


def _inject_fault(fault: str | None, replacements: list[dict]) -> list[dict]:
    if not replacements:
        return replacements
    first = dict(replacements[0])
    if fault == "trailing_whitespace":
        first["old_text"] = first["old_text"].rstrip() + "   "
    elif fault == "retyped_quotes":
        # Text the file doesn't contain: costs a retry, as when a model tidies what it copies.
        first["old_text"] = first["old_text"] + " # retyped"
    elif fault == "broad_alias" and "np." in first["old_text"]:
        alias = NUMPY_ALIAS.search(first["old_text"])
        if alias:
            return [{"old_text": alias.group(0), "new_text": alias.group(1), "replace_all": True}]
    return [first, *replacements[1:]]


class SimulatedResponsesAPI:
    def __init__(self, dataset: dict) -> None:
        self.migration_of = {case["repository"]: case["migration"] for case in dataset["cases"]}
        self.requests: list[dict] = []

    def _fault(self, repository: str) -> str | None:
        bucket = int(hashlib.sha256(repository.encode()).hexdigest(), 16) % 20
        if bucket < 3:
            return "trailing_whitespace"
        if bucket < 5:
            return "retyped_quotes"
        if bucket == 5:
            return "disallowed_command"
        if bucket < 8:
            return "broad_alias"
        return None

    def _proposal(self, context: dict) -> dict:
        repository = context["repository"]["full_name"]
        migration = self.migration_of[repository]
        repairing = PREVIOUS_ATTEMPT in (context.get("developer_instructions") or "")
        fault = None if repairing else self._fault(repository)
        sites_by_path: dict[str, list[str]] = {}
        for site in context["impact"]["call_sites"]:
            sites_by_path.setdefault(site["path"], []).append(site["id"])
        steps = []
        edits = []
        files = {item["path"]: item for item in context["files"]}
        for index, path in enumerate(sorted(sites_by_path), start=1):
            source = files.get(path)
            if source is None:
                continue
            replacements = TRANSFORMS[migration](source["content"], repairing=repairing)
            if not replacements:
                continue
            step = f"step-{index}"
            steps.append(
                {
                    "id": step,
                    "description": f"Migrate the call sites in {path}.",
                    "call_site_ids": sites_by_path[path],
                    "expected_paths": [path],
                }
            )
            edits.append(
                {
                    "path": path,
                    "replacements": _inject_fault(fault, replacements),
                    "plan_step_ids": [step],
                }
            )
        if not edits:
            # Mechanical rules cover common forms only, so note the gap rather than guess.
            path = sorted(sites_by_path)[0]
            content = files[path]["content"]
            line = next(
                (
                    text.rstrip("\r")
                    for text in content.split("\n")
                    if text.strip() and content.count(text.rstrip("\r")) == 1
                ),
                content,
            )
            steps = [
                {
                    "id": "step-1",
                    "description": "No mechanical migration applies.",
                    "call_site_ids": sites_by_path[path],
                    "expected_paths": [path],
                }
            ]
            edits = [
                {
                    "path": path,
                    "replacements": [{"old_text": line, "new_text": line, "replace_all": False}],
                    "plan_step_ids": ["step-1"],
                }
            ]
        command = {"id": "guess", "kind": "unit_test", "argv": ["pytest", "tests"]}
        if fault == "disallowed_command":
            command = {"id": "guess", "kind": "unit_test", "argv": ["./run_tests.sh"]}
        return {
            "plan": {
                "summary": "Migrate the affected call sites.",
                "steps": steps,
                "verification_strategy": ["Run the repository's tests."],
                "assumptions": [],
                "unresolved": [],
            },
            "patch": {
                "summary": "Migrate the affected call sites.",
                "edits": edits,
                "tests": [],
                "verification_commands": [command],
                "unresolved": [],
            },
        }

    def _review(self) -> dict:
        return {
            "review": {
                "summary": "Simulated review.",
                "findings": [],
                "provenance": "model_inferred",
                "model": {"id": "simulated", "version": "1"},
            },
            "recommendation": {
                "action": "revise",
                "rationale": "Simulated review.",
                "confidence": {"score": 0.5, "basis": "inferred"},
                "unresolved": [],
            },
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["text"]["format"]["name"]
        user_input = body["input"][1]["content"]
        if name == "delta_code_migration_proposal":
            answer = self._proposal(json.loads(user_input))
            overhead = EXTRA_OUTPUT_TOKENS
        else:
            answer = self._review()
            # Reviews last ran about this long. The canned text here is far shorter.
            overhead = 800
        text = json.dumps(answer, ensure_ascii=False)
        output_tokens = int(len(text) / CHARS_PER_OUTPUT_TOKEN) + overhead
        input_tokens = int(len(request.content) / CHARS_PER_INPUT_TOKEN)
        self.requests.append(
            {"name": name, "bytes": len(request.content), "output_tokens": output_tokens}
        )
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        if output_tokens > body["max_output_tokens"]:
            return httpx.Response(200, json={"status": "incomplete", "usage": usage})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": text}]}
                ],
                "usage": usage,
            },
        )
