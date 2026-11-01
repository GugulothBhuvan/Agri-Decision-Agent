"""Environment variables and the rules config (rules.yaml)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=value lines from .env into the environment. Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


if os.environ.get("FS_SKIP_DOTENV") != "1":
    _load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


class Settings:
    def __init__(self) -> None:
        self.database_url = _env("DATABASE_URL") or f"sqlite:///{ROOT / 'local.db'}"
        self.sarvam_api_key = _env("SARVAM_API_KEY")
        self.sarvam_speaker = _env("SARVAM_SPEAKER")
        self.llm_provider = (_env("LLM_PROVIDER") or ("anthropic" if _env("LLM_API_KEY") else "none")).lower()
        self.llm_api_key = _env("LLM_API_KEY")
        self.llm_model = _env("LLM_MODEL")
        self.llm_timeout_s = float(_env("LLM_TIMEOUT_S", "8"))
        self.ops_passcode = _env("OPS_PASSCODE")
        self.session_secret = _env("SESSION_SECRET") or "dev-only-insecure-secret"
        self.nominatim_user_agent = _env("NOMINATIM_USER_AGENT") or "fosol-sathi/0.1"
        self.schedule_hours = float(_env("SCHEDULE_HOURS", "3"))
        self.enable_scheduler = _env("ENABLE_SCHEDULER", "1") != "0"
        self.timezone = "Asia/Kolkata"


@lru_cache
def settings() -> Settings:
    return Settings()


@lru_cache
def rules() -> dict:
    with open(ROOT / "rules.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
