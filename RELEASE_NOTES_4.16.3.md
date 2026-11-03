# 4.16.3 — executed acceptance evidence and measured token coverage

Automatic passing validation now supplies bounded, actually executed successful test IDs and pending original requirement blocks to the model. Current source fingerprints and execution receipts remain required. Truncated source blocks explicitly require a full read. This supplies missing evidence metadata without weakening acceptance or treating a passing unrelated test as satisfaction of a requirement.

Token totals accept nonnegative integer provider receipts only. Missing receipts stay unknown; partial totals are lower bounds. A usage frame without a terminal stream marker is incomplete. No estimated fallback is counted as measured usage. Four isolated UI checks also pass. Their stale harness was corrected to load the operation classifier from the shipped main HTML and provide the DOM methods used by the live extension; this required no product UI change.

## Retained comparison

Sixteen matched local-model attempts covered four small tasks (JavaScript subtotal, JSON persistence, CSV parsing and URL query handling), two repetitions per task per variant. Current 4.16.2 and the candidate used the same model/settings and alternating order. Every attempt and failure was retained privately; all sixteen independent behavioral checks passed. Reviewed delivery was 7/8 versus 8/8. Baseline used 102 generations and 1,191,073 tokens; candidate used 81 and 814,280. These are sample aggregate reductions of 20.6% and 31.6%, not a universal performance claim. CSV aggregate token use worsened from 182,621 to 201,133 (about 10.1%). One JSON repetition also worsened. Bounded runs are not evidence of production readiness for arbitrary complex applications.

The final conservative missing-terminal metrics correction followed the model comparison. Its 195 isolated regressions passed; 183 retained provider receipts were replayed without changing those benchmark totals. Stable 4.16.3 differs from the frozen 4.16.3rc1 product source only in release metadata. The stable source passed all 195 isolated regressions again.

## Installation and continuity

The RC received a physical offline clean installation with 8,136 packaged files verified and mandatory runtime checks. A real program-generated field task passed pause, process restart and explicit resume; 13 generations had 13 complete token receipts. An additional idle restart preserved conversation, drafts, requirement IDs and previous tool receipts without generating model requests. Stable promotion used the verified source updater with unchanged dependency locks, pre-install compilation, post-install runtime self-test and a recorded source rollback transaction.

The previous release and private state were backed up locally. Selection migrated to the existing isolated working project; recent-project lists were merged. Original projects remained untouched. The manager and already loaded model were preserved while only the Studio UI changed. Duplicate field launch entries now open the primary UI. No project content, conversation, model settings, checkpoint, raw trace, runtime or weight is included in this publication.

## Remaining human and field review

Human visual/usability feedback is still required. Ambiguous legacy MFA interpretation remains pending. A separate complex user project was cancelled and has no accepted delivery; adopting this Studio release does not certify that generated website. Real-use monitoring continues, and confirmed new program defects must be reproduced and fixed in isolation before another primary update.
