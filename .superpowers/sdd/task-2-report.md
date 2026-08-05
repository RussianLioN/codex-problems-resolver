# Task 2 Report: Repository Policy Automation

## Status

DONE_WITH_CONCERNS

## RED evidence

Initial RED after writing tests first:

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 1
Evidence:
ModuleNotFoundError: No module named 'repository_policy'
ModuleNotFoundError: No module named 'validate_repository'
FAILED (errors=2)
```

Contract correction RED after splitting GitHub Actions endpoints and adding required governance checks:

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 1
Evidence:
test_classic_branch_protection_unavailable_returns_two_not_drift failed: expected exit 2, got 1
test_governance_drift_job_passes_github_token_through_environment failed: GH_TOKEN was missing
test_required_path_that_exists_but_is_git_ignored_is_reported failed: ignored required path was not reported
FAILED (failures=3)
```

Fix follow-up RED for complete GitHub policy payloads:

```text
Command: python3 -m unittest tests.test_repository_policy
Result: exit 1
Evidence:
test_policy_declares_complete_actions_permissions errored with KeyError: 'enabled'
test_apply_puts_complete_actions_permissions_payload expected enabled/allowed_actions/sha_pinning_required but got only sha_pinning_required
test_check_reports_actions_enabled_and_allowed_actions_drift expected drift but got exit 0
test_classic_branch_protection_payload_enforces_admins expected True but got None
test_check_reports_classic_enforce_admins_drift expected drift but got exit 0
FAILED (failures=4, errors=1)
```

Review follow-up RED for ruleset backend hardening:

```text
Command: python3 -m unittest tests.test_repository_policy tests.test_validate_repository
Result: exit 1
Evidence:
test_policy_declares_ruleset_backend_and_no_bypass_actors errored with KeyError: 'backend'
test_check_reports_ruleset_bypass_actors_drift expected drift but got exit 0
test_required_path_that_exists_but_is_untracked_is_reported_in_git_checkout did not report the untracked required path
test_job_level_reusable_workflow_uses_must_be_pinned_to_full_sha did not report the reusable workflow ref
test_ruleset_backend_missing_ruleset_is_drift_without_classic_probe expected drift but got exit 0
test_apply_ruleset_not_found_without_feature_evidence_returns_two_and_does_not_use_classic expected exit 2 but got 1
test_apply_ruleset_permission_error_without_feature_evidence_returns_two_and_does_not_use_classic expected exit 2 but got 1
FAILED (failures=11, errors=1)
```

Fallback contract RED after audit concretization:

```text
Command: python3 -m unittest tests.test_repository_policy tests.test_validate_repository
Result: exit 1
Evidence:
test_classic_backend_without_tracked_evidence_is_invalid_before_api_access made API calls before rejecting missing evidence
test_apply_feature_unavailable_uses_classic_but_reports_ruleset_drift_until_policy_changes missed the required NOTICE
test_apply_does_not_fallback_when_ruleset_patch_reports_plan_feature_unavailable expected exit 2 but got 1
test_apply_ruleset_auth_error_with_ruleset_words_returns_two_and_does_not_use_classic expected exit 2 but got 1
FAILED (failures=4)
```

## GREEN evidence

Fallback contract GREEN after audit concretization:

```text
Command: python3 -m unittest tests.test_repository_policy tests.test_validate_repository
Result: exit 0
Evidence: Ran 40 tests in 1.035s - OK
```

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 0
Evidence: Ran 40 tests in 1.012s - OK
```

```text
Command: python3 scripts/validate_repository.py
Result: exit 0
Evidence: OK: repository validation passed
```

```text
Command: python3 scripts/repository_policy.py validate
Result: exit 0
Evidence: OK: policy schema is valid
```

```text
Command: git diff --check
Result: exit 0
Evidence: no output
```

Review follow-up GREEN:

```text
Command: python3 -m unittest tests.test_repository_policy tests.test_validate_repository
Result: exit 0
Evidence: Ran 32 tests in 1.041s - OK
```

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 0
Evidence: Ran 32 tests in 1.105s - OK
```

```text
Command: python3 scripts/validate_repository.py
Result: exit 0
Evidence: OK: repository validation passed
```

```text
Command: python3 scripts/repository_policy.py validate
Result: exit 0
Evidence: OK: policy schema is valid
```

```text
Command: git diff --check
Result: exit 0
Evidence: no output
```

Fix follow-up GREEN after complete GitHub policy payload corrections:

```text
Command: python3 -m unittest tests.test_repository_policy
Result: exit 0
Evidence: Ran 17 tests in 0.013s - OK
```

