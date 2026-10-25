"""Чистое преобразование двух назначений Shift + стрелки в iTerm2.

Модуль не читает и не записывает настройки. Вызывающий код отдельно получает
снимки назначений и применяет возвращённый словарь.
"""

from copy import deepcopy


_REPLACEMENTS = {
    "0xf702-0x220000": ("[D", "[1;2D"),
    "0xf703-0x220000": ("[C", "[1;2C"),
}


class MappingConflict(ValueError):
    """Назначения или снимки не допускают безопасное преобразование."""

    def __init__(self):
        super().__init__("Назначения клавиш не соответствуют ожидаемому состоянию.")


def _same_value(left, right):
    """Сравнить данные с учётом типов, включая вложенные значения."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            {(type(key), key) for key in left}
            == {(type(key), key) for key in right}
            and all(_same_value(value, right[key]) for key, value in left.items())
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(
            _same_value(a, b) for a, b in zip(left, right)
        )
    return left == right


def plan_keymap(mapping: dict) -> dict:
    """Вернуть глубокую копию с двумя проверенными назначениями.

    Принимаются только Action=10 целого типа и исходная либо уже исправленная
    последовательность соответствующего направления. Весь вход проверяется
    до создания изменённой копии.
    """
    if not isinstance(mapping, dict):
        raise MappingConflict()
    for key, (original_text, applied_text) in _REPLACEMENTS.items():
        entry = mapping.get(key)
        if not isinstance(entry, dict):
            raise MappingConflict()
        if type(entry.get("Action")) is not int or entry["Action"] != 10:
            raise MappingConflict()
        if type(entry.get("Text")) is not str or entry["Text"] not in (
            original_text, applied_text
        ):
            raise MappingConflict()

    planned = deepcopy(mapping)
    for key, (_, applied_text) in _REPLACEMENTS.items():
        planned[key] = deepcopy(mapping[key])
        planned[key]["Text"] = applied_text
    return planned


def restore_keymap(current: dict, original: dict, applied: dict) -> dict:
    """Откатить только реально изменённые записи без потери чужих правок.

    Снимок applied должен точно совпадать с plan_keymap(original). Для каждой
    изменённой записи допустимы лишь исходное или применённое состояние целиком.
    Уже исправленные до применения записи не принадлежат этому откату.
    """
    if not isinstance(current, dict) or not isinstance(applied, dict):
        raise MappingConflict()
    expected = plan_keymap(original)
    if not _same_value(expected, applied):
        raise MappingConflict()

    changed_keys = [
        key for key in _REPLACEMENTS if not _same_value(original[key], applied[key])
    ]
    for key in changed_keys:
        if key not in current or not (
            _same_value(current[key], applied[key])
            or _same_value(current[key], original[key])
        ):
            raise MappingConflict()

    restored = deepcopy(current)
    for key in changed_keys:
        restored[key] = deepcopy(original[key])
    return restored
