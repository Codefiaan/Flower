"""Settings read from environment variables (or a `.env` file in the project root)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    base_currency: str = os.environ.get("BASE_CURRENCY", "EUR").upper()
    demo: bool = _bool("FLOWER_DEMO")
    db_path: Path = Path(os.environ.get("FLOWER_DB", ROOT / "data" / "flower.db"))
    host: str = os.environ.get("HOST", "127.0.0.1")
    port: int = int(os.environ.get("PORT", "8000"))
    user: str = os.environ.get("FLOWER_USER", "")
    password: str = os.environ.get("FLOWER_PASSWORD", "")
    # Contact address sent in the User-Agent to SEC EDGAR (required by the SEC's fair-access policy).
    sec_contact: str = os.environ.get("SEC_CONTACT", "flower-terminal@example.com")


settings = Settings()
