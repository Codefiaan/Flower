import importlib

import pytest

from backend import cache, db, prefs


@pytest.fixture()
def fresh_db(tmp_path):
    db.set_path(tmp_path / "p.db")
    cache.clear()
    prefs._verified.clear()
    yield


def test_dotenv_parsing(tmp_path, monkeypatch):
    from backend import config

    env = tmp_path / ".env"
    env.write_text('# comment\nBASE_CURRENCY="usd"\nPORT=9001\nFLOWER_PASSWORD=\'s3cret\'\nbroken line\n', encoding="utf-8")
    for k in ("BASE_CURRENCY", "PORT", "FLOWER_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    config._load_dotenv()
    import os
    assert os.environ["BASE_CURRENCY"] == "usd" and os.environ["PORT"] == "9001" and os.environ["FLOWER_PASSWORD"] == "s3cret"
    monkeypatch.setenv("PORT", "1234")  # real environment wins over .env
    config._load_dotenv()
    assert os.environ["PORT"] == "1234"


def test_defaults_follow_environment(fresh_db, monkeypatch):
    from backend.config import Settings

    monkeypatch.setattr(prefs, "settings", Settings(base_currency="CHF", demo=False, sec_contact="a@b.ch"))
    assert prefs.get("base_currency") == "CHF" and prefs.get("data_mode") == "live" and prefs.get("sec_contact") == "a@b.ch"
    monkeypatch.setattr(prefs, "settings", Settings(base_currency="XYZ"))
    assert prefs.get("base_currency") == "EUR"  # unsupported env value falls back safely


def test_update_is_all_or_nothing(fresh_db):
    with pytest.raises(ValueError):
        prefs.update({"theme": "dark", "refresh_minutes": 3})
    assert prefs.get("theme") == "system"
    prefs.update({"theme": "dark", "market_symbols": "aapl; ^gdaxi  AAPL"})
    assert prefs.get("market_symbols") == ["AAPL", "^GDAXI"]


def test_legacy_ai_language_is_read(fresh_db):
    db.set_setting("ai_language", "German")
    assert prefs.get("ai_language") == "German"


def test_login_hash_and_cache(fresh_db):
    assert prefs.login_source() == "" and not prefs.check_login("a", "b")
    prefs.set_login("eric", "long password")
    assert db.get_setting("login_hash") and "long password" not in str(db.rows("SELECT * FROM settings"))
    assert prefs.check_login("eric", "long password")
    assert prefs._verified  # remembered, so the next request skips PBKDF2
    assert not prefs.check_login("eric", "wrong password") and not prefs.check_login("other", "long password")
    prefs.set_login("eric", "another password")
    assert not prefs.check_login("eric", "long password")  # old password no longer accepted
    with pytest.raises(ValueError):
        prefs.set_login("", "12345678")
    with pytest.raises(ValueError):
        prefs.set_login("x", "short")
    prefs.clear_login()
    assert prefs.login_source() == ""


def test_cache_uncached_values_are_not_stored():
    from backend.cache import ttl_cache, uncached

    calls = []

    @ttl_cache(60)
    def f(x):
        calls.append(x)
        return uncached(None) if x < 0 else x * 2

    cache.clear()
    assert f(2) == 4 and f(2) == 4 and calls == [2]
    assert f(-1) is None and f(-1) is None and calls == [2, -1, -1]


def test_main_module_refuses_public_host_without_login(fresh_db, monkeypatch):
    import sys

    from backend.config import Settings

    main_mod = importlib.import_module("backend.__main__")
    monkeypatch.setattr(main_mod, "settings", Settings(host="0.0.0.0"))
    monkeypatch.setattr(prefs, "settings", Settings(host="0.0.0.0"))
    called = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda *a, **k: called.append(a))
    monkeypatch.setattr(sys, "argv", ["backend"])
    with pytest.raises(SystemExit, match="Refusing"):
        main_mod.main()
    prefs.set_login("eric", "long password")
    main_mod.main()
    assert called


@pytest.mark.parametrize("addr", ["x@users.noreply.github.com", "no-reply@company.com", "NoReply@x.org"])
def test_sec_contact_rejects_noreply(fresh_db, addr):
    # CI run #3 proved the SEC answers HTTP 403 to no-reply contact addresses
    with pytest.raises(ValueError, match="no-reply"):
        prefs.update({"sec_contact": addr})
    prefs.update({"sec_contact": "eric@example.org"})
    assert prefs.get("sec_contact") == "eric@example.org"
