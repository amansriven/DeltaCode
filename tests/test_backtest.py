import re
from types import SimpleNamespace

from app.hardening.backtest import CaseResult, changed_lines, score_call_sites, summarize

PATTERN = re.compile(r"\bold_api\(")


def _site(path, start, end=None):
    return SimpleNamespace(path=path, start_line=start, end_line=end or start)


def test_changed_lines_reports_old_file_numbers_across_hunks():
    patch = "\n".join(
        [
            "@@ -10,3 +10,3 @@ def first():",
            " unchanged",
            "-    old_api(1)",
            "+    new_api(1)",
            " unchanged",
            "@@ -40,2 +40,2 @@ def second():",
            "-    keep()",
            "-    old_api(2)",
            "+    keep()",
            "+    new_api(2)",
        ]
    )

    assert changed_lines(patch, PATTERN) == ([11, 41], 0)


def test_changed_lines_ignores_comments_and_counts_added_usage():
    patch = "\n".join(
        [
            "@@ -1,2 +1,3 @@",
            "-# old_api(1) is deprecated",
            "-// old_api(2) is deprecated",
            "+old_api(3)",
            "\\ No newline at end of file",
        ]
    )

    assert changed_lines(patch, PATTERN) == ([], 1)


def test_score_matches_multiline_sites_and_separates_unconfirmed_flags():
    source = {"a.py": ["x = 1", "old_api(", "  1)", "unrelated()", "old_api(9)"]}

    found, confirmed, unconfirmed = score_call_sites(
        {("a.py", 3), ("a.py", 99)},
        [_site("a.py", 2, 3), _site("a.py", 4), _site("a.py", 5)],
        source.__getitem__,
        PATTERN,
    )

    assert found == {("a.py", 3)}
    assert confirmed == 2
    assert unconfirmed == ["a.py:4 unrelated()"]


def test_summary_keeps_failed_cases_visible_and_excludes_them_from_rates():
    results = [
        CaseResult(
            id="a",
            migration="m",
            repository="o/a",
            ground_truth_lines=4,
            found_lines=3,
            flagged_sites=5,
            flagged_confirmed=4,
            after_conclusion="affected",
            analyze_seconds=1.0,
        ),
        CaseResult(id="b", migration="m", repository="o/b", ground_truth_lines=9, error="boom"),
    ]

    summary = summarize(results)

    assert summary["overall"]["line_recall"] == 0.75
    assert summary["overall"]["flag_precision"] == 0.8
    assert summary["overall"]["after_migration_conclusion_affected"] == 1
    assert summary["failed_cases"] == ["b: boom"]
