import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_repository


class ValidateRepositoryTests(unittest.TestCase):
    def test_current_repository_is_valid(self):
        result = validate_repository.validate_root(Path(__file__).resolve().parents[1])

        self.assertEqual([], result.errors)

    def test_external_workflow_uses_must_be_pinned_to_full_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            workflow = root / ".github" / "workflows" / "governance.yml"
            workflow.write_text(
                workflow.read_text(encoding="utf-8").replace(
                    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
                    "actions/checkout@v4",
                ),
                encoding="utf-8",
            )

            result = validate_repository.validate_root(root)

        self.assertIn(
            ".github/workflows/governance.yml: external uses entry is not pinned to a full 40-hex SHA: actions/checkout@v4",
            result.errors,
        )

    def test_governance_drift_job_passes_github_token_through_environment(self):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "governance.yml"
        text = workflow.read_text(encoding="utf-8")

        self.assertIn("secrets.POLICY_AUDIT_TOKEN", text)
        self.assertIn("name: Select drift check mode", text)
        self.assertIn("DRIFT_MODE=full", text)
        self.assertIn("DRIFT_MODE=builtin", text)
        self.assertIn("--mode full", text)
        self.assertIn("--mode builtin", text)
        self.assertIn("GH_TOKEN: ${{ secrets.POLICY_AUDIT_TOKEN }}", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertIn("::notice::POLICY_AUDIT_TOKEN is not configured; running builtin drift check", text)
        self.assertIn("$GITHUB_STEP_SUMMARY", text)
        self.assertNotIn("--token", text)
        self.assertNotIn("actions: read", text)

    def test_governance_workflow_never_uses_github_token_for_full_drift_check(self):
        workflow = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "governance.yml"
        text = workflow.read_text(encoding="utf-8")

        self.assertNotIn("--mode full\n        env:\n          GH_TOKEN: ${{ github.token }}", text)

    def test_required_path_that_exists_but_is_git_ignored_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (root / ".gitignore").write_text("scripts/\n", encoding="utf-8")
            for rel in validate_repository.REQUIRED_PATHS:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")

            result = validate_repository.validate_root(root)

        self.assertIn(
            "scripts/validate_repository.py: required repository path is missing or ignored",
            result.errors,
        )

    def test_required_path_that_exists_but_is_untracked_is_reported_in_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for rel in validate_repository.REQUIRED_PATHS:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")
                if rel != "scripts/validate_repository.py":
                    subprocess.run(["git", "add", rel], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            result = validate_repository.validate_root(root)

        self.assertIn(
            "scripts/validate_repository.py: required repository path is missing or ignored",
            result.errors,
        )

    def test_required_paths_in_plain_directory_are_checked_by_existence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in validate_repository.REQUIRED_PATHS:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("placeholder\n", encoding="utf-8")

            result = validate_repository.validate_root(root)

        self.assertNotIn(
            "scripts/validate_repository.py: required repository path is missing or ignored",
            result.errors,
        )

    def test_agents_md_word_count_must_be_between_200_and_400_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            (root / "AGENTS.md").write_text("word " * 199 + "\n", encoding="utf-8")

            low_result = validate_repository.validate_root(root)

            (root / "AGENTS.md").write_text("word " * 401 + "\n", encoding="utf-8")

            high_result = validate_repository.validate_root(root)

        self.assertIn("AGENTS.md: word count must be between 200 and 400 words, got 199", low_result.errors)
        self.assertIn("AGENTS.md: word count must be between 200 and 400 words, got 401", high_result.errors)

    def test_current_agents_md_word_count_is_valid(self):
        result = validate_repository.validate_root(Path(__file__).resolve().parents[1])

        self.assertNotIn(
            "AGENTS.md: word count must be between 200 and 400 words, got 328",
            result.errors,
        )

    def test_classic_evidence_reference_must_exist_in_plain_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            policy = self._write_classic_policy(root, "docs/incidents/2026-08-05-ruleset-plan-limitation.md")

            result = validate_repository.validate_root(root)

        self.assertIn(
            f"{policy}: classic_evidence.tracked_reference does not exist: docs/incidents/2026-08-05-ruleset-plan-limitation.md",
            result.errors,
        )

    def test_classic_evidence_reference_must_be_tracked_in_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            evidence = root / "docs" / "incidents" / "2026-08-05-ruleset-plan-limitation.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("# Evidence\n", encoding="utf-8")
            policy = self._write_classic_policy(root, evidence.relative_to(root).as_posix())

            result = validate_repository.validate_root(root)

        self.assertIn(
            f"{policy}: classic_evidence.tracked_reference must be tracked by Git: docs/incidents/2026-08-05-ruleset-plan-limitation.md",
            result.errors,
        )

    def test_classic_evidence_reference_tracked_file_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            evidence = root / "docs" / "incidents" / "2026-08-05-ruleset-plan-limitation.md"
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text("# Evidence\n", encoding="utf-8")
            policy = self._write_classic_policy(root, evidence.relative_to(root).as_posix())
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "add", "."], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            result = validate_repository.validate_root(root)

        self.assertNotIn(
            f"{policy}: classic_evidence.tracked_reference must be tracked by Git: docs/incidents/2026-08-05-ruleset-plan-limitation.md",
            result.errors,
        )
        self.assertNotIn(
            f"{policy}: classic_evidence.tracked_reference does not exist: docs/incidents/2026-08-05-ruleset-plan-limitation.md",
            result.errors,
        )

    def _write_classic_policy(self, root, reference):
        rel = "ops/github/repository-policy.json"
        policy_path = root / rel
        data = json.loads(policy_path.read_text(encoding="utf-8"))
        data["main_protection"]["backend"] = "classic"
        data["main_protection"]["classic_evidence"] = {
            "status": 403,
            "operation": "POST /repos/RussianLioN/codex-problems-resolver/rulesets",
            "category": "plan_feature_unavailable",
            "message_excerpt": "rulesets feature is unavailable for this plan",
            "tracked_reference": reference,
        }
        policy_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return rel

    def test_job_level_reusable_workflow_uses_must_be_pinned_to_full_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            workflow = root / ".github" / "workflows" / "governance.yml"
            workflow.write_text(
                """
name: reusable
on:
  workflow_dispatch:
jobs:
  call:
    uses: owner/repo/.github/workflows/reusable.yml@v1
""".lstrip(),
                encoding="utf-8",
            )

            result = validate_repository.validate_root(root)

        self.assertIn(
            ".github/workflows/governance.yml: external uses entry is not pinned to a full 40-hex SHA: owner/repo/.github/workflows/reusable.yml@v1",
            result.errors,
        )

    def test_external_uses_allows_full_sha_with_inline_comment_and_local_relative_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            workflow = root / ".github" / "workflows" / "governance.yml"
            workflow.write_text(
                """
name: local-and-pinned
on:
  workflow_dispatch:
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # pinned v4
      - uses: ./.github/actions/local
""".lstrip(),
                encoding="utf-8",
            )

            result = validate_repository.validate_root(root)

        self.assertNotIn(
            ".github/workflows/governance.yml: external uses entry is not pinned to a full 40-hex SHA: actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            result.errors,
        )

    def test_quoted_external_uses_must_be_pinned_to_full_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            workflow = root / ".github" / "workflows" / "governance.yml"
            workflow.write_text(
                """
name: quoted
on:
  workflow_dispatch:
jobs:
  validate:
    uses: "owner/repo/.github/workflows/reusable.yml@v1" # quoted reusable
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: 'actions/checkout@v4'
""".lstrip(),
                encoding="utf-8",
            )

            result = validate_repository.validate_root(root)

        self.assertIn(
            ".github/workflows/governance.yml: external uses entry is not pinned to a full 40-hex SHA: owner/repo/.github/workflows/reusable.yml@v1",
            result.errors,
        )
        self.assertIn(
            ".github/workflows/governance.yml: external uses entry is not pinned to a full 40-hex SHA: actions/checkout@v4",
            result.errors,
        )

    def test_quoted_full_sha_and_quoted_local_uses_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            workflow = root / ".github" / "workflows" / "governance.yml"
            workflow.write_text(
                """
name: quoted-pinned
on:
  workflow_dispatch:
jobs:
  validate:
    uses: "owner/repo/.github/workflows/reusable.yml@11d5960a326750d5838078e36cf38b85af677262" # quoted full sha
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: './.github/actions/local'
      - uses: "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" # quoted full sha
""".lstrip(),
                encoding="utf-8",
            )

            result = validate_repository.validate_root(root)

        self.assertEqual([], [error for error in result.errors if "external uses entry" in error])

    def test_policy_schema_errors_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            policy = root / "ops" / "github" / "repository-policy.json"
            data = json.loads(policy.read_text(encoding="utf-8"))
            data["repository"] = "Wrong/Repository"
            policy.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = validate_repository.validate_root(root)

        self.assertIn(
            "ops/github/repository-policy.json: repository must be RussianLioN/codex-problems-resolver",
            result.errors,
        )

    def test_cli_returns_one_for_validation_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(Path(__file__).resolve().parents[1], root, dirs_exist_ok=True)
            (root / "README.md").write_text("# Broken\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "validate_repository.py"),
                    "--root",
                    str(root),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(1, completed.returncode)
        self.assertIn("README.md: missing required heading: ## Текущее состояние", completed.stdout)


if __name__ == "__main__":
    unittest.main()
