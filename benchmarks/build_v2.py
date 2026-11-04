"""Generate benchmarks/repository-impact-v2.json.

Cases are labeled by what is true of the code (would this repository break under the
change?), never by what the analyzer currently outputs. Each scenario family is
instantiated against several provider shapes so one lucky pattern cannot carry the score.

Usage: python benchmarks/build_v2.py
"""

import json
from pathlib import Path

PROVIDERS = [
    {"mod": "acme_sdk", "pkg": "acme-sdk", "cls": "Client", "meth": "send",
     "ep": "/v1/messages", "fld": "legacy_token", "npm": "@acme/sdk"},
    {"mod": "payfast", "pkg": "payfast", "cls": "Gateway", "meth": "charge",
     "ep": "/v2/charges/create", "fld": "card_token", "npm": "payfast-node"},
    {"mod": "mapkit", "pkg": "mapkit-py", "cls": "Router", "meth": "route",
     "ep": "/directions/v1", "fld": "avoid_tolls", "npm": "mapkit"},
    {"mod": "storekit", "pkg": "storekit", "cls": "Bucket", "meth": "upload",
     "ep": "/objects/put", "fld": "acl_mode", "npm": "storekit-js"},
]

cases: list[dict] = []


def add(case_id, family, category, affected, files, targets, difficulty="easy"):
    cases.append(
        {
            "id": case_id,
            "family": family,
            "category": category,
            "difficulty": difficulty,
            "affected": affected,
            "files": files,
            "targets": targets,
        }
    )


def symbol(p):
    return [{"kind": "symbol", "name": f"{p['cls']}.{p['meth']}", "package": p["pkg"],
             "ecosystem": "pypi"}]


