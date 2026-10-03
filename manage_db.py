"""Online backup and verified restore; stop all app processes before restoring."""

import argparse
import asyncio
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from config import config
from database import Database, SCHEMA_VERSION


async def restore_database(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if source == destination or not source.is_file():
        raise ValueError("Выберите отдельный существующий snapshot")
    if any(Path(str(destination) + suffix).exists() for suffix in ("-wal", "-shm")):
        raise ValueError(
            "Обнаружен WAL/SHM. Остановите процессы и завершите checkpoint перед восстановлением"
        )
    with closing(
        sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    ) as snapshot:
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Snapshot повреждён")
        if snapshot.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Snapshot содержит нарушенные связи")
        exists = snapshot.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()
        if (
            exists
            and (
                snapshot.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0]
                or 0
            )
            > SCHEMA_VERSION
        ):
            raise ValueError("Snapshot создан более новой версией приложения")
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(
            prefix=".restore-", suffix=".db", dir=destination.parent
        )
        os.close(handle)
        temporary = Path(name)
        try:
            with closing(sqlite3.connect(temporary)) as target:
                snapshot.backup(target)
            if destination.exists():
                backup = (
                    destination.parent / "backups" / f"before_restore_{uuid4().hex}.db"
                )
                await Database(destination).backup(backup)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("path", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("path", type=Path)
    restore.add_argument(
        "--offline",
        action="store_true",
        help="Подтверждаю, что web/bot/CLI остановлены",
    )
    args = parser.parse_args()
    if args.command == "backup":
        await Database().backup(args.path)
    else:
        if not args.offline:
            parser.error("Для восстановления остановите приложение и укажите --offline")
        await restore_database(args.path, config.db_path)
    print("Операция завершена; integrity_check пройден")


if __name__ == "__main__":
    asyncio.run(main())
