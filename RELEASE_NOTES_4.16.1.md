# Local AI Project Studio 4.16.1

This patch fixes large source-view reuse, repeated large-file creation recovery, output-limit protocol switching, and source-only update preflight. It does not weaken source/SHA, schema, requirement-review, acceptance or uncertain-command safeguards.

Validation: 173 isolated regressions passed. The full default installer path passed with both staged and installed self-tests enabled, using a clean folder, fresh offline Python environments and physical Node/browser runtime on the same Windows host; 8,132 installed files matched the final full archive. Source-only updates also passed with the corrected preflight and installed self-test. Private-copy forward/backward migration, interrupted updates, rollback, draft persistence and restart checks passed. This is not a fresh OS or another-machine certification.

All 12 bounded local-model comparison attempts are retained locally and summarized in [aggregate evidence](docs/validation-4.16.1.json). Baseline accepted 2/6, candidate 3/6; independent behavior oracle passed 6 and 6 respectively. Actual generation requests: 94 baseline, 88 candidate. The same loaded model and settings, alternating order, original tests and independent oracles were used. A passing oracle is not full host acceptance. Failures and budget stops are included, not replaced by best runs.

The initial eight attempts remain in the aggregate. Some early JS timings overlapped observer installation/CPU checks. A prespecified quiet follow-up added one matched repetition per task/version after other work finished; it is reported separately: baseline accepted 1/2 with 31 requests, candidate 1/2 with 28 requests. The quiet subset does not replace the initial attempts. Wall times on one host and this small sample do not establish a general speedup.

The focused source-view test repeated fifty equivalent large-page requests in each of five fresh caches: baseline executed fifty backend views, candidate one, with identical contents. This measures that specific mechanism. Cached contents still enter model context, so it does not establish general token savings or arbitrary-project productivity gains.

A larger disposable application trial generated a database layer and corrected independently reported defects through Studio's own tools. Its database checks passed while application/server/authentication completion remained outside proven delivery; intermediate generated-test failures are not counted as a finished product. Local-model planning and requirement/evidence mapping may still fail or hit the bounded request/time limits.

## Upgrade and recovery

Source-only updates require an already prepared installation with identical runtime/dependency locks. Changed dependencies require a full package. Older updater versions with the source-preflight defect cannot bootstrap this fix using their default source-only path: use the full local package or run the corrected updater from reviewed source while services are stopped. The updater verifies installed dependencies, checks installed code after replacement, and rolls code back on failure. Code rollback preserves newer private data; it does not overwrite conversations with an older backup.

Only source, synthetic tests and aggregate evidence are published. User projects, private data, raw attempts, model weights and runtime binaries are excluded.
