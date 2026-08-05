#!/usr/bin/env python3
"""Validate, check, and apply the GitHub repository policy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path("ops/github/repository-policy.json")
EXPECTED_REPOSITORY = "RussianLioN/codex-problems-resolver"
RULESET_NAME = "main-protection"


@dataclass
class CommandResult:
    exit_code: int
    lines: list[str]


class GhApiError(Exception):
    def __init__(self, message: str, exit_code: int = 2, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.status = status


class GhClient:
    def call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        command = ["gh", "api", "--method", method, path]
        input_text = None
        if payload is not None:
            command.extend(["--input", "-"])
            input_text = json.dumps(payload, ensure_ascii=False)
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GhApiError("gh command is not available", exit_code=2) from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "gh api failed").strip()
            raise GhApiError(message, exit_code=2, status=_extract_status(message))
        if not completed.stdout.strip():
            return {}
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise GhApiError(f"gh api returned non-JSON output for {path}", exit_code=2) from exc


def _extract_status(message: str) -> int | None:
    for code in (401, 403, 404, 422, 500):
        if str(code) in message:
            return code
    return None


def load_policy(path: Path | str = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = {
        "schema_version": 1,
        "repository": EXPECTED_REPOSITORY,
        "visibility": "private",
        "default_branch": "main",
    }
    for key, value in expected.items():
        if policy.get(key) != value:
            errors.append(f"{key} must be {value}")

    features = policy.get("features")
    if features != {"issues": True, "wiki": False, "projects": False}:
        errors.append("features must enable issues and disable wiki/projects")

    merge = policy.get("merge")
    if merge != {"squash": True, "merge_commit": False, "rebase": False, "delete_branch_on_merge": True}:
        errors.append("merge settings must allow squash only and delete merged branches")

    actions = policy.get("actions")
    if actions != {
        "enabled": True,
        "allowed_actions": "all",
        "default_workflow_permissions": "read",
        "workflow_pr_approval": False,
        "sha_pinning_required": True,
        "pin_external_uses_to_full_sha": True,
    }:
        errors.append("actions settings must require enabled Actions, all actions, SHA pinning, read workflow permission, and no PR approval")

    protection = policy.get("main_protection")
    if not isinstance(protection, dict):
        errors.append("main_protection must be an object")
        return errors
    required_protection = {
        "name": RULESET_NAME,
        "bypass_actors": [],
        "target": "branch",
        "enforcement": "active",
        "include": ["~DEFAULT_BRANCH"],
        "exclude": [],
        "require_pull_request": True,
        "required_approving_review_count": 0,
        "required_review_thread_resolution": True,
        "required_linear_history": True,
        "required_status_checks": ["validate"],
        "strict_required_status_checks_policy": True,
        "block_deletions": True,
        "block_force_pushes": True,
    }
    backend = protection.get("backend")
    if backend not in {"ruleset", "classic"}:
        errors.append("main_protection.backend must be ruleset or classic")
    if backend == "classic":
        errors.extend(_validate_classic_evidence(policy, protection))
    actual_protection = {key: protection.get(key) for key in required_protection}
    if actual_protection != required_protection:
        errors.append("main_protection must match the required main-protection ruleset")

    return errors


def _validate_classic_evidence(policy: dict[str, Any], protection: dict[str, Any]) -> list[str]:
    evidence = protection.get("classic_evidence")
    if not isinstance(evidence, dict):
        return ["main_protection.classic_evidence is required when backend is classic"]
    expected_operation = f"POST /repos/{policy.get('repository')}/rulesets"
    errors: list[str] = []
    if evidence.get("status") not in {403, 404}:
        errors.append("main_protection.classic_evidence.status must be 403 or 404")
    if evidence.get("operation") != expected_operation:
        errors.append(f"main_protection.classic_evidence.operation must be {expected_operation}")
    if evidence.get("category") != "plan_feature_unavailable":
        errors.append("main_protection.classic_evidence.category must be plan_feature_unavailable")
    message = str(evidence.get("message_excerpt", "")).lower()
    if not message or "ruleset" not in message or not any(word in message for word in ("plan", "feature")):
        errors.append("main_protection.classic_evidence.message_excerpt must mention ruleset(s) and plan/feature")
    if not evidence.get("tracked_reference"):
        errors.append("main_protection.classic_evidence.tracked_reference is required")
    return errors


def expected_repository_settings(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "private": policy["visibility"] == "private",
        "default_branch": policy["default_branch"],
        "has_issues": policy["features"]["issues"],
        "has_wiki": policy["features"]["wiki"],
        "has_projects": policy["features"]["projects"],
        "allow_squash_merge": policy["merge"]["squash"],
        "allow_merge_commit": policy["merge"]["merge_commit"],
        "allow_rebase_merge": policy["merge"]["rebase"],
        "delete_branch_on_merge": policy["merge"]["delete_branch_on_merge"],
    }


def expected_actions_permissions(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": policy["actions"]["enabled"],
        "allowed_actions": policy["actions"]["allowed_actions"],
        "sha_pinning_required": policy["actions"]["sha_pinning_required"],
    }


def expected_workflow_permissions(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "default_workflow_permissions": policy["actions"]["default_workflow_permissions"],
        "can_approve_pull_request_reviews": policy["actions"]["workflow_pr_approval"],
    }


def expected_ruleset_payload(policy: dict[str, Any]) -> dict[str, Any]:
    protection = policy["main_protection"]
    return {
        "name": protection["name"],
        "bypass_actors": protection["bypass_actors"],
        "target": protection["target"],
        "enforcement": protection["enforcement"],
        "conditions": {
            "ref_name": {
                "include": protection["include"],
                "exclude": protection["exclude"],
            }
        },
        "rules": [
            {
                "type": "pull_request",
                "parameters": {
                    "required_approving_review_count": protection["required_approving_review_count"],
                    "dismiss_stale_reviews_on_push": False,
                    "require_code_owner_review": False,
                    "require_last_push_approval": False,
                    "required_review_thread_resolution": protection["required_review_thread_resolution"],
                },
            },
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": protection["strict_required_status_checks_policy"],
                    "required_status_checks": [
                        {"context": check} for check in protection["required_status_checks"]
                    ],
                },
            },
            {"type": "required_linear_history"},
            {"type": "deletion"},
            {"type": "non_fast_forward"},
        ],
    }


def expected_classic_branch_protection(policy: dict[str, Any]) -> dict[str, Any]:
    protection = policy["main_protection"]
    checks = protection["required_status_checks"]
    return {
        "required_status_checks": {
            "strict": protection["strict_required_status_checks_policy"],
            "contexts": checks,
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "required_approving_review_count": protection["required_approving_review_count"],
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": True,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": protection["required_review_thread_resolution"],
    }


def normalize_ruleset(ruleset: dict[str, Any], include_bypass_actors: bool = True) -> dict[str, Any]:
    normalized = {
        "name": ruleset.get("name"),
        "target": ruleset.get("target"),
        "enforcement": ruleset.get("enforcement"),
        "conditions": ruleset.get("conditions", {}),
        "rules": [],
    }
    if include_bypass_actors:
        normalized["bypass_actors"] = _normalize_bypass_actors(ruleset.get("bypass_actors"))
    for rule in ruleset.get("rules", []):
        item = {"type": rule.get("type")}
        if "parameters" in rule:
            item["parameters"] = rule.get("parameters") or {}
        normalized["rules"].append(item)
    normalized["rules"] = sorted(normalized["rules"], key=lambda item: item["type"] or "")
    return normalized


def _normalize_bypass_actors(value: Any) -> list[dict[str, Any]]:
    actors = value or []
    return sorted(actors, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))


def _without_ids(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in {"id", "node_id", "_links"}}


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _diff(expected: Any, actual: Any, prefix: str) -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        lines: list[str] = []
        for key in sorted(expected):
            lines.extend(_diff(expected.get(key), actual.get(key), f"{prefix}.{key}"))
        return lines
    if isinstance(expected, list) and isinstance(actual, list):
        if expected == actual:
            return []
        return [f"DRIFT {prefix}: expected {_json_value(expected)} actual {_json_value(actual)}"]
    if expected != actual:
        return [f"DRIFT {prefix}: expected {_json_value(expected)} actual {_json_value(actual)}"]
    return []


def _get_rulesets(client: Any, repo: str) -> list[dict[str, Any]]:
    response = client.call("GET", f"/repos/{repo}/rulesets")
    if isinstance(response, dict) and "rulesets" in response:
        response = response["rulesets"]
    return list(response or [])


def _get_named_ruleset(client: Any, repo: str, name: str) -> dict[str, Any] | None:
    for ruleset in _get_rulesets(client, repo):
        if ruleset.get("name") == name:
            ruleset_id = ruleset.get("id")
            if ruleset_id is None:
                return ruleset
            return client.call("GET", f"/repos/{repo}/rulesets/{ruleset_id}")
    return None


def _normalize_classic_protection(raw: dict[str, Any]) -> dict[str, Any]:
    reviews = raw.get("required_pull_request_reviews") or {}
    checks = raw.get("required_status_checks") or {}
    contexts = checks.get("contexts")
    if contexts is None:
        contexts = [item.get("context") for item in checks.get("checks", []) if item.get("context")]
    return {
        "required_status_checks": {
            "strict": checks.get("strict"),
            "contexts": sorted(contexts or []),
        },
        "enforce_admins": _enabled_value(raw.get("enforce_admins")),
        "required_pull_request_reviews": {
            "required_approving_review_count": reviews.get("required_approving_review_count"),
            "dismiss_stale_reviews": reviews.get("dismiss_stale_reviews", False),
            "require_code_owner_reviews": reviews.get("require_code_owner_reviews", False),
            "require_last_push_approval": reviews.get("require_last_push_approval", False),
        },
        "restrictions": None,
        "required_linear_history": _enabled_value(raw.get("required_linear_history")),
        "allow_force_pushes": _enabled_value(raw.get("allow_force_pushes")),
        "allow_deletions": _enabled_value(raw.get("allow_deletions")),
        "required_conversation_resolution": _enabled_value(raw.get("required_conversation_resolution")),
    }


def _enabled_value(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("enabled", False))
    return bool(value)


def check_policy(policy: dict[str, Any], repo: str, client: Any, mode: str = "full") -> CommandResult:
    validation_errors = validate_policy(policy)
    if validation_errors:
        return CommandResult(1, [f"POLICY {error}" for error in validation_errors])
    if mode not in {"full", "builtin"}:
        return CommandResult(1, [f"MODE must be full or builtin, got {mode}"])
    if repo != policy["repository"]:
        return CommandResult(1, [f"REPO argument must be {policy['repository']}"])

    try:
        live_repo = client.call("GET", f"/repos/{repo}")
        lines = _diff(expected_repository_settings(policy), live_repo, "repository")
        notices: list[str] = []
        if mode == "full":
            live_actions = _call_full_admin_read(client, repo, f"/repos/{repo}/actions/permissions")
            live_workflow = _call_full_admin_read(client, repo, f"/repos/{repo}/actions/permissions/workflow")
            lines.extend(_diff(expected_actions_permissions(policy), live_actions, "actions"))
            lines.extend(_diff(expected_workflow_permissions(policy), live_workflow, "actions.workflow"))
        else:
            notices.append("NOTICE: builtin mode did not check Actions settings")

        backend = policy["main_protection"]["backend"]
        if backend == "classic":
            if mode == "builtin":
                notices.append(
                    "NOTICE: builtin mode cannot verify classic branch protection because it requires Repository Administration read permission"
                )
            else:
                classic_result = _check_classic_branch_protection(policy, repo, client)
                if classic_result.exit_code == 2:
                    return classic_result
                if classic_result.exit_code == 0:
                    lines.extend(classic_result.lines)
                else:
                    lines.extend(classic_result.lines)
        else:
            ruleset = _get_named_ruleset(client, repo, RULESET_NAME)
            if ruleset is None:
                lines.append(f"DRIFT ruleset.{RULESET_NAME}: expected present actual missing")
            else:
                bypass_visible = "bypass_actors" in ruleset
                if mode == "full" and not bypass_visible:
                    return CommandResult(
                        2,
                        [
                            "ERROR: full mode cannot verify ruleset bypass_actors for main-protection; token lacks sufficient ruleset detail visibility",
                        ],
                    )
                include_bypass = mode == "full" or bypass_visible
                if mode == "builtin" and not bypass_visible:
                    notices.append(
                        "NOTICE: builtin mode did not verify ruleset bypass_actors because the field is not visible"
                    )
                expected = normalize_ruleset(expected_ruleset_payload(policy), include_bypass_actors=include_bypass)
                actual = normalize_ruleset(_without_ids(ruleset), include_bypass_actors=include_bypass)
                lines.extend(_diff(expected, actual, "ruleset.main-protection"))
    except GhApiError as exc:
        return CommandResult(2, [f"ERROR: {exc.message}"])

    if lines:
        drift = [line for line in lines if line.startswith("DRIFT")]
        if drift:
            return CommandResult(1, drift)
        return CommandResult(0, lines)
    if notices:
        return CommandResult(0, notices)
    return CommandResult(0, ["OK: repository policy matches"])


def _call_full_admin_read(client: Any, repo: str, path: str) -> Any:
    try:
        return client.call("GET", path)
    except GhApiError as exc:
        if exc.status == 403:
            raise GhApiError(
                f"full mode requires a token with Repository Administration read permission for {path}: {exc.message}",
                exit_code=2,
                status=403,
            ) from exc
        raise


def _check_classic_branch_protection(policy: dict[str, Any], repo: str, client: Any) -> CommandResult:
    try:
        live = client.call("GET", f"/repos/{repo}/branches/{policy['default_branch']}/protection")
    except GhApiError as exc:
        if exc.status == 404 and "branch not protected" in exc.message.lower():
            return CommandResult(1, ["DRIFT classic_branch_protection: expected present actual missing"])
        return CommandResult(2, [f"ERROR: {exc.message}"])
    if not live:
        return CommandResult(1, ["DRIFT classic_branch_protection: expected present actual missing"])
    lines = _diff(
        expected_classic_branch_protection(policy),
        _normalize_classic_protection(live),
        "classic_branch_protection",
    )
    if lines:
        return CommandResult(1, lines)
    return CommandResult(0, ["OK: repository policy matches via classic branch protection fallback"])


def _needs_update(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    return bool(_diff(expected, actual, "state"))


def apply_policy(policy: dict[str, Any], repo: str, confirm: str, client: Any) -> CommandResult:
    if confirm != repo or confirm != policy.get("repository"):
        return CommandResult(
            1,
            [f"--confirm must exactly match --repo and policy repository ({policy.get('repository')}) before any API call"],
        )
    validation_errors = validate_policy(policy)
    if validation_errors:
        return CommandResult(1, [f"POLICY {error}" for error in validation_errors])

    try:
        live_repo = client.call("GET", f"/repos/{repo}")
        repo_patch = {
            key: value
            for key, value in expected_repository_settings(policy).items()
            if live_repo.get(key) != value
        }
        if repo_patch:
            client.call("PATCH", f"/repos/{repo}", repo_patch)

        live_actions = client.call("GET", f"/repos/{repo}/actions/permissions")
        actions_payload = expected_actions_permissions(policy)
        if _needs_update(actions_payload, live_actions):
            client.call("PUT", f"/repos/{repo}/actions/permissions", actions_payload)

        live_workflow = client.call("GET", f"/repos/{repo}/actions/permissions/workflow")
        workflow_payload = expected_workflow_permissions(policy)
        if _needs_update(workflow_payload, live_workflow):
            client.call("PUT", f"/repos/{repo}/actions/permissions/workflow", workflow_payload)

        fallback_applied = False
        if policy["main_protection"]["backend"] == "classic":
            client.call(
                "PUT",
                f"/repos/{repo}/branches/{policy['default_branch']}/protection",
                expected_classic_branch_protection(policy),
            )
        else:
            fallback_applied = _upsert_ruleset_or_fallback(policy, repo, client)
        result = check_policy(policy, repo, client)
        if fallback_applied and result.exit_code == 1:
            return CommandResult(
                1,
                [
                    "NOTICE: classic fallback applied after ruleset plan/feature unavailability; record tracked evidence and set main_protection.backend=classic in a follow-up policy change",
                    *result.lines,
                ],
            )
        if result.exit_code == 0:
            return CommandResult(0, ["OK: repository policy applied", *result.lines])
        return result
    except GhApiError as exc:
        return CommandResult(2, [f"ERROR: {exc.message}"])


def _upsert_ruleset_or_fallback(policy: dict[str, Any], repo: str, client: Any) -> bool:
    payload = expected_ruleset_payload(policy)
    existing = None
    existing_id = None
    for ruleset in _get_rulesets(client, repo):
        if ruleset.get("name") == RULESET_NAME:
            existing_id = ruleset.get("id")
            existing = client.call("GET", f"/repos/{repo}/rulesets/{existing_id}") if existing_id is not None else ruleset
            break

    if existing is None:
        try:
            client.call("POST", f"/repos/{repo}/rulesets", payload)
            return False
        except GhApiError as exc:
            if _is_ruleset_feature_unavailable(exc):
                try:
                    client.call(
                        "PUT",
                        f"/repos/{repo}/branches/{policy['default_branch']}/protection",
                        expected_classic_branch_protection(policy),
                    )
                except GhApiError as fallback_exc:
                    raise GhApiError(
                        f"ruleset creation unavailable: {exc.message}; classic fallback failed: {fallback_exc.message}",
                        exit_code=2,
                        status=fallback_exc.status,
                    ) from fallback_exc
                return True
            raise
    if _needs_update(normalize_ruleset(payload), normalize_ruleset(_without_ids(existing))):
        client.call("PATCH", f"/repos/{repo}/rulesets/{existing_id}", payload)
    return False


def _is_ruleset_feature_unavailable(error: GhApiError) -> bool:
    text = error.message.lower()
    blocked = any(phrase in text for phrase in ("auth", "permission", "not found", "bad url", "bad URL".lower(), "url"))
    has_plan_or_feature = any(phrase in text for phrase in ("plan", "feature", "not available", "unavailable"))
    has_ruleset = "ruleset" in text or "rulesets" in text
    return error.status in {403, 404} and has_ruleset and has_plan_or_feature and not blocked


def _print_result(result: CommandResult) -> int:
    for line in result.lines:
        print(line)
    return result.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and reconcile GitHub repository policy.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)

    check_parser = subparsers.add_parser("check")
    check_parser.add_argument("--repo", required=True)
    check_parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    check_parser.add_argument("--mode", choices=("full", "builtin"), default="full")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--repo", required=True)
    apply_parser.add_argument("--confirm", required=True)
    apply_parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read policy: {exc}")
        return 1

    if args.command == "validate":
        errors = validate_policy(policy)
        if errors:
            for error in errors:
                print(f"POLICY {error}")
            return 1
        print("OK: policy schema is valid")
        return 0

    client = GhClient()
    if args.command == "check":
        return _print_result(check_policy(policy, args.repo, client, mode=args.mode))
    if args.command == "apply":
        return _print_result(apply_policy(policy, args.repo, args.confirm, client))
    return 2


if __name__ == "__main__":
    sys.exit(main())