Fix follow-up full validation:

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 0
Evidence: Ran 24 tests in 0.844s - OK
```

```text
Command: python3 scripts/validate_repository.py
Result: exit 0
Evidence: OK: repository validation passed
```

```text
Command: python3 scripts/repository_policy.py validate
Result: exit 0
Evidence: OK: policy schema is valid
```

```text
Command: git diff --check
Result: exit 0
Evidence: no output
```

```text
Command: python3 -m unittest discover -s tests -p 'test_*.py'
Result: exit 0
Evidence: Ran 19 tests in 0.246s - OK
```

```text
Command: python3 scripts/validate_repository.py
Result: exit 0
Evidence: OK: repository validation passed
```

```text
Command: python3 scripts/repository_policy.py validate
Result: exit 0
Evidence: OK: policy schema is valid
```

```text
Command: PYTHONPYCACHEPREFIX=/tmp/codex-problems-resolver-pycache python3 -m py_compile scripts/validate_repository.py scripts/repository_policy.py tests/test_validate_repository.py tests/test_repository_policy.py
Result: exit 0
Evidence: no output
```

```text
Command: git diff --check
Result: exit 0
Evidence: no output
```

## Files changed

- `.github/workflows/governance.yml`
- `AGENTS.md`
- `README.md`
- `ops/github/repository-policy.json`
- `scripts/repository_policy.py`
- `scripts/validate_repository.py`
- `tests/test_repository_policy.py`
- `tests/test_validate_repository.py`
- `.superpowers/sdd/task-2-report.md`

## Implemented behavior

- Added schema version 1 repository policy for `RussianLioN/codex-problems-resolver`.
- Added repository validation for required controlled paths, UTF-8, final newline, trailing whitespace, document headings, policy schema, and full 40-hex SHA pinning for external workflow `uses:` entries.
- Added policy `validate`, `check`, and guarded `apply` commands with Python 3 standard library only.
- Split GitHub Actions state across `/actions/permissions` for `sha_pinning_required` and `/actions/permissions/workflow` for workflow permission state.
- Added deterministic drift reporting and exit codes `0`, `1`, and `2`.
- Added guarded `apply` confirmation before any API call.
- Added named ruleset upsert without deleting or altering unrelated rulesets.
- Added classic branch protection fallback only for evidenced ruleset plan/feature 403 or 404.
- Added governance workflow with `validate` job, drift check on `main`, manual dispatch, daily schedule, `contents: read`, concurrency, no `pull_request_target`, full-SHA-pinned checkout, and `GH_TOKEN` passed through environment rather than command line.
- Corrected Actions repository permissions to declaratively manage `enabled=true`, `allowed_actions="all"`, and `sha_pinning_required=true` as the complete `/actions/permissions` PUT payload.
- Corrected classic branch protection fallback to send and normalize `enforce_admins=true`.
- Added `main_protection.backend` with current value `ruleset`; read-only `check` requires the named ruleset for this backend and does not treat matching classic branch protection as success.
- Added explicit `bypass_actors=[]` to the ruleset payload and drift comparison.
- Tightened ruleset-to-classic fallback: mutating `apply` uses classic only after a 403/404 with explicit plan, feature, not available, or unavailable evidence. Ordinary not found, permission, or URL errors return exit 2 and do not call classic protection.
- Tightened required-path validation in Git checkouts to require `git ls-files --error-unmatch -- <path>` for every required path; plain directories without `.git` still use existence checks.
- Tightened workflow `uses:` scanning to catch job-level reusable workflows and allow inline comments after full-SHA refs.
- Final fallback contract: `main_protection.backend=ruleset` is the bootstrap state. Read-only `check` never probes or accepts classic protection for this backend; missing `main-protection` ruleset is drift.
- Mutating `apply` may call classic branch protection only when creating the ruleset with `POST /repos/{repo}/rulesets` fails with status 403 or 404 and the message simultaneously proves both plan/feature unavailability and ruleset context. The result remains exit 1 with NOTICE until tracked evidence is committed and policy backend changes to `classic`.
- Fallback never runs for ruleset list or patch failures, authentication, permission, not found, or bad URL errors; those return exit 2.
- `main_protection.backend=classic` is valid only with `classic_evidence` containing status 403/404, operation `POST /repos/{repo}/rulesets`, category `plan_feature_unavailable`, a plan/feature plus ruleset message excerpt, and a tracked reference. Without that evidence, validation fails before any API call.

## Concerns

- Live `gh api` repository drift check was not run because the assigned final checks were local validators and unit tests, and the task forbids remote mutation. GitHub transport behavior is covered through mocked transport-boundary tests.
- The local validator treats required paths in a Git checkout as valid if they are tracked or untracked and not ignored. In a plain directory without `.git`, it falls back to existence checks so copied source trees can still be validated.
