from __future__ import annotations

import os
import secrets
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

WEB_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = WEB_ROOT.parent
load_dotenv(WEB_ROOT / ".env")
load_dotenv(PROJECT_ROOT / ".env")
INSTANCE_DIR = WEB_ROOT / "instance"
UPLOADS_DIR = WEB_ROOT / "uploads"
CONTENT_ROOT = Path(
    os.getenv("GRADE9_ROADMAP_DIR")
    or (WEB_ROOT / "content")
).resolve()

LEVEL_BANDS = ("1-2", "3-4", "5-6", "7-8", "9-10")
LEVEL_LABELS = {
    "1-2": "1–2 балла",
    "3-4": "3–4 балла",
    "5-6": "5–6 баллов",
    "7-8": "7–8 баллов",
    "9-10": "9–10 баллов",
}


def _secret_key() -> str:
    env = os.getenv("SECRET_KEY", "").strip()
    if env:
        return env
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    path = INSTANCE_DIR / "secret.key"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    return key


def _database_uri() -> str:
    raw = os.getenv("DATABASE_URL", "").strip()
    if raw:
        if raw.startswith("postgres://"):
            raw = "postgresql+psycopg2://" + raw[len("postgres://") :]
        elif raw.startswith("postgresql://") and "+psycopg2" not in raw:
            raw = "postgresql+psycopg2://" + raw[len("postgresql://") :]
        if raw.startswith("sqlite:///") and not raw.startswith("sqlite:////"):
            rel = raw[len("sqlite:///") :]
            path = Path(rel)
            if not path.is_absolute():
                path = (WEB_ROOT / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return "sqlite:///" + path.as_posix()
        return raw
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + (INSTANCE_DIR / "math9.db").resolve().as_posix()


def _decimal_env(name: str, default: str) -> Decimal:
    raw = (os.getenv(name) or "").strip() or default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        value = Decimal(default)
    if value < 0:
        return Decimal(default)
    return value


def _telegram_username(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    value = value.split("?", 1)[0].rstrip("/")
    lower = value.lower()
    for prefix in ("https://t.me/", "http://t.me/", "https://telegram.me/", "http://telegram.me/"):
        if lower.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.lstrip("@").strip()
    if "/" in value:
        value = value.split("/", 1)[0]
    return value


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        n = int(raw) if raw else default
    except ValueError:
        n = default
    return max(lo, min(hi, n))


class Config:
    SECRET_KEY = _secret_key()
    SQLALCHEMY_DATABASE_URI = _database_uri()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {
            "check_same_thread": False,
            "timeout": 30,
        }
    else:
        SQLALCHEMY_ENGINE_OPTIONS["pool_size"] = max(5, _int_env("AI_CHECK_CONCURRENCY", 4, 1, 8) + 4)
        SQLALCHEMY_ENGINE_OPTIONS["max_overflow"] = 12

    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024
    JSON_AS_ASCII = False

    CONTENT_ROOT = CONTENT_ROOT
    UPLOADS_DIR = UPLOADS_DIR
    PROJECT_ROOT = PROJECT_ROOT

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme").strip() or "changeme"
    ADMIN_NAME = os.getenv("ADMIN_NAME", "Администратор").strip() or "Администратор"
    ADMIN_TELEGRAM = _telegram_username(os.getenv("ADMIN_TELEGRAM", "Putsiato"))
    CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "druswayne@gmail.com").strip() or "druswayne@gmail.com"

    DEMO_SEED = os.getenv("DEMO_SEED", "1").strip() not in {"0", "false", "False"}
    DEMO_USERNAME = os.getenv("DEMO_USERNAME", "ivan").strip() or "ivan"
    DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "ivan123").strip() or "ivan123"
    DEMO_NAME = os.getenv("DEMO_NAME", "Иван Петров").strip() or "Иван Петров"
    DEMO_LEVEL = os.getenv("DEMO_LEVEL", "3-4").strip() or "3-4"

    AI_MODEL = (
        os.getenv("AI_MODEL")
        or os.getenv("MODEL")
        or "openai/gpt-5.6-luna-pro"
    )
    AI_REASONING_EFFORT = (os.getenv("AI_REASONING_EFFORT") or "high").strip() or "high"
    AI_CHECK_CONCURRENCY = _int_env("AI_CHECK_CONCURRENCY", 4, 1, 8)
    # Как часто снова отправлять на проверку фото, которые всё ещё в статусе «не проверено».
    PRACTICE_RECHECK_HOURS = _int_env("PRACTICE_RECHECK_HOURS", 3, 1, 24)
    # Цены openai/gpt-5.6-luna-pro на OpenRouter, USD за 1 млн токенов.
    AI_INPUT_USD_PER_MTOK = _decimal_env("AI_INPUT_USD_PER_MTOK", "0.20")
    AI_OUTPUT_USD_PER_MTOK = _decimal_env("AI_OUTPUT_USD_PER_MTOK", "1.20")
    AI_INPUT_USD_PER_MTOK_LONG = _decimal_env("AI_INPUT_USD_PER_MTOK_LONG", "0.40")
    AI_OUTPUT_USD_PER_MTOK_LONG = _decimal_env("AI_OUTPUT_USD_PER_MTOK_LONG", "1.80")
    AI_LONG_CONTEXT_TOKENS = _int_env("AI_LONG_CONTEXT_TOKENS", 272_000, 1, 2_000_000)
    # Цена для ученика = себестоимость × 20 (наценка 1900%).
    # При обычном режиме это около 20 Br с ученика за 4 недели.
    AI_PRICE_MARKUP = Decimal("20")
    TEST_PASS_PERCENT = float(os.getenv("TEST_PASS_PERCENT", "80"))
    PRACTICE_PASS_PERCENT = float(os.getenv("PRACTICE_PASS_PERCENT", "80"))

    # Канонический адрес сайта без слэша на конце. Пусто — берётся адрес текущего запроса.
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    GOOGLE_SITE_VERIFICATION = os.getenv("GOOGLE_SITE_VERIFICATION", "").strip()
    YANDEX_VERIFICATION = os.getenv("YANDEX_VERIFICATION", "").strip()
    BING_SITE_VERIFICATION = os.getenv("BING_SITE_VERIFICATION", "").strip()
    FACEBOOK_DOMAIN_VERIFICATION = os.getenv("FACEBOOK_DOMAIN_VERIFICATION", "").strip()
