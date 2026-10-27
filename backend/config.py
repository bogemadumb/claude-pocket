"""Настройки из окружения. Секреты — файлами (путь в *_FILE), чтобы не светились в `ps` и журнале systemd."""
import json
import os
import secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("POCKET_DATA_DIR", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)


def _secret(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "")
    path = os.environ.get(f"{name}_FILE")
    if not value and path and Path(path).exists():
        value = Path(path).read_text().strip()
    if required and not value:
        raise RuntimeError(f"не задан {name} (или {name}_FILE)")
    return value


BOT_TOKEN = _secret("BOT_TOKEN")
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))          # Telegram user.id единственного владельца
GROUP_ID = int(os.environ.get("GROUP_ID", "0") or 0)     # запасной чат для пушей (необязательно)

APP_URL = os.environ.get("APP_URL", "https://example.com/code/")
PERMISSION_TIMEOUT = 30 * 60     # столько ждём ответа на запрос разрешения

# подписочный OAuth-токен Claude Code (claude setup-token); читается при каждом запуске сессии
OAUTH_TOKEN_FILE = Path(os.environ.get("CLAUDE_OAUTH_TOKEN_FILE", DATA / "claude-token"))
DB_PATH = os.environ.get("POCKET_DB", str(DATA / "data.db"))

# вложения с телефона — в свою папку
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", str(DATA / "uploads"))

INITDATA_MAX_AGE = 86400
JWT_TTL_SECONDS = 30 * 24 * 3600

# папки, которые предлагаются при создании чата (как «открыть папку» в VS Code):
# POCKET_PROJECTS='[{"path": "/srv/app", "name": "Приложение", "note": ""}]'
PROJECTS = json.loads(os.environ.get("POCKET_PROJECTS") or json.dumps([
    {"path": str(Path.home() / "workspace"), "name": "Рабочая папка", "note": "память и CLAUDE.md агента"},
    {"path": str(BASE.parent), "name": "Это приложение", "note": "код Mini App"},
]))

MAX_ACTIVE_SESSIONS = int(os.environ.get("MAX_ACTIVE_SESSIONS", "3"))   # сессия claude ≈ 250 МБ памяти
IDLE_UNLOAD_SECONDS = 10 * 60    # простаивающие клиенты выгружаем, чат продолжится по resume


def _file_secret(name: str, size: int) -> str:
    f = DATA / name
    if not f.exists():
        f.write_text(secrets.token_hex(size))
        f.chmod(0o600)
    return f.read_text().strip()


JWT_SECRET = _file_secret("jwt-secret", 32)
# токен для бота-моста: он не проходит через Telegram initData
SERVICE_TOKEN = _file_secret("service-token", 24)


def oauth_token() -> str:
    """Читаем при каждом запуске сессии: токен могут заменить, рестарт не нужен."""
    return OAUTH_TOKEN_FILE.read_text().strip()
