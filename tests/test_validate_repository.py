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

        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertNotIn("--token", text)

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
