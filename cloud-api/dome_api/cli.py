"""``dome-api``: run migrations, then serve. ``dome-api --reload`` restarts on code changes."""

from __future__ import annotations

import argparse
import sys

import uvicorn

from dome_api.db.migrate import upgrade_to_head
from dome_api.logging import configure_logging, get_logger
from dome_api.settings import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dome-api", description="DoMe cloud API and relay")
    parser.add_argument("--reload", action="store_true", help="development auto-reload")
    parser.add_argument("--skip-migrations", action="store_true", help="do not run alembic upgrade head first")
    args = parser.parse_args(argv)

    try:
        settings = get_settings()
    except Exception as exc:  # noqa: BLE001 - configuration errors are reported plainly and stop the process
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level, json_output=settings.env != "development")
    log = get_logger("dome_api.cli")
    if not args.skip_migrations:
        log.info("migrations.start")
        upgrade_to_head(settings.database_url)
        log.info("migrations.done")
    host, port = settings.bind_host_port
    log.info(
        "serve",
        host=host,
        port=port,
        env=settings.env,
        reload=args.reload,
        trusted_proxies=settings.trusted_proxy_list or "none",
    )
    uvicorn.run(
        "dome_api.main:create_app",
        host=host,
        port=port,
        reload=args.reload,
        factory=True,
        log_config=None,
        access_log=False,
        ws_max_size=settings.relay_max_frame_bytes,
        ws_ping_interval=20.0,
        ws_ping_timeout=20.0,
        # Forwarded headers are resolved by the application itself (dome_api.security.proxy) against
        # DOME_TRUSTED_PROXIES; uvicorn's own handling (and the FORWARDED_ALLOW_IPS environment
        # variable) is switched off so no deployment can widen the trust to "*" by accident.
        proxy_headers=False,
        timeout_graceful_shutdown=20,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
