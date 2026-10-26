#!/usr/bin/env python3
"""Точечное применение через официальный API iTerm2 из временного окружения.

Библиотека iterm2 нужна только для этой эксплуатационной команды. Чистая логика
и проверки репозитория используют стандартную библиотеку Python.
"""

import argparse
import asyncio
import hashlib
import json
import os
import signal
from pathlib import Path
import stat
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from iterm_shift_arrows import MappingConflict, plan_keymap, restore_keymap


class RepairError(Exception):
    """Ошибка без вывода содержимого профилей и идентификаторов."""


def private_directory(path):
    path = path.expanduser().absolute()
    if path.is_symlink() or path.resolve().is_relative_to(ROOT):
        raise RepairError("Резервная копия должна находиться вне рабочей копии Git")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RepairError("Каталог резервной копии должен иметь права 0700")
    return path


def save_private(path, value, exclusive=False):
    if path.is_symlink():
        raise RepairError("Символическая ссылка вместо файла состояния")
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(path.parent)
        return
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot_digest(snapshot):
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_journal(path, snapshot):
    try:
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError
        with path.open(encoding="utf-8") as stream:
            journal = json.load(stream)
        if (journal.get("schema") != 1
                or journal.get("snapshot") != snapshot_digest(snapshot)
                or journal.get("status") not in {
                    "applying", "writing", "applied", "rolling_back",
                    "rolled_back", "failed_and_rolled_back"}
                or not isinstance(journal.get("started"), list)):
            raise ValueError
        seen = set()
        for entry in journal["started"]:
            if (not isinstance(entry, dict) or set(entry) != {"kind", "index"}
                    or entry["kind"] not in ("profiles", "sessions")
                    or type(entry["index"]) is not int
                    or not 1 <= entry["index"] <= len(snapshot[entry["kind"]])):
                raise ValueError
            key = (entry["kind"], entry["index"])
            if key in seen:
                raise ValueError
            seen.add(key)
        return journal
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise RepairError("Нет согласованного приватного журнала; откат запрещён") from None


def remember(journal, kind, index):
    entry = {"kind": kind, "index": index}
    if entry not in journal["started"]:
        journal["started"].append(entry)


def load_snapshot(path):
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RepairError("Небезопасный файл резервной копии")
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    if data.get("schema") != 1 or len(data.get("profiles", [])) != 5:
        raise RepairError("Неожиданный формат резервной копии")
    for item in data["profiles"] + data["sessions"]:
        if plan_keymap(item["original"]) != item["applied"]:
            raise RepairError("Несогласованные карты в резервной копии")
    return data


async def inventory(iterm2, connection):
    profiles = await iterm2.Profile.async_get(connection)
    if len(profiles) != 5 or len({p.guid for p in profiles}) != 5:
        raise RepairError("Ожидалось ровно пять сохранённых профилей")
    profile_ids = {p.guid for p in profiles}
    app = await iterm2.async_get_app(connection)
    sessions = {s.session_id: s for s in app.buried_sessions}
    for window in app.terminal_windows:
        for tab in window.tabs:
            sessions.update({s.session_id: s for s in tab.all_sessions})
    result = {"schema": 1, "profiles": [], "sessions": []}
    for profile in sorted(profiles, key=lambda p: p.guid):
        original = profile.key_mappings
        result["profiles"].append({"id": profile.guid, "original": original,
                                   "applied": plan_keymap(original)})
    skipped = 0
    for session_id, session in sorted(sessions.items()):
        profile = await session.async_get_profile()
        parent = profile.original_guid or profile.guid
        if parent not in profile_ids:
            skipped += 1
            continue
        original = profile.key_mappings
        result["sessions"].append({"id": session_id, "parent": parent,
                                   "original": original,
                                   "applied": plan_keymap(original)})
    current = os.environ.get("ITERM_SESSION_ID", "").split(":")[-1]
    if current not in {s["id"] for s in result["sessions"]}:
        raise RepairError("Текущий сеанс не сопоставлен с выбранными профилями")
    result["skipped_sessions"] = skipped
    return result


async def get_target(iterm2, connection, kind, item):
    if kind == "profiles":
        profiles = await iterm2.Profile.async_get(connection, [item["id"]])
        if len(profiles) != 1:
            raise RepairError("Сохранённый профиль недоступен")
        return profiles[0]
    app = await iterm2.async_get_app(connection)
    session = app.get_session_by_id(item["id"])
    if session is None:
        raise RepairError("Сеанс из снимка недоступен")
    profile = await session.async_get_profile()
    if (profile.original_guid or profile.guid) != item["parent"]:
        raise RepairError("Сеанс сменил исходный профиль")
    return profile


