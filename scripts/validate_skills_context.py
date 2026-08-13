#!/usr/bin/env python3
"""Проверка усечения описаний навыков в model-visible вводе Codex."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
ROOT_PATTERN = re.compile(r"^- `(r[0-9]+)` = `(.+?)`$", re.MULTILINE)
SKILL_PATTERN = re.compile(r"^- (.+?): (.*?) \(file: ([^)]+)\)$", re.MULTILINE)


@dataclass
class ValidationResult:
    shortened: list[tuple[str, int]]
    errors: list[str]


def validate_prompt(payload: object) -> ValidationResult:
    """Сверить видимые описания навыков с их локальными метаданными."""
    try:
        text = _extract_text(payload)
        roots, skills = _parse_catalog(text)
    except ValueError as exc:
        return ValidationResult([], [str(exc)])

    shortened: list[tuple[str, int]] = []
    errors: list[str] = []
    for name, visible_description, source in skills:
        try:
            path = _resolve_skill_path(source, roots)
            full_description = _read_description(path)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
            continue

        if visible_description == full_description:
            continue
        if full_description.startswith(visible_description):
            shortened.append((name, len(full_description) - len(visible_description)))
        else:
            errors.append(f"{name}: видимое описание не является началом локального описания")
    return ValidationResult(shortened, errors)


def run(raw_json: str) -> tuple[int, str]:
    """Вернуть код завершения и безопасную строку отчёта для stdin-интерфейса."""
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return 2, "ОШИБКА: некорректный JSON во вводе Codex"

    result = validate_prompt(payload)
    if result.errors:
        return 2, "\n".join(f"ОШИБКА: {error}" for error in result.errors)
    if result.shortened:
        lines = ["УСЕЧЕНИЕ: описания навыков сокращены"]
        lines.extend(f"- {name}: потеряно символов: {lost}" for name, lost in result.shortened)
        return 1, "\n".join(lines)
    return 0, "OK: описания навыков не усечены"


def _extract_text(payload: object) -> str:
    if not isinstance(payload, list):
        raise ValueError("ожидался список сообщений Codex")
    fragments: list[str] = []
    for message in payload:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "input_text":
                value = item.get("text")
                if isinstance(value, str):
                    fragments.append(value)
    if not fragments:
        raise ValueError("не найдены текстовые сообщения Codex")
    return "\n".join(fragments)


def _parse_catalog(text: str) -> tuple[dict[str, Path], list[tuple[str, str, str]]]:
    try:
        roots_block = text.split("### Skill roots", 1)[1].split("### Available skills", 1)[0]
        skills_block = text.split("### Available skills", 1)[1].split("</skills_instructions>", 1)[0]
    except IndexError as exc:
        raise ValueError("не найден каталог навыков Codex") from exc
    roots = {alias: Path(raw_root) for alias, raw_root in ROOT_PATTERN.findall(roots_block)}
    skills = SKILL_PATTERN.findall(skills_block)
    if not roots or not skills:
        raise ValueError("каталог навыков Codex неполон")
    return roots, skills


def _resolve_skill_path(source: str, roots: dict[str, Path]) -> Path:
    source_path = PurePosixPath(source)
    if source_path.is_absolute() or len(source_path.parts) < 3:
        raise ValueError("некорректный путь навыка")
    alias, *relative_parts = source_path.parts
    if alias not in roots:
        raise ValueError("путь навыка ссылается на неизвестный корень")
    if ".." in relative_parts or relative_parts[-1] != "SKILL.md":
        raise ValueError("путь навыка выходит за объявленный корень")

    root = roots[alias].resolve()
    candidate = (root.joinpath(*relative_parts)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("путь навыка выходит за объявленный корень") from exc
    return candidate


def _read_description(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError("файл навыка не найден") from exc
    if not lines or lines[0] != "---":
        raise ValueError("не найдены метаданные навыка")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("не завершены метаданные навыка") from exc

    metadata = lines[1:end]
    for index, line in enumerate(metadata):
        if not line.startswith("description:"):
            continue
        value = line.partition(":")[2].strip()
        if value not in {"|", ">", "|-", ">-"}:
            return value.strip("\"'")
        nested = []
        for candidate in metadata[index + 1 :]:
            if candidate.startswith((" ", "\t")):
                nested.append(candidate.strip())
            else:
                break
        return "\n".join(nested).strip()
    raise ValueError("в метаданных нет описания")


def main() -> int:
    code, output = run(sys.stdin.read())
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
