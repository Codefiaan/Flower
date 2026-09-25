"""User preferences editable on the Settings page.

Values live in the SQLite `settings` table (as JSON under "pref:<key>"). Anything not set
there falls back to the environment / `.env` defaults from `config.py`.
Login credentials are stored as a salted PBKDF2 hash, never in plain text.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Any

from . import db
from .config import settings

CURRENCIES = ["EUR", "USD", "CHF", "GBP", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK", "PLN"]
DEFAULT_MARKET = ["^GSPC", "^NDX", "^GDAXI", "^STOXX50E", "^N225", "^VIX", "^TNX", "EURUSD=X", "GC=F", "CL=F", "BTC-USD"]
LENSES = ["general", "value", "growth"]
PLACEHOLDER_SEC_CONTACT = "flower-terminal@example.com"
SYMBOL_RE = re.compile(r"^[A-Za-z0-9^=.\-]{1,20}$")


def _defaults() -> dict[str, Any]:
    return {
        "base_currency": settings.base_currency if settings.base_currency in CURRENCIES else "EUR",
        "data_mode": "demo" if settings.demo else "live",
        "start_page": "terminal",
        "theme": "system",
        "refresh_minutes": 0,
        "market_symbols": DEFAULT_MARKET,
        "sec_contact": settings.sec_contact,
        "ai_language": "English",
        "ai_lens": "general",
    }


def _validate(key: str, value: Any) -> Any:
    if key == "base_currency":
        v = str(value).upper()
        if v not in CURRENCIES:
            raise ValueError(f"Unsupported currency {value}")
        return v
    if key == "data_mode":
        if value not in ("live", "demo"):
            raise ValueError("data_mode must be live or demo")
        return value
    if key == "start_page":
        if value not in ("terminal", "overview"):
            raise ValueError("start_page must be terminal or overview")
        return value
    if key == "theme":
        if value not in ("system", "light", "dark"):
            raise ValueError("theme must be system, light or dark")
        return value
    if key == "refresh_minutes":
        v = int(value)
        if v not in (0, 1, 5, 15, 30, 60):
            raise ValueError("refresh_minutes must be 0, 1, 5, 15, 30 or 60")
        return v
    if key == "market_symbols":
        items = value if isinstance(value, list) else re.split(r"[\s,;]+", str(value))
        syms = list(dict.fromkeys(s.strip().upper() for s in items if s and s.strip()))
        bad = [s for s in syms if not SYMBOL_RE.match(s)]
        if bad:
            raise ValueError(f"Invalid symbols: {', '.join(bad)}")
        if not 1 <= len(syms) <= 24:
            raise ValueError("Enter between 1 and 24 market symbols")
        return syms
    if key == "sec_contact":
        v = str(value).strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("SEC contact must be an e-mail address")
        if re.search(r"no-?reply", v, re.I):
            raise ValueError("The SEC blocks no-reply addresses - use an address you can receive mail at")
        return v
    if key == "ai_language":
        v = str(value).strip()[:40]
        return v or "English"
    if key == "ai_lens":
        if value not in LENSES:
            raise ValueError("Unknown AI lens")
        return value
    raise ValueError(f"Unknown setting {key}")


def get(key: str) -> Any:
    raw = db.get_setting(f"pref:{key}", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    if key == "ai_language":  # stored without prefix by earlier versions
        legacy = db.get_setting("ai_language", "")
        if legacy:
            return legacy
    return _defaults()[key]


def all_prefs() -> dict[str, Any]:
    return {k: get(k) for k in _defaults()}


def update(values: dict[str, Any]) -> dict[str, Any]:
    """Validate everything first, then store - so one bad field changes nothing."""
    clean = {k: _validate(k, v) for k, v in values.items() if v is not None}
    for k, v in clean.items():
        db.set_setting(f"pref:{k}", json.dumps(v))
    return clean


def is_demo() -> bool:
    return get("data_mode") == "demo"


# --- login ---------------------------------------------------------------------

def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def login_source() -> str:
    """'env' if .env defines the login, 'app' if set on the Settings page, '' if none."""
    if settings.password:
        return "env"
    return "app" if db.get_setting("login_hash", "") else ""


def login_user() -> str:
    if settings.password:
        return settings.user or "flower"
    return db.get_setting("login_user", "")


# PBKDF2 is deliberately slow; remember verified credentials so each API call stays fast.
_verified: set[str] = set()


def _fingerprint(user: str, password: str) -> str:
    return hashlib.sha256(f"{user}\0{password}\0{db.get_setting('login_hash', '')}".encode()).hexdigest()


def set_login(user: str, password: str) -> None:
    user = user.strip()
    if not user or len(password) < 8:
        raise ValueError("Username required and password must have at least 8 characters")
    salt = secrets.token_hex(16)
    db.set_setting("login_user", user)
    db.set_setting("login_salt", salt)
    db.set_setting("login_hash", _hash(password, salt))
    _verified.clear()


def clear_login() -> None:
    for k in ("login_user", "login_salt", "login_hash"):
        db.set_setting(k, "")
    _verified.clear()


def check_login(user: str, password: str) -> bool:
    if settings.password:
        return secrets.compare_digest(user, settings.user or "flower") and secrets.compare_digest(password, settings.password)
    stored = db.get_setting("login_hash", "")
    if not stored:
        return False
    fp = _fingerprint(user, password)
    if fp in _verified:
        return True
    ok_user = secrets.compare_digest(user, db.get_setting("login_user", ""))
    ok_pw = secrets.compare_digest(_hash(password, db.get_setting("login_salt", "")), stored)
    if ok_user and ok_pw:
        _verified.add(fp)
    return ok_user and ok_pw