async def verify(iterm2, connection, snapshot):
    current_ids = {p.guid for p in await iterm2.Profile.async_get(connection)}
    if current_ids != {p["id"] for p in snapshot["profiles"]}:
        raise RepairError("Состав сохранённых профилей изменился")
    for kind in ("profiles", "sessions"):
        for item in snapshot[kind]:
            profile = await get_target(iterm2, connection, kind, item)
            if profile.key_mappings != item["applied"]:
                raise RepairError("Карта клавиш отличается от ожидаемого результата")


async def related_sessions(iterm2, connection, snapshot, item):
    related = []
    for index, session in enumerate(snapshot["sessions"], 1):
        if session["parent"] == item["id"]:
            profile = await get_target(iterm2, connection, "sessions", session)
            related.append((index, session, profile.key_mappings))
    return related


async def rollback(iterm2, connection, snapshot, journal):
    failures = []
    # Сначала профили: их запись может распространиться на связанные сеансы.
    for kind in ("profiles", "sessions"):
        for entry in journal["started"]:
            if entry["kind"] != kind:
                continue
            index = entry["index"]
            item = snapshot[kind][index - 1]
            try:
                profile = await get_target(iterm2, connection, kind, item)
                current = profile.key_mappings
                restored = restore_keymap(current, item["original"], item["applied"])
                if restored != current:
                    if kind == "profiles":
                        for number, session, mapping in await related_sessions(
                                iterm2, connection, snapshot, item):
                            started = {"kind": "sessions", "index": number} in journal["started"]
                            # Не разрешаем распространению затронуть чужую запись.
                            expected = (restore_keymap(mapping, session["original"],
                                                       session["applied"])
                                        if started else session["original"])
                            if mapping == current and expected != restored:
                                raise RepairError("Распространение отката затронет чужую карту")
                    fresh = await get_target(iterm2, connection, kind, item)
                    if fresh.key_mappings != current:
                        raise RepairError("Карта изменена во время отката")
                    await fresh.async_set_key_mappings(restored)
                fresh = await get_target(iterm2, connection, kind, item)
                if fresh.key_mappings != restored:
                    raise RepairError("Проверка отката не прошла")
            except BaseException as error:
                failures.append({"kind": kind, "index": index,
                                 "error": type(error).__name__})
    return failures


async def finish_rollback(iterm2, connection, snapshot, journal, path, status):
    async def finish():
        journal["status"] = "rolling_back"
        journal_unavailable = False
        try:
            save_private(path, journal)
        except Exception:
            journal_unavailable = True
        # Отказ диска не отменяет восстановление уже начатых записей.
        journal["rollback_conflicts"] = await rollback(
            iterm2, connection, snapshot, journal)
        journal["status"] = status
        try:
            save_private(path, journal)
        except Exception:
            raise RepairError(
                "Попытка отката завершена; итог не удалось сохранить в приватном журнале"
            ) from None
        if journal_unavailable:
            print("Промежуточный журнал был недоступен; итог попытки отката сохранён",
                  file=sys.stderr)
        return journal["rollback_conflicts"]

    # Сильная ссылка и повторное ожидание не дают отмене вызывающей задачи
    # оборвать защитный откат. Результат сохраняется до возврата управления.
    task = asyncio.create_task(finish())
    interruption = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except (asyncio.CancelledError, KeyboardInterrupt) as error:
            interruption = error
    result = task.result()
    if interruption is not None:
        raise interruption
    return result


