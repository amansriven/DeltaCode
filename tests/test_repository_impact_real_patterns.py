"""Patterns the real-migration backtest exposed that the synthetic benchmarks did not."""

import pytest

from app.hardening.benchmark import _change

# isort: split
from app.hardening.migration_eval import _analyze_tree
from app.repository_intelligence.analyzer import _specifiers_disjoint

SELENIUM = [
    {
        "kind": "symbol",
        "name": "find_element_by_id",
        "package": "selenium",
        "ecosystem": "pypi",
    }
]


def _impact(root, files, targets):
    for path, content in files.items():
        (root / path).write_text(content)
    change = _change({"targets": targets}, 1)
    return _analyze_tree(root, "example/project", "a" * 40, change, "case-1")[2]


@pytest.mark.parametrize(
    "source",
    [
        "class Page:\n    def open(self):\n        self.driver.find_element_by_id('x')\n",
        "def login(browser):\n    browser.find_element_by_id('x')\n",
        "def step(context):\n    context.browser.find_element_by_id('x')\n",
    ],
)
def test_local_receivers_are_found_at_lower_confidence(tmp_path, source):
    impact = _impact(tmp_path, {"page.py": source}, SELENIUM)

    assert len(impact.call_sites) == 1
    assert impact.call_sites[0].confidence.score < 1
    assert "not statically resolved" in impact.call_sites[0].reason


def test_receiver_resolved_through_the_package_import_stays_certain(tmp_path):
    source = (
        "from selenium import webdriver\n\n"
        "driver = webdriver.Chrome()\n"
        "driver.find_element_by_id('x')\n"
    )

    impact = _impact(tmp_path, {"page.py": source}, SELENIUM)

    assert len(impact.call_sites) == 1
    assert impact.call_sites[0].confidence.score == 1


def test_symbol_imported_from_an_unrelated_module_is_still_rejected(tmp_path):
    source = "from other_sdk import Client\n\nClient().send(1)\n"
    targets = [{"kind": "symbol", "name": "Client.send", "package": "acme-sdk"}]

    assert _impact(tmp_path, {"app.py": source}, targets).call_sites == []


def test_declaring_a_package_is_not_impact_from_a_symbol_change(tmp_path):
    impact = _impact(
        tmp_path,
        {"requirements.txt": "selenium==4.20.0\n", "app.py": "print('no browser here')\n"},
        SELENIUM,
    )

    assert impact.conclusion == "unaffected"
    assert [match.package for match in impact.dependency_matches] == ["selenium"]


def test_a_changed_package_is_impact_from_the_declaration_alone(tmp_path):
    targets = [{"kind": "sdk_package", "name": "selenium", "ecosystem": "pypi"}]

    impact = _impact(
        tmp_path,
        {"requirements.txt": "selenium==4.20.0\n", "app.py": "print('no browser here')\n"},
        targets,
    )

    assert impact.conclusion == "affected"


@pytest.mark.parametrize(
    ("declared", "disjoint"),
    [("openai>=1.0", True), ("openai>=0.27", False), ("openai", False)],
)
def test_declared_ranges_outside_the_affected_range_do_not_match(tmp_path, declared, disjoint):
    targets = [
        {
            "kind": "sdk_package",
            "name": "openai",
            "ecosystem": "pypi",
            "version_scope": {"affected_range": "<1.0", "scheme": "semver"},
        }
    ]

    impact = _impact(
        tmp_path, {"requirements.txt": f"{declared}\n", "app.py": "print('x')\n"}, targets
    )

    assert (impact.conclusion != "affected") is disjoint


@pytest.mark.parametrize(
    ("declared", "affected", "expected"),
    [
        (">=1.0", "<1.0", True),
        ("~=2.1", "<2.0", True),
        (">=2,<3", "<2.0", True),
        (">=0.27", "<1.0", False),
        (">0.5", "<1.0", False),
        ("<1.0", ">=0.9,<2", False),
        ("==0.28.1", "<1.0", False),
        ("not a specifier", "<1.0", False),
        (">=1.0", "also not one", False),
    ],
)
def test_specifier_overlap(declared, affected, expected):
    assert _specifiers_disjoint(declared, affected) is expected
