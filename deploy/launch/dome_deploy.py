#!/usr/bin/env python3
"""DoMe one-button deploy to Fly.io: everything the "Deploy" GitHub Actions workflow does.

    dome_deploy.py gate     before approval (no secrets): branch, no repository-level copies of the deploy
                            secrets, both GitHub Environments protected, and for production, staging runs
                            this commit, healthy
    dome_deploy.py deploy   set up what is missing, deploy, check (needs FLY_API_TOKEN)
    dome_deploy.py status   look only: HTTP, DNS and TLS of one environment (no secrets, never runs flyctl)

Configuration comes from environment variables (the workflow's env block), never from arguments, so no
secret ever appears on a command line. See docs/LAUNCH_SETUP.md for what the founder pastes where and
deploy/README.md for what each step does.

Secret rules enforced here (tested in tests/test_dome_deploy.py):

* Secrets live in memory only. Values generated here are registered with ``::add-mask::`` before any other
  use (GitHub masks only the secrets it stores), and reach Fly only through ``flyctl secrets import`` on
  stdin. Every command line is checked for known secret values before it runs.
* Child output is redacted: known values, credentials in URLs, Fly tokens and private-key blocks.
  ``flyctl mpg create`` and ``flyctl mpg attach`` print the database password, so their output is never
  shown at all; only a fixed line and the exit code are.
* Child processes do not inherit DOME_OIDC_CLIENT_SECRET, DEPLOY_DATABASE_URL (the GitHub secret DATABASE_URL),
  DOME_STRIPE_SECRET_KEY, DOME_STRIPE_WEBHOOK_SECRET, DOME_TURN_API_TOKEN, DOME_TURN_ANALYTICS_TOKEN or GH_TOKEN; flyctl
  gets FLY_API_TOKEN because it needs it. The
  database password inside DATABASE_URL is masked on its own too (GitHub masks only the whole stored value).
* The summary (``$GITHUB_STEP_SUMMARY``) names secrets and says what happened to them; it never holds a value.
* The gate job never sees a secret value: GitHub evaluates ``secrets.X != ''`` and hands over only the NAMES of
  deploy secrets that exist at repository level (REPO_LEVEL_SECRETS); its GH_TOKEN is the job's read-only token.

Databases (``database_mode`` in environments.toml): staging uses an EXTERNAL database (Neon's free plan) whose address
is the required GitHub Environment secret DATABASE_URL, stored in Fly again on every deploy; it never touches Fly
Managed Postgres. Production uses Fly Managed Postgres ("fly-managed"), created only with the founder's tick; its
DATABASE_URL secret is used only when Fly will not list or attach the cluster, and a Neon address there is refused.
Staging and production must resolve to two different Fly apps (checked before any flyctl call).

Billing (Stripe, docs/BILLING.md): the GitHub Environment is the only source of the seven Stripe values. All seven or
none, checked before any flyctl call. With all seven, the two secrets are staged into Fly on stdin and the five price
ids are passed with ``flyctl deploy -e`` on every deploy; with none, billing is off and any DOME_STRIPE_* secret an
earlier deploy left in Fly is removed with ``flyctl secrets unset --stage`` (the app refuses to start with only some
of the seven). A live key is refused on staging; production accepts test keys, and the summary says which mode runs.
With billing on, the deploy then asks Stripe itself, still before any flyctl call (GET only, the key in a header): which
account the key belongs to, whether it has a name (Checkout refuses an account without one) and, for a live key, may
take payments; whether each price exists for that key with the type, interval, currency and amount DoMe shows
(shared/protocol/plans.json); whether a webhook endpoint for this environment listens to the events DoMe needs; and
whether the customer portal has been saved. A mismatch stops the run with the founder's fix; Stripe not answering
never stops a deploy (the summary says the check did not run).

Each deploy writes a temporary per-environment copy of deploy/fly.toml with that environment's machine-sleep settings
(staging sleeps when idle, production never does) and deletes it afterwards.

Nothing in this program has run against a live Fly.io account yet. flyctl output shapes were read from the
flyctl v0.4.110 source and are parsed leniently; anything unexpected stops the run with a plain message
before it can change something it does not understand.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, Any, TextIO
from urllib.parse import parse_qs, quote, urlencode, urlsplit

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
ENVIRONMENTS_FILE = HERE / "environments.toml"
FLY_CONFIG = "deploy/fly.toml"
# The per-environment copy handed to `flyctl deploy --config`. It sits next to deploy/fly.toml so every path in it
# means the same thing; it is deleted after the deploy and ignored by git (.gitignore).
GENERATED_FLY_CONFIG = "deploy/fly.{name}.generated.toml"
DOCKERFILE = "deploy/Dockerfile"
NEON_CONSOLE = "https://console.neon.tech"

ENVIRONMENTS = ("staging", "production")
# Environment variables a child process never receives (flyctl reads FLY_API_TOKEN itself and keeps it).
CHILD_ENV_DROP = frozenset(
    {
        "DOME_OIDC_CLIENT_SECRET",
        "DEPLOY_DATABASE_URL",
        "DOME_STRIPE_SECRET_KEY",
        "DOME_STRIPE_WEBHOOK_SECRET",
        "DOME_TURN_API_TOKEN",
        "DOME_TURN_ANALYTICS_TOKEN",
        "GH_TOKEN",
    }
)
# The deploy secrets that must exist only as GitHub ENVIRONMENT secrets. The gate job (no environment) receives
# the names of any that exist at repository or organization level in REPO_LEVEL_SECRETS (deploy.yml), never values.
DEPLOY_SECRET_NAMES = (
    "FLY_API_TOKEN",
    "DOME_OIDC_CLIENT_SECRET",
    "DATABASE_URL",
    "DOME_STRIPE_SECRET_KEY",
    "DOME_STRIPE_WEBHOOK_SECRET",
    "DOME_TURN_API_TOKEN",
    "DOME_TURN_ANALYTICS_TOKEN",
)
GITHUB_API = "https://api.github.com"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 section 1.3
GENERATED_SECRETS = ("DOME_SESSION_SECRET", "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64")
DATABASE_SECRET_NAMES = ("DATABASE_URL", "DOME_DATABASE_URL")
# Billing: the seven values of cloud-api/dome_api/settings.py (_STRIPE_FIELDS), all or none. The two secrets are GitHub
# Environment SECRETS staged into Fly on stdin; the five price ids are GitHub Environment VARIABLES passed with -e.
STRIPE_SECRET_NAMES = ("DOME_STRIPE_SECRET_KEY", "DOME_STRIPE_WEBHOOK_SECRET")
STRIPE_PRICES = {  # variable -> what the founder calls that price (docs/LAUNCH_SETUP.md section 11A)
    "DOME_STRIPE_PRICE_PRO_MONTHLY": "DoMe Pro, monthly",
    "DOME_STRIPE_PRICE_PRO_ANNUAL": "DoMe Pro, yearly",
    "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY": "DoMe Pro Plus, monthly",
    "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL": "DoMe Pro Plus, yearly",
    "DOME_STRIPE_PRICE_TOPUP": "DoMe AI top-up, one-time",
}
STRIPE_NAMES = (*STRIPE_SECRET_NAMES, *STRIPE_PRICES)
STRIPE_TEST_KEY_PREFIXES = ("sk_test_", "rk_test_")
STRIPE_LIVE_KEY_PREFIXES = ("sk_live_", "rk_live_")
STRIPE_WEBHOOK_PATH = "/v1/billing/webhook"  # cloud-api/dome_api/routes/billing.py, mounted under /v1
# The events the webhook acts on (cloud-api/dome_api/billing/service.py: SUBSCRIPTION_EVENTS, CHECKOUT_DONE_EVENTS and
# checkout.session.expired), then the ones it only records for the operator (OPERATOR_EVENTS). Anything else is ignored.
STRIPE_WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.expired",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
    "invoice.payment_action_required",
)
STRIPE_OPERATOR_EVENTS = (
    "charge.refunded",
    "charge.dispute.created",
    "charge.dispute.updated",
    "charge.dispute.closed",
)
BILLING_MODE_TEXT = {
    "off": "Stripe: off — nothing can be bought, everyone stays on Free",
    "test": "Stripe: TEST MODE — no real charges",
    "live": "Stripe: LIVE — real charges",
}
# With billing on, the deploy asks Stripe itself (GET only) whether the seven values fit together, before any flyctl
# call. The API version is the one cloud-api's stripe SDK pins (stripe/_api_version.py), so this check reads the shapes
# the app gets; every path, parameter and field used was checked against Stripe's OpenAPI spec of that version
# (github.com/stripe/openapi, spec3.json) and the requests were run against stripe-mock v0.206.0.
STRIPE_API = "https://api.stripe.com"
STRIPE_API_VERSION = "2026-09-30.endive"
STRIPE_SDK_VERSION = "16.0.0"  # cloud-api/uv.lock; tests/test_deploy_stripe_check.py fails when it changes
STRIPE_TIMEOUT = 10.0
PLANS_FILE = REPO_ROOT / "shared" / "protocol" / "plans.json"
# What each price must be at Stripe: its recurring interval ("" = one-time), and where plans.json keeps the amount DoMe
# shows for it (under pricing_defaults).
STRIPE_PRICE_TERMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "DOME_STRIPE_PRICE_PRO_MONTHLY": ("month", ("plans", "pro", "monthly_cents")),
    "DOME_STRIPE_PRICE_PRO_ANNUAL": ("year", ("plans", "pro", "annual_cents")),
    "DOME_STRIPE_PRICE_PRO_PLUS_MONTHLY": ("month", ("plans", "pro_plus", "monthly_cents")),
    "DOME_STRIPE_PRICE_PRO_PLUS_ANNUAL": ("year", ("plans", "pro_plus", "annual_cents")),
    "DOME_STRIPE_PRICE_TOPUP": ("", ("topup", "price_cents")),
}
# Screen view, Phase 1 beta (docs/design/screen-view.md §12, §13, §20; cloud-api settings.py _v_screen). The relay
# server (Cloudflare Realtime TURN) for the PHONE needs two values, both or neither: the Environment VARIABLE
# DOME_TURN_KEY_ID and the Environment SECRET DOME_TURN_API_TOKEN (staged into Fly on stdin). The SECRET
# DOME_TURN_ANALYTICS_TOKEN (read-only analytics scope, for the hourly usage reconciliation) is optional and accepted
# only with them. DOME_SCREEN_VIEW_ENABLED is a VARIABLE and the beta's switch: "true" switches it on; anything else
# leaves it off, and the summary says so honestly. Without the TURN values Screen view works only where a direct
# connection exists (the app says `turn: unavailable`).
TURN_KEY_ID = "DOME_TURN_KEY_ID"
TURN_SECRET_NAMES = ("DOME_TURN_API_TOKEN", "DOME_TURN_ANALYTICS_TOKEN")
TURN_NAMES = (TURN_KEY_ID, *TURN_SECRET_NAMES)
SCREEN_VIEW_SWITCH = "DOME_SCREEN_VIEW_ENABLED"
SCREEN_VIEW_TEXT = {"off": "Screen view: off", "on": "Screen view: ON (beta)"}
TURN_TEXT = {
    "off": "relay server: none (no `DOME_TURN_` values; Screen view works only where a direct connection exists)",
    "on": "relay server: Cloudflare TURN",
}
DATABASE_MODES = ("external", "fly-managed")
AUTO_STOP_VALUES = ("off", "stop", "suspend")
# The three [http_service] keys that differ per environment (environments.toml -> the generated fly.toml copy).
MACHINE_SLEEP_KEYS = ("auto_stop_machines", "auto_start_machines", "min_machines_running")
# Cloudflare's published edge ranges (cloudflare.com/ips): a record pointing here is proxied (orange cloud).
CLOUDFLARE_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)

# Time budgets in seconds (module attributes so tests can shrink them). The workflow job allows 90 minutes;
# RUN_BUDGET keeps the whole run inside 80 so the summary is always written before GitHub stops the job.
RUN_BUDGET = 80 * 60.0
MIN_TIME_TO_DEPLOY = 20 * 60.0  # build + deploy + checks need at least this much of what is left
DEFAULT_TIMEOUT = 180.0
MPG_CREATE_TIMEOUT = 25 * 60.0  # `flyctl mpg create` waits for the cluster itself, with no limit of its own
MPG_READY_WAIT = 15 * 60.0
MPG_POLL_INTERVAL = 20.0
BUILD_TIMEOUT = 40 * 60.0
DEPLOY_TIMEOUT = 30 * 60.0
SMOKE_WAIT = 180.0
SMOKE_INTERVAL = 10.0
WAKE_ATTEMPTS = 4  # a sleeping staging machine (and its database) needs a few seconds to answer the first request
HTTP_TIMEOUT = 15.0

sleep: Callable[[float], None] = time.sleep
monotonic: Callable[[], float] = time.monotonic

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_APP_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
_ORG_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_HOSTNAME = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_ISSUER_PATH = re.compile(r"^[A-Za-z0-9._~/-]*$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9._~:@-]{1,64}$")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@]+@")
_FLY_TOKENS = re.compile(r"\b(FlyV1\s+\S+|f[mo][12]_[A-Za-z0-9_+/=-]{8,})")
_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)
_STRIPE_SECRETS = re.compile(r"\b((?:sk|rk)_(?:test|live)_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9+/=]{8,})")
_STRIPE_PRICE_ID = re.compile(r"^price_[A-Za-z0-9_]{4,}$")  # cloud-api settings.py _PRICE_ID
# Keys and signing secrets in Stripe's own error texts (Stripe shows a key part-masked; stripe-mock echoes the header)
_STRIPE_KEYISH = re.compile(r"(?i)\bbearer\s+\S+|\b(?:sk|rk|pk)_(?:test|live)_[^\s'\"]*|\bwhsec_[^\s'\"]*")
_STRIPE_ACCOUNT_ID = re.compile(r"^acct_[A-Za-z0-9]{1,64}$")
# cloud-api settings.py turn_key_id pattern, with a floor so a cut-off paste is caught here, before Fly
_TURN_KEY_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_SECRET_ASSIGNMENT = re.compile(r"\b([A-Z][A-Z0-9_]*(?:URL|SECRET|PASSWORD|TOKEN|KEY|B64))=\S+")


# ======================================================================================================
# Small types
# ======================================================================================================


class Stop(Exception):  # noqa: N818 - a planned stop, not an error
    """A planned stop. ``kind``: "you" (the founder has to do something), "engineer" or "failed"."""

    def __init__(self, kind: str, message: str, *, next_steps: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.next_steps = list(next_steps)


@dataclass(frozen=True)
class EnvConfig:
    name: str
    dome_env: str
    fly_app: str
    region: str
    public_origin: str
    hostname: str
    dns_zone: str
    dns_name: str
    release_channel: str
    database_mode: str  # "external" (the GitHub secret DATABASE_URL) or "fly-managed" (Fly Managed Postgres)
    pg_major_version: int
    requires_staging_commit: bool
    auto_stop_machines: str
    auto_start_machines: bool
    min_machines_running: int
    # fly-managed only
    database_cluster: str = ""
    database_plan: str = ""
    database_volume_gb: int = 0
    # external only: the database provider's project name the founder's guide uses (shown in "needs you" steps)
    external_database_project: str = ""

    @property
    def title(self) -> str:
        return self.name.capitalize()

    @property
    def callback_url(self) -> str:
        return self.public_origin + "/v1/auth/callback"

    @property
    def webhook_url(self) -> str:
        """Where Stripe sends this environment's billing events (the founder types it into Stripe)."""
        return self.public_origin + STRIPE_WEBHOOK_PATH

    @property
    def external_database(self) -> bool:
        return self.database_mode == "external"

    @property
    def sleeps_when_idle(self) -> bool:
        return self.auto_stop_machines != "off"

    @property
    def machine_sleep(self) -> dict[str, str | bool | int]:
        return {
            "auto_stop_machines": self.auto_stop_machines,
            "auto_start_machines": self.auto_start_machines,
            "min_machines_running": self.min_machines_running,
        }


def environment_problems(cfg: EnvConfig) -> list[str]:
    """What is inconsistent in one environments.toml section (empty when it is usable)."""
    problems = []
    if cfg.database_mode not in DATABASE_MODES:
        problems.append(f"database_mode must be one of {', '.join(DATABASE_MODES)}")
    if cfg.database_mode == "fly-managed" and not (
        cfg.database_cluster and cfg.database_plan and cfg.database_volume_gb > 0
    ):
        problems.append("a fly-managed database needs database_cluster, database_plan and database_volume_gb")
    if cfg.database_mode == "external" and (cfg.database_cluster or cfg.database_plan or cfg.database_volume_gb):
        problems.append("an external database has no Fly cluster: remove database_cluster, _plan and _volume_gb")
    if cfg.database_mode == "external" and not cfg.external_database_project:
        problems.append("an external database needs external_database_project (named in the founder's steps)")
    if cfg.auto_stop_machines not in AUTO_STOP_VALUES:
        problems.append(f"auto_stop_machines must be one of {', '.join(AUTO_STOP_VALUES)}")
    if not isinstance(cfg.auto_start_machines, bool):
        problems.append("auto_start_machines must be true or false")
    if not isinstance(cfg.min_machines_running, int) or isinstance(cfg.min_machines_running, bool):
        problems.append("min_machines_running must be a whole number")
    elif cfg.min_machines_running not in (0, 1):
        problems.append("min_machines_running must be 0 or 1 (the relay runs on exactly one machine)")
    if cfg.sleeps_when_idle and cfg.auto_start_machines is not True:
        problems.append("a machine that stops when idle must start again on the next request (auto_start_machines)")
    if cfg.dome_env == "production" and (cfg.sleeps_when_idle or cfg.min_machines_running != 1):
        problems.append("production must stay on (every linked PC holds a WebSocket): auto_stop off, 1 machine")
    return problems


def load_environments(path: Path = ENVIRONMENTS_FILE) -> dict[str, EnvConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, EnvConfig] = {}
    for name in ENVIRONMENTS:
        section = data[name]
        cfg = EnvConfig(name=name, **section)
        problems = environment_problems(cfg)
        if problems:
            raise ValueError(f"{path.name} [{name}]: " + "; ".join(problems))
        out[name] = cfg
    return out


def toml_literal(value: str | bool | int) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value)  # a TOML basic string for the plain ASCII values used here


def render_fly_config(cfg: EnvConfig, base_text: str) -> str:
    """deploy/fly.toml with this environment's machine-sleep settings in [http_service]. The copy must parse to
    exactly the base configuration with only those three keys changed, or nothing is deployed."""
    text = base_text
    for key, value in cfg.machine_sleep.items():
        pattern = re.compile(rf"^([ \t]*){key}[ \t]*=[^\n]*$", re.MULTILINE)
        if len(pattern.findall(text)) != 1:
            raise Stop("engineer", f"{FLY_CONFIG} must set {key} on exactly one line; nothing was deployed.")
        line = f"{key} = {toml_literal(value)}"
        text = pattern.sub(lambda m: m.group(1) + line, text)  # noqa: B023 - used immediately, inside this iteration
    base = tomllib.loads(base_text)
    expected = copy.deepcopy(base)
    service = expected.get("http_service")
    if not isinstance(service, dict):
        raise Stop("engineer", f"{FLY_CONFIG} has no [http_service] section; nothing was deployed.")
    service.update(cfg.machine_sleep)
    if tomllib.loads(text) != expected:
        raise Stop("engineer", f"The {cfg.name} copy of {FLY_CONFIG} would change more than machine sleep.")
    header = (
        f"# GENERATED by deploy/launch/dome_deploy.py for {cfg.name} from {FLY_CONFIG} (machine sleep from "
        "deploy/launch/environments.toml).\n# Deleted after the deploy; never edit or commit it.\n"
    )
    return header + text


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None