async def run(iterm2, connection, args, directory):
    snapshot_path = directory / "snapshot.json"
    journal_path = directory / "journal.json"
    if args.action == "snapshot":
        snapshot = await inventory(iterm2, connection)
        save_private(snapshot_path, snapshot, exclusive=True)
        print(json.dumps({"status": "snapshot_saved", "profiles": 5,
                          "sessions": len(snapshot["sessions"]),
                          "skipped_sessions": snapshot["skipped_sessions"]}))
        return
    snapshot = load_snapshot(snapshot_path)
    if args.action == "verify":
        await verify(iterm2, connection, snapshot)
        print(json.dumps({"status": "verified", "profiles": 5,
                          "sessions": len(snapshot["sessions"])}))
        return
    if args.action == "rollback":
        journal = load_journal(journal_path, snapshot)
        failures = await finish_rollback(iterm2, connection, snapshot, journal,
                                         journal_path, "rolled_back")
        if failures:
            raise RepairError("Откат требует разбора конфликтов; журнал сохранён локально")
        print('{"status": "rolled_back"}')
        return

    # Полная проверка исходных карт и состава пяти профилей до первой записи.
    current = await inventory(iterm2, connection)
    if current != snapshot:
        await verify(iterm2, connection, snapshot)
        print('{"status": "already_applied"}')
        return
    if journal_path.exists():
        previous = load_journal(journal_path, snapshot)
        if previous["status"] == "applied":
            await verify(iterm2, connection, snapshot)
            print('{"status": "already_applied"}')
            return
        if (previous["status"] not in {"rolled_back", "failed_and_rolled_back"}
                or previous.get("rollback_conflicts")):
            raise RepairError("Предыдущая операция требует отката по журналу")
    journal = {"schema": 1, "snapshot": snapshot_digest(snapshot),
               "status": "applying", "started": []}
    save_private(journal_path, journal)
    try:
        for kind in ("profiles", "sessions"):
            for index, item in enumerate(snapshot[kind], 1):
                profile = await get_target(iterm2, connection, kind, item)
                if profile.key_mappings == item["applied"]:
                    continue
                if profile.key_mappings != item["original"]:
                    raise RepairError("Карта изменена после снимка")
                propagated = []
                if kind == "profiles":
                    for number, session, mapping in await related_sessions(
                            iterm2, connection, snapshot, item):
                        started = {"kind": "sessions", "index": number} in journal["started"]
                        allowed = (session["original"], session["applied"]) if started else (session["original"],)
                        if mapping not in allowed:
                            raise RepairError("Связанный сеанс изменён после снимка")
                        if mapping == item["original"] and mapping != item["applied"]:
                            propagated.append(number)
                    # Интерфейс не предоставляет условную запись. Проверки до и
                    # после записи лишь сокращают окно конкурентного изменения.
                    profile = await get_target(iterm2, connection, kind, item)
                    if profile.key_mappings != item["original"]:
                        raise RepairError("Карта изменена перед записью")
                previous_started = list(journal["started"])
                for number in propagated:
                    remember(journal, "sessions", number)
                remember(journal, kind, index)
                journal["status"] = "writing"
                try:
                    # До setter: ответ может потеряться.
                    save_private(journal_path, journal)
                except BaseException:
                    journal["started"] = previous_started
                    raise
                await profile.async_set_key_mappings(item["applied"])
                fresh = await get_target(iterm2, connection, kind, item)
                if fresh.key_mappings != item["applied"]:
                    raise RepairError("Проверка записи не прошла")
        await verify(iterm2, connection, snapshot)
        journal["status"] = "applied"
        save_private(journal_path, journal)
    except BaseException as error:
        await finish_rollback(iterm2, connection, snapshot, journal,
                              journal_path, "failed_and_rolled_back")
        if not isinstance(error, Exception):
            raise
        raise RepairError("Применение не завершено; результат отката в локальном журнале") from None
    print(json.dumps({"status": "applied", "profiles": 5,
                      "sessions": len(snapshot["sessions"])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "apply", "verify", "rollback"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        directory = private_directory(args.state_dir)
        import iterm2

        exit_code = 0

        async def connected(connection):
            nonlocal exit_code
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            previous_handlers = {number: signal.getsignal(number)
                                 for number in (signal.SIGINT, signal.SIGTERM)}
            # Повторный сигнал отменяет задачу, сохраняя работающий цикл отката.
            for number in previous_handlers:
                signal.signal(number, lambda *_: loop.call_soon_threadsafe(task.cancel))
            # Библиотека печатает traceback необработанной ошибки callback.
            # Перехватываем её здесь, не передавая содержимое API в вывод.
            try:
                await run(iterm2, connection, args, directory)
            except (RepairError, MappingConflict) as error:
                print(str(error), file=sys.stderr)
                exit_code = 1
            except BaseException as error:
                print(f"Операция не завершена: {type(error).__name__}", file=sys.stderr)
                exit_code = 130 if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)) else 1
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)

        iterm2.run_until_complete(connected)
        return exit_code
    except (RepairError, MappingConflict) as error:
        print(str(error), file=sys.stderr)
        return 1
    except BaseException as error:
        print(f"Операция не завершена: {type(error).__name__}", file=sys.stderr)
        return 130 if isinstance(error, (KeyboardInterrupt, asyncio.CancelledError)) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
