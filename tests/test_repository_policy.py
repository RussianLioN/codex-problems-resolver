import io
import json
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import repository_policy


POLICY_PATH = Path(__file__).resolve().parents[1] / "ops" / "github" / "repository-policy.json"


class FakeGhClient:
    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls = []

    def call(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        key = (method, path)
        if key in self.errors:
            raise self.errors[key]
        response = self.responses.get(key)
        if callable(response):
            return response(method, path, payload)
        if response is None:
            return {}
        return json.loads(json.dumps(response))


def matching_repo():
    return {
        "private": True,
        "default_branch": "main",
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
        "allow_squash_merge": True,
        "allow_merge_commit": False,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
    }


def matching_actions_permissions():
    return {
        "enabled": True,
        "allowed_actions": "all",
        "sha_pinning_required": True,
    }


def matching_workflow_permissions():
    return {
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": False,
    }


def matching_ruleset():
    return {
        "id": 101,
        "name": "main-protection",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": 0,
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [{"context": "validate"}],
                },
            },
            {"type": "required_linear_history"},
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
    }


def matching_client():
    repo = "RussianLioN/codex-problems-resolver"
    return FakeGhClient(
        {
            ("GET", f"/repos/{repo}"): matching_repo(),
            ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
            ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
            ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
            ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
        }
    )


class RepositoryPolicyTests(unittest.TestCase):
    def test_policy_file_validates(self):
        policy = repository_policy.load_policy(POLICY_PATH)

        self.assertEqual([], repository_policy.validate_policy(policy))

    def test_check_reports_deterministic_drift(self):
        client = matching_client()
        client.responses[("GET", "/repos/RussianLioN/codex-problems-resolver")]["has_wiki"] = True
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, "RussianLioN/codex-problems-resolver", client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "DRIFT repository.has_wiki: expected false actual true",
            ],
            result.lines,
        )

    def test_check_reports_sha_pinning_drift_from_actions_permissions(self):
        client = matching_client()
        client.responses[("GET", "/repos/RussianLioN/codex-problems-resolver/actions/permissions")][
            "sha_pinning_required"
        ] = False
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, "RussianLioN/codex-problems-resolver", client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "DRIFT actions.sha_pinning_required: expected true actual false",
            ],
            result.lines,
        )

    def test_check_returns_two_for_auth_or_api_errors(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            errors={
                ("GET", f"/repos/{repo}"): repository_policy.GhApiError(
                    "authentication required", exit_code=2, status=401
                )
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("authentication required", result.lines[0])

    def test_classic_branch_protection_unavailable_returns_two_not_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        client.responses[("GET", f"/repos/{repo}/rulesets")] = []
        client.errors[("GET", f"/repos/{repo}/branches/main/protection")] = repository_policy.GhApiError(
            "GitHub API rate limit", exit_code=2, status=500
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("GitHub API rate limit", result.lines[0])

    def test_unprotected_branch_404_is_reported_as_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        client.responses[("GET", f"/repos/{repo}/rulesets")] = []
        client.errors[("GET", f"/repos/{repo}/branches/main/protection")] = repository_policy.GhApiError(
            "branch not protected", exit_code=2, status=404
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(["DRIFT ruleset.main-protection: expected present actual missing"], result.lines)

    def test_apply_refuses_without_exact_confirmation_before_api_access(self):
        client = FakeGhClient()
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(
            policy,
            "RussianLioN/codex-problems-resolver",
            "RussianLioN/other",
            client,
        )

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertIn("--confirm must exactly match", result.lines[0])

    def test_apply_is_idempotent_when_live_state_matches(self):
        client = matching_client()
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(
            policy,
            "RussianLioN/codex-problems-resolver",
            "RussianLioN/codex-problems-resolver",
            client,
        )

        self.assertEqual(0, result.exit_code)
        self.assertFalse([call for call in client.calls if call[0] in {"PATCH", "PUT", "POST", "DELETE"}])

    def test_apply_updates_both_actions_endpoints_only_when_they_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        permissions = matching_actions_permissions()
        permissions["sha_pinning_required"] = False
        workflow = matching_workflow_permissions()
        workflow["default_workflow_permissions"] = "write"

        def put_actions_permissions(method, path, payload):
            permissions.update(payload)
            return permissions

        def put_workflow_permissions(method, path, payload):
            workflow.update(payload)
            return workflow

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): lambda method, path, payload: permissions,
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): lambda method, path, payload: workflow,
                ("PUT", f"/repos/{repo}/actions/permissions"): put_actions_permissions,
                ("PUT", f"/repos/{repo}/actions/permissions/workflow"): put_workflow_permissions,
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        mutating_calls = [(method, path) for method, path, _ in client.calls if method in {"PUT", "PATCH", "POST"}]
        self.assertIn(("PUT", f"/repos/{repo}/actions/permissions"), mutating_calls)
        self.assertIn(("PUT", f"/repos/{repo}/actions/permissions/workflow"), mutating_calls)

    def test_apply_updates_only_named_ruleset_and_preserves_unrelated_rulesets(self):
        repo = "RussianLioN/codex-problems-resolver"
        unrelated = {"id": 202, "name": "unrelated", "target": "branch", "enforcement": "active", "rules": []}
        updated = {"done": False}

        def ruleset_detail(method, path, payload):
            if path.endswith("/101") and updated["done"]:
                return matching_ruleset()
            current = matching_ruleset()
            current["enforcement"] = "disabled"
            return current

        def update_ruleset(method, path, payload):
            updated["done"] = True
            return matching_ruleset()

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [unrelated, matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): ruleset_detail,
                ("PATCH", f"/repos/{repo}/rulesets/101"): update_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertIn(("PATCH", f"/repos/{repo}/rulesets/101"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("PATCH", f"/repos/{repo}/rulesets/202"), [(m, p) for m, p, _ in client.calls])
        self.assertFalse([call for call in client.calls if call[0] == "DELETE"])

    def test_apply_falls_back_to_classic_branch_protection_when_rulesets_are_unavailable(self):
        repo = "RussianLioN/codex-problems-resolver"
        fallback_state = {"protected": False}

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("rulesets feature is unavailable for this plan", exit_code=2, status=403)

        def put_protection(method, path, payload):
            fallback_state["protected"] = True
            return {}

        def get_protection(method, path, payload):
            if fallback_state["protected"]:
                return repository_policy.expected_classic_branch_protection(
                    repository_policy.load_policy(POLICY_PATH)
                )
            raise repository_policy.GhApiError("branch is not protected", exit_code=1, status=404)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
                ("PUT", f"/repos/{repo}/branches/main/protection"): put_protection,
                ("GET", f"/repos/{repo}/branches/main/protection"): get_protection,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_cli_validate_reports_schema_errors(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = repository_policy.main(["validate", "--policy", str(POLICY_PATH)])

        self.assertEqual(0, exit_code)
        self.assertIn("OK: policy schema is valid", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
