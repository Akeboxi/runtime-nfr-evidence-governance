# Versions and verified supplementary results

| Version | Meaning | Result artifacts |
| --- | --- | --- |
| Original v1 | Frozen numerical routing and human-study outputs | Repository-root `artifacts/`, frozen original commit 1786cc5 |
| v1-corrected | Corrected minimum-sample metadata and true-all-zero fact | `artifacts/rebuild/`, grid reconstruction, `artifacts/assessment_20260930/` |
| Paper rule v1.1 | Clarified B/D boundary in the frozen controlled human evaluation | Repository-root `artifacts/bd-boundary-v1.1/`; not a human test of software 1.2.0 |
| Software 1.2.0 | Explicit evidence, source checks, scoped closures and rechecking | `slo_evidence/`, `tests/`, synthetic lifecycle artifacts |
| Assessment 20260930 | Corrected interpretation of real measurement material | `artifacts/existing_pool_v12_*`, `artifacts/learned_g/`, assessment artifacts |

## Historical corrections

The original pool has 8,668 cards: A=1,054, B=2,424, C=948 and D=4,242. The
4,426 cards requiring evidence tasks account for 51.1%. Correcting true-all-zero
flags removes 1,306 erroneous flags and retains 1,429 genuine all-zero error
cards; it does not change the original main routes. Ninety minimum-sample
metadata rows contained an extra one. The corrected value is derived from the
window and coverage threshold, rather than chosen independently.

At 24 hours, changing coverage from 0.50 to 0.40 changes the sample minimum from
720 to 576. The resulting 196 migrations are 109 A to D, 63 A to B, and 24 A to
C. The portable grid verifies the counts, minima and migrations for all 540
configurations from reconstructed statistics.

## Current empirical assessment

All 8,668 cards lack M1, M2 and M4. M3 covers only the local transformation.
Current main routes are A=1,054 and B=7,614, while 1,082 triggered context facts
remain recorded. These results belong to the explicit evidence implementation
and assessment; they do not replace the historical v1 distribution.

## Learning-source interface experiment

The frozen selection contains 72 windows. Fifty-five fit the fixed
StandardScaler plus QuantileRegressor model (quantile 0.95, alpha 0.01); 17 fail
because training or historical holdout is too short. Those failures remain in
the selection and output tables. Among the 55 successful pairs, 46 candidate
values differ and zero main routes change. Both sources use the same H and
shared observational and measurement evidence. Successful paired routes are
A=7 and B=48. Statistical routes across all 72 selections are A=24 and B=48;
that total must not be compared with the successful 55 as a paired migration.

Training, feature standardization, target scaling and candidate inputs meet
the audit-time constraints. Two error-ratio cards have 802 clipped predictions.
The two paired examples follow the frozen order, rather than outcome selection.
All successful empirical pairs retain unknown M1, M2 and M4. Equal routing
supports the tested interface behavior; it is not evidence of model superiority
or unrestricted model applicability.

## Closure behavior

The synthetic executable sequence is C, C, D, B, C: initial context task, a
plain note, valid scoped confirmation, remaining measurement gap, and changed
candidate invalidating the previous closure. Nine archived scenarios include
expired records, wrong windows, missing roles and mismatched evidence. These
are implementation checks, not observations of real personnel reviewing cards.
