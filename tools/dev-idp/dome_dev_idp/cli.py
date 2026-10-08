from __future__ import annotations

import os

import uvicorn

from .app import DevIdpSettings, create_app


def main() -> None:
    bind = os.environ.get("DEV_IDP_BIND", "127.0.0.1:8081")
    host, _, port = bind.rpartition(":")
    settings = DevIdpSettings.from_env()
    print(f"DoMe development identity provider — issuer {settings.issuer} — DEVELOPMENT ONLY")
    uvicorn.run(create_app(settings), host=host or "127.0.0.1", port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
