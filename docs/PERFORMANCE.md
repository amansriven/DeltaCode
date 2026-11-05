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
| Correct GPT-4o patches on 36 new repositories, by diff review | **50%** of all 36; **67%** of the 27 attempted | [Grades](../benchmarks/migration-eval-fresh-review.json) |
| Same review on the 104 earlier repositories | 32% of all 104; 66% of the 50 attempted | [First](../benchmarks/migration-eval-holdout-review.json), [second](../benchmarks/migration-eval-tuning-review.json) |
| Repositories reaching the model | 75% of 36, up from 48% of 104, on different samples | [New run](../benchmarks/migration-eval-fresh.json) |
| Generation cost on the new repositories | $0.03 median per run, $1.29 in total | Same run |
| Automated tests | 341 passing, plus 3 run only against PostgreSQL | See [Tests](#tests) |

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

The [newest run](#run-on-new-repositories) is the current result. The first two runs below explain
how it got there.

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

### Run on new repositories

The first two runs shaped the fixes, so neither could give a clean score. A third set of 36
repositories was selected under the same mechanical rules, excluding every earlier repository, and
committed before scoring. [Predictions](../benchmarks/migration-eval-fresh-predictions.md) were also
written first.

This run included these changes:

- **Targeted edits.** The model returns text replacements rather than retyping whole files.
- **Partial files.** Large files are sent as the imports plus the code around each call site. On this
  set, none of the 546 old-API lines in supplied files were hidden.
- **Checks Delta Code controls.** Python patches run import, call-argument, undefined-name, and
  baseline test checks in place of commands the model guessed. Each reports only differences from
  the original code.
- **Retries.** One retry for text that doesn't match, and one for a regression a check found.

Of the 36 repositories, 27 reached the model. The other 9 stopped before any spend: 3 were too large
to upload, 2 had too much to rewrite, 2 contained what looks like a real secret, 1 had too much
context, and 1 had no call sites found.

| Migration | Correct | Attempted | Earlier runs | Predicted |
| --- | --- | --- | --- | --- |
| React 18 root API | 7 | 8 | 13 of 14 | 6–8 |
| NumPy 1.24 aliases | 6 | 9 | 5 of 6 | 7–9 |
| Selenium 4 locators | 2 | 5 | 9 of 11 | 4–5 |
| OpenAI Python 1.x | 2 | 3 | 4 of 14 | 1–3 |
| Pydantic v2 validators | 1 | 2 | 2 of 5 | 0–2 |
| **Total** | **18** | **27** | **33 of 50** | **18–27** |

The first 8 repositories ran as a paid test, and graded 2 of 5 correct. Those defects led to the
undefined-name check, separate retry budgets, and a prompt rule on imports. Those 5 grades were kept.
The remaining repositories graded 16 of 22 correct (73%), but they include all the NumPy and React
cases and no OpenAI ones, so the two groups can't be compared.

**What this shows:**

- **More repositories get a patch.** Correct patches rose from 32% to 50% of all repositories tested,
  mostly because more repositories reached the model. The samples differ, so the size of the gain is
  uncertain.
- **Accuracy did not clearly improve.** 67% of attempted repositories were correct, against 66% before,
  even with an easier mix of migrations. The result landed at the bottom of the predicted range.
- **The failures changed.** Earlier runs lost code and damaged text. Here, the 7 defects were 3 incomplete
  migrations, 2 undefined names, 1 syntax error, and 1 type error.

Automatic checks again overstated quality: 21 patches parsed, removed all old usage, and added the
new API, and only 18 of those were correct.

### Remaining limits

- **Incomplete patches pass unnoticed.** Nothing checks whether old call sites remain after a patch.
  `ciao-contrib` kept 2 removed aliases, its tests didn't reach them, and the review approved it. That
  was 1 of the 3 approvals in this run.
- **Regression repair is unproven.** Only one patch reached it, and that patch still failed.
- **Text matching still loses repositories.** Two were refused twice: one because the model wrote quotes
  with a backslash, the other because it copied a line that isn't in the file. Together they cost
  $0.25.
- **Little is verified by running code.** Only 3 of the 27 repositories had tests that could give a
  verdict, and JavaScript and TypeScript patches have no automatic checks.
- **The grades come from one AI reviewer.** `landscape-server-operator` is the closest call: if
  validators running on raw input counts as a defect, the total is 17.

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
| Backend | 341 | Includes real Docker isolation tests, skipped when Docker is not running |
| PostgreSQL | 3 | Skipped without `TEST_DATABASE_URL`; run by `make test-postgres` and CI |
| Frontend | 6 | Server-rendered routes and identity |
| Sandbox worker | 4 | Path, argument, quoting, and log-redaction policy |

Backend line coverage is 71% overall and 95% for the impact analyzer.

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

To rehearse a run for free, add `--simulate` to the command below. It uses the real client, budget,
sandbox, and checks against a simulated API, so its costs are estimates and its patches say nothing
about the model.

The paid evaluation needs an explicit spend limit, and it can resume without paying twice:

```bash
python -m app.hardening.migration_eval benchmarks/repository-impact-backtest-holdout.json \
  --out benchmarks/migration-eval-holdout.json --max-cost 5 --resume --patches-dir <directory>
```

`benchmarks/build_backtest.py` regenerates the case lists. GitHub search results change over time,
so it will not reproduce them exactly, and the frozen files in `benchmarks/` are the record.
