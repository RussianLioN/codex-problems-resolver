#!/usr/bin/env python3
"""Local repository consistency checks for codex-problems-resolver."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import repository_policy


REQUIRED_PATHS = [
    "AGENTS.md",
    "README.md",
    "SECURITY.md",
    "docs/GITOPS.md",
    ".github/ISSUE_TEMPLATE/incident.yml",
    ".github/pull_request_template.md",
    ".github/workflows/governance.yml",
    "ops/github/repository-policy.json",
    "scripts/repository_policy.py",
    "scripts/validate_repository.py",
    "tests/test_repository_policy.py",
    "tests/test_validate_repository.py",
]

REQUIRED_HEADINGS = {
    "AGENTS.md": [
        "# Repository Guidelines",
        "## Назначение репозитория",
        "## Структура проекта",
        "## Команды управления и проверки",
        "## Безопасность и редактура данных",
    ],
    "README.md": [
        "# Codex Problems Resolver",
        "## Текущее состояние",
        "## Навигация",
        "## Локальные проверки",
        "## Правила изменений",
    ],
    "SECURITY.md": [
        "# Политика безопасности",
        "## Сообщение о уязвимостях",
        "## Редактура журналов и доказательств",
        "## Запрещённые материалы",
    ],
    "docs/GITOPS.md": [
        "# GitOps для инцидентов Codex",
        "## Источник истины",
        "## Жизненный цикл инцидента",
        "## Разделение плана и применения",
    ],
}

TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".gitignore"}
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([\"']?)([^@\s#\"']+)(?:@([^\s#\"']+))?\1\s*(?:#.*)?$")
LOCAL_ACTION_PREFIXES = ("./", "../")


@dataclass
class ValidationResult:
    errors: list[str]


def validate_root(root: Path | str = Path(".")) -> ValidationResult:
    root = Path(root)
    errors: list[str] = []
    errors.extend(_check_required_paths(root))
    errors.extend(_check_text_files(root))
    errors.extend(_check_required_headings(root))
    errors.extend(_check_policy(root))
    errors.extend(_check_workflow_uses(root))
    return ValidationResult(sorted(errors))


def _git_visible_paths(root: Path) -> tuple[list[Path], bool]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode == 0:
            return sorted(root / line for line in completed.stdout.splitlines() if line), True
    except FileNotFoundError:
        pass
    return [], False


def _candidate_paths(root: Path) -> list[Path]:
    visible, is_git_checkout = _git_visible_paths(root)
    paths: set[Path] = set(visible)
    if not is_git_checkout:
        paths.update(path for path in root.rglob("*") if path.is_file() and ".git" not in path.parts)
    for required in REQUIRED_PATHS:
        path = root / required
        if path.exists():
            paths.add(path)
    return sorted(paths)


def _check_required_paths(root: Path) -> list[str]:
    errors = []
    _, is_git_checkout = _git_visible_paths(root)
    if is_git_checkout:
        visible = {required for required in REQUIRED_PATHS if _git_path_is_tracked(root, required)}
    else:
        visible = {required for required in REQUIRED_PATHS if (root / required).exists()}
    for required in REQUIRED_PATHS:
        if required not in visible:
            errors.append(f"{required}: required repository path is missing or ignored")
    return errors


def _git_path_is_tracked(root: Path, rel: str) -> bool:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return False
    return completed.returncode == 0


def _is_text_path(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name == ".gitignore"


def _check_text_files(root: Path) -> list[str]:
    errors: list[str] = []
    for path in _candidate_paths(root):
        if not path.exists() or not path.is_file() or not _is_text_path(path):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{rel}: file must be UTF-8 text")
            continue
        if data and not data.endswith(b"\n"):
            errors.append(f"{rel}: file must end with a newline")
        for number, line in enumerate(text.splitlines(), start=1):
            if line.rstrip(" \t") != line:
                errors.append(f"{rel}:{number}: trailing whitespace")
    return errors


def _check_required_headings(root: Path) -> list[str]:
    errors: list[str] = []
    for rel, headings in REQUIRED_HEADINGS.items():
        path = root / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for heading in headings:
            if heading not in text:
                errors.append(f"{rel}: missing required heading: {heading}")
    return errors


def _check_policy(root: Path) -> list[str]:
    rel = "ops/github/repository-policy.json"
    path = root / rel
    if not path.exists():
        return [f"{rel}: required repository path is missing or ignored"]
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{rel}: invalid JSON: {exc}"]
    return [f"{rel}: {error}" for error in repository_policy.validate_policy(policy)]


def _check_workflow_uses(root: Path) -> list[str]:
    errors: list[str] = []
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return errors
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        rel = path.relative_to(root).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES_LINE.match(line)
            if not match:
                continue
            _, action, ref = match.groups()
            if action.startswith(LOCAL_ACTION_PREFIXES):
                continue
            if ref is None or not FULL_SHA.match(ref):
                suffix = f"@{ref}" if ref is not None else ""
                errors.append(f"{rel}: external uses entry is not pinned to a full 40-hex SHA: {action}{suffix}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate local repository structure and governance files.")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    result = validate_root(args.root)
    if result.errors:
        for error in result.errors:
            print(error)
        return 1
    print("OK: repository validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