class HttpFailure(Exception):  # noqa: N818
    """``kind``: "dns", "tls", "connect" or "timeout"."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Hands a 3xx answer back as a result (the sign-in check reads its Location header itself)."""

    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: HTTPMessage, newurl: str
    ) -> None:
        return None


def http_get(
    url: str,
    *,
    timeout: float = HTTP_TIMEOUT,
    headers: Mapping[str, str] | None = None,
    follow_redirects: bool = True,
) -> HttpResult:
    """GET with TLS verification. Non-2xx answers are results, not exceptions (3xx too without redirects)."""
    request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")  # noqa: S310 - https only
    if not url.startswith("https://"):
        raise ValueError("https only")
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as resp:
            return HttpResult(resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(1 << 20))
    except urllib.error.HTTPError as exc:
        return HttpResult(exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read(1 << 20))
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, ssl.SSLError | ssl.CertificateError):
            raise HttpFailure("tls", str(reason)) from None
        if isinstance(reason, socket.gaierror):
            raise HttpFailure("dns", str(reason)) from None
        if isinstance(reason, TimeoutError):
            raise HttpFailure("timeout", str(reason)) from None
        raise HttpFailure("connect", str(reason)) from None
    except (ssl.SSLError, ssl.CertificateError) as exc:
        raise HttpFailure("tls", str(exc)) from None
    except TimeoutError as exc:
        raise HttpFailure("timeout", str(exc)) from None
    except OSError as exc:
        raise HttpFailure("connect", str(exc)) from None


def resolve(hostname: str) -> list[str]:
    """Every address the name resolves to (A and AAAA). Raises OSError when it does not resolve."""
    infos = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
    return sorted({str(ipaddress.ip_address(str(info[4][0]).split("%")[0])) for info in infos})


def websocket_status(host: str, path: str, *, timeout: float = HTTP_TIMEOUT) -> int:
    """Send an RFC 6455 upgrade request without credentials over TLS and return the HTTP status of the answer
    (0 when it is not HTTP; 101 only with a correct Sec-WebSocket-Accept). Raises HttpFailure like http_get."""
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nUser-Agent: dome-deploy\r\n\r\n"
    )
    data = b""
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host) as tls:
                tls.sendall(request.encode("ascii"))
                while b"\r\n\r\n" not in data and len(data) < 16384:
                    chunk = tls.recv(4096)
                    if not chunk:
                        break
                    data += chunk
    except (ssl.SSLError, ssl.CertificateError) as exc:
        raise HttpFailure("tls", str(exc)) from None
    except socket.gaierror as exc:
        raise HttpFailure("dns", str(exc)) from None
    except TimeoutError as exc:
        raise HttpFailure("timeout", str(exc)) from None
    except OSError as exc:
        raise HttpFailure("connect", str(exc)) from None
    lines = data.partition(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")
    match = re.match(r"^HTTP/1\.[01] (\d{3})\b", lines[0])
    if not match:
        return 0
    status = int(match.group(1))
    if status == 101:
        headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:])}
        accept = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")  # noqa: S324 - RFC 6455
        return 101 if headers.get("sec-websocket-accept") == accept else 0
    return status


def github_get(env: Mapping[str, str], path: str) -> HttpResult:
    """GitHub REST API GET with the job's own token (GH_TOKEN, read-only), never a founder secret."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dome-deploy",
    }
    token = env.get("GH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return http_get(GITHUB_API + path, headers=headers)


# ======================================================================================================
# Secrets
# ======================================================================================================


def importable(value: str) -> bool:
    """True when ``NAME=VALUE`` survives flyctl's stdin parser unchanged: printable ASCII without spaces,
    quotes or ``#`` (the parser cuts comments at ``#`` and strips surrounding quotes)."""
    return bool(value) and all(0x21 <= ord(c) <= 0x7E and c not in "#\"'`\\" for c in value)


class SecretBook:
    """Every secret value this run knows. Values never leave memory except through flyctl's stdin."""

    MIN_LENGTH = 8

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._values: list[str] = []

    def add(self, value: str, *, mask: bool) -> None:
        """Remember ``value`` (and each of its lines) for argv checks and redaction. ``mask``: also register
        each line with GitHub (``::add-mask::`` takes one line; a multi-line command would print the rest)."""
        lines = [line.strip() for line in value.splitlines()]
        parts = [value] if len(lines) <= 1 else [value, *(line for line in lines if not line.startswith("-----"))]
        for part in parts:
            if len(part.strip()) < self.MIN_LENGTH or part in self._values:
                continue
            self._values.append(part)
            if mask and "\n" not in part and "\r" not in part:
                # GitHub masks only the secrets it stores; a generated value must be registered before use.
                print(f"::add-mask::{part}", file=self._out, flush=True)
        self._values.sort(key=len, reverse=True)

    def add_fly_token(self, token: str) -> None:
        """The whole token is a stored GitHub secret already; its comma-separated parts are not."""
        self.add(token, mask=False)
        body = token.strip()
        if body.startswith("FlyV1 "):
            body = body[len("FlyV1 ") :]
        for part in body.split(","):
            self.add(part, mask=True)

    def check_argv(self, argv: Sequence[str]) -> None:
        for arg in argv:
            for value in self._values:
                if value in arg:
                    raise RuntimeError("refusing to run a command whose arguments contain a secret value")

    def redact(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "***")
        text = _PRIVATE_KEY_BLOCK.sub("[private key hidden]", text)
        text = _URL_CREDENTIALS.sub(r"\1:***@", text)
        text = _FLY_TOKENS.sub("[fly token hidden]", text)
        text = _STRIPE_SECRETS.sub("[stripe secret hidden]", text)
        return _SECRET_ASSIGNMENT.sub(r"\1=***", text)


# ======================================================================================================
# Running commands
# ======================================================================================================


@dataclass
class Result:
    code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


