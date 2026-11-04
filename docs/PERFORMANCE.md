# Performance

Measured results for Delta Code's impact analysis and migration generation, recorded on 2026-11-04.
These results describe the code in this repository, not the deployed service.

## Summary

| Measure | Result | Evidence |
| --- | --- | --- |
| Changed call sites found, held-out real migrations | **95.5%** (385 of 403 lines, 50 repositories) | [Results](../benchmarks/backtest-holdout-results.json) |
| Repositories with every changed line found | 45 of 50 | Same run |
| Migrated repositories marked affected with no call site left | 0 of 50 | Same run |
| Analysis time per repository | 55 ms median, 3.5 s maximum (up to 1,911 files) | Same run, on a developer laptop |
| Correct GPT-4o migration patches, by diff review | **61%** (19 of 31 repositories) | [Grades](../benchmarks/migration-eval-holdout-review.json) |
| Same review after two fixes, on different repositories | **74%** (14 of 19 repositories) | [Grades](../benchmarks/migration-eval-tuning-review.json) |
| Generation cost | About $0.04 median per repository | [First run](../benchmarks/migration-eval-holdout.json), [second run](../benchmarks/migration-eval-tuning.json) |
| Automated tests | 292 passing, plus 3 run only against PostgreSQL | See [Tests](#tests) |

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

## Migration generation

GPT-4o ran through the production migration service on the held-out repositories, with verification
in a local Docker sandbox that has networking off. The hosted sandbox was not involved.

Of the 50 repositories, 31 reached the model. The other 19 stopped before any spend: 6 had too much
context, 5 were too large to upload, 4 had files too large to return whole, 3 had no call sites
found, and 1 contained what looks like a real secret.

| Migration | Correct | Attempted |
| --- | --- | --- |
| React 18 root API | 8 | 8 |
| NumPy 1.24 removed aliases | 4 | 5 |
| Selenium 4 locators | 5 | 7 |
| OpenAI Python 1.0 | 2 | 9 |
| Pydantic 2 validators | 0 | 2 |
| **Total** | **19** | **31** |

Each patch was read as a diff against its parent commit. This was an AI-assisted review, and the
patches were not run against the services they call. A patch counts as correct only if it migrates
the API and damages nothing else, so correct does not mean proven in production.

| Why the 12 others failed | Repositories |
| --- | --- |
| Wrong use of the new API, such as an invalid argument or a client built at import time | 5 |
| Lost code: returned part of a file, or removed about 60 lines | 4 |
| Stripped non-ASCII characters from an otherwise correct migration | 1 |
| Changed the repository's own method instead of the library call | 1 |
| Proposed a verification command the policy refuses, so no patch was produced | 1 |

### Automatic checks were not enough

26 of the 30 patches passed the automatic checks for parsing and for removing the old API, but only
19 were correct. One passed while deleting about 60 lines, including a license notice.

Tests could not settle this. Only 4 of the 30 had tests passing offline before the patch, and in all
4 the developers' own migration also failed some of those tests in this environment. The model
recommended approval once and revision 29 times. The retest below shows why that happens.

### Fixes and retest

The first run led to two fixes:

- **Prompt encoding.** Prompts sent non-ASCII text as `\uXXXX` escapes, and five of the six patches
  touching such files lost all of it. A controlled test sent one file with 44 non-ASCII characters
  three times each way. Escaped prompts kept 0, 40, and 0 characters and never returned the exact
  patch. Raw Unicode kept all 44 and returned the exact patch every time.
- **Lost code.** Patches now fail policy if they remove more than 15% of a file and more than 20
  lines, and the prompt says that each edit replaces the whole file.

The retest ran on the 54 tuning repositories, which had never gone through generation. The analyzer
had been tuned on them, but neither fix was developed from them. 19 reached the model.

| Migration | First run (31 repositories) | Retest (19 different repositories) |
| --- | --- | --- |
| React 18 root API | 8 / 8 | 5 / 6 |
| NumPy 1.24 removed aliases | 4 / 5 | 1 / 1 |
| Selenium 4 locators | 5 / 7 | 4 / 4 |
| OpenAI Python 1.0 | 2 / 9 | 2 / 5 |
| Pydantic 2 validators | 0 / 2 | 2 / 3 |
| **Total** | **19 / 31 (61%)** | **14 / 19 (74%)** |

The repositories and migration mix differ, and the samples are small, so this does not prove how much
the fixes raised accuracy. The defect categories are clearer. Patches losing large parts of a file
went from 4 to 0, and corrupted characters from five patches to none, though two patches deleted
comments containing non-ASCII text. The 15% limit never fired, so the lost-code result cannot be
credited to it alone.

Of the 5 retest misses, 4 misused the API or test setup, and 1 was refused as credential-like. That
proposal was not saved, so whether the refusal was correct is unknown.

Four retest repositories had usable tests, and they agreed with the review each time: the two with no
regressions were graded correct, and the two with regressions were graded defective.

### Remaining limits

- **The model guesses verification commands.** In the retest, 17 of 18 first checks failed, mostly
  because a guessed path such as `tests` did not exist, or because JavaScript tools were not installed.
  So the model recommended revision on 17 of 18 patches, including most correct ones. Its
  recommendation is not currently a useful quality signal, and the repository's real test command
  should be detected rather than guessed.
- **Large files cannot be migrated.** Edits must fit in 6,000 output tokens, so files above roughly
  17 KB are skipped. Sending targeted edits instead of whole files would remove this limit.
- **Refused proposals are not saved**, which makes a refusal impossible to audit afterward.

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
| Backend | 282 | Includes real Docker isolation tests, skipped when Docker is not running |
| PostgreSQL | 3 | Skipped without `TEST_DATABASE_URL`; run by `make test-postgres` and CI |
| Frontend | 6 | Server-rendered routes and identity |
| Sandbox worker | 4 | Path, argument, quoting, and log-redaction policy |

Backend line coverage is 73% overall and 95% for the impact analyzer.

## What has not been measured

- **Hosted sandbox verification.** The evaluation used a local Docker sandbox. The hosted Cloudflare
  Sandbox is not configured on the current deployment, so migration generation cannot finish there
  yet.
- **Whether patches work when run.** Few repositories have tests that can run offline, so most
  patches were judged by reading them.
- **JavaScript and TypeScript syntax in JSX and TSX files**, which the local check cannot parse.
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

The paid evaluation needs an explicit spend limit, and it can resume without paying twice:

```bash
python -m app.hardening.migration_eval benchmarks/repository-impact-backtest-holdout.json \
  --out benchmarks/migration-eval-holdout.json --max-cost 5 --resume --patches-dir <directory>
```

`benchmarks/build_backtest.py` regenerates the case lists. GitHub search results change over time,
so it will not reproduce them exactly, and the frozen files in `benchmarks/` are the record.
