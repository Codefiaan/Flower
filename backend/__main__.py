"""Start the app: `python -m backend`."""
import sys

import uvicorn

from . import db, prefs
from .config import settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}

if __name__ == "__main__":
    db.init()
    if settings.host not in LOOPBACK and not prefs.login_source():
        sys.exit("Refusing to listen on a public address without a login. Set FLOWER_USER and "
                 "FLOWER_PASSWORD in .env, or set a login on the Settings page first (with HOST=127.0.0.1).")
    print(f"Flower Terminal on http://{settings.host}:{settings.port}  (data: {prefs.get('data_mode')})")
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port)