class Runner:
    def __init__(self, env: Mapping[str, str], book: SecretBook, out: TextIO) -> None:
        self.child_env = {k: v for k, v in env.items() if k not in CHILD_ENV_DROP}
        self.book = book
        self.out = out
        self.calls = 0

    def _resolve(self, argv: Sequence[str]) -> list[str]:
        found = shutil.which(argv[0], path=self.child_env.get("PATH", os.defpath))
        if found is None:
            raise Stop("engineer", f"`{argv[0]}` is not installed on the runner.")
        return [found, *argv[1:]]

    def _announce(self, argv: Sequence[str], note: str = "") -> None:
        self.book.check_argv(argv)
        print(f"$ {shlex.join(argv)}{note}", file=self.out, flush=True)

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        sensitive: bool = False,
        echo: bool = True,
    ) -> Result:
        """Run to completion. ``sensitive``: the output may hold credentials and is never shown."""
        self._announce(argv, "   (output hidden: it can contain credentials)" if sensitive else "")
        self.calls += 1
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, no shell, checked for secrets
                self._resolve(argv),
                input=input_text,
                stdin=subprocess.DEVNULL if input_text is None else None,
                capture_output=True,
                encoding="utf-8",
                errors="replace",  # one stray byte in a tool's output must not end the run
                timeout=timeout,
                env=self.child_env,
                cwd=REPO_ROOT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"  ... stopped after {int(timeout)} s", file=self.out, flush=True)
            return Result(code=-1, timed_out=True)
        result = Result(proc.returncode, proc.stdout or "", proc.stderr or "")
        if sensitive:
            print(f"  exit code {result.code}", file=self.out, flush=True)
        elif echo:
            for text in (result.stdout, result.stderr):
                text = self.book.redact(text).strip()
                if text:
                    print(_indent(_tail(text, 6000)), file=self.out, flush=True)
            if result.code != 0:
                print(f"  exit code {result.code}", file=self.out, flush=True)
        return result

    def stream(self, argv: Sequence[str], *, timeout: float) -> Result:
        """Run with live, redacted output (builds and deploys take minutes)."""
        self._announce(argv)
        self.calls += 1
        proc = subprocess.Popen(  # noqa: S603 - argv list, no shell, checked for secrets
            self._resolve(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",  # one stray byte in a build log must not end the run
            env=self.child_env,
            cwd=REPO_ROOT,
        )
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(timeout, kill)
        timer.start()
        tail: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = self.book.redact(line.rstrip("\n"))
                print(f"  {clean}", file=self.out, flush=True)
                tail = (tail + [clean])[-40:]
            code = proc.wait()
        except BaseException:
            # never leave a deploy running unattended behind an error in this program
            proc.kill()
            proc.wait()
            raise
        finally:
            timer.cancel()
        if timed_out.is_set():
            print(f"  ... stopped after {int(timeout)} s", file=self.out, flush=True)
        return Result(code, "\n".join(tail), "", timed_out.is_set())


def _indent(text: str) -> str:
    return "\n".join("  " + line for line in text.splitlines())


def _tail(text: str, limit: int) -> str:
    return text if len(text) <= limit else "...\n" + text[-limit:]


# ======================================================================================================
# flyctl output parsing (lenient: shapes read from the flyctl v0.4.110 source, never seen live)
# ======================================================================================================


def field_of(item: Mapping[str, Any], *names: str) -> Any:
    """Case-insensitive key lookup (flyctl prints some structs with Go field names, some with json tags)."""
    lowered = {str(k).lower(): v for k, v in item.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def parse_list(text: str, *, empty_markers: Sequence[str] = ()) -> list[dict[str, Any]] | None:
    """A JSON list of objects; ``null``, empty output or an "empty" text marker is an empty list.
    Anything else is None (unparsable), which callers treat as "unknown", never as "absent"."""
    stripped = text.strip()
    if not stripped or any(stripped.startswith(marker) for marker in empty_markers):
        return []
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    if data is None:
        return []
    if isinstance(data, dict):
        for key in ("certificates", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        return None
    return data


def parse_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text.strip())
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# ======================================================================================================
# Summary
# ======================================================================================================


HEADLINES = {
    "you": "Stopped: needs you",
    "engineer": "Failed: needs an engineer",
    "failed": "Failed",
}


@dataclass
class Summary:
    title: str
    headline: str = ""
    steps: list[tuple[str, str]] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    problem: str = ""

    def step(self, name: str, result: str) -> None:
        self.steps.append((name, result))

    def render(self) -> str:
        lines = [f"# {self.title}", "", f"## {self.headline}", ""]
        if self.problem:
            lines += [self.problem, ""]
        if self.next_steps:
            lines += ["### What to do next", ""]
            lines += [f"{i}. {s}" for i, s in enumerate(self.next_steps, 1)]
            lines.append("")
        if self.steps:
            lines += ["### What this run did", "", "| Step | Result |", "| --- | --- |"]
            lines += [f"| {name} | {result} |" for name, result in self.steps]
            lines.append("")
        if self.notes:
            lines += ["### Notes", ""]
            lines += [f"- {n}" for n in self.notes]
            lines.append("")
        return "\n".join(lines)


def write_summary(summary: Summary, env: Mapping[str, str], out: TextIO) -> None:
    text = summary.render()
    path = env.get("GITHUB_STEP_SUMMARY", "")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    print("\n" + text, file=out, flush=True)


# ======================================================================================================
# Inputs
# ======================================================================================================


def environments_page(env: Mapping[str, str]) -> str:
    repo = env.get("GITHUB_REPOSITORY", "Heshh999/DoMe")
    return f"https://github.com/{repo}/settings/environments"


def variables_page(env: Mapping[str, str]) -> str:
    repo = env.get("GITHUB_REPOSITORY", "Heshh999/DoMe")
    return f"https://github.com/{repo}/settings/variables/actions"


def secrets_page(env: Mapping[str, str]) -> str:
    repo = env.get("GITHUB_REPOSITORY", "Heshh999/DoMe")
    return f"https://github.com/{repo}/settings/secrets/actions"


def token_next_steps(env: Mapping[str, str]) -> list[str]:
    """What the founder does when Fly refuses the token or does not know the organization."""
    name = env.get("DEPLOY_ENVIRONMENT", "").strip() or "this environment"
    return [
        f"Open {environments_page(env)}, choose **{name}**, and under **Environment secrets** paste the whole Fly "
        "token again (with `FlyV1 ` and the space at the start) over `FLY_API_TOKEN`. It must be an "
        "**organization** token from the Fly Tokens page. If you use another Fly organization than Personal, "
        "check the environment variable `FLY_ORG`.",
        "Run Deploy again.",
    ]


def normalize_issuer(raw: str) -> str:
    """Accepts what Auth0 shows as "Domain" (``x.us.auth0.com``) or a full issuer URL, with or without the
    trailing slash; returns ``https://host[/path]`` without it. Raises ValueError (message without the value)."""
    value = raw.strip()
    if not value:
        raise ValueError("it is empty")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if parts.scheme != "https":
        raise ValueError("it must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment or parts.port is not None:
        raise ValueError("it must be a plain https address (no user, port, ? or #)")
    host = (parts.hostname or "").lower()
    if not _HOSTNAME.match(host):
        raise ValueError("it does not look like a domain name (for example dome-staging.us.auth0.com)")
    path = parts.path.rstrip("/")
    if not _ISSUER_PATH.match(path):
        raise ValueError("its path has unexpected characters")
    return f"https://{host}{path}"


def app_name_for(cfg: EnvConfig, env: Mapping[str, str]) -> str:
    """Fly app names are global; FLY_STAGING_APP / FLY_PRODUCTION_APP (repository variables) override."""
    override = env.get(f"FLY_{cfg.name.upper()}_APP", "").strip()
    name = override or cfg.fly_app
    if not _APP_NAME.match(name):
        raise Stop(
            "you",
            f"The repository variable FLY_{cfg.name.upper()}_APP is not a valid Fly app name: use lower-case "
            "letters, digits and dashes only (for example domeremote-staging-2).",
            next_steps=[f"Fix it at {variables_page(env)} (Variables tab), then run Deploy again."],
        )
    return name


def check_distinct_apps(env: Mapping[str, str], envs: Mapping[str, EnvConfig]) -> None:
    """Staging and production must be two different Fly apps. Either Fly token reaches every app of the organization
    (docs/SECURITY.md section 6), so one wrong repository variable would otherwise send a staging deploy into the
    production app (staging's Neon address over production's database, DOME_ENV=staging, a machine that sleeps), or
    let production's checks look at production itself instead of staging. Runs before any flyctl call."""
    staging = app_name_for(envs["staging"], env)
    production = app_name_for(envs["production"], env)
    if staging != production:
        return
    raise Stop(
        "you",
        f"Staging and production would both use the Fly app {staging}. They must be two different apps, or a "
        "deploy of one would overwrite the other's settings and database address. Check the repository variables "
        "`FLY_STAGING_APP` and `FLY_PRODUCTION_APP`. Nothing was created or changed.",
        next_steps=[
            f"Open {variables_page(env)} (Variables tab) and correct `FLY_STAGING_APP` or `FLY_PRODUCTION_APP` so the "
            f"two names differ. Without these variables, staging is {envs['staging'].fly_app} and production is "
            f"{envs['production'].fly_app}.",
            "Run Deploy again.",
        ],
    )


def database_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_neon_host(host: str) -> bool:
    return host == "neon.tech" or host.endswith(".neon.tech")


@dataclass(frozen=True)
class Billing:
    """The Stripe values of one GitHub Environment. ``mode``: "off" (none of the seven), "test" or "live" (all seven,
    by the secret key's prefix). The two secrets live in memory only and reach Fly on stdin."""

    mode: str = "off"
    secret_key: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    prices: tuple[tuple[str, str], ...] = ()

    @property
    def on(self) -> bool:
        return self.mode != "off"


@dataclass
class BillingCheck:
    billing: Billing
    problems: list[str] = field(default_factory=list)
    missing_secrets: list[str] = field(default_factory=list)
    missing_vars: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def _names(names: Sequence[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def stripe_key_steps(env: Mapping[str, str], cfg: EnvConfig, name: str) -> str:
    """How to replace one Stripe secret that others may have seen (it was stored somewhere that is not hidden)."""
    page = environments_page(env)
    if name == "DOME_STRIPE_SECRET_KEY":
        source = (
            "In Stripe, open **Developers** > **API keys** and choose **Roll key** in the secret key's menu (this ends "
            "the old key at once)."
        )
    else:
        source = (
            f"In Stripe, open **Developers** > **Webhooks** > the endpoint `{cfg.webhook_url}` and roll its **Signing "
            "secret**."
        )
    return (
        f"{source} Then open {page}, choose **{cfg.name}**, and add the new value under **Environment secrets** as "
        f"`{name}`."
    )


def read_billing(env: Mapping[str, str], cfg: EnvConfig, book: SecretBook) -> BillingCheck:
    """The seven Stripe values, checked before any flyctl call with the rules of cloud-api settings.py (all or none;
    sk_/rk_ key, test-only outside production; whsec_ signing secret; five different price_ ids) plus the mix-ups a
    founder can make. Messages name variables, never values."""
    page = environments_page(env)
    key = env.get("DOME_STRIPE_SECRET_KEY", "").strip()
    webhook = env.get("DOME_STRIPE_WEBHOOK_SECRET", "").strip()
    for value in (key, webhook):
        if value:
            book.add(value, mask=False)  # stored GitHub secrets: GitHub masks the whole value itself
    prices = {name: env.get(name, "").strip() for name in STRIPE_PRICES}
    values = {"DOME_STRIPE_SECRET_KEY": key, "DOME_STRIPE_WEBHOOK_SECRET": webhook, **prices}
    # NAMES (never values) of the two secrets saved as Environment variables instead (deploy.yml evaluates
    # `vars.X != ''`): a variable is never hidden, so such a value must be replaced at Stripe.
    as_variables = [n for n in STRIPE_SECRET_NAMES if n in env.get("STRIPE_SECRETS_AS_VARIABLES", "").split()]
    check = BillingCheck(Billing())
    if not any(values.values()) and not as_variables:
        return check

    for name in as_variables:
        check.problems.append(
            f"`{name}` is saved as an Environment **variable**. It is a password, and GitHub never hides variables, "
            "so treat it as seen by others."
        )
        check.next_steps.append(
            stripe_key_steps(env, cfg, name)
            + f" Delete the **variable** `{name}` (Environment variables, the bin icon next to it)."
        )

    missing = [n for n in STRIPE_NAMES if not values[n] and n not in as_variables]
    if missing:
        check.problems.append(
            f"Payments are half set up in the {cfg.name} environment: {_names(missing)} "
            + ("is" if len(missing) == 1 else "are")
            + " missing. DoMe takes all seven Stripe values or none (with none, payments simply stay off)."
        )
        check.missing_secrets += [n for n in missing if n in STRIPE_SECRET_NAMES]
        check.missing_vars += [n for n in missing if n in STRIPE_PRICES]
        labels = "; ".join(f"`{n}` = {label}" for n, label in STRIPE_PRICES.items())
        check.next_steps.append(
            f"Each price variable is the id (it starts with `price_`) of one Stripe price: {labels} "
            "(docs/LAUNCH_SETUP.md section 11A). Price ids are **variables**, the key and the signing secret are "
            "**secrets**. To keep payments off instead, delete every `DOME_STRIPE_` secret and variable from the "
            f"{cfg.name} environment."
        )

    mode = ""
    if key:
        if not importable(key):
            check.problems.append(
                "`DOME_STRIPE_SECRET_KEY` contains a space, a quote or #. Copy it again with Stripe's copy button."
            )
        elif key.startswith(("pk_test_", "pk_live_")):
            check.problems.append(
                "`DOME_STRIPE_SECRET_KEY` is Stripe's **publishable** key (it starts with `pk_`). DoMe needs the "
                "**secret** key: in Stripe > **Developers** > **API keys**, click **Reveal** next to **Secret key** "
                "(it starts with `sk_test_` in Test mode, `sk_live_` in Live mode) and paste it over this one. The "
                "publishable key is public, so it needs no other change."
            )
        elif key.startswith("whsec_"):
            check.problems.append(
                "`DOME_STRIPE_SECRET_KEY` holds the webhook's signing secret (`whsec_...`). That one belongs in "
                "`DOME_STRIPE_WEBHOOK_SECRET`; `DOME_STRIPE_SECRET_KEY` is the secret key from **Developers** > "
                "**API keys** (`sk_test_...` or `sk_live_...`)."
            )
        elif not key.startswith(STRIPE_TEST_KEY_PREFIXES + STRIPE_LIVE_KEY_PREFIXES):
            check.problems.append(
                "`DOME_STRIPE_SECRET_KEY` is not a Stripe secret key: it must start with `sk_test_` (Test mode) or "
                "`sk_live_` (Live mode); a restricted key (`rk_test_` / `rk_live_`) works too."
            )
        elif len(key) < 24:
            check.problems.append(
                "`DOME_STRIPE_SECRET_KEY` is too short to be a whole Stripe key. Copy it again with Stripe's copy "
                "button."
            )
        elif key.startswith(STRIPE_LIVE_KEY_PREFIXES) and cfg.dome_env != "production":
            check.problems.append(
                f"`DOME_STRIPE_SECRET_KEY` in the {cfg.name} environment is a **live** key (`sk_live_...`), which "
                f"charges real cards. {cfg.title} must only ever use Test mode: switch the Stripe dashboard to **Test "
                "mode**, copy that secret key (`sk_test_...`) and paste it over this one. A live key belongs only in "
                "the production environment."
            )
        else:
            mode = "live" if key.startswith(STRIPE_LIVE_KEY_PREFIXES) else "test"

    if webhook:
        if not importable(webhook):
            check.problems.append(
                "`DOME_STRIPE_WEBHOOK_SECRET` contains a space, a quote or #. Copy it again with Stripe's copy button."
            )
        elif webhook.startswith(("sk_", "rk_", "pk_")):
            check.problems.append(
                "`DOME_STRIPE_WEBHOOK_SECRET` holds a Stripe API key, not the webhook's signing secret. Put the "
                "secret key in `DOME_STRIPE_SECRET_KEY`, and here the endpoint's **Signing secret** (it starts with "
                "`whsec_`)."
            )
        elif not webhook.startswith("whsec_") or len(webhook) < 16:
            check.problems.append(
                "`DOME_STRIPE_WEBHOOK_SECRET` must be the webhook endpoint's **Signing secret**, which starts with "
                f"`whsec_` (Stripe > **Developers** > **Webhooks** > the endpoint `{cfg.webhook_url}` > **Signing "
                "secret** > **Reveal**)."
            )

    for name, value in prices.items():
        label = STRIPE_PRICES[name]
        if not value or _STRIPE_PRICE_ID.match(value):
            continue
        if value.startswith(("sk_", "rk_", "whsec_")):
            secret = "DOME_STRIPE_WEBHOOK_SECRET" if value.startswith("whsec_") else "DOME_STRIPE_SECRET_KEY"
            check.problems.append(
                f"The variable `{name}` holds a Stripe secret instead of a price id. Variables are visible in this "
                "public log, so treat that secret as exposed."
            )
            check.next_steps.append(
                stripe_key_steps(env, cfg, secret)
                + f" Then put the price id of {label} (it starts with `price_`) in the variable `{name}`."
            )
        elif value.startswith("prod_"):
            check.problems.append(
                f"The variable `{name}` holds a product id (`prod_...`), not a price id. In Stripe > **Product "
                f"catalog**, open the product, click its price for {label}, and copy the id that starts with `price_`."
            )
        else:
            check.problems.append(
                f"The variable `{name}` ({label}) is not a Stripe price id: it must start with `price_` and have no "
                "spaces. In Stripe > **Product catalog** > the product > the price, copy the id that starts with "
                "`price_`."
            )
    seen: dict[str, list[str]] = {}
    for name, value in prices.items():
        if _STRIPE_PRICE_ID.match(value):
            seen.setdefault(value, []).append(name)
    for names in seen.values():
        if len(names) > 1:
            check.problems.append(
                f"The variables {_names(names)} hold the same price id. Each of the five must be a different Stripe "
                "price: " + "; ".join(STRIPE_PRICES.values()) + "."
            )

    if mode and not check.problems and not check.missing_secrets and not check.missing_vars:
        check.billing = Billing(mode, key, webhook, tuple(prices.items()))
    if check.problems or check.missing_secrets or check.missing_vars:
        check.next_steps.append(f"The Stripe values are at {page} > **{cfg.name}** (docs/LAUNCH_SETUP.md section 11A).")
    return check


def billing_step_text(billing: Billing, *, stored: bool = True) -> str:
    """The summary row "Billing (Stripe)"; ``stored`` False for a run that stopped before changing anything on Fly."""
    if billing.on and not stored:
        return (
            f"**{BILLING_MODE_TEXT[billing.mode]}** (read from this GitHub Environment; nothing was stored in Fly, as "
            "this run stopped before changing anything)"
        )
    if billing.on:
        return (
            f"**{BILLING_MODE_TEXT[billing.mode]}** (the secret key and the webhook signing secret stored in Fly, the "
            "five price ids passed to the app, all from this GitHub Environment)"
        )
    return f"**{BILLING_MODE_TEXT['off']}** (this GitHub Environment has no `DOME_STRIPE_` values)"


def billing_next_steps(cfg: EnvConfig, billing: Billing) -> list[str]:
    """What the founder checks in Stripe after a deploy with billing on (a deploy cannot see the webhook's settings)."""
    if not billing.on:
        return []
    mode = "Test mode" if billing.mode == "test" else "Live mode"
    steps = [
        f"In Stripe ({mode}) > **Developers** > **Webhooks**, the endpoint for {cfg.name} must be exactly "
        f"`{cfg.webhook_url}` and listen to these events: "
        + ", ".join(f"`{e}`" for e in STRIPE_WEBHOOK_EVENTS)
        + "; and, so an engineer hears about refunds and disputes, "
        + ", ".join(f"`{e}`" for e in STRIPE_OPERATOR_EVENTS)
        + f". Its **Signing secret** is the {cfg.name} Environment secret `DOME_STRIPE_WEBHOOK_SECRET`."
    ]
    if billing.mode == "test":
        steps.append(
            f'To try a payment: sign in at https://{cfg.hostname}, open **Billing** (it says "Test mode: no real '
            'charges"), choose DoMe Pro and pay with the test card `4242 4242 4242 4242`, any future date, any '
            "3-digit CVC (docs/LAUNCH_SETUP.md section 11A)."
        )
    return steps


@dataclass(frozen=True)
class ScreenView:
    """The Screen view values of one GitHub Environment: the switch, and the relay server (TURN) values when both
    are present. The secrets live in memory only and reach Fly on stdin."""

    enabled: bool = False
    turn: str = "off"  # "off" or "on"
    key_id: str = ""
    api_token: str = field(default="", repr=False)
    analytics_token: str = field(default="", repr=False)

    @property
    def turn_on(self) -> bool:
        return self.turn == "on"

    @property
    def reconciliation(self) -> bool:
        return self.turn_on and bool(self.analytics_token)


@dataclass
class ScreenViewCheck:
    screen: ScreenView
    problems: list[str] = field(default_factory=list)
    missing_secrets: list[str] = field(default_factory=list)
    missing_vars: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def turn_token_steps(name: str) -> str:
    """How to replace a Cloudflare token that others may have seen (it was stored somewhere that is not hidden)."""
    if name == "DOME_TURN_API_TOKEN":
        return (
            "In the Cloudflare dashboard open **Realtime** > **TURN Server**, delete the key this token belongs to and "
            "create a new one (docs/design/screen-view.md section 20, step 1); paste the new key's token only as the "
            "Environment secret `DOME_TURN_API_TOKEN` and its id as the Environment variable `DOME_TURN_KEY_ID`."
        )
    return (
        "In the Cloudflare dashboard open **My Profile** > **API Tokens**, roll (or delete and recreate) the read-only "
        "analytics token, and paste the new one only as the Environment secret `DOME_TURN_ANALYTICS_TOKEN`."
    )


def read_screen_view(env: Mapping[str, str], cfg: EnvConfig, book: SecretBook) -> ScreenViewCheck:
    """The Screen view switch and the relay server values, checked before any flyctl call with the rules of cloud-api
    settings.py (`_v_screen`): key id and API token both or neither, the analytics token only with them, usable shapes.
    Messages name variables, never values."""
    page = environments_page(env)
    switch = env.get(SCREEN_VIEW_SWITCH, "").strip().lower()
    key_id = env.get(TURN_KEY_ID, "").strip()
    api_token = env.get("DOME_TURN_API_TOKEN", "").strip()
    analytics = env.get("DOME_TURN_ANALYTICS_TOKEN", "").strip()
    for value in (api_token, analytics):
        if value:
            book.add(value, mask=False)  # stored GitHub secrets: GitHub masks the whole value itself
    # NAMES (never values) of the two secrets saved as Environment variables instead (deploy.yml evaluates
    # `vars.X != ''`): a variable is never hidden, so such a value must be replaced at Cloudflare.
    as_variables = [n for n in TURN_SECRET_NAMES if n in env.get("TURN_SECRETS_AS_VARIABLES", "").split()]
    check = ScreenViewCheck(ScreenView())
    if switch not in ("", "true", "false"):
        check.problems.append(
            f"The variable `{SCREEN_VIEW_SWITCH}` must be `true` or `false` (or absent, which means off)."
        )
    enabled = switch == "true"
    for name in as_variables:
        check.problems.append(
            f"`{name}` is saved as an Environment **variable**. It is a password, and GitHub never hides variables, "
            "so treat it as seen by others."
        )
        check.next_steps.append(
            turn_token_steps(name)
            + f" Delete the **variable** `{name}` (Environment variables, the bin icon next to it)."
        )
    if not key_id and not api_token and not analytics and not as_variables:
        check.screen = ScreenView(enabled=enabled)
        return check

    if bool(key_id) != bool(api_token) and not as_variables:
        missing = "DOME_TURN_API_TOKEN" if key_id else TURN_KEY_ID
        check.problems.append(
            f"Screen view's relay server is half set up in the {cfg.name} environment: `{missing}` is missing. DoMe "
            f"takes both `{TURN_KEY_ID}` and `DOME_TURN_API_TOKEN` or neither (with neither, Screen view works only "
            "where a direct connection exists)."
        )
        (check.missing_secrets if missing == "DOME_TURN_API_TOKEN" else check.missing_vars).append(missing)
        check.next_steps.append(
            f"Both come from the Cloudflare dashboard, **Realtime** > **TURN Server** > the key for {cfg.name}: the "
            f"key id is the **variable** `{TURN_KEY_ID}`, the token is the **secret** `DOME_TURN_API_TOKEN` "
            "(docs/design/screen-view.md section 20, step 1). To keep the relay server off instead, delete every "
            f"`DOME_TURN_` secret and variable from the {cfg.name} environment."
        )
    if analytics and not (key_id and api_token) and not as_variables:
        check.problems.append(
            "`DOME_TURN_ANALYTICS_TOKEN` is set, but the relay server key is not: the usage reconciliation only makes "
            f"sense with `{TURN_KEY_ID}` and `DOME_TURN_API_TOKEN`. Add them, or delete the analytics token."
        )
    if key_id and not _TURN_KEY_ID.match(key_id):
        check.problems.append(
            f"The variable `{TURN_KEY_ID}` is not a TURN key id (letters, digits, `-` and `_` only, at least 8 "
            "characters, no spaces). Copy it again from Cloudflare > **Realtime** > **TURN Server**."
        )
    if api_token and (not importable(api_token) or len(api_token) < 16):
        check.problems.append(
            "`DOME_TURN_API_TOKEN` contains a space, a quote or #, or is too short to be a whole token. Copy it again "
            "with Cloudflare's copy button."
        )
    if analytics and (not importable(analytics) or len(analytics) < 16):
        check.problems.append(
            "`DOME_TURN_ANALYTICS_TOKEN` contains a space, a quote or #, or is too short to be a whole token. Copy it "
            "again with Cloudflare's copy button."
        )
    if not check.problems and not check.missing_secrets and not check.missing_vars and key_id and api_token:
        check.screen = ScreenView(enabled, "on", key_id, api_token, analytics)
    if check.problems or check.missing_secrets or check.missing_vars:
        check.next_steps.append(
            f"The Screen view values are at {page} > **{cfg.name}** (docs/design/screen-view.md section 20)."
        )
    return check


def screen_view_step_text(screen: ScreenView) -> str:
    switch = f"**{SCREEN_VIEW_TEXT['on' if screen.enabled else 'off']}**"
    if not screen.enabled:
        switch += f" (the variable `{SCREEN_VIEW_SWITCH}` is not `true` in this GitHub Environment)"
    if screen.turn_on:
        relay = f"{TURN_TEXT['on']} (`{TURN_KEY_ID}` passed to the app, `DOME_TURN_API_TOKEN` stored in Fly; usage " + (
            "reconciliation on: `DOME_TURN_ANALYTICS_TOKEN` stored in Fly)"
            if screen.reconciliation
            else "reconciliation off: no `DOME_TURN_ANALYTICS_TOKEN`, the app's own bound carries the guard)"
        )
    else:
        relay = TURN_TEXT["off"]
    return f"{switch}; {relay}"


def screen_view_of(health: Health) -> tuple[str, str]:
    """What the running app says (GET /healthz): (`screen_view`, `turn`) as cloud-api routes/health.py reports them,
    or ("unknown", "unknown") for an older build without the fields."""
    body = health.body if health.reachable and health.status_code == 200 else {}
    switch, turn = body.get("screen_view"), body.get("turn")
    if switch not in ("on", "off") or turn not in ("ok", "unconfigured", "error"):
        return "unknown", "unknown"
    return str(switch), str(turn)


def screen_view_status_text(health: Health) -> str:
    switch, turn = screen_view_of(health)
    if switch == "unknown":
        return "unknown (/healthz has no Screen view fields: an older build)"
    text = f"**{SCREEN_VIEW_TEXT[switch]}**"
    if turn == "ok":
        text += f"; {TURN_TEXT['on']} configured"
    elif turn == "error":
        text += f"; {TURN_TEXT['on']} configured but not answering (the app mints no relay credentials for a while)"
    else:
        text += f"; {TURN_TEXT['off']}"
    return text


@dataclass
class Inputs:
    cfg: EnvConfig
    app: str
    sha: str
    org: str
    issuer: str
    client_id: str
    client_secret: str
    # The GitHub Environment secret DATABASE_URL (env DEPLOY_DATABASE_URL): required for an external database
    # (staging's Neon). For Fly Managed Postgres (production) only a fallback when Fly will not list or attach the
    # cluster, and never a Neon address.
    database_url: str
    create_database: bool
    support_url: str
    support_expectation: str
    billing: Billing = field(default_factory=Billing)
    screen: ScreenView = field(default_factory=ScreenView)


def url_password(url: str) -> str:
    try:
        return urlsplit(url).password or ""
    except ValueError:
        return ""


def external_database_problem(url: str) -> str:
    """Why ``url`` is not a usable DIRECT, TLS-only PostgreSQL connection string ("" when it is). Never quotes it.

    Neon's pooled host ("-pooler") is refused: it is PgBouncer in transaction mode, where psycopg's server-side
    prepared statements and other session state are not guaranteed (deploy/README.md "Staging database")."""
    name = "The Environment secret `DATABASE_URL`"
    if not url.startswith(("postgresql://", "postgres://")):
        return (
            f"{name} is not a database connection string. It must be the address itself, starting with "
            "`postgresql://` (not a `psql` command and not a line such as `DATABASE_URL=...`)."
        )
    if not importable(url):
        return f"{name} contains a space, a quote or #. Copy it again with Neon's copy button."
    parts = urlsplit(url)
    try:
        parts.port  # noqa: B018 - raises ValueError for a port that is not a number
    except ValueError:
        return f"{name} has an unreadable port number. Copy it again with Neon's copy button."
    if not (parts.hostname and parts.username and parts.password and parts.path.strip("/")):
        return (
            f"{name} is missing its user name, password, host or database name. Copy the whole connection string "
            "again with Neon's copy button."
        )
    if set(parts.password) == {"*"}:
        return (
            f"{name} has stars where the password should be (the hidden form Neon shows on screen). Copy it again "
            "with Neon's copy button, which copies the real password."
        )
    if "-pooler" in parts.hostname:
        return (
            f"{name} is Neon's pooled address (its host name contains `-pooler`). DoMe needs the direct one: in "
            "Neon's **Connect** window turn **Connection pooling** off, then copy it again."
        )
    query = parse_qs(parts.query)
    sslmode = query.get("sslmode", [""])[-1]
    if sslmode not in ("require", "verify-ca", "verify-full"):
        return (
            f"{name} does not include `sslmode=require`, so the connection might not be encrypted. Copy it again "
            "exactly as Neon shows it."
        )
    # sslmode=require encrypts but does not check the server's certificate; SCRAM channel binding (enforced by the
    # bundled libpq, cloud-api/tests/test_database_url.py) stops a relayed login. Only verify-full may go without it.
    if sslmode != "verify-full" and query.get("channel_binding", [""])[-1] != "require":
        return (
            f"{name} does not include `channel_binding=require`. Copy it again exactly as Neon shows it (it ends with "
            "`?sslmode=require&channel_binding=require`)."
        )
    return ""


def external_database_steps(env: Mapping[str, str], cfg: EnvConfig) -> list[str]:
    """Where the founder copies the external (Neon) database address and where it goes."""
    return [
        f"In Neon ({NEON_CONSOLE}), open the project **{cfg.external_database_project}** and click **Connect**. Turn "
        "**Connection pooling** off, then copy the connection string with the copy button. It starts with "
        "`postgresql://`, ends with `?sslmode=require&channel_binding=require`, and its host name has no `-pooler`. "
        "Do not paste it anywhere else.",
        f"Open {environments_page(env)}, choose **{cfg.name}**, and under **Environment secrets** add (or update) "
        "`DATABASE_URL` with that value (docs/LAUNCH_SETUP.md section 6).",
    ]


def check_ref(env: Mapping[str, str]) -> None:
    default = env.get("DEFAULT_BRANCH", "").strip()
    ref = env.get("GITHUB_REF", "")
    if not default or ref != f"refs/heads/{default}":
        raise Stop(
            "you",
            f"Deploy only runs from the default branch ({default or 'unknown'}); this run was started from "
            f"{ref or 'an unknown ref'}. Nothing was created or changed.",
            next_steps=["Run Deploy again and keep the branch selector on the default branch."],
        )


def check_no_repository_secrets(env: Mapping[str, str]) -> None:
    """GitHub hands a REPOSITORY secret to any workflow run without approval, and the deploy job silently uses
    one when the environment has no secret of that name. REPO_LEVEL_SECRETS holds the names (never values) of
    deploy secrets the gate job (no environment) can see, i.e. repository or organization secrets."""
    found = [n for n in DEPLOY_SECRET_NAMES if n in env.get("REPO_LEVEL_SECRETS", "").split()]
    if not found:
        return
    listed = ", ".join(f"`{n}`" for n in found)
    steps = [
        f"Open {secrets_page(env)} and, under **Repository secrets**, delete {listed} (the bin icon next to each). "
        "Keep them only as **Environment secrets** (Settings > Environments > staging / production).",
    ]
    if "FLY_API_TOKEN" in found:
        steps.append(
            "On the Fly Tokens page, revoke the token you stored there, create a new **organization** token, and "
            "paste it only as the Environment secret `FLY_API_TOKEN` (docs/LAUNCH_SETUP.md section 4)."
        )
    if "DOME_OIDC_CLIENT_SECRET" in found:
        steps.append(
            "In Auth0 (application > **Credentials** > Rotate), make a new Client Secret and paste it only as the "
            "Environment secret `DOME_OIDC_CLIENT_SECRET`."
        )
    if "DATABASE_URL" in found:
        steps.append(
            "Change the database password, because every run could read it: for staging, in Neon open the project > "
            "**Branches** > `production` (Neon's name for its main branch, marked Default; it is still staging's "
            "database) > **Roles & Databases**, choose **Reset password** in the role's menu, and paste the new "
            "connection string only as the staging Environment secret `DATABASE_URL` (docs/LAUNCH_SETUP.md section "
            "6). For production, tell an engineer."
        )
    if "DOME_STRIPE_SECRET_KEY" in found:
        steps.append(
            "In Stripe > **Developers** > **API keys**, choose **Roll key** in the secret key's menu, and paste the "
            "new key only as the Environment secret `DOME_STRIPE_SECRET_KEY` of the environment it belongs to (a test "
            "key `sk_test_...` for staging; production's key for production; docs/LAUNCH_SETUP.md section 11A)."
        )
    if "DOME_STRIPE_WEBHOOK_SECRET" in found:
        steps.append(
            "In Stripe > **Developers** > **Webhooks**, open the endpoint, roll its **Signing secret**, and paste the "
            "new one only as the Environment secret `DOME_STRIPE_WEBHOOK_SECRET` of that endpoint's environment."
        )
    for name in TURN_SECRET_NAMES:
        if name in found:
            steps.append(turn_token_steps(name))
    steps.append("Run Deploy again.")
    raise Stop(
        "you",
        f"{listed} exist as repository secrets. GitHub gives a repository secret to every workflow run without "
        "asking you, so the Deploy button refuses to run while one exists. Nothing was created or changed.",
        next_steps=steps,
    )


def check_environment_protection(env: Mapping[str, str], name: str, default_branch: str) -> str:
    """Raises Stop unless the GitHub Environment ``name`` exists with a required reviewer (self-review allowed),
    no administrator bypass, and exactly one deployment branch rule: the default branch. Returns a summary line.
    Without these, a run nobody approved could receive that environment's Fly token."""
    repo = env.get("GITHUB_REPOSITORY", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise Stop("engineer", "GITHUB_REPOSITORY is missing or malformed.")
    page = environments_page(env)
    base = f"/repos/{repo}/environments/{name}"
    setup = f"Set up **{name}** exactly as docs/LAUNCH_SETUP.md section 3 says, at {page}, then run Deploy again."

    def get(path: str) -> HttpResult:
        try:
            return github_get(env, path)
        except HttpFailure as exc:
            raise Stop(
                "failed",
                f"GitHub's API did not answer ({exc.kind}) when this run checked the environment {name}. "
                "Nothing was created or changed.",
                next_steps=["Run Deploy again in a few minutes."],
            ) from None

    result = get(base)
    if result.status == 404:
        raise Stop(
            "you",
            f"The GitHub Environment **{name}** does not exist yet. Both environments must be set up before the "
            "first deploy. Nothing was created or changed.",
            next_steps=[setup],
        )
    doc = result.json() if result.status == 200 else None
    if not isinstance(doc, dict):
        raise Stop("engineer", f"GitHub answered HTTP {result.status} when asked about the environment {name}.")

    problems: list[str] = []
    rules = doc.get("protection_rules")
    rules = rules if isinstance(rules, list) else []
    reviewer_rules = [r for r in rules if isinstance(r, dict) and r.get("type") == "required_reviewers"]
    if not any(isinstance(r.get("reviewers"), list) and r["reviewers"] for r in reviewer_rules):
        problems.append("**Required reviewers** is not ticked, or names nobody (it should name you, Heshh999)")
    elif any(r.get("prevent_self_review") is True for r in reviewer_rules):
        problems.append("**Prevent self-review** is ticked (it locks you out: you are the only reviewer)")
    if doc.get("can_admins_bypass", False) is not False:
        problems.append("**Allow administrators to bypass configured protection rules** is ticked")
    policy = doc.get("deployment_branch_policy")
    if not isinstance(policy, dict) or policy.get("custom_branch_policies") is not True:
        problems.append("**Deployment branches and tags** is not set to **Selected branches and tags**")
    else:
        branches = get(base + "/deployment-branch-policies?per_page=100")
        data = branches.json() if branches.status == 200 else None
        items = data.get("branch_policies") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise Stop("engineer", f"GitHub answered HTTP {branches.status} for the branch rules of {name}.")
        found = [(p.get("type", "branch"), p.get("name")) for p in items if isinstance(p, dict)]
        if found != [("branch", default_branch)] or len(items) != 1:
            shown = ", ".join(f"`{n}`" for _, n in found) or "none"
            problems.append(
                f"the deployment branch rules must be exactly one branch rule named `{default_branch}` (found: {shown})"
            )
    if problems:
        raise Stop(
            "you",
            f"The GitHub Environment **{name}** is not protected yet, so a run nobody approved could receive its Fly "
            "token. Nothing was created or changed." + "".join(f"\n\n- {p}" for p in problems),
            next_steps=[setup],
        )
    return f"protected: required reviewer, branch `{default_branch}` only, no administrator bypass"


def read_environment_choice(env: Mapping[str, str], envs: Mapping[str, EnvConfig]) -> EnvConfig:
    name = env.get("DEPLOY_ENVIRONMENT", "").strip()
    if name not in envs:
        raise Stop("engineer", "DEPLOY_ENVIRONMENT must be staging or production.")
    return envs[name]


def read_sha(env: Mapping[str, str]) -> str:
    sha = env.get("GITHUB_SHA", "").strip().lower()
    if not _HEX40.match(sha):
        raise Stop("engineer", "GITHUB_SHA is missing or not a 40-character commit id.")
    return sha


def read_inputs(env: Mapping[str, str], cfg: EnvConfig, book: SecretBook, summary: Summary) -> Inputs:
    page = environments_page(env)
    problems: list[str] = []
    missing_secrets: list[str] = []
    missing_vars: list[str] = []

    token = env.get("FLY_API_TOKEN", "").strip()
    if not token:
        missing_secrets.append("FLY_API_TOKEN")
    else:
        book.add_fly_token(token)
        body = token[len("FlyV1 ") :] if token.startswith("FlyV1 ") else token
        if body.startswith("fo1_"):
            problems.append(
                "FLY_API_TOKEN is a personal sign-in token (it starts with fo1_). Create an organization token on "
                "the Fly dashboard's Tokens page instead and paste it over the old one."
            )
        elif not (token.startswith("FlyV1 ") or body.startswith(("fm1_", "fm2_"))):
            summary.notes.append(
                "FLY_API_TOKEN does not start with 'FlyV1 ' as Fly dashboard tokens usually do. If Fly refuses "
                "it below, copy the whole token again, including 'FlyV1 ' and the space."
            )

    client_secret = env.get("DOME_OIDC_CLIENT_SECRET", "").strip()
    if not client_secret:
        missing_secrets.append("DOME_OIDC_CLIENT_SECRET")
    else:
        book.add(client_secret, mask=False)
        if not importable(client_secret):
            problems.append(
                "DOME_OIDC_CLIENT_SECRET contains a space, a quote or #; copy it again from Auth0 (Settings > "
                "Client Secret, using the copy button) and paste it over the old one."
            )

    database_url = env.get("DEPLOY_DATABASE_URL", "").strip()
    database_problem = ""
    neon_in_fly_managed = False
    if database_url:
        book.add(database_url, mask=False)  # a stored GitHub secret: GitHub masks the whole value itself
        password = url_password(database_url)
        if password:
            book.add(password, mask=True)  # ... but not the password on its own
    if cfg.external_database:
        if not database_url:  # its own steps below say where to copy it from, so not in missing_secrets
            problems.append(
                f"{cfg.title} keeps its data in a free Neon database, and its address, the Environment secret "
                "`DATABASE_URL`, is missing."
            )
        else:
            database_problem = external_database_problem(database_url)
            if database_problem:
                problems.append(database_problem)
    elif database_url and not (database_url.startswith(("postgres://", "postgresql://")) and importable(database_url)):
        problems.append(
            "The DATABASE_URL secret does not look like a database address (it should start with "
            "postgresql:// and have no spaces). Copy the pooled URL again from Fly > Managed Postgres > "
            "your cluster > Connect."
        )
    elif database_url and is_neon_host(database_host(database_url)):
        # Fly Managed Postgres only: a Neon address here is staging's free database (6-hour restore window, monthly
        # compute-hours shared with staging), never production's.
        neon_in_fly_managed = True
        problems.append(
            f"The {cfg.name} Environment secret `DATABASE_URL` is a Neon address (staging's database). {cfg.title} "
            f"must not use it: delete `DATABASE_URL` from the {cfg.name} environment (docs/LAUNCH_SETUP.md section "
            "11) and run Deploy again."
        )

    issuer = ""
    raw_issuer = env.get("DOME_OIDC_ISSUER", "")
    if not raw_issuer.strip():
        missing_vars.append("DOME_OIDC_ISSUER")
    else:
        try:
            issuer = normalize_issuer(raw_issuer)
        except ValueError as exc:
            problems.append(f"The variable DOME_OIDC_ISSUER is not usable: {exc}. Paste the Auth0 Domain.")

    client_id = env.get("DOME_OIDC_CLIENT_ID", "").strip()
    if not client_id:
        missing_vars.append("DOME_OIDC_CLIENT_ID")
    elif (client_secret and client_id == client_secret) or len(client_id) > 48:
        problems.append(
            "The variable DOME_OIDC_CLIENT_ID looks like the Client Secret, not the Client ID. Variables are "
            "visible in this public log, so treat that secret as exposed: in Auth0 open the application's "
            "Credentials tab and rotate the secret, paste the NEW secret under Environment secrets, and put "
            "the Client ID (the shorter value) under Environment variables."
        )
    elif not _CLIENT_ID.match(client_id):
        problems.append("The variable DOME_OIDC_CLIENT_ID has unexpected characters; copy the Auth0 Client ID again.")

    org = env.get("FLY_ORG", "").strip() or "personal"
    if not _ORG_SLUG.match(org):
        problems.append("The variable FLY_ORG is not a Fly organization slug (lower-case letters, digits, dashes).")

    support_url = env.get("VITE_DOME_SUPPORT_URL", "").strip()
    if support_url and (not support_url.startswith(("mailto:", "https://")) or any(c.isspace() for c in support_url)):
        problems.append("The variable VITE_DOME_SUPPORT_URL must start with mailto: or https:// and have no spaces.")
    expectation = env.get("DOME_SUPPORT_RESPONSE_EXPECTATION", "").strip()
    if len(expectation) > 200 or "\n" in expectation:
        problems.append("The variable DOME_SUPPORT_RESPONSE_EXPECTATION must be one line of at most 200 characters.")

    billing = read_billing(env, cfg, book)
    problems += billing.problems
    missing_secrets += billing.missing_secrets
    missing_vars += billing.missing_vars
    screen = read_screen_view(env, cfg, book)
    problems += screen.problems
    missing_secrets += screen.missing_secrets
    missing_vars += screen.missing_vars

    next_steps: list[str] = []
    if missing_secrets:
        next_steps.append(
            f"Open {page}, choose **{cfg.name}**, and under **Environment secrets** add: "
            + ", ".join(f"`{n}`" for n in missing_secrets)
            + ". They must be Environment secrets of this environment, not repository secrets (the Deploy "
            "button refuses to run while a repository copy of one exists)."
        )
    if missing_vars:
        next_steps.append(
            f"Open {page}, choose **{cfg.name}**, and under **Environment variables** add: "
            + ", ".join(f"`{n}`" for n in missing_vars)
            + "."
        )
    if cfg.external_database and (not database_url or database_problem):
        next_steps += external_database_steps(env, cfg)
    if neon_in_fly_managed:
        next_steps.append(
            f"Open {page}, choose **{cfg.name}**, and under **Environment secrets** delete `DATABASE_URL` (the bin "
            f"icon next to it). {cfg.title} keeps its data in Fly Managed Postgres: on its first deploy, tick **create "
            "the database**. Never paste the Neon address into this environment."
        )
    next_steps += billing.next_steps
    next_steps += screen.next_steps
    if missing_secrets or missing_vars or problems:
        next_steps.append("Run Deploy again. Nothing was created or changed by this run.")
        raise Stop(
            "you",
            "Some values this deploy needs are missing or not usable." + "".join(f"\n\n- {p}" for p in problems),
            next_steps=next_steps,
        )

    return Inputs(
        cfg=cfg,
        app=app_name_for(cfg, env),
        sha=read_sha(env),
        org=org,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        database_url=database_url,
        create_database=env.get("CREATE_DATABASE", "").strip().lower() == "true",
        support_url=support_url,
        support_expectation=expectation,
        billing=billing.billing,
        screen=screen.screen,
    )


# ======================================================================================================
# Checks that need no secrets: OIDC discovery, healthz, DNS/TLS
# ======================================================================================================


def check_discovery(issuer: str) -> str:
    """Returns the provider's authorization_endpoint (the sign-in redirect check compares with it)."""
    url = issuer + "/.well-known/openid-configuration"
    try:
        result = http_get(url)
    except HttpFailure as exc:
        raise Stop(
            "you",
            f"The sign-in provider at {issuer} could not be reached ({exc.kind}). Check that the variable "
            "DOME_OIDC_ISSUER is the Auth0 Domain exactly as Auth0 shows it.",
        ) from None
    doc = result.json() if result.status == 200 else None
    if not isinstance(doc, dict):
        raise Stop(
            "you",
            f"{url} did not answer with a sign-in configuration (HTTP {result.status}). Check that the variable "
            "DOME_OIDC_ISSUER is the Auth0 Domain exactly as Auth0 shows it.",
        )
    published = doc.get("issuer")
    if not isinstance(published, str) or published.rstrip("/") != issuer:
        raise Stop("you", f"The sign-in provider at {issuer} names a different issuer; check DOME_OIDC_ISSUER.")
    methods = doc.get("token_endpoint_auth_methods_supported")
    if isinstance(methods, list) and "client_secret_post" not in methods:
        raise Stop(
            "engineer",
            "The sign-in provider does not accept client_secret_post, the method DoMe uses "
            "(cloud-api/dome_api/auth/oidc.py).",
        )
    endpoint = doc.get("authorization_endpoint")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
        raise Stop("engineer", f"The sign-in provider at {issuer} publishes no https authorization_endpoint.")
    return endpoint


@dataclass
class Health:
    reachable: bool
    status_code: int = 0
    body: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def commit(self) -> str | None:
        value = self.body.get("commit")
        return value if isinstance(value, str) else None

    def healthy_for(self, sha: str) -> bool:
        return (
            self.status_code == 200
            and self.body.get("status") == "ok"
            and self.body.get("database") == "ok"
            and self.commit == sha
            and self.body.get("forwarded_client") == "ok"
        )


def get_health(base: str) -> Health:
    try:
        result = http_get(base + "/healthz")
    except HttpFailure as exc:
        return Health(False, error=exc.kind)
    body = result.json()
    return Health(True, result.status, body if isinstance(body, dict) else {}, headers=result.headers)


def wake_health(base: str) -> Health:
    """/healthz, asked again (up to WAKE_ATTEMPTS) while the app does not answer or answers 502-504: a staging machine
    that stopped while idle starts on this first request, and its Neon database wakes with it. A name that does not
    resolve is not retried (that app does not exist)."""
    health = get_health(base)
    attempts = 1
    while attempts < WAKE_ATTEMPTS and (
        (not health.reachable and health.error != "dns") or health.status_code in (502, 503, 504)
    ):
        sleep(SMOKE_INTERVAL)
        health = get_health(base)
        attempts += 1
    return health


def wait_for_health(base: str, sha: str) -> Health:
    """Poll until healthy with this commit, or until SMOKE_WAIT (a rolling deploy can answer with the old one)."""
    deadline = monotonic() + SMOKE_WAIT
    health = get_health(base)
    while not health.healthy_for(sha) and monotonic() < deadline:
        sleep(SMOKE_INTERVAL)
        health = get_health(base)
    return health


def describe_health(health: Health, sha: str) -> str:
    if not health.reachable:
        return f"not reachable ({health.error})"
    if health.status_code != 200:
        return f"HTTP {health.status_code}"
    commit = health.commit
    parts = [
        f"status {health.body.get('status')}",
        f"database {health.body.get('database')}",
        f"commit {commit[:12] if commit else 'unknown'}" + (" (this commit)" if commit == sha else ""),
        f"client address check {health.body.get('forwarded_client')}",
    ]
    return ", ".join(parts)


def external_database_down(cfg: EnvConfig, health: Health) -> bool:
    """True when an environment on an external database (staging's Neon) answers, but without its database: /healthz
    answers 503 (cloud-api routes/health.py) or reports a database that is not ok. With Neon's free plan the usual
    cause is a month's compute-hours used up, which suspends the database until the next month."""
    return (
        cfg.external_database
        and health.reachable
        and (health.status_code == 503 or health.body.get("database") not in (None, "ok"))
    )


def external_database_down_step(cfg: EnvConfig) -> str:
    return (
        f"{cfg.title}'s database at Neon is not answering. In {NEON_CONSOLE} check whether this month's free "
        "compute-hours are used up (docs/LAUNCH_SETUP.md section 6)."
    )


def production_gate(env: Mapping[str, str], envs: Mapping[str, EnvConfig], sha: str, summary: Summary) -> None:
    staging = envs["staging"]
    staging_app = app_name_for(staging, env)
    base = f"https://{staging_app}.fly.dev"
    health = wake_health(base)  # staging may be asleep
    summary.step("Staging runs this commit", describe_health(health, sha))
    if health.healthy_for(sha):
        return
    if health.reachable and health.status_code == 200 and health.body.get("forwarded_client") not in (None, "ok"):
        raise Stop(
            "engineer",
            "Staging runs, but its client-address self-check is not ok, so per-person limits there would treat "
            "everyone as one visitor. Production is not deployed until an engineer fixes this "
            "(deploy/README.md 'Client addresses').",
        )
    steps = [
        "Run Deploy with environment **staging** for this same commit and wait for it to finish green.",
        "Then run Deploy with environment **production** again.",
    ]
    if external_database_down(staging, health):
        # A staging deploy cannot finish either while Neon is suspended (its migrations need the database).
        steps = [
            external_database_down_step(staging)
            + " If they are, production keeps running, but no new version reaches production until staging works "
            "again: wait for the next month, or ask an engineer about Neon's paid plan for the rest of this month.",
            *steps,
        ]
    raise Stop(
        "you",
        f"Production only receives a commit that staging already runs and that is healthy there. Staging "
        f"({base}) is {describe_health(health, sha)}; this run is commit {sha[:12]}.",
        next_steps=steps,
    )


@dataclass
class HostReport:
    state: str  # live | no_dns | proxied | elsewhere | tls_pending | unreachable | wrong_commit | unhealthy
    addresses: list[str] = field(default_factory=list)
    detail: str = ""


def check_custom_host(hostname: str, app_addresses: set[str], sha: str | None, *, production: bool) -> HostReport:
    """``sha`` None: any commit is accepted (status runs compare with what fly.dev serves)."""
    try:
        addresses = resolve(hostname)
    except OSError:
        return HostReport("no_dns")
    parsed = [ipaddress.ip_address(a) for a in addresses]
    if any(any(ip in net for net in CLOUDFLARE_NETWORKS) for ip in parsed):
        return HostReport("proxied", addresses)
    if app_addresses and not set(addresses) <= app_addresses:
        return HostReport("elsewhere", addresses)
    try:
        result = http_get(f"https://{hostname}/healthz")
    except HttpFailure as exc:
        return HostReport("tls_pending" if exc.kind == "tls" else "unreachable", addresses, exc.kind)
    body = result.json()
    body = body if isinstance(body, dict) else {}
    if result.status != 200 or body.get("status") != "ok":
        return HostReport("unhealthy", addresses, f"HTTP {result.status}")
    if sha is not None and body.get("commit") != sha:
        return HostReport("wrong_commit", addresses, str(body.get("commit"))[:12])
    if production:
        if "x-robots-tag" in result.headers or "strict-transport-security" not in result.headers:
            return HostReport("unhealthy", addresses, "production headers are wrong (search-engine or HSTS header)")
    return HostReport("live", addresses)


_VALUE_LABEL = {"CNAME": "Target", "A": "IPv4 address", "AAAA": "IPv6 address"}


def host_next_steps(cfg: EnvConfig, report: HostReport, records: list[tuple[str, str, str]]) -> list[str]:
    zone_page = f"Cloudflare > **{cfg.dns_zone}** > DNS > Records"
    record_text = "; ".join(f"Type **{t}**, Name **{n}**, {_VALUE_LABEL[t]} **{v}**" for t, n, v in records)
    grey = "Proxy status **off** (grey cloud, 'DNS only')"
    if report.state == "no_dns":
        if not records:
            return [f"Add the DNS record for {cfg.hostname} that the next successful Deploy run lists."]
        return [
            f"In {zone_page}, first delete any existing A, AAAA or CNAME record named {dns_name_text(cfg)}.",
            f"Then add: {record_text}. {grey} on each. Save.",
            "About 10 minutes later, run Deploy with action **status**.",
        ]
    if report.state == "proxied":
        return [
            f"In {zone_page}, the record(s) named {dns_name_text(cfg)} are proxied (orange cloud). Click each and "
            "switch Proxy status to **DNS only** (grey cloud): Fly cannot issue the certificate through "
            "Cloudflare's proxy.",
            "About 10 minutes later, run Deploy with action **status**.",
        ]
    if report.state == "elsewhere":
        return [
            f"{cfg.hostname} points at {', '.join(report.addresses)}, which is not this app. In {zone_page}, replace "
            f"the record(s) named {dns_name_text(cfg)} with: {record_text or 'the records a Deploy run lists'}. "
            f"{grey}.",
        ]
    if report.state == "tls_pending":
        return [
            "DNS is right; Fly has not finished the certificate yet. Wait 10 minutes, then run Deploy with action "
            "**status**. If it still says this after an hour, ask an engineer (Fly allows only 5 failed "
            "certificate attempts per hour)."
        ]
    if report.state == "wrong_commit":
        return [
            f"{cfg.hostname} answers with a different build ({report.detail}). Run Deploy with action **status** "
            "again in a few minutes."
        ]
    if report.state in ("unreachable", "unhealthy"):
        return [f"{cfg.hostname} resolves correctly but did not answer well ({report.detail}). Ask an engineer."]
    return []


def dns_name_text(cfg: EnvConfig) -> str:
    """How the record's Name appears: Cloudflare's list shows ``@`` as the zone name itself."""
    if cfg.dns_name == "@":
        return f"**@** (shown as **{cfg.dns_zone}** in the list)"
    return f"**{cfg.dns_name}**"


def host_result_text(cfg: EnvConfig, report: HostReport) -> str:
    return {
        "live": f"live at https://{cfg.hostname} (this commit)",
        "no_dns": "no DNS record yet",
        "proxied": "DNS record is proxied by Cloudflare (must be DNS only)",
        "elsewhere": "DNS points somewhere else",
        "tls_pending": "DNS correct, certificate not issued yet",
        "wrong_commit": "answers with a different build",
        "unreachable": "resolves, but no answer",
        "unhealthy": f"resolves, but unhealthy ({report.detail})",
    }.get(report.state, report.state)


PRODUCTION_NOT_READY = [
    "Restore rehearsal from a Managed Postgres backup (docs/OPERATIONS.md) - not done.",
    "Failed-deploy recovery rehearsal - not done.",
    "External uptime monitor on https://domeremote.com/healthz with alerts - not set up.",
    "Backup retention recorded in docs/OPERATIONS.md - not done.",
    "Support contact (variable VITE_DOME_SUPPORT_URL) - check it is set.",
    "Auth0 production tenant: own email provider for sign-up/reset emails - not checked by this run.",
    "Auth0 production: Google sign-in either off or using your own Google keys - not checked by this run.",
    "Sign-in itself is never tested by this workflow: sign up and sign in by hand.",
    "Branch protection with required review on the default branch (docs/SECURITY.md section 6) - not set up.",
]


# ======================================================================================================
# Stripe itself: the account, the five prices, the webhook endpoint and the customer portal (deploy with billing on,
# before any flyctl call; GET only)
# ======================================================================================================


class StripeDown(Exception):  # noqa: N818
    """Stripe gave no usable answer: no connection, a timeout, 429, 5xx, a redirect, or a body that is not a JSON
    object. A deploy never stops for this; it goes on without the check and the summary says so."""


class _KeyRefused(Exception):  # noqa: N818
    """Stripe answered 401: it does not accept the secret key."""


@dataclass
class StripeCheck:
    """What Stripe itself says about one environment's Stripe values. ``problems`` stop the deploy (with
    ``next_steps``); ``warnings`` become notes; ``row`` is the summary row "Stripe account"."""

    row: str = ""
    problems: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def step(self, text: str) -> None:
        if text not in self.next_steps:
            self.next_steps.append(text)


def stripe_get(key: str, path: str, query: Sequence[tuple[str, str]] = ()) -> HttpResult:
    """GET from Stripe's REST API. The key travels only in the Authorization header, never in the URL, and redirects
    are not followed, so it never reaches another host. Raises HttpFailure like http_get."""
    url = STRIPE_API + path + ("?" + urlencode(query) if query else "")
    headers = {"Authorization": f"Bearer {key}", "Stripe-Version": STRIPE_API_VERSION, "User-Agent": "dome-deploy"}
    return http_get(url, timeout=STRIPE_TIMEOUT, headers=headers, follow_redirects=False)


def stripe_text(book: SecretBook, text: str) -> str:
    """Stripe's own words, fit for the log and the summary: one line, every known secret and anything shaped like a
    Stripe key, signing secret or Authorization header hidden, no table pipes, at most 300 characters."""
    text = _STRIPE_KEYISH.sub("[key hidden]", book.redact(" ".join(text.split())))
    return text.replace("|", "/")[:300]


def stripe_read(
    key: str, book: SecretBook, path: str, query: Sequence[tuple[str, str]] = ()
) -> tuple[int, dict[str, Any], str]:
    """(HTTP status, the JSON object, Stripe's error message through stripe_text). Raises StripeDown when there is no
    usable answer."""
    try:
        result = stripe_get(key, path, query)
    except HttpFailure as exc:
        raise StripeDown(f"no answer: {exc.kind}") from None
    status = result.status
    if status < 200 or 300 <= status < 400 or status == 429 or status >= 500:
        raise StripeDown(f"HTTP {status}")
    body = result.json()
    if not isinstance(body, dict):
        raise StripeDown(f"HTTP {status} with an answer that is not Stripe's")
    error = body.get("error")
    raw = error.get("message") if isinstance(error, dict) and status != 200 else None
    return status, body, stripe_text(book, raw) if isinstance(raw, str) else ""


def display_prices() -> tuple[dict[str, int], str]:
    """(variable -> the amount DoMe shows for that price, in cents; the currency, lower-case) from plans.json's
    pricing_defaults, the source of the pricing page, the Billing page and GET /v1/plans."""
    try:
        pricing = json.loads(PLANS_FILE.read_text(encoding="utf-8"))["pricing_defaults"]
        amounts: dict[str, int] = {}
        for name, (_, where) in STRIPE_PRICE_TERMS.items():
            value: Any = pricing
            for part in where:
                value = value[part]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(name)
            amounts[name] = value
        currency = pricing["currency"]
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Za-z]{3}", currency):
            raise ValueError("currency")
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop(
            "engineer",
            "shared/protocol/plans.json does not hold DoMe's prices (pricing_defaults) in a shape the Deploy button "
            "can compare with Stripe. Nothing was created or changed.",
        ) from None
    return amounts, currency.lower()


def _plain(book: SecretBook, value: object, limit: int = 80) -> str:
    """A name Stripe returned, safe inside a summary table: one line, redacted, no Markdown that could break it."""
    return re.sub(r"[|*`<>\[\]]", "", book.redact(" ".join(str(value).split())))[:limit]


def _period(count: object, interval: object) -> str:
    if not isinstance(interval, str) or not isinstance(count, int):
        return "an unknown period"
    return interval if count == 1 else f"{count} {interval}s"


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def price_problems(
    name: str, price: Mapping[str, Any], *, live: bool, cents: int, currency: str
) -> list[tuple[str, str]]:
    """What is wrong with one Stripe price for the variable ``name``, as (clause, fix) pairs; fix is "unarchive",
    "new_price", "amount" or "same_account". Empty when the price is what DoMe shows and sells."""
    interval = STRIPE_PRICE_TERMS[name][0]
    out: list[tuple[str, str]] = []
    if price.get("active") is not True:
        out.append(("is archived in Stripe", "unarchive"))
    product = price.get("product")
    if isinstance(product, dict):  # expanded (expand[]=product); a product or a deleted_product
        if product.get("deleted") is True:
            out.append(("its product was deleted in Stripe", "new_price"))
        elif product.get("active") is not True:
            out.append(("its product is archived in Stripe", "unarchive"))
    if price.get("livemode") is not live:
        theirs = "Live" if price.get("livemode") is True else "Test"
        mine = "Live" if live else "Test"
        out.append((f"is a {theirs} mode price, but the key is a {mine} mode key", "same_account"))
    found_currency = str(price.get("currency") or "").lower()
    if found_currency != currency:
        out.append((f"is priced in {found_currency.upper() or 'no currency'}, not {currency.upper()}", "new_price"))
    kind = price.get("type")
    recurring = price.get("recurring")
    recurring = recurring if isinstance(recurring, dict) else {}
    if not interval:
        if kind != "one_time":
            out.append(("is a recurring (subscription) price; the AI top-up must be a **one-time** price", "new_price"))
    elif kind != "recurring":
        out.append((f"is a one-time price; it must be **recurring**, billed every {interval}", "new_price"))
    else:
        every, count = recurring.get("interval"), recurring.get("interval_count")
        if every != interval or count != 1:
            out.append((f"bills every {_period(count, every)}; it must bill every {interval}", "new_price"))
        if recurring.get("usage_type") != "licensed":
            out.append(("is usage-based (metered); it must be one flat amount per period", "new_price"))
    amount = price.get("unit_amount")
    if price.get("billing_scheme") != "per_unit":
        out.append(("uses tiered pricing; it must be one flat amount", "new_price"))
    elif not isinstance(amount, int) or isinstance(amount, bool):
        out.append(("has no fixed amount (the customer chooses it); it must be one flat amount", "new_price"))
    elif found_currency == currency and amount != cents:
        out.append((f"Stripe would charge {_money(amount)} but DoMe shows {_money(cents)}", "amount"))
    return out


class StripeChecker:
    """Reads Stripe with one environment's secret key (GET only) and compares what it finds with what DoMe needs:
    the account (named; for a live key, allowed to take payments), the five prices (plans.json), the webhook endpoint
    for this environment (the nine events DoMe acts on) and the customer portal (saved). Messages name GitHub
    variables and Stripe click paths, never a secret value."""

    def __init__(
        self,
        env: Mapping[str, str],
        cfg: EnvConfig,
        billing: Billing,
        book: SecretBook,
        amounts: Mapping[str, int],
        currency: str,
    ) -> None:
        self.env = env
        self.cfg = cfg
        self.billing = billing
        self.book = book
        self.amounts = amounts
        self.currency = currency
        self.live = billing.mode == "live"
        self.mode = "Live mode" if self.live else "Test mode"
        self.page = environments_page(env)
        self.app = app_name_for(cfg, env)  # already checked by read_inputs
        self.label = ""  # "**name** (`acct_...`)" once the account was read
        self.matched: list[str] = []  # what was checked and matches
        self.unchecked: list[str] = []  # what the (restricted) key may not read
        self.answered = False  # Stripe answered a read at least once (HTTP 200)
        self.foreign = False  # the key's account knows none of the five prices: it is not the account DoMe set up
        self.check = StripeCheck()

    def read(self, what: str, path: str, query: Sequence[tuple[str, str]] = ()) -> tuple[int, dict[str, Any], str]:
        """Status 403 (a restricted key without read access to ``what``) is noted for a warning, not a stop. Raises
        _KeyRefused on 401 and StripeDown when Stripe gives no usable answer."""
        status, body, message = stripe_read(self.billing.secret_key, self.book, path, query)
        self.answered = self.answered or status == 200
        if status == 401:
            raise _KeyRefused(message)
        if status == 403 and what not in self.unchecked:
            self.unchecked.append(what)
        return status, body, message

    def dome_host(self, hostname: str) -> bool:
        """An address of DoMe's own: this environment's DNS zone (domeremote.com and its subdomains) or its Fly app."""
        hostname = hostname.lower().rstrip(".")
        zone = self.cfg.dns_zone.lower()
        return hostname in (zone, f"{self.app}.fly.dev") or hostname.endswith("." + zone)

    @property
    def scope(self) -> str:
        """Where something was looked for, for a sentence: "in **name** (`acct_...`), Test mode"."""
        return f"in {self.label}, {self.mode}" if self.label else f"in {self.mode}"

    def same_account_step(self) -> str:
        prefix = "sk_live_" if self.live else "sk_test_"
        return (
            "In the Stripe dashboard, check the account name at the top left"
            + (f" (this key belongs to {self.label})" if self.label else "")
            + f". Switch to the account or sandbox that holds DoMe's prices ({self.mode}), and copy "
            f"from inside it both the secret key (**Developers** > **API keys**, `{prefix}...`) and the five price "
            "ids (**Product catalog** > the product > the price, `price_...`). Paste the key over the Environment "
            "**secret** `DOME_STRIPE_SECRET_KEY` and the ids over the Environment **variables** "
            f"`DOME_STRIPE_PRICE_...` at {self.page} > **{self.cfg.name}**. The webhook endpoint and the customer "
            "portal settings must be in that same account or sandbox, and `DOME_STRIPE_WEBHOOK_SECRET` must be the "
            "**Signing secret** of its endpoint: copy it again from there if unsure, as the Deploy button cannot "
            "check it (docs/LAUNCH_SETUP.md section 11A)."
        )

    def run(self) -> StripeCheck:
        down = ""
        try:
            self.account()
            self.prices()
            if not self.foreign:  # another account's webhook endpoint and portal would only add wrong fixes
                self.webhook()
                self.portal()
        except _KeyRefused as exc:
            self.check.problems.append(
                "Stripe refused the secret key in `DOME_STRIPE_SECRET_KEY` (HTTP 401): it was rolled or deleted in "
                "Stripe, or not copied whole. With it, no customer can pay"
                + (f" (Stripe says: {exc})." if str(exc) else ".")
            )
            self.check.step(
                f"In the Stripe dashboard ({self.mode}), check the account name at the top left: it must be the "
                "account or sandbox that holds DoMe's prices, webhook endpoint and customer portal. There, open "
                "**Developers** > **API keys**, click **Reveal** next to **Secret key** (or create a new secret key), "
                f"copy it with the copy button and paste it over `DOME_STRIPE_SECRET_KEY` at {self.page} > "
                f"**{self.cfg.name}** > **Environment secrets** (docs/LAUNCH_SETUP.md section 11A, step 3)."
            )
            self.check.row = "**key refused by Stripe** (HTTP 401)"
            return self.check
        except StripeDown as exc:
            down = str(exc)
        if self.unchecked:
            what = ", ".join(self.unchecked)
            self.check.warnings.append(
                f"Stripe would not let this key read {what} (HTTP 403: a restricted key without that permission), so "
                f"the Deploy button could not check {'it' if len(self.unchecked) == 1 else 'them'}. Check by hand "
                "(docs/LAUNCH_SETUP.md section 11A), or give the key read access in Stripe > **Developers** > **API "
                "keys** > the restricted key > **Edit**."
            )
        if down:
            self.check.warnings.append(
                f"The Stripe check could not run{' to the end' if self.answered else ''} (Stripe's API: {down}). The "
                "deploy went on without it: Stripe being unreachable never stops a deploy. Run Deploy again later to "
                "have the account, prices, webhook endpoint and customer portal checked."
            )
        lead = f"{self.label}, {self.mode}: " if self.label else f"{self.mode}: "
        if self.check.problems:
            self.check.row = lead + "**does not match** what DoMe needs (see the problem above)"
        elif down:
            self.check.row = lead + f"**not checked**: Stripe's API did not answer ({down}); the deploy went on"
        else:
            parts = self.matched or ["nothing"]
            joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
            self.check.row = lead + joined + (" match" if self.matched else " could be checked")
            if self.unchecked:
                self.check.row += f"; not checked: {', '.join(self.unchecked)} (the key may not read them)"
        if self.check.warnings and not self.check.problems:
            count = len(self.check.warnings)
            self.check.row += f"; {count} warning{'s' if count > 1 else ''} in the Notes"
        return self.check

    # ----- the account ----------------------------------------------------------------------------
    def account(self) -> None:
        status, account, message = self.read("the account", "/v1/account")
        if status == 403:
            return
        if status != 200:
            raise StripeDown(f"HTTP {status}" + (f": {message}" if message else ""))
        settings = account.get("settings")
        dashboard = settings.get("dashboard") if isinstance(settings, dict) else None
        profile = account.get("business_profile")
        names = [
            dashboard.get("display_name") if isinstance(dashboard, dict) else None,
            profile.get("name") if isinstance(profile, dict) else None,
        ]
        raw = next((n for n in names if isinstance(n, str) and n.strip()), "")
        name = (_plain(self.book, raw) or "?") if raw else ""
        account_id = str(account.get("id") or "")
        shown_id = f" (`{account_id}`)" if _STRIPE_ACCOUNT_ID.match(account_id) else ""
        self.label = f"**{name}**{shown_id}" if name else f"an account without a name{shown_id}"
        if not name:
            self.check.problems.append(
                f"The Stripe account this key belongs to{shown_id} has neither an account name nor a business name. "
                "Stripe's payment page (Checkout) refuses to open for such an account (\"you must set an account or "
                'business name"), so no customer could pay.'
            )
            self.check.step(
                f"In Stripe ({self.mode}), open **Settings** > **Business** and fill in the **Public business name** "
                "(under **Public details**; for example DoMe), or the account name under **Account details**, and "
                "click **Save**."
            )
        if self.live and account.get("charges_enabled") is not True:
            self.check.problems.append(
                f"Stripe has not activated payments for {self.label} yet, so a Live mode key cannot take a card "
                "(Stripe reports `charges_enabled` false)."
            )
            self.check.step(
                "In the Stripe dashboard (Live mode), finish the activation Stripe asks for (the **Activate payments** "
                "banner: business details, bank account, identity); Stripe emails when payments are on. Until then, "
                "keep production in Test mode (docs/LAUNCH_SETUP.md section 11A, step 8)."
            )

    # ----- the five prices ------------------------------------------------------------------------
    def prices(self) -> None:
        unknown: list[tuple[str, str]] = []
        wrong: list[str] = []
        fixes: set[str] = set()
        good = 0
        for name, price_id in self.billing.prices:
            path = "/v1/prices/" + quote(price_id, safe="")
            status, price, message = self.read("the prices", path, [("expand[]", "product")])
            if status == 403:
                continue
            if status == 404:
                unknown.append((name, message))
                continue
            if status != 200:
                self.check.warnings.append(
                    f"Stripe would not show the price in `{name}` (HTTP {status}"
                    + (f": {message}" if message else "")
                    + "), so the Deploy button could not check it."
                )
                continue
            if price.get("object") != "price":
                raise StripeDown("an answer for a price that is not a price")
            found = price_problems(name, price, live=self.live, cents=self.amounts[name], currency=self.currency)
            if found:
                wrong.append(f"`{name}` ({STRIPE_PRICES[name]}): " + "; ".join(c for c, _ in found) + ".")
                fixes |= {fix for _, fix in found}
            else:
                good += 1
        if unknown:
            names = [n for n, _ in unknown]
            every = len(names) == len(STRIPE_PRICES)
            text = (
                f"Stripe does not know {'any of the five price ids' if every else 'the price id in ' + _names(names)} "
                f"{self.scope}, where `DOME_STRIPE_SECRET_KEY` belongs. The key and the prices come from different "
                "Stripe accounts or sandboxes (every sandbox has its own keys and its own prices): for example, the "
                "key was copied from the main account's Test mode while the prices were made in the sandbox "
                '"domeremote sandbox"' + ("." if every else ", or a price was deleted.")
            )
            if any("similar object exists in" in m for _, m in unknown):
                text += " Stripe adds that a price with that id exists in the other mode: Test and Live never mix."
            if every:
                self.foreign = True
                text += (
                    " The webhook endpoint and the customer portal were not checked here: they are checked once the "
                    "key comes from the account or sandbox that holds the prices."
                )
            self.check.problems.append(text)
            fixes.add("same_account")
        self.check.problems += wrong
        if "same_account" in fixes:
            self.check.step(self.same_account_step())
        if "unarchive" in fixes:
            self.check.step(
                f"In Stripe ({self.mode}) > **Product catalog**, open the archived product or price named above and "
                "choose **Unarchive** (archived products are listed under the **Archived** filter), or make a new "
                "price as below."
            )
        if fixes & {"new_price", "unarchive"}:
            self.check.step(
                "Stripe prices cannot be changed once made. In Stripe "
                f"({self.mode}) > **Product catalog** > the product, add a new price exactly as docs/LAUNCH_SETUP.md "
                "section 11A step 2 lists it (US dollars, tax-inclusive; recurring monthly or yearly for DoMe Pro and "
                "DoMe Pro Plus, one-time for the AI top-up), copy its id (`price_...`) and paste it over the variable "
                f"named above at {self.page} > **{self.cfg.name}** > **Environment variables**. If the customer portal "
                "offers the old price for switching plans, swap it there too (**Settings** > **Billing** > **Customer "
                "portal**)."
            )
        if "amount" in fixes:
            self.check.step(
                "For an amount that differs: Stripe prices cannot be changed once made, so either add a new price with "
                f"the amount DoMe shows (in Stripe ({self.mode}) > **Product catalog** > the product; copy the new "
                f"`price_...` id into the variable named above at {self.page} > **{self.cfg.name}** > **Environment "
                "variables**), or, if Stripe's amount is the one you want, ask an engineer to change DoMe's display "
                "price (shared/protocol/plans.json) so the app and the website show what Stripe charges."
            )
        if good and good == len(self.billing.prices):
            self.matched.append(f"{good} prices")

    # ----- the webhook endpoint -------------------------------------------------------------------
    def webhook(self) -> None:
        url = self.cfg.webhook_url
        status, listing, message = self.read("the webhook endpoints", "/v1/webhook_endpoints", [("limit", "100")])
        if status == 403:
            return
        data = listing.get("data")
        if status != 200 or not isinstance(data, list):
            raise StripeDown(f"HTTP {status}" + (f": {message}" if message else ""))
        endpoints = [e for e in data if isinstance(e, dict)]
        same = [e for e in endpoints if e.get("url") == url]
        add_steps = (
            f"In Stripe ({self.mode}) > **Developers** > **Webhooks** > **Add destination**, add the endpoint "
            f"`{url}` with the events of docs/LAUNCH_SETUP.md section 11A step 4, then copy its **Signing secret** "
            f"(`whsec_...`) over the Environment secret `DOME_STRIPE_WEBHOOK_SECRET` at {self.page} > "
            f"**{self.cfg.name}**."
        )
        if not same:
            # Only DoMe's own addresses are shown (scheme, host, port and path; never a user, password or query): the
            # summary is public, and another service's webhook address can carry its secret in the path or the host
            # (a capability URL), so the account's other endpoints are only counted.
            shown: list[str] = []
            others = 0
            for e in endpoints:
                try:
                    parts = urlsplit(str(e.get("url") or ""))
                    hostname = parts.hostname or ""
                    host = hostname + (f":{parts.port}" if parts.port else "")
                except ValueError:
                    others += 1
                    continue
                if self.dome_host(hostname) and len(shown) < 5:
                    shown.append(f"`{_plain(self.book, f'{parts.scheme}://{host}{parts.path}', 120)}`")
                else:
                    others += 1
            there = (
                [f"Endpoints for DoMe there now: {', '.join(shown)} (the address must match exactly)."] if shown else []
            )
            if others:
                there.append(
                    f"{others} other endpoint{'s' if others > 1 else ''} there send{'' if others > 1 else 's'} to "
                    "other addresses (not shown)."
                )
            self.check.problems.append(
                f"Stripe has no webhook endpoint for `{url}` {self.scope}, so Stripe would never tell DoMe that "
                'someone paid (the Billing page would stay on "Pending activation").' + "".join(" " + t for t in there)
            )
            self.check.step(add_steps)
            return

        def missing(endpoint: Mapping[str, Any], events: Sequence[str]) -> list[str]:
            enabled = endpoint.get("enabled_events")
            enabled = enabled if isinstance(enabled, list) else []
            return [] if "*" in enabled else [e for e in events if e not in enabled]

        best = min(same, key=lambda e: (e.get("status") != "enabled", len(missing(e, STRIPE_WEBHOOK_EVENTS))))
        problems = 0
        if best.get("status") != "enabled":
            problems += 1
            self.check.problems.append(
                f"The webhook endpoint `{url}` is **disabled** in Stripe ({self.mode}), so Stripe sends DoMe nothing "
                "(Stripe also switches an endpoint off by itself after days of failed deliveries)."
            )
            self.check.step(
                f"In Stripe ({self.mode}) > **Developers** > **Webhooks**, open the endpoint `{url}` and enable it "
                "again (its menu > **Enable**). If Stripe had switched it off, check that the site answers first."
            )
        lacking = missing(best, STRIPE_WEBHOOK_EVENTS)
        if lacking:
            problems += 1
            self.check.problems.append(
                f"The webhook endpoint `{url}` does not listen to {_names(lacking)}, so DoMe would not hear about "
                "those payment events (a paid plan could stay inactive, or stay active after it ended)."
            )
            self.check.step(
                f"In Stripe ({self.mode}) > **Developers** > **Webhooks**, open the endpoint `{url}`, edit it and tick "
                "the events named above (docs/LAUNCH_SETUP.md section 11A step 4). The signing secret stays the same."
            )
        recorded = missing(best, STRIPE_OPERATOR_EVENTS)
        if recorded:
            self.check.warnings.append(
                f"The webhook endpoint `{url}` does not listen to {_names(recorded)}. Payments work without them, but "
                "DoMe records refunds and disputes only for these events, so an engineer would not hear about them. "
                f"Add them in Stripe ({self.mode}) > **Developers** > **Webhooks** > the endpoint (edit it)."
            )
        if len(same) > 1:
            self.check.warnings.append(
                f"{len(same)} webhook endpoints in Stripe ({self.mode}) send to `{url}`. Each has its own signing "
                "secret and only one can match `DOME_STRIPE_WEBHOOK_SECRET`, so the other's deliveries fail: delete "
                "the extra one in **Developers** > **Webhooks**."
            )
        if not problems:
            self.matched.append("webhook endpoint")

    # ----- the customer portal --------------------------------------------------------------------
    def portal(self) -> None:
        status, listing, message = self.read(
            "the customer portal settings", "/v1/billing_portal/configurations", [("is_default", "true")]
        )
        if status == 403:
            return
        data = listing.get("data")
        if status != 200 or not isinstance(data, list):
            raise StripeDown(f"HTTP {status}" + (f": {message}" if message else ""))
        default = next((c for c in data if isinstance(c, dict) and c.get("is_default") is True), None)
        if default is None or default.get("active") is not True:
            self.check.problems.append(
                f"The customer portal is not set up {self.scope} (its settings were never saved, or are switched "
                "off). DoMe's **Manage subscription** button opens Stripe's customer portal, which fails without saved "
                "settings, so customers could not cancel or change their card."
            )
            self.check.step(
                f"In Stripe ({self.mode}) > **Settings** > **Billing** > **Customer portal**, set it up as "
                "docs/LAUNCH_SETUP.md section 11A step 5 says and click **Save** once."
            )
            return
        features = default.get("features")
        features = features if isinstance(features, dict) else {}

        def feature(name: str) -> dict[str, Any]:
            value = features.get(name)
            return value if isinstance(value, dict) else {}

        where = f"Stripe ({self.mode}) > **Settings** > **Billing** > **Customer portal**"
        cancel = feature("subscription_cancel")
        if cancel.get("enabled") is not True:
            self.check.warnings.append(
                f"The customer portal does not let customers cancel. Turn on cancelling, at the end of the billing "
                f"period, in {where} and save."
            )
        elif cancel.get("mode") != "at_period_end":
            self.check.warnings.append(
                "The customer portal cancels subscriptions **immediately**; DoMe's setup is at the end of the billing "
                f"period (the customer keeps what they paid for). Change it in {where} and save."
            )
        if feature("payment_method_update").get("enabled") is not True:
            self.check.warnings.append(
                f"The customer portal does not let customers update their card. Turn it on in {where} and save."
            )
        update = feature("subscription_update")
        offered: set[str] = set()
        products = update.get("products")
        for item in products if isinstance(products, list) else []:
            prices = item.get("prices") if isinstance(item, dict) else None
            offered |= {p for p in (prices if isinstance(prices, list) else []) if isinstance(p, str)}
        allowed = update.get("default_allowed_updates")
        subscription_prices = [(n, p) for n, p in self.billing.prices if STRIPE_PRICE_TERMS[n][0]]
        if update.get("enabled") is not True or not isinstance(allowed, list) or "price" not in allowed:
            self.check.warnings.append(
                "The customer portal does not let customers switch between DoMe Pro and DoMe Pro Plus (monthly or "
                f"yearly). Turn on switching plans with the four subscription prices in {where} and save."
            )
        elif not isinstance(products, list):
            # Stripe may leave the product list out of this answer (Deploy #9 warned about all four prices on a portal
            # the founder had set up), so an absent list is "not shown", never "nothing offered"
            pass
        elif not_offered := [n for n, p in subscription_prices if p not in offered]:
            one = len(not_offered) == 1
            self.check.warnings.append(
                "Switching plans in the customer portal does not offer the "
                + (f"price in {_names(not_offered)}. Add it (the product and that price)" if one else
                   f"prices in {_names(not_offered)}. Add them (the products and those prices)")
                + f" in {where} and save."
            )  # fmt: skip
        self.matched.append("customer portal")


def check_stripe_setup(
    env: Mapping[str, str], cfg: EnvConfig, billing: Billing, book: SecretBook, summary: Summary
) -> None:
    """With billing on, before any flyctl call: what Stripe itself says about the seven values. Writes the summary row
    "Stripe account" and any warnings as notes; raises Stop("you") when payments could not work."""
    amounts, currency = display_prices()
    check = StripeChecker(env, cfg, billing, book, amounts, currency).run()
    summary.step("Stripe account", check.row)
    summary.notes += [f"Stripe check: {w}" for w in check.warnings]
    if check.problems:
        raise Stop(
            "you",
            "Stripe's own answers show that payments would not work with this environment's Stripe values (customers "
            "would see \"Payments aren't working on DoMe's side right now\" or never get their plan). Nothing was "
            "created or changed." + "".join(f"\n\n- {p}" for p in check.problems),
            next_steps=[*check.next_steps, "Run Deploy again."],
        )


# ======================================================================================================
# deploy
# ======================================================================================================


class Deployer:
    def __init__(self, env: Mapping[str, str], out: TextIO, envs: Mapping[str, EnvConfig]) -> None:
        self.env = env
        self.out = out
        self.envs = envs
        self.book = SecretBook(out)
        self.runner = Runner(env, self.book, out)
        self.summary = Summary(title="Deploy")
        self.mutated = False
        self.deadline = monotonic() + RUN_BUDGET

    def remaining(self) -> float:
        return self.deadline - monotonic()

    # ----- helpers --------------------------------------------------------------------------------
    def log(self, message: str) -> None:
        print(f"==> {message}", file=self.out, flush=True)

    def fly(self, *args: str, **kwargs: Any) -> Result:
        return self.runner.run(["flyctl", *args], **kwargs)

    def fly_list(self, what: str, *args: str, empty_markers: Sequence[str] = ()) -> list[dict[str, Any]]:
        result = self.fly(*args, echo=False)
        items = parse_list(result.stdout, empty_markers=empty_markers) if result.ok else None
        if items is None:
            hint = permission_hint(result)
            raise Stop(
                "you" if hint else "engineer",
                f"Could not read {what} from Fly ({'timed out' if result.timed_out else f'exit {result.code}'})."
                + (f" {hint[0].upper()}{hint[1:]}." if hint else ""),
                next_steps=token_next_steps(self.env) if hint else (),
            )
        return items

    def secret_names(self, app: str) -> set[str]:
        items = self.fly_list(f"the secrets of {app}", "secrets", "list", "-a", app, "--json")
        names = {str(field_of(i, "name") or "") for i in items}
        if "" in names:
            raise Stop("engineer", f"Fly listed a secret of {app} without a name; nothing was generated.")
        return names

    def unstage(self, app: str, names: list[str]) -> None:
        """Remove Fly secrets with this deploy (`flyctl secrets unset --stage`: no restart until the deploy)."""
        result = self.fly("secrets", "unset", "--stage", "-a", app, *names)
        if not result.ok:
            raise Stop("failed", "Fly did not remove the secrets (" + ", ".join(names) + ").")
        self.mutated = True

    def stage(self, app: str, items: list[tuple[str, str]]) -> None:
        for name, value in items:
            if not importable(value):
                raise Stop("engineer", f"{name} has characters flyctl's import cannot carry; it was not set.")
        payload = "".join(f"{name}={value}\n" for name, value in items)
        result = self.fly("secrets", "import", "--stage", "-a", app, input_text=payload)
        if not result.ok:
            raise Stop("failed", "Fly did not accept the secrets (" + ", ".join(n for n, _ in items) + ").")
        self.mutated = True

    # ----- flow -----------------------------------------------------------------------------------
    def run(self) -> int:
        billing: Billing | None = None
        try:
            check_ref(self.env)
            cfg = read_environment_choice(self.env, self.envs)
            self.summary.title = f"{cfg.title} deploy"
            check_distinct_apps(self.env, self.envs)  # before anything reads or changes an app
            inputs = read_inputs(self.env, cfg, self.book, self.summary)
            billing = inputs.billing
            self.summary.step("Inputs", "all present")
            self.summary.step("Billing (Stripe)", billing_step_text(inputs.billing))
            self.summary.step("Screen view", screen_view_step_text(inputs.screen))
            self.deploy(inputs)
        except Stop as stop:
            self.summary.headline = HEADLINES[stop.kind]
            self.summary.problem = stop.message
            self.summary.next_steps = stop.next_steps + self.summary.next_steps
            said = stop.message + " ".join(stop.next_steps)
            if not self.mutated and billing is not None:  # the row said "stored in Fly", which this run never did
                row = ("Billing (Stripe)", billing_step_text(billing, stored=False))
                self.summary.steps = [row if name == row[0] else (name, r) for name, r in self.summary.steps]
            if not self.mutated and "Nothing was created" not in said:
                self.summary.notes.append("Nothing was created or changed on Fly.io by this run.")
            elif self.mutated:
                self.summary.notes.append(
                    "Some steps of this run already changed things on Fly.io; they are listed above."
                )
            write_summary(self.summary, self.env, self.out)
            return 1
        except Exception as exc:  # noqa: BLE001 - always write the summary; the message is redacted
            self.summary.headline = HEADLINES["engineer"]
            self.summary.problem = "Unexpected error: " + self.book.redact(f"{type(exc).__name__}: {exc}")
            write_summary(self.summary, self.env, self.out)
            return 1
        write_summary(self.summary, self.env, self.out)
        return 0

    def deploy(self, inp: Inputs) -> None:
        cfg, app = inp.cfg, inp.app
        self.log(f"Checking the sign-in provider {inp.issuer}")
        authorization_endpoint = check_discovery(inp.issuer)
        self.summary.step("Sign-in provider (OIDC discovery)", f"{inp.issuer} answers")

        if inp.billing.on:
            self.log("Asking Stripe about the account, the five prices, the webhook endpoint and the customer portal")
            check_stripe_setup(self.env, cfg, inp.billing, self.book, self.summary)

        if cfg.requires_staging_commit:
            self.log("Checking that staging already runs this commit")
            production_gate(self.env, self.envs, inp.sha, self.summary)

        # ---- preflight: read only -----------------------------------------------------------------
        self.log(f"Reading Fly organization {inp.org}")
        apps = self.fly_list(
            f"the apps of organization {inp.org}", "apps", "list", "-o", inp.org, "--json", empty_markers=("No apps",)
        )
        app_exists = any(field_of(a, "name") == app for a in apps)
        names = self.secret_names(app) if app_exists else set()
        missing_generated = [n for n in GENERATED_SECRETS if n not in names]

        if app_exists and missing_generated and cfg.dome_env == "production":
            machines = self.fly_list(f"the machines of {app}", "machine", "list", "-a", app, "--json")
            if machines:
                raise Stop(
                    "engineer",
                    f"The production app already runs but has no {' and no '.join(missing_generated)}. Creating new "
                    "ones would sign everyone out or invalidate every linked PC, so this run stops. An engineer "
                    "has to look at the app's secrets first. Nothing was changed.",
                )

        if app_exists and missing_generated and cfg.dome_env != "production":
            self.summary.notes.append(
                f"The existing app had no {' and no '.join(missing_generated)}; new ones are generated. On staging "
                "that signs testers out and PCs linked to staging must link again."
            )

        cluster: dict[str, Any] | None = None
        if cfg.external_database:
            # Never Fly Managed Postgres: the address is the GitHub secret, stored in Fly again below on every deploy.
            database_plan = "external"
            if "DOME_DATABASE_URL" in names:
                raise Stop(
                    "engineer",
                    f"The app {app} has a Fly secret DOME_DATABASE_URL. It takes precedence over DATABASE_URL "
                    "(deploy/entrypoint.sh), so the address from GitHub would never be used. An engineer must check "
                    f"where it points and remove it (`flyctl secrets unset DOME_DATABASE_URL -a {app}`). Nothing was "
                    "changed.",
                )
            if inp.create_database:
                self.summary.notes.append(
                    f"The **create the database** tick is for production only. {cfg.title} keeps its data in its "
                    "free Neon database, so this run created no database."
                )
        else:
            has_database = bool(names & set(DATABASE_SECRET_NAMES))
            database_plan = "existing"
            if not has_database:
                cluster, database_plan = self.choose_database(inp)
            if has_database and inp.database_url:
                self.summary.notes.append(
                    "The app already has its database address, so the GitHub secret DATABASE_URL was not used. "
                    "You can delete that GitHub secret (one copy of a password fewer)."
                )

        # ---- changes ------------------------------------------------------------------------------
        if app_exists:
            self.summary.step(f"Fly app {app}", "already there, unchanged")
        else:
            self.create_app(inp)
        addresses = self.ensure_ips(app)
        if cfg.external_database:
            self.summary.step(
                "Database",
                f"outside Fly ({cfg.external_database_project} on Neon): address from the GitHub secret "
                "DATABASE_URL, stored in Fly again with this deploy",
            )
        else:
            database_plan = self.ensure_database(inp, cluster, database_plan)

        staged: list[tuple[str, str]] = []
        for name in GENERATED_SECRETS:
            if name in names:
                self.summary.step(name, "already there, unchanged")
                continue
            value = self.generate(name)
            staged.append((name, value))
            self.summary.step(name, "created (generated inside this run, stored only in Fly)")
        staged.append(("DOME_OIDC_CLIENT_SECRET", inp.client_secret))
        if database_plan in ("fallback", "external"):
            staged.append(("DATABASE_URL", inp.database_url))
        if inp.billing.on:
            staged += [
                ("DOME_STRIPE_SECRET_KEY", inp.billing.secret_key),
                ("DOME_STRIPE_WEBHOOK_SECRET", inp.billing.webhook_secret),
            ]
        turn_staged: list[str] = []
        if inp.screen.turn_on:
            staged.append(("DOME_TURN_API_TOKEN", inp.screen.api_token))
            turn_staged.append("DOME_TURN_API_TOKEN")
            if inp.screen.analytics_token:
                staged.append(("DOME_TURN_ANALYTICS_TOKEN", inp.screen.analytics_token))
                turn_staged.append("DOME_TURN_ANALYTICS_TOKEN")
        self.log("Storing secrets in Fly (staged; they reach the app with this deploy)")
        self.stage(app, staged)
        self.summary.step("DOME_OIDC_CLIENT_SECRET", "updated from GitHub")
        if inp.billing.on:
            self.summary.step(", ".join(STRIPE_SECRET_NAMES), "updated from GitHub")
        if turn_staged:
            self.summary.step(", ".join(turn_staged), "updated from GitHub")
        # Removal: the GitHub Environment is the only source of the Stripe values, and the app refuses to start with
        # only some of the seven, so a DOME_STRIPE_* secret an earlier deploy (or a person) left in Fly goes away when
        # GitHub no longer has it (all of them when billing is off; any price id kept as a Fly secret when it is on,
        # because the price ids now come with -e). The same holds for the Screen view relay server values: the two
        # TURN secrets go when GitHub no longer has them (the key id comes with -e), so the app never starts with a
        # token and no key id.
        staged_names = {name for name, _ in staged}
        leftovers = [n for n in STRIPE_NAMES if n in names and n not in staged_names]
        turn_leftovers = [n for n in TURN_NAMES if n in names and n not in staged_names]
        if leftovers:
            self.unstage(app, leftovers)
            self.summary.step(
                ", ".join(leftovers), "removed from Fly with this deploy (not in this GitHub Environment)"
            )
            if not inp.billing.on:
                self.summary.notes.append(
                    "Billing was switched **off** by this deploy: the Stripe secrets an earlier deploy stored in Fly "
                    "were removed because this GitHub Environment has no `DOME_STRIPE_` values. Subscriptions that "
                    "already exist keep running and charging at Stripe, but DoMe no longer hears about them: cancel "
                    "them in the Stripe dashboard, or put all seven values back and run Deploy again."
                )
        if turn_leftovers:
            self.unstage(app, turn_leftovers)
            self.summary.step(
                ", ".join(turn_leftovers), "removed from Fly with this deploy (not in this GitHub Environment)"
            )
            if not inp.screen.turn_on:
                self.summary.notes.append(
                    "The Screen view relay server was switched **off** by this deploy: the Cloudflare TURN secrets an "
                    "earlier deploy stored in Fly were removed because this GitHub Environment has no `DOME_TURN_` "
                    "values. Screen view, if on, now works only where a direct connection exists."
                )

        self.build_and_deploy(inp)
        self.ensure_one_machine(app)
        records = self.ensure_certificate(inp, addresses)
        self.smoke(inp, addresses, records, authorization_endpoint)

    # ----- database -------------------------------------------------------------------------------
    def list_clusters(self, inp: Inputs) -> list[dict[str, Any]] | None:
        """None when Fly would not list them (permission, old token, unknown output)."""
        result = self.fly("mpg", "list", "-o", inp.org, "--json", echo=False)
        if not result.ok:
            hint = permission_hint(result)
            self.summary.notes.append(
                "Fly did not list Managed Postgres clusters for this token" + (f" ({hint})" if hint else "") + "."
            )
            return None
        return parse_list(result.stdout, empty_markers=("No managed postgres clusters found",))

    def choose_database(self, inp: Inputs) -> tuple[dict[str, Any] | None, str]:
        """The GitHub secret DATABASE_URL is used only when Fly would not list the clusters (the token cannot manage
        Managed Postgres, so the founder made the database on the dashboard). When Fly lists them and production's
        is not there, the run stops for the founder's cost consent even with that secret set."""
        clusters = self.list_clusters(inp)
        if clusters is None:
            if inp.database_url:
                return None, "fallback"
            raise Stop(
                "you",
                f"{inp.cfg.title} has no database yet, and Fly would not let this token create or list Managed "
                "Postgres databases. Nothing was created or changed.",
                next_steps=dashboard_database_steps(inp),
            )
        for c in clusters:
            if field_of(c, "name") == inp.cfg.database_cluster:
                return c, "attach"
        if inp.create_database:
            return None, "create"
        message = (
            f"{inp.cfg.title} has no database yet. Fly charges about $41 a month for one: Managed Postgres Basic "
            f"($38) plus about $3 for its {inp.cfg.database_volume_gb} GB of storage. With the app machine, "
            f"{inp.cfg.name} costs about $45 a month (docs/LAUNCH_SETUP.md section 0)."
        )
        steps = ["If you agree to that cost, run Deploy again with **create the database** ticked."]
        others = [n for n in (str(field_of(c, "name") or "") for c in clusters) if re.fullmatch(r"[A-Za-z0-9._-]+", n)]
        if others:
            message += (
                f" Fly lists other Managed Postgres databases in organization {inp.org} ("
                + ", ".join(f"`{n}`" for n in others[:5])
                + f"), but none named `{inp.cfg.database_cluster}`."
            )
            steps.insert(
                0, f"If one of those is meant to be {inp.cfg.name}'s database, do not tick: ask an engineer first."
            )
        if inp.database_url:
            message += (
                f" The {inp.cfg.name} Environment secret `DATABASE_URL` was not used: it is only for when Fly cannot "
                f"list or connect {inp.cfg.name}'s own Managed Postgres database."
            )
        raise Stop("you", message + " Nothing was created or changed.", next_steps=steps)

    def ensure_database(self, inp: Inputs, cluster: dict[str, Any] | None, plan: str) -> str:
        """Fly Managed Postgres only (never for an external database). Returns the plan that ended up applying:
        "existing", "attached" or "fallback" (stage the GitHub secret: Fly would not list the clusters, or the
        attach did not set the address)."""
        cfg, app = inp.cfg, inp.app
        if plan == "existing":
            self.summary.step("Database", "already connected, unchanged")
            return plan
        if plan == "fallback":
            self.summary.step("Database", "address taken from the GitHub secret DATABASE_URL")
            return plan
        if plan == "create":
            self.log(f"Creating Managed Postgres cluster {cfg.database_cluster} (this takes several minutes)")
            create_timeout = min(MPG_CREATE_TIMEOUT, self.remaining() - MIN_TIME_TO_DEPLOY)
            result = self.fly(
                "mpg",
                "create",
                "--name",
                cfg.database_cluster,
                "--org",
                inp.org,
                "--region",
                cfg.region,
                "--plan",
                cfg.database_plan,
                "--volume-size",
                str(cfg.database_volume_gb),
                "--pg-major-version",
                str(cfg.pg_major_version),
                sensitive=True,
                timeout=max(create_timeout, 1.0),
            )
            self.mutated = True
            if result.timed_out:
                raise Stop(
                    "you",
                    "Fly is still preparing the database. Some steps already ran (listed below); nothing is broken.",
                    next_steps=[
                        "Run Deploy again in about 10 minutes (with or without **create the database** ticked)."
                    ],
                )
            clusters = self.list_clusters(inp) or []
            cluster = next((c for c in clusters if field_of(c, "name") == cfg.database_cluster), None)
            if cluster is None:
                hint = permission_hint(result)
                raise Stop(
                    "you" if hint else "engineer",
                    "The database could not be created" + (f" ({hint})" if hint else f" (exit {result.code})") + ".",
                    next_steps=dashboard_database_steps(inp),
                )
            self.summary.step(
                f"Database {cfg.database_cluster}", f"created (Managed Postgres {cfg.database_plan}, {cfg.region})"
            )
        assert cluster is not None
        cluster = self.wait_until_ready(inp, cluster)
        cluster_id = str(field_of(cluster, "id") or "")
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", cluster_id):
            raise Stop("engineer", "Fly listed the database cluster without a usable id.")
        self.log(f"Connecting database {cfg.database_cluster} to {app}")
        attach = self.fly("mpg", "attach", cluster_id, "-a", app, sensitive=True)
        self.mutated = True
        names = self.secret_names(app)
        if names & set(DATABASE_SECRET_NAMES):
            self.summary.step("Database", f"connected to {app} (DATABASE_URL set by Fly)")
            return "attached"
        if inp.database_url:
            self.summary.step("Database", "Fly could not connect it; address taken from the GitHub secret DATABASE_URL")
            return "fallback"
        hint = permission_hint(attach)
        raise Stop(
            "you",
            "The database exists, but connecting it to the app did not work" + (f" ({hint})" if hint else "") + ".",
            next_steps=dashboard_database_steps(inp),
        )

    def wait_until_ready(self, inp: Inputs, cluster: dict[str, Any]) -> dict[str, Any]:
        deadline = min(monotonic() + MPG_READY_WAIT, self.deadline - MIN_TIME_TO_DEPLOY)
        while True:
            status = str(field_of(cluster, "status") or "").lower()
            if status == "ready":
                return cluster
            if status in ("failed", "error"):
                raise Stop(
                    "engineer",
                    f"Fly reports the database cluster as {status}. Ask an engineer "
                    "(Fly dashboard > Managed Postgres).",
                )
            if monotonic() >= deadline:
                raise Stop(
                    "you",
                    f"Fly is still preparing the database (status: {status or 'unknown'}).",
                    next_steps=["Run Deploy again in about 10 minutes."],
                )
            self.log(f"Database status: {status or 'unknown'}; waiting")
            sleep(MPG_POLL_INTERVAL)
            clusters = self.list_clusters(inp) or []
            cluster = next((c for c in clusters if field_of(c, "name") == inp.cfg.database_cluster), cluster)

    # ----- app, IPs, generated secrets ------------------------------------------------------------
    def create_app(self, inp: Inputs) -> None:
        self.log(f"Creating Fly app {inp.app}")
        result = self.fly("apps", "create", inp.app, "--org", inp.org)
        if not result.ok:
            text = (result.stdout + result.stderr).lower()
            if "taken" in text or "already exists" in text or "already been" in text:
                var = f"FLY_{inp.cfg.name.upper()}_APP"
                raise Stop(
                    "you",
                    f"The Fly app name {inp.app} belongs to someone else (app names are global on Fly.io). "
                    "Nothing was created or changed.",
                    next_steps=[
                        f"Open {variables_page(self.env)} (Variables tab) and add a repository variable **{var}** "
                        f"with another name, for example {inp.app}-2 (lower-case letters, digits, dashes).",
                        "Run Deploy again."
                        + (" Production checks staging by this name too." if inp.cfg.name == "staging" else ""),
                    ],
                )
            hint = permission_hint(result)
            raise Stop(
                "you" if hint else "engineer",
                "Fly did not create the app" + (f" ({hint})." if hint else "."),
                next_steps=token_next_steps(self.env) if hint else (),
            )
        self.mutated = True
        self.summary.step(f"Fly app {inp.app}", f"created in organization {inp.org}")

    def ensure_ips(self, app: str) -> set[str]:
        items = self.fly_list(f"the IP addresses of {app}", "ips", "list", "-a", app, "--json")
        types = {str(field_of(i, "type") or "").lower() for i in items}
        if "v6" not in types:
            if not self.fly("ips", "allocate-v6", "-a", app).ok:
                raise Stop("failed", "Fly did not give the app its IPv6 address.")
            self.mutated = True
            self.summary.step("Public IPv6 address", "allocated (free)")
        if not types & {"shared_v4", "v4"}:
            # The free shared address; a dedicated IPv4 costs $2/month and is never allocated here.
            if not self.fly("ips", "allocate-v4", "--shared", "-a", app, "--yes").ok:
                raise Stop("failed", "Fly did not give the app its shared IPv4 address.")
            self.mutated = True
            self.summary.step("Public IPv4 address", "shared address allocated (free)")
        items = self.fly_list(f"the IP addresses of {app}", "ips", "list", "-a", app, "--json")
        addresses: set[str] = set()
        for item in items:
            kind = str(field_of(item, "type") or "").lower()
            raw = str(field_of(item, "address") or "")
            if kind in ("v6", "v4", "shared_v4"):
                try:
                    addresses.add(str(ipaddress.ip_address(raw)))
                except ValueError:
                    continue
        return addresses

    def generate(self, name: str) -> str:
        if name == "DOME_SESSION_SECRET":
            value = secrets.token_hex(32)
            self.book.add(value, mask=True)
            return value
        result = self.runner.run(["openssl", "genpkey", "-algorithm", "ed25519"], sensitive=True)
        pem = result.stdout
        if result.ok:
            self.book.add(pem, mask=True)
        if not result.ok or not pem.startswith("-----BEGIN PRIVATE KEY-----"):
            raise Stop("engineer", "openssl did not produce an Ed25519 key on the runner.")
        value = base64.b64encode(pem.encode("ascii")).decode("ascii")
        self.book.add(value, mask=True)
        return value

    # ----- build, deploy, scale, certificate, smoke -----------------------------------------------
    def build_and_deploy(self, inp: Inputs) -> None:
        cfg, app = inp.cfg, inp.app
        if self.remaining() < MIN_TIME_TO_DEPLOY:
            raise Stop(
                "you",
                "This run spent most of its time waiting for Fly (usually the new database). Everything it set up "
                "so far is kept; nothing is broken.",
                next_steps=["Run Deploy again for the same environment; it continues where this run stopped."],
            )
        label = f"git-{inp.sha[:12]}"
        image = f"dome-launch:{label}"
        self.log("Building the image on the runner (the same docker build CI runs)")
        build = self.runner.stream(
            [
                "docker",
                "build",
                "--file",
                DOCKERFILE,
                "--platform",
                "linux/amd64",
                "--tag",
                image,
                "--build-arg",
                f"VITE_DOME_RELEASE_CHANNEL={cfg.release_channel}",
                "--build-arg",
                f"VITE_DOME_SUPPORT_URL={inp.support_url}",
                "--build-arg",
                f"DOME_BUILD_COMMIT={inp.sha}",
                ".",
            ],
            timeout=min(BUILD_TIMEOUT, self.remaining() - 10 * 60.0),
        )
        if not build.ok:
            raise Stop("engineer", "The image did not build on the runner (see the log above).")
        self.summary.step("Image", f"built from commit {inp.sha[:12]}")

        env_args = [
            f"DOME_ENV={cfg.dome_env}",
            f"DOME_PUBLIC_ORIGIN={cfg.public_origin}",
            f"DOME_OIDC_ISSUER={inp.issuer}",
            f"DOME_OIDC_CLIENT_ID={inp.client_id}",
        ]
        if inp.support_expectation:
            env_args.append(f"DOME_SUPPORT_RESPONSE_EXPECTATION={inp.support_expectation}")
        env_args += [f"{name}={value}" for name, value in inp.billing.prices]  # not secret: price ids
        # Screen view: the switch is passed explicitly either way (off is the honest default), the TURN key id only with
        # its token (both or neither, like the app's own check)
        env_args.append(f"{SCREEN_VIEW_SWITCH}={'true' if inp.screen.enabled else 'false'}")
        if inp.screen.turn_on:
            env_args.append(f"{TURN_KEY_ID}={inp.screen.key_id}")  # not secret: the key's id
        config = self.write_fly_config(cfg)
        argv = [
            "flyctl",
            "deploy",
            "--config",
            config,
            "--app",
            app,
            "--image",
            image,
            # Look for the image on this runner only. --local-only alone is not enough in flyctl v0.4.110: a
            # Fly-side feature flag (managed builder) or the Depot default could still pick a remote builder,
            # which cannot see the image built here (deploy_build.go determineImage, imgsrc.NewDockerDaemonType).
            "--local-only",
            "--builder-pool=false",
            "--depot=false",
            "--image-label",
            label,
            "--primary-region",
            cfg.region,
            "--ha=false",
            "--no-public-ips",
            "--yes",
        ]
        for item in env_args:
            argv += ["-e", item]
        self.log(f"Deploying to {app} (runs the database migrations first)")
        try:
            deployed = self.runner.stream(argv, timeout=min(DEPLOY_TIMEOUT, self.remaining() - 4 * 60.0))
        finally:
            (REPO_ROOT / config).unlink(missing_ok=True)
        self.mutated = True
        if not deployed.ok:
            raise Stop(
                "engineer",
                "Fly did not finish the deploy (see the log above: a failing database migration or a start-up "
                "configuration error are the usual causes). The previous version, if any, keeps running.",
                next_steps=[
                    # the migrations run first and need the database: a suspended Neon database fails them
                    f"If the log above says the database could not be reached, open {NEON_CONSOLE} and check whether "
                    "this month's free compute-hours are used up (docs/LAUNCH_SETUP.md section 6).",
                    "Otherwise share the run link with an engineer.",
                ]
                if cfg.external_database
                else (),
            )
        self.summary.step("Deploy", f"done ({app}, migrations ran first)")
        if cfg.sleeps_when_idle:
            self.summary.step(
                "Machine sleep",
                "sleeps when nobody uses it and starts again on the next visit, which then takes a few seconds "
                "(a connected PC keeps it awake)",
            )
        else:
            self.summary.step("Machine sleep", "always on: never stopped, because PCs keep a connection open")

    def write_fly_config(self, cfg: EnvConfig) -> str:
        """Writes this environment's copy of deploy/fly.toml next to it; returns its path relative to the repository
        (the caller deletes it after `flyctl deploy`)."""
        text = render_fly_config(cfg, (REPO_ROOT / FLY_CONFIG).read_text(encoding="utf-8"))
        relative = GENERATED_FLY_CONFIG.format(name=cfg.name)
        (REPO_ROOT / relative).write_text(text, encoding="utf-8")
        sleep_text = ", ".join(f"{k}={toml_literal(v)}" for k, v in cfg.machine_sleep.items())
        self.log(f"Wrote {relative} from {FLY_CONFIG} with {sleep_text}")
        return relative

    def ensure_one_machine(self, app: str) -> None:
        machines = self.fly_list(f"the machines of {app}", "machine", "list", "-a", app, "--json")
        live = [
            m
            for m in machines
            if str(field_of(m, "state") or "").lower() not in ("destroyed", "destroying") and process_group(m) == "app"
        ]
        if len(live) > 1:
            if not self.fly("scale", "count", "1", "--process-group", "app", "-a", app, "--yes").ok:
                raise Stop(
                    "engineer",
                    f"{app} runs {len(live)} machines and Fly did not scale it to 1. The relay needs exactly one.",
                )
            self.summary.step("Machines", f"scaled from {len(live)} to 1 (the relay needs exactly one)")
        else:
            self.summary.step("Machines", f"{len(live)} running")

    def ensure_certificate(self, inp: Inputs, addresses: set[str]) -> list[tuple[str, str, str]]:
        cfg, app = inp.cfg, inp.app
        certs = self.fly_list(f"the certificates of {app}", "certs", "list", "-a", app, "--json")
        detail: dict[str, Any] | None = None
        if not any(str(field_of(c, "hostname") or "").lower() == cfg.hostname for c in certs):
            added = self.fly("certs", "add", cfg.hostname, "-a", app, "--json", echo=False)
            if not added.ok:
                raise Stop("failed", f"Fly did not add the certificate for {cfg.hostname}.")
            self.summary.step(f"Certificate for {cfg.hostname}", "requested")
            detail = parse_object(added.stdout)
        check = self.fly("certs", "check", cfg.hostname, "-a", app, "--json", echo=False)
        if check.ok:
            detail = parse_object(check.stdout) or detail
            status = str(field_of(detail or {}, "status") or "unknown")
            if not any(name.startswith("Certificate for") for name, _ in self.summary.steps):
                self.summary.step(f"Certificate for {cfg.hostname}", f"status {status}")
            for problem in (field_of(detail or {}, "validation_errors") or [])[:3]:
                if isinstance(problem, dict) and isinstance(problem.get("message"), str):
                    self.summary.notes.append("Fly certificate check: " + self.book.redact(problem["message"])[:300])
        else:
            self.summary.notes.append("Fly's certificate check did not answer; run Deploy with action status later.")
        return dns_records(cfg, app, detail, addresses)

    def smoke(
        self, inp: Inputs, addresses: set[str], records: list[tuple[str, str, str]], authorization_endpoint: str
    ) -> None:
        cfg, app = inp.cfg, inp.app
        base = f"https://{app}.fly.dev"
        self.log(f"Checking {base}")
        health = wait_for_health(base, inp.sha)
        self.summary.step(f"{app}.fly.dev /healthz", describe_health(health, inp.sha))
        problems: list[str] = []
        if not health.healthy_for(inp.sha):
            if (
                health.reachable
                and health.status_code == 200
                and health.commit == inp.sha
                and health.body.get("forwarded_client") != "ok"
            ):
                problems.append(
                    f"The client-address self-check says {health.body.get('forwarded_client')!r}: every visitor would "
                    "share one address for per-person limits. An engineer must check DOME_TRUSTED_PROXIES and "
                    "DOME_CLIENT_IP_HEADER in deploy/fly.toml (deploy/README.md 'Client addresses')."
                )
            else:
                problems.append(f"{base}/healthz is {describe_health(health, inp.sha)}.")
        page = self.page_problems(base, production=cfg.dome_env == "production")
        problems += page
        self.summary.step(f"{app}.fly.dev web page", "ok" if not page else "; ".join(page))
        login = login_problems(base, cfg, inp.client_id, authorization_endpoint)
        problems += login
        self.summary.step(
            f"{app}.fly.dev sign-in start (/v1/auth/login)",
            "sends visitors to the sign-in provider with this client id and callback (sign-in itself not tested)"
            if not login
            else "; ".join(login),
        )
        socket_problems = websocket_problems(f"{app}.fly.dev")
        problems += socket_problems
        self.summary.step(
            f"{app}.fly.dev WebSocket (/ws/agent)",
            "the upgrade reaches the app through Fly's proxy (refused without a PC token, as it should be)"
            if not socket_problems
            else "; ".join(socket_problems),
        )
        reported = billing_mode_of(base)
        if reported == inp.billing.mode:
            self.summary.step(f"{app}.fly.dev billing (/v1/plans)", f"reports {reported}, as configured")
        else:
            problem = (
                f"/v1/plans did not answer with a billing mode ({reported})"
                if reported not in BILLING_MODE_TEXT
                else f"/v1/plans reports billing {reported}, but this deploy configured {inp.billing.mode}"
            )
            problems.append(problem)
            self.summary.step(f"{app}.fly.dev billing (/v1/plans)", problem)
        switch, turn = screen_view_of(health)
        wanted_switch = "on" if inp.screen.enabled else "off"
        wanted_turn = "ok" if inp.screen.turn_on else "unconfigured"
        if (switch, turn) == (wanted_switch, wanted_turn):
            turn_text = TURN_TEXT["on" if turn == "ok" else "off"].split(" (")[0]
            self.summary.step(
                f"{app}.fly.dev Screen view (/healthz)",
                f"reports {SCREEN_VIEW_TEXT[switch].lower()}, {turn_text}, as configured",
            )
        else:
            problem = (
                "/healthz did not answer with the Screen view fields (an older build?)"
                if switch == "unknown"
                else f"/healthz reports Screen view {switch} with relay server {turn}, but this deploy configured "
                f"{wanted_switch} with relay server {wanted_turn}"
            )
            problems.append(problem)
            self.summary.step(f"{app}.fly.dev Screen view (/healthz)", problem)
        if problems:
            raise Stop(
                "engineer",
                "The new version is deployed but does not pass its checks: "
                + " ".join(p if p.endswith(".") else p + "." for p in problems),
                next_steps=[external_database_down_step(cfg)] if external_database_down(cfg, health) else (),
            )

        report = check_custom_host(cfg.hostname, addresses, inp.sha, production=cfg.dome_env == "production")
        self.summary.step(cfg.hostname, host_result_text(cfg, report))
        if report.state == "unhealthy" or (report.state == "wrong_commit" and cfg.dome_env == "production"):
            # fly.dev passed just now, so a bad answer on the real name is a real misconfiguration, not a delay
            raise Stop(
                "engineer",
                f"The new version passes its checks at {base}, but {cfg.hostname} "
                f"{host_result_text(cfg, report)}. Visitors at {cfg.hostname} are not served correctly.",
                next_steps=host_next_steps(cfg, report, records),
            )
        if report.state == "live":
            self.summary.headline = f"{cfg.title} deploy finished: live at https://{cfg.hostname}"
            self.summary.next_steps.append(
                f"Open https://{cfg.hostname}, create an account and sign in (this workflow cannot test sign-in). "
                f"The Auth0 Allowed Callback URL must be exactly {cfg.callback_url}."
            )
        else:
            self.summary.headline = f"{cfg.title} deploy finished: running at {base}; {host_result_text(cfg, report)}"
            self.summary.next_steps += host_next_steps(cfg, report, records)
            self.summary.next_steps.append(f"In Auth0, the Allowed Callback URL must be exactly {cfg.callback_url}.")
        self.summary.next_steps += billing_next_steps(cfg, inp.billing)
        if inp.billing.mode == "live":
            self.summary.notes.append("**Stripe: LIVE — real charges.** Real cards are charged from now on.")
        elif inp.billing.mode == "test" and cfg.dome_env == "production":
            self.summary.notes.append(
                "**Production is in Stripe Test mode**: anyone who visits can get Pro or Pro Plus with a test card, "
                "for free. Keep this short. Before switching to Live mode, cancel every test subscription in Stripe "
                "(Test mode) and check those accounts show Free: after the switch the live key cannot see test "
                "subscriptions, so one left over would keep its plan (docs/LAUNCH_SETUP.md section 11A, step 8)."
            )
        if cfg.dome_env == "production":
            self.summary.notes += ["Still not done before production can be called ready:"] + PRODUCTION_NOT_READY

    def page_problems(self, base: str, *, production: bool) -> list[str]:
        try:
            page = http_get(base + "/")
        except HttpFailure as exc:
            return [f"the web page did not load ({exc.kind})"]
        problems = []
        if page.status != 200 or not page.headers.get("content-type", "").startswith("text/html"):
            problems.append(f"the web page answered HTTP {page.status}")
        if "content-security-policy" not in page.headers or page.headers.get("x-content-type-options") != "nosniff":
            problems.append("security headers are missing")
        if "noindex" not in page.headers.get("x-robots-tag", ""):
            problems.append("the fly.dev address is missing its do-not-index header")
        if production and "strict-transport-security" not in page.headers:
            problems.append("HSTS is missing")
        return problems


def billing_mode_of(base: str) -> str:
    """What the running app says about billing (GET /v1/plans, public): "off", "test" or "live"; otherwise a short
    reason. Values are never part of the answer."""
    try:
        result = http_get(base + "/v1/plans")
    except HttpFailure as exc:
        return f"no answer: {exc.kind}"
    body = result.json() if result.status == 200 else None
    if not isinstance(body, dict):
        return f"HTTP {result.status}"
    mode = body.get("billing_mode")
    enabled = body.get("billing_enabled")
    if mode not in BILLING_MODE_TEXT or not isinstance(enabled, bool) or enabled != (mode != "off"):
        return "an unexpected answer"
    return str(mode)


def login_problems(base: str, cfg: EnvConfig, client_id: str, authorization_endpoint: str) -> list[str]:
    """The app's own sign-in start: it must fetch the provider's discovery document from inside Fly and send the
    visitor there with this client id and callback. The redirect is not followed and sign-in is not attempted
    (it leaves one unfinished sign-in attempt row, which expires by itself)."""
    try:
        result = http_get(base + "/v1/auth/login", follow_redirects=False)
    except HttpFailure as exc:
        return [f"/v1/auth/login did not answer ({exc.kind})"]
    if result.status != 303:
        why = (
            " (the app could not reach the sign-in provider from Fly, or its sign-in settings are wrong)"
            if result.status in (500, 502, 503)
            else ""
        )
        return [f"/v1/auth/login answered HTTP {result.status} instead of sending visitors to sign in{why}"]
    target = urlsplit(result.headers.get("location", ""))
    expected = urlsplit(authorization_endpoint)
    if (target.scheme, target.netloc, target.path) != (expected.scheme, expected.netloc, expected.path):
        return [
            f"/v1/auth/login sends visitors to {target.scheme}://{target.netloc}{target.path} instead of the sign-in "
            f"provider's {authorization_endpoint} (check DOME_OIDC_ISSUER)"
        ]
    query = parse_qs(target.query)
    problems = []
    if query.get("client_id") != [client_id]:
        problems.append("the app's sign-in request carries a different client id than the variable DOME_OIDC_CLIENT_ID")
    if query.get("redirect_uri") != [cfg.callback_url]:
        shown = ", ".join(query.get("redirect_uri", [])) or "none"
        problems.append(f"the app's sign-in request returns visitors to {shown} instead of {cfg.callback_url}")
    return problems


def websocket_problems(host: str) -> list[str]:
    """A WebSocket upgrade through Fly's proxy must reach the app. Without a PC token the app refuses it with its
    own HTTP 401 (cloud-api/dome_api/relay/ws_http.py); a 502, 404 or proxy error means the relay is unreachable."""
    try:
        status = websocket_status(host, "/ws/agent")
    except HttpFailure as exc:
        return [f"a WebSocket upgrade to /ws/agent could not connect ({exc.kind})"]
    if status in (101, 401):
        return []
    answer = f"HTTP {status}" if status else "something that is not a valid HTTP answer"
    return [
        f"a WebSocket upgrade to /ws/agent answered {answer} instead of the app's own refusal (HTTP 401), so PCs "
        "could not connect to the relay"
    ]


def permission_hint(result: Result) -> str:
    text = (result.stdout + " " + result.stderr).lower()
    if "require updated tokens" in text or "macaroon" in text:
        return "this token type is not accepted; create an organization token on the Fly dashboard's Tokens page"
    if any(w in text for w in ("unauthorized", "not authorized", "forbidden", "permission", "401", "403")):
        return "Fly refused this token for that action"
    if "could not find organization" in text or "organization not found" in text:
        return "Fly does not know the organization in the variable FLY_ORG (default 'personal')"
    return ""


def dashboard_database_steps(inp: Inputs) -> list[str]:
    """Fly Managed Postgres by hand (fly-managed environments only)."""
    cfg = inp.cfg
    return [
        f"On fly.io, open the dashboard (https://fly.io/dashboard), then **Managed Postgres** > **Create new "
        f"cluster**: name `{cfg.database_cluster}`, region **{cfg.region}** (Ashburn, Virginia), plan "
        f"**{cfg.database_plan}**, storage **{cfg.database_volume_gb} GB**, PostgreSQL **{cfg.pg_major_version}**. "
        "This costs about $41 a month. Wait until it says ready.",
        f"Run Deploy for **{cfg.name}** again (without ticking create): it connects that cluster by itself. "
        f"If it still cannot, open the cluster's **Connect** tab, choose the app **{inp.app}** and click **Set "
        "secret and deploy** (if the app is not in the list yet, run Deploy once more first).",
    ]


def process_group(machine: Mapping[str, Any]) -> str:
    config = field_of(machine, "config")
    metadata = field_of(config, "metadata") if isinstance(config, dict) else None
    group = field_of(metadata, "fly_process_group") if isinstance(metadata, dict) else None
    return str(group) if group else "app"


def dns_records(
    cfg: EnvConfig, app: str, detail: Mapping[str, Any] | None, addresses: Iterable[str]
) -> list[tuple[str, str, str]]:
    """The Cloudflare records to add: from Fly's certificate requirements, else from the app's addresses."""
    req = field_of(detail, "dns_requirements") if isinstance(detail, Mapping) else None
    req = req if isinstance(req, dict) else {}
    a = [x for x in (field_of(req, "a") or []) if isinstance(x, str)]
    aaaa = [x for x in (field_of(req, "aaaa") or []) if isinstance(x, str)]
    cname = field_of(req, "cname")
    if cfg.dns_name != "@" and isinstance(cname, str) and cname:
        return [("CNAME", cfg.dns_name, cname)]
    if cfg.dns_name != "@" and detail is None:
        return [("CNAME", cfg.dns_name, f"{app}.fly.dev")]
    ips = sorted(addresses)
    a = a or [x for x in ips if ipaddress.ip_address(x).version == 4]
    aaaa = aaaa or [x for x in ips if ipaddress.ip_address(x).version == 6]
    records = [("A", cfg.dns_name, x) for x in a[:1]] + [("AAAA", cfg.dns_name, x) for x in aaaa[:1]]
    if not records and cfg.dns_name != "@":
        records = [("CNAME", cfg.dns_name, f"{app}.fly.dev")]
    return records


# ======================================================================================================
# gate and status (no secrets, no flyctl)
# ======================================================================================================


def run_gate(env: Mapping[str, str], out: TextIO, envs: Mapping[str, EnvConfig]) -> int:
    summary = Summary(title="Deploy: checks before approval")
    try:
        check_ref(env)
        cfg = read_environment_choice(env, envs)
        summary.title = f"{cfg.title} deploy: checks before approval"
        sha = read_sha(env)
        app_name_for(cfg, env)
        check_distinct_apps(env, envs)
        check_no_repository_secrets(env)
        summary.step("Repository secrets", "none of the deploy secrets is a repository secret")
        default_branch = env.get("DEFAULT_BRANCH", "").strip()
        for name in ENVIRONMENTS:  # both: either token can reach both apps (docs/SECURITY.md section 6)
            summary.step(f"GitHub Environment {name}", check_environment_protection(env, name, default_branch))
        own_run = (
            "Approve only if you started this run yourself in the last few minutes; otherwise click Reject and "
            "tell an engineer."
        )
        if cfg.requires_staging_commit:
            production_gate(env, envs, sha, summary)
            summary.headline = "Staging runs this commit and is healthy: production may be approved"
        else:
            summary.headline = "Staging needs no earlier environment: approve the deploy when GitHub asks"
        summary.next_steps.append(own_run)
    except Stop as stop:
        summary.headline = HEADLINES[stop.kind]
        summary.problem = stop.message
        summary.next_steps = stop.next_steps
        write_summary(summary, env, out)
        return 1
    write_summary(summary, env, out)
    return 0


def run_status(env: Mapping[str, str], out: TextIO, envs: Mapping[str, EnvConfig]) -> int:
    summary = Summary(title="Status")
    try:
        cfg = read_environment_choice(env, envs)
        summary.title = f"{cfg.title} status"
        app = app_name_for(cfg, env)
        check_distinct_apps(env, envs)
        sha = env.get("GITHUB_SHA", "").strip().lower()
        base = f"https://{app}.fly.dev"
        health = wake_health(base)  # a sleeping staging machine starts on this request
        summary.step(f"{app}.fly.dev /healthz", describe_health(health, sha))
        if cfg.sleeps_when_idle:
            summary.notes.append(
                f"{cfg.title} sleeps when nobody uses it; this check may have woken it, which takes a few seconds."
            )
        if (not health.reachable and health.error == "dns") or health.status_code == 404:
            summary.headline = f"{cfg.title} is not deployed yet"
            summary.next_steps.append(f"Run Deploy with environment **{cfg.name}** and action **deploy**.")
            write_summary(summary, env, out)
            return 0
        if not health.reachable:
            summary.headline = f"{cfg.title} could not be reached at {base} ({health.error})"
            summary.next_steps.append(
                "Run Deploy with action **status** again in a few minutes. If it was never deployed, run Deploy with "
                "action **deploy**; if it was, and this persists, ask an engineer."
            )
            write_summary(summary, env, out)
            return 1
        if health.status_code != 200 or health.body.get("status") != "ok":
            summary.headline = f"{cfg.title} is deployed but not healthy ({describe_health(health, sha)})"
            if external_database_down(cfg, health):
                summary.next_steps.append(
                    external_database_down_step(cfg)
                    + " If they are, staging works again next month (sooner only on Neon's paid plan: ask an "
                    "engineer), and until then no new version can reach production (production itself keeps running)."
                )
            summary.next_steps.append(
                "Run Deploy with action **status** again in a few minutes; if it stays like this, ask an engineer "
                "(Fly dashboard > the app > Monitoring shows its log)."
            )
            write_summary(summary, env, out)
            return 1
        if cfg.dome_env == "production":
            staging_app = app_name_for(envs["staging"], env)
            staging = get_health(f"https://{staging_app}.fly.dev")
            summary.step("Staging commit", (staging.commit or "unknown")[:12])
            summary.step("Production commit", (health.commit or "unknown")[:12])
            if external_database_down(envs["staging"], staging):
                summary.notes.append(
                    external_database_down_step(envs["staging"])
                    + " Production keeps running, but no new version reaches it until staging works again."
                )
        billing = billing_mode_of(base)
        summary.step(
            "Billing (Stripe)",
            f"**{BILLING_MODE_TEXT[billing]}**" if billing in BILLING_MODE_TEXT else f"unknown (/v1/plans: {billing})",
        )
        summary.step("Screen view", screen_view_status_text(health))
        try:
            app_addresses = set(resolve(f"{app}.fly.dev"))
        except OSError:
            app_addresses = set()
        running_commit = health.commit or ""
        report = check_custom_host(cfg.hostname, app_addresses, health.commit, production=cfg.dome_env == "production")
        summary.step(cfg.hostname, host_result_text(cfg, report))
        records = dns_records(cfg, app, None, app_addresses)
        forwarded_bad = health.body.get("forwarded_client") not in (None, "ok")
        host_bad = report.state == "unhealthy" or (report.state == "wrong_commit" and cfg.dome_env == "production")
        if report.state == "live" and health.body.get("forwarded_client") == "ok":
            summary.headline = (
                f"{cfg.title} is live at https://{cfg.hostname} (commit {running_commit[:12] or 'unknown'})"
            )
            summary.next_steps.append(f"Open https://{cfg.hostname} and sign in to check sign-in yourself.")
        else:
            summary.headline = f"{cfg.title} is running at {base}; {host_result_text(cfg, report)}"
            summary.next_steps += host_next_steps(cfg, report, records)
            if forwarded_bad:
                summary.headline += "; client-address self-check not ok"
                summary.next_steps.append(
                    "The client-address self-check is not ok: ask an engineer (deploy/README.md 'Client addresses')."
                )
        summary.notes.append("This run only looked; it has no secrets and changed nothing.")
        if forwarded_bad or host_bad:
            # red in the Actions list: something is deployed and wrong (not merely waiting for DNS or a certificate)
            write_summary(summary, env, out)
            return 1
    except Stop as stop:
        summary.headline = HEADLINES[stop.kind]
        summary.problem = stop.message
        summary.next_steps = stop.next_steps
        write_summary(summary, env, out)
        return 1
    write_summary(summary, env, out)
    return 0


# ======================================================================================================
# entry point
# ======================================================================================================


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None, out: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    environ: Mapping[str, str] = os.environ if env is None else env
    stream = sys.stdout if out is None else out
    if len(args) != 1 or args[0] not in ("deploy", "status", "gate"):
        print("usage: dome_deploy.py {deploy|status|gate}  (configured by environment variables)", file=sys.stderr)
        return 2
    envs = load_environments()
    if args[0] == "deploy":
        return Deployer(environ, stream, envs).run()
    if args[0] == "gate":
        return run_gate(environ, stream, envs)
    return run_status(environ, stream, envs)


if __name__ == "__main__":
    raise SystemExit(main())
