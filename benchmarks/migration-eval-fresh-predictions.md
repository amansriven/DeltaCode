# Predictions for the fresh-repository run

Written before any model call on `repository-impact-fresh.json`, so the result can be compared with what was expected. These are judgments from two earlier runs and a simulated run, not measurements.

## Expected result

27 of 36 repositories should reach the model. Grades come from reading each diff.

| Migration | Reach the model | Earlier correct | Predicted correct |
|---|---:|---:|---:|
| NumPy aliases | 9 | 5 of 6 | 7–9 |
| React 18 root | 8 | 13 of 14 | 6–8 |
| Selenium locators | 5 | 9 of 11 | 4–5 |
| OpenAI Python | 3 | 4 of 14 | 1–3 |
| Pydantic validators | 2 | 2 of 5 | 0–2 |
| **Total** | **27** | **33 of 50** | **18–27, most likely about 22** |

About 22 correct would be roughly 80% of repositories reaching the model and 60% of all 36.

## Why the score may mislead

- **Easier mix:** 17 of the 27 are NumPy or React, and only 5 are OpenAI or Pydantic. A higher total than the earlier 66% would not by itself show that the fixes worked.
- **Fixes were informed by earlier runs:** the guides and checks were written after reading those failures. This set was excluded from that work, except that the simulated run led to one addition to the Pydantic guide (`allow_reuse` and `each_item`).
- **Automatic scores run high:** in the first run they suggested 26 patches passed when 19 were correct, so only manual grades should be reported.

## Where it may go wrong

- **Text that doesn't match:** targeted edits have never run against the real model. A mismatch costs a retry, and failing twice loses the repository.
- **Thin test evidence:** only 3 repositories have tests that can give a verdict, so most results rest on diff review and import checks.
- **React has no automatic checks:** its 8 repositories depend entirely on diff review.
- **Approvals won't mean much:** Python patches can only earn approval with a test verdict, and React guesses its own commands, so expect mostly "revise".
- **Partial views:** about 4 repositories have files sent as windows, so the model may place imports badly. No old-API lines were hidden in any of them.
- **Answer size:** output estimates are not yet calibrated, so an answer could be cut off after it is paid for.

## Stopping rules

Run the first 8 repositories (4 OpenAI, 4 Selenium) with a $0.60 limit, then read the results. Stop and fix before continuing if any of these happen:

- OpenAI rejects the request schema.
- 2 or more repositories fail with text that doesn't match, even after the retry.
- Any answer is cut off for length.
- Cost is above $0.10 per repository.

The full run is limited to $2.50, and it halts by itself after 3 paid failures in a row.

## After the first 8 repositories

Recorded before running the rest. It cost $0.14 for 5 patches, about half the estimate, and no stopping rule fired. Diff review graded 2 of the 5 correct: OpenAI 2 of 3 and Selenium 0 of 2. Automatic checks alone would have passed 4 of them, and that is below the Selenium prediction.

All 3 defects were cheap to catch, so an undefined-name check, separate retry budgets and a prompt rule on imports were added. Replayed over all 53 saved patches, the name check flagged 2 of the 18 defective patches and none of the 35 correct ones.

The 5 patches keep their grades and are not rerun. The remaining repositories are the clean test of these changes, and results will be reported for each group as well as combined.

## Outcome

18 of 27 correct, at the bottom of the predicted range, and 50% of all 36 rather than the 60% expected. It cost $1.29.

- **Within range:** React 7 of 8, OpenAI 2 of 3, Pydantic 1 of 2.
- **Below range:** NumPy 6 of 9 and Selenium 2 of 5.
- **Predicted correctly:** text mismatches lost repositories (2), test evidence was thin (3 repositories), and automatic scores ran high (21 against 18).
- **Not predicted:** incomplete patches were the most common defect, and the review approved one of them.