for p in PROVIDERS:
    m, C, f, ep, fld = p["mod"], p["cls"], p["meth"], p["ep"], p["fld"]
    tag = m
    T = symbol(p)

    def py(family, affected, files, difficulty="easy", targets=None, category="python-symbol"):
        add(f"{family}:{tag}", family, category, affected, files, targets or T, difficulty)

    # ---- Python symbol: positives -------------------------------------------------
    py("py-direct", True, {"app.py": f"from {m} import {C}\n\nclient = {C}()\nclient.{f}('hello')\n"})
    py("py-module-alias", True, {"app.py": f"import {m} as sdk\n\nsdk.{C}().{f}(1)\n"})
    py("py-from-alias", True,
       {"app.py": f"from {m} import {C} as Api\n\napi = Api()\napi.{f}(2)\n"})
    py("py-assign-chain", True,
       {"app.py": f"import {m}\n\nFactory = {m}.{C}\nobj = Factory()\nobj.{f}(3)\n"})
    py("py-nested-loop", True, {"svc/jobs.py": (
        f"import {m}\n\n\ndef run(items):\n    for item in items:\n"
        f"        {m}.{C}().{f}(item)\n")})
    py("py-multiline-kwargs", True, {"app.py": (
        f"from {m} import {C}\n\nc = {C}(api_key='k')\nc.{f}(\n    payload={{'a': 1}},\n"
        "    timeout=5,\n)\n")})
    py("py-in-tests", True, {"tests/test_flow.py": (
        f"from {m} import {C}\n\n\ndef test_it():\n    {C}().{f}('x')\n")})
    py("py-deep-package", True, {
        "pkg/__init__.py": "",
        "pkg/core/__init__.py": "",
        "pkg/core/util.py": "def add(a, b):\n    return a + b\n",
        "pkg/integrations/notify.py": f"from {m} import {C}\n\n\ndef go():\n    return {C}().{f}()\n",
    })
    py("py-conditional-import", True, {"app.py": (
        f"try:\n    from {m} import {C}\nexcept ImportError:\n    {C} = None\n\n{C}().{f}(1)\n")})
    py("py-function-local-import", True, {"app.py": (
        f"def go():\n    from {m} import {C}\n    return {C}().{f}()\n")})
    py("py-star-import", True, {"app.py": f"from {m} import *\n\n{C}().{f}()\n"})

    # ---- Python symbol: hard positives (real breakage, static analysis struggles) --
    py("py-attribute-reference", True, {"app.py": (
        f"from {m} import {C}\n\nclient = {C}()\nhandler = client.{f}\nhandler('x')\n")}, "hard")
    py("py-getattr-dynamic", True, {"app.py": (
        f"from {m} import {C}\n\nclient = {C}()\ngetattr(client, '{f}')('x')\n")}, "hard")
    py("py-self-attribute", True, {"app.py": (
        f"from {m} import {C}\n\n\nclass Svc:\n    def __init__(self):\n"
        f"        self.client = {C}()\n\n    def go(self):\n        self.client.{f}('x')\n")}, "hard")
    py("py-param-annotation", True, {"app.py": (
        f"from {m} import {C}\n\n\ndef go(c: {C}):\n    c.{f}('x')\n")}, "hard")
    py("py-with-statement", True, {"app.py": (
        f"from {m} import {C}\n\nwith {C}() as c:\n    c.{f}('x')\n")}, "hard")
    py("py-factory-wrapper", True, {"app.py": (
        f"from {m} import {C}\n\n\ndef make():\n    return {C}()\n\n\nmake().{f}('x')\n")}, "hard")

    # ---- Python symbol: negatives -------------------------------------------------
    py("py-clean", False, {"app.py": "print('safe')\n"})
    py("py-comment-mention", False, {"app.py": f"# TODO: migrate {C}.{f} soon\nprint('ok')\n"})
    py("py-docstring-mention", False, {"app.py": (
        f'"""Notes: {C}.{f} is deprecated upstream."""\n\n\ndef noop():\n    return 1\n')})
    py("py-unrelated-class", False, {"app.py": (
        f"class Logger:\n    def {f}(self, x):\n        return x\n\n\nLogger().{f}('x')\n")})
    py("py-method-definition-only", False, {"app.py": (
        f"class Own{C}:\n    def {f}(self):\n        return 1\n")})
    py("py-markdown-mention", False, {
        "README.md": f"Migrate away from `{C}.{f}`.\n", "app.py": "x = 1\n"})
    py("py-vendored-site-packages", False, {
        "app.py": "x = 1\n",
        ".venv/lib/python3.12/site-packages/thirdparty/x.py": (
            f"from {m} import {C}\n{C}().{f}()\n")}, "hard")
    py("py-js-neighbor-clean", False, {
        "app.py": "x = 1\n", "web/index.js": "export const a = 1;\n"})
    py("py-many-clean-files", False, {
        f"mod{i}.py": f"def f{i}(x):\n    return x + {i}\n" for i in range(40)})
    py("py-many-files-one-hit", True, {
        **{f"mod{i}.py": f"def f{i}(x):\n    return x + {i}\n" for i in range(40)},
        "hit.py": f"from {m} import {C}\n\n{C}().{f}()\n"})
    # Hard negatives: the target names collide but the code does not use the changed SDK.
    py("py-other-sdk-same-name", False, {"app.py": (
        f"import other_vendor\n\nother_vendor.{C}().{f}('x')\n")}, "hard")
    py("py-local-class-shadow", False, {"app.py": (
        f"class {C}:\n    def {f}(self, x):\n        return x\n\n\n{C}().{f}('x')\n")}, "hard")

    # ---- Endpoints ----------------------------------------------------------------
    E = [{"kind": "endpoint", "name": ep, "operation": "POST"}]

    def en(family, affected, files, difficulty="easy"):
        add(f"{family}:{tag}", family, "endpoint", affected, files, E, difficulty)

    en("ep-python-requests", True, {"c.py": f"import requests\n\nrequests.post('{ep}', json={{}})\n"})
    en("ep-python-httpx-client", True, {"c.py": (
        f"import httpx\n\nclient = httpx.Client()\nclient.post(\"{ep}\", json={{}})\n")})
    en("ep-js-fetch", True, {"src/c.js": f"fetch('{ep}', {{ method: 'POST' }});\n"})
    en("ep-ts-axios", True, {"src/c.ts": f"await axios.post(\"{ep}\", body);\n"})
    en("ep-python-constant", True, {"c.py": (
        f"import requests\n\nURL = '{ep}'\nrequests.post(URL)\n")}, "hard")
    en("ep-python-concat", True, {"c.py": (
        f"import requests\n\nBASE = 'https://api.example.com'\nrequests.post(BASE + '{ep}')\n")},
       "hard")
    en("ep-js-template-literal", True, {"src/c.js": f"fetch(`{ep}`, {{ method: 'POST' }});\n"},
       "hard")
    en("ep-python-full-url", True, {"c.py": (
        f"import requests\n\nrequests.post('https://api.example.com{ep}')\n")}, "hard")
    en("ep-python-comment", False, {"c.py": f"# legacy path {ep} was removed\nx = 1\n"})
    en("ep-python-near-miss", False, {"c.py": (
        f"import requests\n\nrequests.post('{ep}/refund-v2')\n")})
    en("ep-python-other-version", False, {"c.py": (
        f"import requests\n\nrequests.post('/v9{ep}')\n")})
    en("ep-js-near-miss", False, {"src/c.js": f"fetch('{ep}-new');\n"})
    en("ep-js-comment-quoted", False, {"src/c.js": (
        f"// old call used the '{ep}' endpoint before the rewrite\nexport const x = 1;\n")}, "hard")
    en("ep-clean-python", False, {"c.py": "print('nothing here')\n"})

    # ---- Fields -------------------------------------------------------------------
    F = [{"kind": "field", "name": fld}]

    def fi(family, affected, files, difficulty="easy"):
        add(f"{family}:{tag}", family, "field", affected, files, F, difficulty)

    fi("fld-python-kwarg", True, {"c.py": f"create({fld}='v')\n"})
    fi("fld-python-dict-key", True, {"c.py": f"payload = {{'{fld}': 'v'}}\nsend(payload)\n"})
    fi("fld-js-object-key", True, {"src/c.js": f"send({{ {fld}: 'v' }});\n"})
    fi("fld-js-quoted-key", True, {"src/c.ts": f"send({{ '{fld}': 'v' }});\n"})
    fi("fld-python-attribute-read", True, {"c.py": f"value = response.{fld}\n"}, "hard")
    fi("fld-js-property-read", True, {"src/c.js": f"const v = response.{fld};\n"}, "hard")
    fi("fld-python-subscript", True, {"c.py": f"value = data['{fld}']\n"}, "hard")
    fi("fld-python-near-name", False, {"c.py": f"create({fld}_id='v')\n"})
    fi("fld-python-comment", False, {"c.py": f"# the {fld} field is going away\nx = 1\n"})
    fi("fld-js-near-name", False, {"src/c.js": f"send({{ {fld}_v2: 'v' }});\n"})
    fi("fld-clean", False, {"c.py": "print('ok')\n"})

    # ---- Dependencies -------------------------------------------------------------
    pkg = p["pkg"]
    D = [{"kind": "sdk_package", "name": pkg, "package": pkg, "ecosystem": "pypi",
          "version_scope": {"affected_range": "<3.0", "scheme": "semver"}}]
    N = [{"kind": "sdk_package", "name": p["npm"], "package": p["npm"], "ecosystem": "npm",
          "version_scope": {"affected_range": ">=5.0.0", "scheme": "semver"}}]

    def de(family, affected, files, targets, difficulty="easy"):
        add(f"{family}:{tag}", family, "dependency", affected, files, targets, difficulty)

    de("dep-requirements-pinned-in-range", True, {"requirements.txt": f"{pkg}==2.5.0\n"}, D)
    de("dep-requirements-underscore-name", True,
       {"requirements.txt": f"{pkg.replace('-', '_')}==2.5.0\n"}, D)
    de("dep-requirements-mixed-case", True, {"requirements.txt": f"{pkg.upper()}==2.5.0\n"}, D)
    de("dep-requirements-extras", True, {"requirements.txt": f"{pkg}[async]==2.1.0\n"}, D)
    de("dep-requirements-unpinned", True, {"requirements.txt": f"{pkg}\nrequests\n"}, D)
    de("dep-requirements-dev-file", True, {"requirements-dev.txt": f"{pkg}==2.0.0\n"}, D)
    de("dep-requirements-upper-boundary-inside", True,
       {"requirements.txt": f"{pkg}==2.9.9\n"}, D)
    de("dep-pyproject-range", True, {"pyproject.toml": (
        f"[project]\nname='x'\nversion='0.1'\ndependencies=['{pkg}>=2,<3']\n")}, D)
    de("dep-requirements-out-of-range", False, {"requirements.txt": f"{pkg}==3.1.0\n"}, D)
    de("dep-requirements-boundary-excluded", False, {"requirements.txt": f"{pkg}==3.0.0\n"}, D)
    de("dep-requirements-different-package", False,
       {"requirements.txt": f"{pkg}-extras==2.5.0\n"}, D)
    de("dep-requirements-commented", False, {"requirements.txt": f"# {pkg}==2.5.0\nrequests\n"}, D)
    de("dep-requirements-prefix-package", False, {"requirements.txt": f"x{pkg}==2.5.0\n"}, D)
    de("dep-npm-dependencies", True, {
        "package.json": json.dumps({"dependencies": {p["npm"]: "^5.1.0"}}),
        "src/i.ts": "export const x = 1;\n"}, N)
    de("dep-npm-dev-dependencies", True, {
        "package.json": json.dumps({"devDependencies": {p["npm"]: "^5.1.0"}})}, N)
    de("dep-npm-lockfile", True, {
        "package-lock.json": json.dumps({"lockfileVersion": 3, "packages": {
            "": {"name": "x"},
            f"node_modules/{p['npm']}": {"version": "5.2.0"}}})}, N)
    de("dep-npm-not-listed", False, {
        "package.json": json.dumps({"dependencies": {"left-pad": "^1.0.0"}}),
        "src/i.ts": "export const x = 1;\n"}, N)
    de("dep-cross-ecosystem", False, {
        "package.json": json.dumps({"dependencies": {pkg: "^2.0.0"}}),
        "src/i.js": "export const x = 1;\n"}, D, "hard")

    # ---- JS / TS symbol -----------------------------------------------------------
    JS = [{"kind": "symbol", "name": f"{C}.{f}", "package": p["npm"], "ecosystem": "npm"}]

    def js(family, affected, files, difficulty="easy"):
        add(f"{family}:{tag}", family, "js-symbol", affected, files, JS, difficulty)

    cl = C[0].lower() + C[1:]
    js("js-direct-call", True, {"src/a.ts": f"const {cl} = new {C}();\nawait {cl}.{f}({{}});\n"})
    js("js-chained-call", True, {"src/a.js": f"new {C}().{f}(1);\n"})
    js("js-jsx-handler", True, {"src/a.tsx": (
        f"export function B() {{\n  const onClick = () => {cl}.{f}();\n  return null;\n}}\n")})
    js("js-aliased-import", True, {"src/a.ts": (
        f"import {{ {C} as Api }} from '{p['npm']}';\nconst a = new Api();\na.{f}(1);\n")},
       "hard")
    js("js-destructured-method", True, {"src/a.ts": (
        f"const {{ {f} }} = new {C}();\n{f}(1);\n")}, "hard")
    js("js-computed-property", True, {"src/a.ts": (
        f"const c = new {C}();\nc['{f}'](1);\n")}, "hard")
    js("js-clean", False, {"src/a.ts": "export const value = 1;\n"})
    js("js-comment-only", False, {"src/a.ts": f"// {cl}.{f}( was removed upstream\nexport const x = 1;\n"},
       "hard")
    js("js-string-only", False, {"src/a.ts": f"export const doc = 'see {cl}.{f}(x) docs';\n"},
       "hard")
    js("js-other-receiver", False, {"src/a.ts": f"logger.{f}('x');\nqueue.{f}('y');\n"})

    # ---- Unsupported / other languages -------------------------------------------
    go_target = [{"kind": "symbol", "name": f"{C}.{f}"}]
    add(f"lang-go-clean:{tag}", "lang-go-clean", "unsupported-language", False,
        {"main.go": "package main\nfunc main() {}\n"}, go_target)
    add(f"lang-go-affected:{tag}", "lang-go-affected", "unsupported-language", True,
        {"main.go": f"package main\nfunc main() {{ c := {m}.New{C}(); c.{f.capitalize()}() }}\n"},
        go_target, "hard")
    add(f"lang-java-affected:{tag}", "lang-java-affected", "unsupported-language", True,
        {"src/Main.java": f"class Main {{ void go() {{ new {C}().{f}(); }} }}\n"},
        go_target, "hard")
    add(f"empty-repo:{tag}", "empty-repo", "unsupported-language", False, {"README.md": "# hi\n"},
        go_target)

cases.sort(key=lambda case: case["id"])
families = sorted({case["family"] for case in cases})
document = {
    "schema_version": 2,
    "description": (
        "Provider-neutral repository impact cases labeled by ground truth, not by analyzer "
        "output. Includes hard positives (real breakage that static analysis can miss) and "
        "hard negatives (name collisions, comments, strings, vendored code)."
    ),
    "families": len(families),
    "cases": cases,
}
out = Path(__file__).with_name("repository-impact-v2.json")
out.write_text(json.dumps(document, indent=1, sort_keys=False) + "\n", encoding="utf-8")
print(f"wrote {len(cases)} cases across {len(families)} scenario families -> {out}")
