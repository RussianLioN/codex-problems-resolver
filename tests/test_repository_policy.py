import io
import json
import subprocess
import tempfile
import unittest
import copy
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


def matching_builtin_repo():
    return {
        "private": True,
        "default_branch": "main",
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
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
        "bypass_actors": [],
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
                    "allowed_merge_methods": ["squash"],
                    "required_reviewers": [],
                    "required_review_thread_resolution": True,
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "do_not_enforce_on_create": False,
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


def classic_policy_with_evidence(reference="docs/incidents/README.md"):
    policy = copy.deepcopy(repository_policy.load_policy(POLICY_PATH))
    policy["main_protection"]["backend"] = "classic"
    policy["main_protection"]["classic_evidence"] = {
        "status": 403,
        "operation": "POST /repos/RussianLioN/codex-problems-resolver/rulesets",
        "category": "plan_feature_unavailable",
        "message_excerpt": "rulesets feature is unavailable for this plan",
        "tracked_reference": reference,
    }
    return policy


def init_git_repo(root):
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def write_evidence(root, reference="docs/incidents/evidence.md"):
    path = root / reference
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Evidence\n", encoding="utf-8")
    return path


def track_path(root, reference):
    subprocess.run(["git", "add", reference], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class RepositoryPolicyTests(unittest.TestCase):
    def test_policy_file_validates(self):
        policy = repository_policy.load_policy(POLICY_PATH)

        self.assertEqual([], repository_policy.validate_policy(policy))

    def test_policy_declares_complete_actions_permissions(self):
        policy = repository_policy.load_policy(POLICY_PATH)

        self.assertEqual(True, policy["actions"]["enabled"])
        self.assertEqual("all", policy["actions"]["allowed_actions"])
        self.assertEqual(True, policy["actions"]["sha_pinning_required"])

    def test_policy_declares_ruleset_backend_and_no_bypass_actors(self):
        policy = repository_policy.load_policy(POLICY_PATH)

        self.assertEqual("ruleset", policy["main_protection"]["backend"])
        self.assertEqual([], policy["main_protection"]["bypass_actors"])

    def test_policy_declares_canonical_ruleset_parameters(self):
        policy = repository_policy.load_policy(POLICY_PATH)
        protection = policy["main_protection"]

        self.assertEqual(["squash"], protection["allowed_merge_methods"])
        self.assertEqual([], protection["required_reviewers"])
        self.assertEqual(False, protection["do_not_enforce_on_create"])

    def test_ruleset_payload_declares_canonical_parameters(self):
        policy = repository_policy.load_policy(POLICY_PATH)
        payload = repository_policy.expected_ruleset_payload(policy)
        rules = {rule["type"]: rule for rule in payload["rules"]}

        self.assertEqual(["squash"], rules["pull_request"]["parameters"]["allowed_merge_methods"])
        self.assertEqual([], rules["pull_request"]["parameters"]["required_reviewers"])
        self.assertEqual(False, rules["required_status_checks"]["parameters"]["do_not_enforce_on_create"])

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

    def test_check_full_mode_actions_403_explains_repository_administration_read_token(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        client.errors[("GET", f"/repos/{repo}/actions/permissions")] = repository_policy.GhApiError(
            "Resource not accessible by integration", exit_code=2, status=403
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="full")

        self.assertEqual(2, result.exit_code)
        self.assertEqual(
            [
                "ERROR: full mode requires a token with Repository Administration read permission for /repos/RussianLioN/codex-problems-resolver/actions/permissions: Resource not accessible by integration",
            ],
            result.lines,
        )

    def test_builtin_mode_skips_admin_only_actions_and_reports_notice(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            [
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
            ],
            result.lines,
        )
        self.assertNotIn(("GET", f"/repos/{repo}/actions/permissions"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("GET", f"/repos/{repo}/actions/permissions/workflow"), [(m, p) for m, p, _ in client.calls])

    def test_builtin_mode_partial_repository_response_skips_merge_fields_with_notice(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_builtin_repo(),
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            [
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
            ],
            result.lines,
        )

    def test_builtin_mode_reports_scope_notice_alongside_observed_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): {**matching_repo(), "has_wiki": True},
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "DRIFT repository.has_wiki: expected false actual true",
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
            ],
            result.lines,
        )

    def test_builtin_mode_partial_repository_response_still_reports_visible_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): {**matching_builtin_repo(), "has_wiki": True},
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "DRIFT repository.has_wiki: expected false actual true",
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
            ],
            result.lines,
        )

    def test_full_mode_partial_repository_response_reports_missing_merge_fields_as_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_builtin_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="full")

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "DRIFT repository.allow_merge_commit: expected false actual null",
                "DRIFT repository.allow_rebase_merge: expected false actual null",
                "DRIFT repository.allow_squash_merge: expected true actual null",
                "DRIFT repository.delete_branch_on_merge: expected true actual null",
            ],
            result.lines,
        )

    def test_builtin_mode_missing_bypass_actors_reports_notice_without_claiming_full_ok(self):
        repo = "RussianLioN/codex-problems-resolver"
        ruleset = matching_ruleset()
        del ruleset["bypass_actors"]
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            [
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
                "NOTICE: builtin mode did not verify ruleset bypass_actors because the field is not visible",
            ],
            result.lines,
        )

    def test_full_mode_missing_bypass_actors_is_visibility_error(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        del client.responses[("GET", f"/repos/{repo}/rulesets/101")]["bypass_actors"]
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client, mode="full")

        self.assertEqual(2, result.exit_code)
        self.assertEqual(
            [
                "ERROR: full mode cannot verify ruleset bypass_actors for main-protection; token lacks sufficient ruleset detail visibility",
            ],
            result.lines,
        )

    def test_check_reports_actions_enabled_and_allowed_actions_drift(self):
        client = matching_client()
        live_actions = client.responses[("GET", "/repos/RussianLioN/codex-problems-resolver/actions/permissions")]
        live_actions["enabled"] = False
        live_actions["allowed_actions"] = "selected"
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, "RussianLioN/codex-problems-resolver", client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                'DRIFT actions.allowed_actions: expected "all" actual "selected"',
                "DRIFT actions.enabled: expected true actual false",
            ],
            result.lines,
        )

    def test_check_reports_ruleset_bypass_actors_drift(self):
        client = matching_client()
        client.responses[("GET", "/repos/RussianLioN/codex-problems-resolver/rulesets/101")][
            "bypass_actors"
        ] = [{"actor_id": 1, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, "RussianLioN/codex-problems-resolver", client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                'DRIFT ruleset.main-protection.bypass_actors: expected [] actual [{"actor_id": 1, "actor_type": "RepositoryRole", "bypass_mode": "always"}]',
            ],
            result.lines,
        )

    def test_check_reports_wide_ruleset_allowed_merge_methods_drift(self):
        client = matching_client()
        pull_request = next(
            rule
            for rule in client.responses[("GET", "/repos/RussianLioN/codex-problems-resolver/rulesets/101")]["rules"]
            if rule["type"] == "pull_request"
        )
        pull_request["parameters"]["allowed_merge_methods"] = ["merge", "squash", "rebase"]
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, "RussianLioN/codex-problems-resolver", client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(1, len(result.lines))
        self.assertIn('"allowed_merge_methods": ["squash"]', result.lines[0])
        self.assertIn('"allowed_merge_methods": ["merge", "squash", "rebase"]', result.lines[0])

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

    def test_classic_backend_branch_protection_unavailable_returns_two_not_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        policy = classic_policy_with_evidence()
        client.errors[("GET", f"/repos/{repo}/branches/main/protection")] = repository_policy.GhApiError(
            "GitHub API rate limit", exit_code=2, status=500
        )

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("GitHub API rate limit", result.lines[0])

    def test_ruleset_backend_missing_ruleset_is_drift_without_classic_probe(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        client.responses[("GET", f"/repos/{repo}/rulesets")] = []
        client.responses[("GET", f"/repos/{repo}/branches/main/protection")] = repository_policy.expected_classic_branch_protection(
            repository_policy.load_policy(POLICY_PATH)
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(["DRIFT ruleset.main-protection: expected present actual missing"], result.lines)
        self.assertNotIn(("GET", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_classic_backend_unprotected_branch_404_is_reported_as_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = matching_client()
        policy = classic_policy_with_evidence()
        client.errors[("GET", f"/repos/{repo}/branches/main/protection")] = repository_policy.GhApiError(
            "branch not protected", exit_code=2, status=404
        )

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(["DRIFT classic_branch_protection: expected present actual missing"], result.lines)

    def test_classic_backend_without_tracked_evidence_is_invalid_before_api_access(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient()
        policy = copy.deepcopy(repository_policy.load_policy(POLICY_PATH))
        policy["main_protection"]["backend"] = "classic"

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertIn("classic_evidence", result.lines[0])

    def test_classic_evidence_rejects_unsafe_reference_paths_before_api_access(self):
        unsafe_references = [
            ".superpowers/sdd/task-2-report.md",
            "docs/incidents/../secret.md",
            "docs\\incidents\\2026-08-05-ruleset.md",
            "/docs/incidents/2026-08-05-ruleset.md",
            "docs/runbooks/2026-08-05-ruleset.md",
            "docs/incidents/2026-08-05-ruleset.txt",
        ]
        repo = "RussianLioN/codex-problems-resolver"

        for reference in unsafe_references:
            with self.subTest(reference=reference):
                client = FakeGhClient()
                policy = classic_policy_with_evidence()
                policy["main_protection"]["classic_evidence"]["tracked_reference"] = reference

                result = repository_policy.check_policy(policy, repo, client)

                self.assertEqual(1, result.exit_code)
                self.assertEqual([], client.calls)
                self.assertIn("tracked_reference", result.lines[0])

    def test_check_classic_missing_evidence_refuses_before_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            repo = "RussianLioN/codex-problems-resolver"
            client = FakeGhClient()
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            policy["main_protection"]["classic_evidence"]["tracked_reference"] = "docs/incidents/missing.md"

            result = repository_policy.check_policy(policy, repo, client, repo_root=root)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertEqual(
            ["POLICY classic_evidence.tracked_reference does not exist: docs/incidents/missing.md"],
            result.lines,
        )

    def test_apply_classic_missing_evidence_refuses_before_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            repo = "RussianLioN/codex-problems-resolver"
            client = FakeGhClient()
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            policy["main_protection"]["classic_evidence"]["tracked_reference"] = "docs/incidents/missing.md"

            result = repository_policy.apply_policy(policy, repo, repo, client, repo_root=root)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertEqual(
            ["POLICY classic_evidence.tracked_reference does not exist: docs/incidents/missing.md"],
            result.lines,
        )

    def test_check_classic_untracked_evidence_refuses_before_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            repo = "RussianLioN/codex-problems-resolver"
            client = FakeGhClient()
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            write_evidence(root)

            result = repository_policy.check_policy(policy, repo, client, repo_root=root)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertEqual(
            ["POLICY classic_evidence.tracked_reference must be tracked by Git: docs/incidents/evidence.md"],
            result.lines,
        )

    def test_check_classic_ignored_evidence_refuses_before_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            (root / ".gitignore").write_text("docs/incidents/*.md\n", encoding="utf-8")
            repo = "RussianLioN/codex-problems-resolver"
            client = FakeGhClient()
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            write_evidence(root)

            result = repository_policy.check_policy(policy, repo, client, repo_root=root)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertEqual(
            ["POLICY classic_evidence.tracked_reference must be tracked by Git: docs/incidents/evidence.md"],
            result.lines,
        )

    def test_check_classic_symlink_evidence_refuses_before_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            target = root / "target.md"
            target.write_text("# Target\n", encoding="utf-8")
            evidence = root / "docs" / "incidents" / "evidence.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.symlink_to(target)
            track_path(root, "docs/incidents/evidence.md")
            repo = "RussianLioN/codex-problems-resolver"
            client = FakeGhClient()
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")

            result = repository_policy.check_policy(policy, repo, client, repo_root=root)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([], client.calls)
        self.assertEqual(
            ["POLICY classic_evidence.tracked_reference must be a regular tracked file, not a symlink: docs/incidents/evidence.md"],
            result.lines,
        )

    def test_check_classic_tracked_evidence_allows_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            write_evidence(root)
            track_path(root, "docs/incidents/evidence.md")
            repo = "RussianLioN/codex-problems-resolver"
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            client = FakeGhClient(
                {
                    ("GET", f"/repos/{repo}"): matching_repo(),
                    ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                    ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                    ("GET", f"/repos/{repo}/branches/main/protection"): repository_policy.expected_classic_branch_protection(
                        policy
                    ),
                }
            )

            result = repository_policy.check_policy(policy, repo, client, repo_root=root)

        self.assertEqual(0, result.exit_code)
        self.assertTrue(client.calls)

    def test_apply_classic_tracked_evidence_allows_api_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            write_evidence(root)
            track_path(root, "docs/incidents/evidence.md")
            repo = "RussianLioN/codex-problems-resolver"
            policy = classic_policy_with_evidence("docs/incidents/evidence.md")
            protected = {"done": False}

            def put_protection(method, path, payload):
                protected["done"] = True
                return {}

            def get_protection(method, path, payload):
                if protected["done"]:
                    return repository_policy.expected_classic_branch_protection(policy)
                raise repository_policy.GhApiError("branch not protected", exit_code=2, status=404)

            client = FakeGhClient(
                {
                    ("GET", f"/repos/{repo}"): matching_repo(),
                    ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                    ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                    ("PUT", f"/repos/{repo}/branches/main/protection"): put_protection,
                    ("GET", f"/repos/{repo}/branches/main/protection"): get_protection,
                }
            )

            result = repository_policy.apply_policy(policy, repo, repo, client, repo_root=root)

        self.assertEqual(0, result.exit_code)
        self.assertIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_cli_check_policy_path_rejects_missing_evidence_before_github_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            init_git_repo(root)
            policy_path = root / "ops" / "github" / "repository-policy.json"
            policy_path.parent.mkdir(parents=True, exist_ok=True)
            policy = classic_policy_with_evidence()
            policy["main_protection"]["classic_evidence"]["tracked_reference"] = "docs/incidents/missing.md"
            policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            client = FakeGhClient()

            with mock.patch("repository_policy.GhClient", return_value=client):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    exit_code = repository_policy.main(
                        ["check", "--repo", "RussianLioN/codex-problems-resolver", "--policy", str(policy_path)]
                    )

        self.assertEqual(1, exit_code)
        self.assertEqual([], client.calls)
        self.assertIn(
            "POLICY classic_evidence.tracked_reference does not exist: docs/incidents/missing.md",
            stdout.getvalue(),
        )

    def test_classic_backend_with_evidence_checks_only_classic_protection(self):
        repo = "RussianLioN/codex-problems-resolver"
        policy = classic_policy_with_evidence()
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/branches/main/protection"): repository_policy.expected_classic_branch_protection(
                    policy
                ),
            }
        )

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertNotIn(("GET", f"/repos/{repo}/rulesets"), [(m, p) for m, p, _ in client.calls])

    def test_builtin_mode_classic_backend_reports_admin_only_notice(self):
        repo = "RussianLioN/codex-problems-resolver"
        policy = classic_policy_with_evidence()
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
            }
        )

        result = repository_policy.check_policy(policy, repo, client, mode="builtin")

        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            [
                "NOTICE: builtin mode did not check repository merge settings",
                "NOTICE: builtin mode did not check Actions settings",
                "NOTICE: builtin mode cannot verify classic branch protection because it requires Repository Administration read permission",
            ],
            result.lines,
        )
        self.assertNotIn(("GET", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_cli_check_accepts_builtin_mode(self):
        parser = repository_policy.build_parser()

        args = parser.parse_args(["check", "--repo", "RussianLioN/codex-problems-resolver", "--mode", "builtin"])

        self.assertEqual("builtin", args.mode)

    def test_classic_backend_with_evidence_apply_updates_only_classic_protection(self):
        repo = "RussianLioN/codex-problems-resolver"
        policy = classic_policy_with_evidence()
        protected = {"done": False}

        def put_protection(method, path, payload):
            protected["done"] = True
            return {}

        def get_protection(method, path, payload):
            if protected["done"]:
                return repository_policy.expected_classic_branch_protection(policy)
            raise repository_policy.GhApiError("branch not protected", exit_code=2, status=404)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("PUT", f"/repos/{repo}/branches/main/protection"): put_protection,
                ("GET", f"/repos/{repo}/branches/main/protection"): get_protection,
            }
        )

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("GET", f"/repos/{repo}/rulesets"), [(m, p) for m, p, _ in client.calls])

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

    def test_apply_puts_complete_actions_permissions_payload(self):
        repo = "RussianLioN/codex-problems-resolver"
        permissions = matching_actions_permissions()
        permissions["sha_pinning_required"] = False
        captured_payloads = []

        def put_actions_permissions(method, path, payload):
            captured_payloads.append(payload)
            permissions.update(payload)
            return permissions

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): lambda method, path, payload: permissions,
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("PUT", f"/repos/{repo}/actions/permissions"): put_actions_permissions,
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): matching_ruleset(),
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            [
                {
                    "enabled": True,
                    "allowed_actions": "all",
                    "sha_pinning_required": True,
                }
            ],
            captured_payloads,
        )

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
                ("PUT", f"/repos/{repo}/rulesets/101"): update_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(0, result.exit_code)
        self.assertIn(("PUT", f"/repos/{repo}/rulesets/101"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("PATCH", f"/repos/{repo}/rulesets/101"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("PUT", f"/repos/{repo}/rulesets/202"), [(m, p) for m, p, _ in client.calls])
        self.assertNotIn(("PATCH", f"/repos/{repo}/rulesets/202"), [(m, p) for m, p, _ in client.calls])
        self.assertFalse([call for call in client.calls if call[0] == "DELETE"])

    def test_apply_feature_unavailable_uses_classic_but_reports_ruleset_drift_until_policy_changes(self):
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

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            [
                "NOTICE: classic fallback applied after ruleset plan/feature unavailability; record tracked evidence and set main_protection.backend=classic in a follow-up policy change",
                "DRIFT ruleset.main-protection: expected present actual missing",
            ],
            result.lines,
        )
        self.assertIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_classic_branch_protection_payload_enforces_admins(self):
        repo = "RussianLioN/codex-problems-resolver"
        fallback_state = {"protected": False}
        captured_payloads = []

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("rulesets feature is unavailable for this plan", exit_code=2, status=403)

        def put_protection(method, path, payload):
            captured_payloads.append(payload)
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

        self.assertEqual(1, result.exit_code)
        self.assertEqual(True, captured_payloads[0]["enforce_admins"])

    def test_classic_branch_protection_payload_has_empty_bypass_allowances(self):
        policy = classic_policy_with_evidence()

        payload = repository_policy.expected_classic_branch_protection(policy)

        self.assertEqual(
            {"users": [], "teams": [], "apps": []},
            payload["required_pull_request_reviews"]["bypass_pull_request_allowances"],
        )

    def test_apply_feature_unavailable_returns_two_when_classic_fallback_fails(self):
        repo = "RussianLioN/codex-problems-resolver"

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("rulesets feature is unavailable for this plan", exit_code=2, status=403)

        def put_protection(method, path, payload):
            raise repository_policy.GhApiError("classic branch protection failed", exit_code=2, status=500)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
                ("PUT", f"/repos/{repo}/branches/main/protection"): put_protection,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertEqual(
            [
                "ERROR: ruleset creation unavailable: rulesets feature is unavailable for this plan; classic fallback failed: classic branch protection failed",
            ],
            result.lines,
        )

    def test_check_reports_classic_enforce_admins_drift(self):
        repo = "RussianLioN/codex-problems-resolver"
        policy = classic_policy_with_evidence()
        live_protection = repository_policy.expected_classic_branch_protection(policy)
        live_protection["enforce_admins"] = {"enabled": False}
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("GET", f"/repos/{repo}/branches/main/protection"): live_protection,
            }
        )

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual(
            ["DRIFT classic_branch_protection.enforce_admins: expected true actual false"],
            result.lines,
        )

    def test_check_reports_classic_bypass_user_allowance_drift(self):
        self._assert_classic_bypass_allowance_drift(
            {"users": [{"login": "octocat"}], "teams": [], "apps": []},
            'DRIFT classic_branch_protection.required_pull_request_reviews.bypass_pull_request_allowances.users: expected [] actual [{"login": "octocat"}]',
        )

    def test_check_reports_classic_bypass_team_allowance_drift(self):
        self._assert_classic_bypass_allowance_drift(
            {"users": [], "teams": [{"slug": "admins"}], "apps": []},
            'DRIFT classic_branch_protection.required_pull_request_reviews.bypass_pull_request_allowances.teams: expected [] actual [{"slug": "admins"}]',
        )

    def test_check_reports_classic_bypass_app_allowance_drift(self):
        self._assert_classic_bypass_allowance_drift(
            {"users": [], "teams": [], "apps": [{"slug": "deploy-bot"}]},
            'DRIFT classic_branch_protection.required_pull_request_reviews.bypass_pull_request_allowances.apps: expected [] actual [{"slug": "deploy-bot"}]',
        )

    def _assert_classic_bypass_allowance_drift(self, allowances, expected_line):
        repo = "RussianLioN/codex-problems-resolver"
        policy = classic_policy_with_evidence()
        live_protection = repository_policy.expected_classic_branch_protection(policy)
        live_protection["required_pull_request_reviews"]["bypass_pull_request_allowances"] = allowances
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/branches/main/protection"): live_protection,
            }
        )

        result = repository_policy.check_policy(policy, repo, client)

        self.assertEqual(1, result.exit_code)
        self.assertEqual([expected_line], result.lines)

    def test_apply_ruleset_not_found_without_feature_evidence_returns_two_and_does_not_use_classic(self):
        repo = "RussianLioN/codex-problems-resolver"

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("ruleset not found", exit_code=2, status=404)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("ruleset not found", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_apply_ruleset_auth_error_with_ruleset_words_returns_two_and_does_not_use_classic(self):
        repo = "RussianLioN/codex-problems-resolver"

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("auth failed for rulesets feature", exit_code=2, status=403)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("auth failed", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_apply_bad_ruleset_url_error_returns_two_and_does_not_use_classic(self):
        repo = "RussianLioN/codex-problems-resolver"

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("bad URL for rulesets", exit_code=2, status=404)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("bad URL", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_apply_does_not_fallback_when_ruleset_update_reports_plan_feature_unavailable(self):
        repo = "RussianLioN/codex-problems-resolver"

        def update_ruleset(method, path, payload):
            raise repository_policy.GhApiError("rulesets feature is unavailable for this plan", exit_code=2, status=403)

        current_ruleset = matching_ruleset()
        current_ruleset["enforcement"] = "disabled"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [matching_ruleset()],
                ("GET", f"/repos/{repo}/rulesets/101"): current_ruleset,
                ("PUT", f"/repos/{repo}/rulesets/101"): update_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("rulesets feature is unavailable", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_apply_does_not_fallback_when_ruleset_list_reports_plan_feature_unavailable(self):
        repo = "RussianLioN/codex-problems-resolver"
        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
            },
            errors={
                ("GET", f"/repos/{repo}/rulesets"): repository_policy.GhApiError(
                    "rulesets feature is unavailable for this plan", exit_code=2, status=403
                )
            },
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("rulesets feature is unavailable", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_apply_ruleset_permission_error_without_feature_evidence_returns_two_and_does_not_use_classic(self):
        repo = "RussianLioN/codex-problems-resolver"

        def create_ruleset(method, path, payload):
            raise repository_policy.GhApiError("permission denied for ruleset endpoint", exit_code=2, status=403)

        client = FakeGhClient(
            {
                ("GET", f"/repos/{repo}"): matching_repo(),
                ("GET", f"/repos/{repo}/actions/permissions"): matching_actions_permissions(),
                ("GET", f"/repos/{repo}/actions/permissions/workflow"): matching_workflow_permissions(),
                ("GET", f"/repos/{repo}/rulesets"): [],
                ("POST", f"/repos/{repo}/rulesets"): create_ruleset,
            }
        )
        policy = repository_policy.load_policy(POLICY_PATH)

        result = repository_policy.apply_policy(policy, repo, repo, client)

        self.assertEqual(2, result.exit_code)
        self.assertIn("permission denied", result.lines[0])
        self.assertNotIn(("PUT", f"/repos/{repo}/branches/main/protection"), [(m, p) for m, p, _ in client.calls])

    def test_cli_validate_reports_schema_errors(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = repository_policy.main(["validate", "--policy", str(POLICY_PATH)])

        self.assertEqual(0, exit_code)
        self.assertIn("OK: policy schema is valid", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
