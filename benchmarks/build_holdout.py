"""Generate benchmarks/repository-impact-holdout.json.

A held-out set written after the v2 analyzer fixes, using patterns the fixes were not
designed around. Labels are ground truth about the code, not analyzer output.

Usage: python benchmarks/build_holdout.py
"""

import json
from pathlib import Path

PROVIDERS = [
    {"mod": "acme_sdk", "pkg": "acme-sdk", "cls": "Client", "meth": "send",
     "ep": "/v1/messages", "fld": "legacy_token", "npm": "@acme/sdk"},
    {"mod": "payfast", "pkg": "payfast", "cls": "Gateway", "meth": "charge",
     "ep": "/v2/charges/create", "fld": "card_token", "npm": "payfast-node"},
]

cases: list[dict] = []


def add(family, tag, category, affected, files, targets, difficulty="easy"):
    cases.append({"id": f"{family}:{tag}", "family": family, "category": category,
                  "difficulty": difficulty, "affected": affected, "files": files,
                  "targets": targets})


for p in PROVIDERS:
    m, C, f, ep, fld, pkg = p["mod"], p["cls"], p["meth"], p["ep"], p["fld"], p["pkg"]
    sym = [{"kind": "symbol", "name": f"{C}.{f}", "package": pkg, "ecosystem": "pypi"}]
    jsym = [{"kind": "symbol", "name": f"{C}.{f}", "package": p["npm"], "ecosystem": "npm"}]
    E = [{"kind": "endpoint", "name": ep}]
    F = [{"kind": "field", "name": fld}]
    D = [{"kind": "sdk_package", "name": pkg, "package": pkg, "ecosystem": "pypi",
          "version_scope": {"affected_range": "<3.0", "scheme": "semver"}}]
    t = m

    def py(family, affected, files, difficulty="easy"):
        add(family, t, "python-symbol", affected, files, sym, difficulty)

    def js(family, affected, files, difficulty="easy"):
        add(family, t, "js-symbol", affected, files, jsym, difficulty)

    # Python symbol positives
    py("h-py-async-with", True, {"a.py": (
        f"from {m} import {C}\n\n\nasync def go():\n    async with {C}() as c:\n"
        f"        await c.{f}(1)\n")})
    py("h-py-lambda", True, {"a.py": f"from {m} import {C}\n\ncb = lambda: {C}().{f}(1)\n"})
    py("h-py-comprehension", True, {"a.py": (
        f"from {m} import {C}\n\nout = [{C}().{f}(i) for i in range(3)]\n")})
    py("h-py-alias-of-alias", True, {"a.py": (
        f"from {m} import {C}\n\nc = {C}()\nd = c\nd.{f}(1)\n")})
    py("h-py-unbound-call", True, {"a.py": f"import {m}\n\n{m}.{C}.{f}(None, 1)\n"})
    py("h-py-return-annotation", True, {"a.py": (
        f"from {m} import {C}\n\n\ndef get() -> {C}:\n    return build()\n\n\nget().{f}(1)\n")})
    py("h-py-partial-reference", True, {"a.py": (
        f"import functools\nfrom {m} import {C}\n\nfn = functools.partial({C}().{f}, 1)\n")})
    py("h-py-tuple-unpack", True, {"a.py": (
        f"from {m} import {C}\n\na, b = {C}(), {C}()\na.{f}(1)\n")}, "hard")
    py("h-py-class-attribute", True, {"a.py": (
        f"from {m} import {C}\n\n\nclass S:\n    client = {C}()\n\n\nS.client.{f}(1)\n")}, "hard")
    py("h-py-higher-order", True, {"a.py": (
        f"from {m} import {C}\n\nc = {C}()\nlist(map(c.{f}, [1, 2]))\n")})
    # Python symbol negatives
    py("h-py-string-literal", False, {"a.py": f"msg = '{C}.{f}() is gone'\nprint(msg)\n"})
    py("h-py-class-reference-only", False, {"a.py": (
        f"from {m} import {C}\n\n\ndef is_client(x):\n    return isinstance(x, {C})\n")})
    py("h-py-other-object-same-method", False, {"a.py": (
        f"import queue\n\nq = queue.Queue()\nq.{f}(1)\n")})
    py("h-py-import-unused", False, {"a.py": f"from {m} import {C}\n\nx = 1\n"})
    py("h-py-test-fixture-string", False, {"tests/t.py": (
        f"def test_x():\n    assert 'calls {C}.{f}' in 'calls {C}.{f}'\n")})
    py("h-py-different-module-same-class", False, {"a.py": (
        f"from unrelated_lib import {C}\n\n{C}().{f}(1)\n")}, "hard")

    # JS symbol
    cl = C[0].lower() + C[1:]
    js("h-js-optional-chaining", True, {"a.ts": f"const {cl} = new {C}();\n{cl}?.{f}(1);\n"})
    js("h-js-newline-before-paren", True, {"a.ts": (
        f"const {cl} = new {C}();\n{cl}.{f}\n  (1);\n")})
    js("h-js-this-member", True, {"a.ts": (
        f"class S {{\n  api = new {C}();\n  go() {{ this.api.{f}(1); }}\n}}\n")})
    js("h-js-awaited-factory", True, {"a.ts": (
        f"const v = (await getClient()).{f}(1);\n")}, "hard")
    js("h-js-block-comment", False, {"a.ts": (
        f"/* {cl}.{f}(x) was removed */\nexport const a = 1;\n")})
    js("h-js-similar-method", False, {"a.ts": f"const {cl} = 1;\n{cl}.{f}All(1);\n"})
    js("h-js-similar-receiver", False, {"a.ts": f"my{cl}.{f}(1);\n"})

    # Endpoints
    add("h-ep-fstring", t, "endpoint", True, {"a.py": (
        f"import requests\n\nBASE = 'https://x.test'\nrequests.get(f'{{BASE}}{ep}')\n")}, E)
    add("h-ep-js-query-string", t, "endpoint", True, {"a.js": f"fetch('{ep}?x=1');\n"}, E, "hard")
    add("h-ep-substring-path", t, "endpoint", False, {"a.py": (
        f"import requests\n\nrequests.get('/prefix{ep}')\n")}, E)
    add("h-ep-js-block-comment", t, "endpoint", False, {"a.js": (
        f"/* GET '{ep}' removed */\nexport const x = 1;\n")}, E)

    # Fields
    add("h-fld-getattr", t, "field", True, {"a.py": f"v = getattr(resp, '{fld}')\n"}, F, "hard")
    add("h-fld-store", t, "field", True, {"a.py": f"req.{fld} = 'x'\n"}, F, "hard")
    add("h-fld-js-shorthand", t, "field", True, {"a.js": (
        f"const {fld} = 1;\nsend({{ {fld} }});\n")}, F, "hard")
    add("h-fld-string-value-only", t, "field", False, {"a.py": f"label = '{fld}'\nprint(label)\n"},
        F)

    # Dependencies
    add("h-dep-poetry", t, "dependency", True, {"pyproject.toml": (
        f"[tool.poetry.dependencies]\npython = '^3.12'\n{pkg} = '^2.1'\n")}, D)
    add("h-dep-direct-reference", t, "dependency", True, {"requirements.txt": (
        f"{pkg} @ https://example.test/{pkg}-2.0.0.tar.gz\n")}, D, "hard")
    add("h-dep-env-marker", t, "dependency", True, {"requirements.txt": (
        f"{pkg}==2.4.0 ; python_version >= '3.9'\n")}, D)
    add("h-dep-setup-py", t, "dependency", True, {"setup.py": (
        f"from setuptools import setup\nsetup(install_requires=['{pkg}==2.0.0'])\n")}, D, "hard")
    add("h-dep-nested-requirements-file", t, "dependency", True, {
        "requirements.txt": "-r base.txt\n", "base.txt": f"{pkg}==2.0.0\n"}, D, "hard")
    add("h-dep-lookalike", t, "dependency", False, {"requirements.txt": f"{pkg}-ng==2.0.0\n"}, D)
    add("h-dep-fixed-version", t, "dependency", False, {"requirements.txt": f"{pkg}==4.2.1\n"}, D)

cases.sort(key=lambda case: case["id"])
document = {
    "schema_version": 2,
    "description": "Held-out impact cases written after the v2 analyzer fixes.",
    "families": len({case["family"] for case in cases}),
    "cases": cases,
}
out = Path(__file__).with_name("repository-impact-holdout.json")
out.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
print(f"wrote {len(cases)} cases -> {out}")
