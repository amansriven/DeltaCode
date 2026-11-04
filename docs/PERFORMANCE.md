# Performance

Measured results for Delta Code's repository impact analysis, recorded on 2026-11-04. These
results describe the code in this repository, not the deployed service.

## Summary

| Measure | Result | Evidence |
| --- | --- | --- |
| Changed call sites found, held-out real migrations | **95.5%** (385 of 403 lines, 50 repositories) | [Results](../benchmarks/backtest-holdout-results.json) |
| Repositories with every changed line found | 45 of 50 | Same run |
| Migrated repositories marked affected with no call site left | 0 of 50 | Same run |
| Analysis time per repository | 55 ms median, 3.5 s maximum (up to 1,911 files) | Same run, on a developer laptop |
| Automated tests | 246 passing, plus 3 run only against PostgreSQL | See [Tests](#tests) |

End-to-end migration generation has not been measured yet. See
[What has not been measured](#what-has-not-been-measured).

## Real-migration backtest

The backtest asks one question: when developers really migrated off a breaking API, did the
analyzer find the lines they had to change?

1. **Select.** A script searches merged public pull requests in GitHub result order and keeps the
   first 10 that qualify for each migration, one per repository. A pull request qualifies if it
   removes more non-comment lines using the changed API than it adds. Forks, the library's own
   organization, and repositories over 200 MB are excluded.
2. **Freeze.** The commits and changed lines are saved before the analyzer runs.
3. **Score.** The analyzer runs on the commit before the migration. A line counts as found only if
   a reported call site covers that exact file and line. Repository-level scores would mean little
   here, because declaring a package can make a repository look affected.

Repository code is only parsed, never executed.

### Held-out set

These 50 repositories share none with the tuning set, were selected before they were scored, and
no fix was made against them. Scoring ran once. The analyzer's SHA-256 at that point began
`d108a7f3ced24cc8`.

| Migration | Repositories | Lines found | Recall |
| --- | --- | --- | --- |
| OpenAI Python 1.0 module-level API | 10 | 13 / 13 | 100% |
| React 18 root API | 10 | 15 / 15 | 100% |
| NumPy 1.24 removed aliases | 10 | 91 / 93 | 97.8% |
| Pydantic 2 validators | 10 | 227 / 235 | 96.6% |
| Selenium 4 locators | 10 | 39 / 47 | 83.0% |
| **Total** | **50** | **385 / 403** | **95.5%** |

The analyzer flagged 482 sites. All of them contain the changed API: 451 matched the migration's
text pattern automatically, and the other 31 are direct `validator()` or `root_validator()` calls
that the decorator-only pattern does not cover. That check confirms the API is present. It does
not prove each site needs changing.

### Tuning set

These 54 repositories exposed the bugs listed below and were used to fix them, so this score is
expected to run high. Use the held-out number when quoting performance.

| Stage | Lines found | Recall |
| --- | --- | --- |
| Before the fixes below | 273 / 394 | 69.3% |
| After the fixes below | 365 / 394 | 92.6% |

OpenAI Node v4 appears only in this set, because selection found no further qualifying repositories
for the held-out set. The analyzer found 2 of its 9 lines, so Node SDK coverage is not claimed.

### Bugs the backtest exposed

The synthetic benchmarks caught none of these.

- **Local receivers were dropped.** A check meant to reject symbols from unrelated modules also
  rejected `self.driver.find_element_by_id`. Selenium recall on the tuning set was 40% before the
  fix and 97% after it. These matches now report lower confidence, because the receiver type is
  not resolved.
- **Declaring a package counted as impact.** A repository that declared a package was marked
  affected by any symbol change in it. 40 of 54 already-migrated repositories were flagged with no
  call site left, and 38 still were when the change declared a version range. The count is now 0.
- **Lockfiles blocked generation.** Generated lockfiles of 650 KB or more were sent as model context
  and exceeded the 64 KB per-file limit. They are now left out, and the manifest is still sent.

### Limits of this evidence

- Repositories were found by searching for the old API, so they lean toward direct, textual use.
- Every repository really uses the API, so name collisions are not tested. Matching on a local
  receiver could over-match a generic method name.
- Pydantic accounts for 58% of the held-out lines. The mean across migrations is also 95.5%.
- Analysis is semantic for Python and lexical for JavaScript and TypeScript.

## Synthetic benchmarks

Hand-written cases that cover hard patterns such as aliases, factories, and name collisions. Each
pattern is repeated across provider names, so patterns are the fair count.

| Set | Patterns | Cases | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- |
| Holdout, written after the analyzer fixes | 38 | 76 | 1.00 | 0.58 | 0.74 |
| V2, used for tuning | 86 | 344 | 1.00 | 0.96 | 0.98 |
| V1, release gate | 7 | 7 | 1.00 | 1.00 | 1.00 |

On the synthetic holdout, F1 rose from 0.46 to 0.74 and precision from 0.73 to 1.00 compared with
the analyzer before this work. Go and Java cases count as misses in V2 because those languages are
unsupported.

## Tests

| Suite | Tests | Notes |
| --- | --- | --- |
| Backend | 236 | Includes real Docker isolation tests, skipped when Docker is not running |
| PostgreSQL | 3 | Skipped without `TEST_DATABASE_URL`; run by `make test-postgres` and CI |
| Frontend | 6 | Server-rendered routes and identity |
| Sandbox worker | 4 | Path, argument, quoting, and log-redaction policy |

Backend line coverage is 73% overall and 95% for the impact analyzer.

## What has not been measured

- **Migration generation.** The harness in `app/hardening/migration_eval.py` is built and tested
  with a scripted model, but has not run with GPT-4o. It will score patches only on checks the
  model does not choose: whether the patch parses, whether the old API is gone, and whether tests
  that passed before still pass.
- **Hosted sandbox verification.** The evaluation uses a local Docker sandbox with networking off.
  The hosted Cloudflare Sandbox is not configured on the current deployment, so migration generation
  cannot finish there yet.
- **Real usage.** There are no production usage or adoption figures.

## Reproducing

```bash
make benchmark               # synthetic release gate
make benchmark-v2            # synthetic V2 and holdout, no gate
make backtest                # real tuning set; downloads repositories
make backtest-holdout        # real held-out set; report it, never tune against it
make test-postgres           # SQL tests against a disposable PostgreSQL container
make migration-eval-estimate # model cost estimate; makes no model calls
```

`benchmarks/build_backtest.py` regenerates the case lists. GitHub search results change over time,
so it will not reproduce them exactly, and the frozen files in `benchmarks/` are the record.
