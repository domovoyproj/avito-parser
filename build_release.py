#!/usr/bin/env python3
"""
Скрипт сборки релизных архивов Avito Max Parser для загрузки на GitHub Releases.
"""

import os
import shutil
import tarfile
import zipfile
from pathlib import Path

VERSION = "2.5.0"
PROJECT_DIR = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_DIR / "dist"

# Файлы и папки для включения в релиз
INCLUDE_PATTERNS = [
    "routers", "repositories", "docs",
    "auth_dependencies.py", "dependencies.py", "coordination.py", "monitoring.py",
    "notifications.py", "outbox.py", "observability.py", "security.py",
    "parse_outcomes.py", "parser_jobs.py", "llm_store.py", "export_jobs.py",
    "streaming_export.py", "setup_admin.py", "manage_db.py", "constraints.txt",
    "static",
    "templates",
    "ai_scoring.py",
    "browser_engine.py",
    "config.py",
    "database.py",
    "exporter.py",
    "http_engine.py",
    "main.py",
    "models.py",
    "parser_core.py",
    "proxy_manager.py",
    "telegram_bot.py",
    "web_server.py",
    "requirements.txt",
    ".env.example",
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    "install_windows.bat",
    "start_server.bat",
    "start_telegram_bot.bat",
    "start_all.bat",
    "install_ubuntu.sh",
    "setup_systemd.sh",
    "nginx_avito.conf",
]

# Исключения
EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".log", ".tmp", ".part"}
EXCLUDE_DIRS = {"__pycache__", ".git", "venv", "env", ".idea", ".vscode", "dist"}


def should_include(path: Path) -> bool:
    if path.name == '.env' or path.name.endswith(('-wal', '-shm')):
        return False
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return False
    if path.suffix in EXCLUDE_EXTENSIONS:
        return False
    return True


def build_releases():
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[*] Сборка релизных пакетов Avito Max Parser v{VERSION}...")

    # 1. Windows ZIP Archive
    win_zip_path = DIST_DIR / f"Avito-Max-Parser-v{VERSION}-Windows.zip"
    print(f"[*] Создание {win_zip_path.name}...")
    with zipfile.ZipFile(win_zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item_name in INCLUDE_PATTERNS:
            item_path = PROJECT_DIR / item_name
            if not item_path.exists():
                raise FileNotFoundError(f"Missing release file: {item_name}")
            if item_path.is_file():
                zipf.write(item_path, arcname=f"avito_parser/{item_name}")
            elif item_path.is_dir():
                for root, dirs, files in os.walk(item_path):
                    for file in files:
                        file_path = Path(root) / file
                        if should_include(file_path):
                            arcname = Path("avito_parser") / file_path.relative_to(PROJECT_DIR)
                            zipf.write(file_path, arcname=str(arcname))
        # Empty placeholder directories
        zipf.writestr("avito_parser/data/.gitkeep", "")
        zipf.writestr("avito_parser/exports/.gitkeep", "")
        zipf.writestr("avito_parser/logs/.gitkeep", "")

    print(f"[+] Windows-архив готов: {win_zip_path} ({win_zip_path.stat().st_size / (1024*1024):.2f} MB)")

    # 2. Ubuntu / Linux TAR.GZ Archive
    linux_tar_path = DIST_DIR / f"Avito-Max-Parser-v{VERSION}-Linux.tar.gz"
    print(f"[*] Создание {linux_tar_path.name}...")
    with tarfile.open(linux_tar_path, "w:gz") as tar:
        for item_name in INCLUDE_PATTERNS:
            item_path = PROJECT_DIR / item_name
            if not item_path.exists():
                raise FileNotFoundError(f"Missing release file: {item_name}")
            if item_path.is_file():
                tar.add(item_path, arcname=f"avito_parser/{item_name}")
            elif item_path.is_dir():
                tar.add(item_path, arcname=f"avito_parser/{item_name}", filter=lambda t: t if should_include(Path(t.name)) else None)

    print(f"[+] Linux-архив готов: {linux_tar_path} ({linux_tar_path.stat().st_size / (1024*1024):.2f} MB)")
    print("\n[+] Все релизные архивы успешно собраны в папке dist/!")


if __name__ == "__main__":
    build_releases()
