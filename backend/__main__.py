"""Start the app: `python -m backend`."""
import sys

import uvicorn

from .config import settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}

if __name__ == "__main__":
    if settings.host not in LOOPBACK and not settings.password:
        sys.exit("Refusing to listen on a public address without a login. "
                 "Set FLOWER_USER and FLOWER_PASSWORD in .env (or keep HOST=127.0.0.1).")
    print(f"Flower Terminal on http://{settings.host}:{settings.port}  (demo mode: {settings.demo})")
    uvicorn.run("backend.main:app", host=settings.host, port=settings.port)
