"""A stand-in for ``flyctl`` (and ``docker``) with Fly-shaped state in a JSON file.

Output shapes follow the flyctl v0.4.110 source (read 2026-10-14, see deploy/README.md):

* ``mpg list --json`` prints the TEXT "No managed postgres clusters found in organization <slug>" when there
  are none, and Go field names (``Id``, ``Name``, ``Status``) otherwise.
* ``mpg create`` and ``mpg attach`` print the database password in clear text (the real ones do too).
* ``ips list --json`` uses ``Address``/``Type`` (``v6``, ``shared_v4``); ``secrets list --json`` uses
  ``name``/``digest``/``status``; ``certs`` use json tags (``hostname``, ``dns_requirements``).
* ``deploy --config <file>`` reads that file when it runs (it is the per-environment copy dome_deploy.py writes and
  deletes afterwards); the fake keeps what it parsed in the app's ``deployed_config`` and the path in
  ``deployed_config_path``. A file that is missing or not TOML fails the deploy.

* ``secrets unset --stage -a <app> NAME...`` removes those names (v0.4.110 unset.go shares the ``--stage`` flag).
* ``deploy`` refuses, like the app's start-up check (cloud-api settings.py ``_v_billing``), when only some of the seven
  Stripe values reach the app (Fly secrets plus ``-e``); the ``-e`` values are kept in the app's ``deployed_env``.

Every call is appended to the log file as ``{"tool", "argv", "stdin", "env_has_client_secret",
"env_has_database_url", "env_has_fly_token", "env_has_gh_token", "env_has_stripe_secret"}``. Failures are injected through
``state["fail"]``, keyed by the first two arguments (``"secrets list"``) with ``{"exit": n, "stdout": "...",
"stderr": "..."}``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

FLY_DB_PASSWORD = "SuperSecretFlyDbPw4242"
STRIPE_NAMES = (
    "DOME_STRIPE_SECRET_KEY",
    "DOME_STRIPE_WEBHOOK_SECRET",
    "DOME_STRIPE_PRICE_PRO_MONTHLY",
    "DOME_STRIPE_PRICE_PRO_ANNUAL",
    "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY",
    "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL",
    "DOME_STRIPE_PRICE_TOPUP",
)
V6 = "2a09:8280:1::4b:1"
V4 = "66.241.124.10"


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _save(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _opt(argv: list[str], *names: str) -> str | None:
    for i, arg in enumerate(argv):
        for name in names:
            if arg == name and i + 1 < len(argv):
                return argv[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def _out(text: str) -> None:
    sys.stdout.write(text)


def _err(text: str) -> None:
    sys.stderr.write(text)


def _app(state: dict[str, Any], argv: list[str]) -> dict[str, Any] | None:
    name = _opt(argv, "-a", "--app")
    return state["apps"].get(name) if name else None


def _conn(cluster: dict[str, Any]) -> str:
    return f"postgresql://fly-user:{FLY_DB_PASSWORD}@pgbouncer.{cluster['Id']}.flympg.net/fly-db"


def flyctl(argv: list[str], stdin: str, state: dict[str, Any]) -> int:
    key = " ".join(argv[:2])
    fail = state.get("fail", {}).get(key)
    if fail:
        _out(fail.get("stdout", ""))
        _err(fail.get("stderr", "Error: injected failure\n"))
        if not fail.get("then_continue"):
            return int(fail.get("exit", 1))

    org = _opt(argv, "-o", "--org")
    if key == "apps list":
        apps = [{"Name": n, "Organization": {"Slug": a["org"]}} for n, a in state["apps"].items() if a["org"] == org]
        _out(json.dumps(apps))
        return 0
    if key == "apps create":
        name = argv[2]
        if name in state.get("taken_apps", []) or name in state["apps"]:
            _err("Error: Validation failed: Name has already been taken\n")
            return 1
        state["apps"][name] = {"org": org, "secrets": {}, "ips": [], "machines": [], "certs": []}
        _out(f"New app created: {name}\n")
        return 0

    app = _app(state, argv)
    if key == "secrets list":
        assert app is not None
        if state.get("secrets_list_garbage"):
            _out("<html>502 Bad Gateway</html>")
            return 0
        _out(
            json.dumps(
                [
                    {"name": n, "digest": hashlib.sha256(v.encode()).hexdigest()[:16], "status": "Deployed"}
                    for n, v in sorted(app["secrets"].items())
                ]
            )
        )
        return 0
    if key == "secrets import":
        assert app is not None and "--stage" in argv
        for line in stdin.splitlines():
            name, _, value = line.partition("=")
            app["secrets"][name.strip()] = value
        _out("Secrets are staged for the first deployment\n")
        return 0
    if key == "secrets unset":
        assert app is not None and "--stage" in argv
        names = [a for a in argv[2:] if not a.startswith("-") and a != _opt(argv, "-a", "--app")]
        assert names
        for name in names:
            app["secrets"].pop(name, None)
        _out("Secrets are staged for the next deployment\n")
        return 0
    if key == "ips list":
        assert app is not None
        _out(json.dumps(app["ips"]))
        return 0
    if key == "ips allocate-v6":
        assert app is not None
        app["ips"].append({"Address": V6, "Type": "v6", "Region": "global"})
        _out(f"VERSION  IP  TYPE  REGION\nv6  {V6}  public (dedicated)  global\n")
        return 0
    if key == "ips allocate-v4":
        assert app is not None and "--shared" in argv
        app["ips"].append({"Address": V4, "Type": "shared_v4", "Region": ""})
        _out(f"VERSION  IP  TYPE  REGION\nv4  {V4}  public (shared)\n")
        return 0
    if key == "mpg list":
        clusters = [c for c in state["clusters"] if c["Organization"]["Slug"] == org]
        for c in clusters:
            polls = c.get("ready_after_polls")
            if polls is not None:
                if polls <= 0:
                    c["Status"] = "ready"
                c["ready_after_polls"] = polls - 1
        if not clusters:
            _out(f"No managed postgres clusters found in organization {org}\n")
        else:
            _out(json.dumps([{k: v for k, v in c.items() if k != "ready_after_polls"} for c in clusters]))
        return 0
    if key == "mpg create":
        if state.get("create_hang"):
            _out("Waiting for cluster domeremote-staging-db (abc) to be ready...\n")
            sys.stdout.flush()
            time.sleep(60)
            return 1
        cluster_name = _opt(argv, "--name", "-n")
        cluster = {
            "Id": "c" + hashlib.sha256(str(cluster_name).encode()).hexdigest()[:14],
            "Name": cluster_name,
            "Region": _opt(argv, "--region", "-r"),
            "Status": state.get("create_status", "ready"),
            "Plan": "basic",
            "Organization": {"Slug": org},
        }
        state["clusters"].append(cluster)
        _out(
            "Selected Plan: Basic\n  Price: $38 per month\n\n"
            f"Managed Postgres cluster created successfully!\n  ID: {cluster['Id']}\n"
            f"  Connection string: {_conn(cluster)}\n"
        )
        return 0
    if key == "mpg attach":
        assert app is not None
        cluster = next(c for c in state["clusters"] if c["Id"] == argv[2])
        if state.get("attach_fails"):
            _err(f"Error: failed retrieving cluster {cluster['Id']}: 403 forbidden\n")
            return 1
        app["secrets"]["DATABASE_URL"] = _conn(cluster)
        _out(
            f"\nPostgres cluster {cluster['Id']} is being attached to {argv[4]}\n"
            f"The following secret was added to {argv[4]}:\n  DATABASE_URL={_conn(cluster)}\n"
        )
        return 0
    if key == "mpg status":
        _out(json.dumps({"credentials": {"password": FLY_DB_PASSWORD}}))
        return 0
    if key.startswith("deploy"):
        assert app is not None
        config_path = _opt(argv, "--config", "-c")
        try:
            config = tomllib.loads(Path(str(config_path)).read_text(encoding="utf-8"))  # relative to the cwd, as flyctl
        except (OSError, ValueError) as exc:
            _err(f"Error: failed loading app config from {config_path}: {exc}\n")
            return 1
        app["deployed_config"] = config
        app["deployed_config_path"] = config_path
        for needed in ("DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64", "DOME_SESSION_SECRET", "DOME_OIDC_CLIENT_SECRET"):
            if needed not in app["secrets"]:
                _out(f"Running release_command\nentrypoint: no entitlement signing key ({needed} missing)\n")
                return 1
        if not ({"DATABASE_URL", "DOME_DATABASE_URL"} & set(app["secrets"])):
            _out("Running release_command\nalembic: no database URL\n")
            return 1
        deployed_env = dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")
        app["deployed_env"] = deployed_env
        stripe = [n for n in STRIPE_NAMES if n in app["secrets"] or deployed_env.get(n)]
        if stripe and len(stripe) != len(STRIPE_NAMES):
            _out("Running release_command\nValueError: billing is half configured\n")
            return 1
        count = state.get("machines_after_deploy", 1)
        app["machines"] = [
            {"id": f"m{i}", "state": "started", "config": {"metadata": {"fly_process_group": "app"}}}
            for i in range(count)
        ]
        app["deployed_image_label"] = _opt(argv, "--image-label")
        _out("==> Release command completed successfully\n--> v1 deployed successfully\n")
        return 0
    if key == "machine list":
        assert app is not None
        _out(json.dumps(app["machines"]))
        return 0
    if key == "scale count":
        assert app is not None and argv[2] == "1"
        app["machines"] = app["machines"][:1]
        return 0
    if key == "certs list":
        assert app is not None
        _out(json.dumps([{"hostname": c["hostname"], "status": c["status"]} for c in app["certs"]] or None))
        return 0
    if key in ("certs add", "certs check"):
        assert app is not None
        host = argv[2]
        if key == "certs add":
            app["certs"].append({"hostname": host, "status": "pending"})
        cert = next(c for c in app["certs"] if c["hostname"] == host)
        app_name = _opt(argv, "-a")
        detail = {
            "hostname": host,
            "status": cert["status"],
            "dns_requirements": {
                "a": [ip["Address"] for ip in app["ips"] if ip["Type"] in ("v4", "shared_v4")],
                "aaaa": [ip["Address"] for ip in app["ips"] if ip["Type"] == "v6"],
                "cname": f"{app_name}.fly.dev",
                "acme_challenge": {"name": f"_acme-challenge.{host}", "target": f"{host}.x1y2.flydns.net"},
                "ownership": {"name": "", "app_value": ""},
            },
        }
        _out(json.dumps(detail))
        return 0
    _err(f"fake flyctl: unsupported command {argv}\n")
    return 99


def docker(argv: list[str], state: dict[str, Any]) -> int:
    if argv[:1] != ["build"]:
        _err(f"fake docker: unsupported command {argv}\n")
        return 99
    if state.get("docker_fails"):
        _out("#12 ERROR: process did not complete successfully\n")
        return 1
    _out("#1 [internal] load build definition from Dockerfile\n#20 naming to docker.io/library/image done\n")
    return 0


def main() -> int:
    tool = sys.argv[1]
    argv = sys.argv[2:]
    state_path = Path(os.environ["FAKE_FLY_STATE"])
    stdin = sys.stdin.read() if not sys.stdin.isatty() else ""
    with Path(os.environ["FAKE_FLY_LOG"]).open("a", encoding="utf-8") as log:
        log.write(
            json.dumps(
                {
                    "tool": tool,
                    "argv": argv,
                    "stdin": stdin,
                    "env_has_client_secret": "DOME_OIDC_CLIENT_SECRET" in os.environ,
                    "env_has_database_url": "DEPLOY_DATABASE_URL" in os.environ,
                    "env_has_fly_token": "FLY_API_TOKEN" in os.environ,
                    "env_has_gh_token": "GH_TOKEN" in os.environ,
                    "env_has_stripe_secret": any(
                        n in os.environ for n in ("DOME_STRIPE_SECRET_KEY", "DOME_STRIPE_WEBHOOK_SECRET")
                    ),
                }
            )
            + "\n"
        )
    state = _load(state_path)
    code = docker(argv, state) if tool == "docker" else flyctl(argv, stdin, state)
    _save(state_path, state)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
