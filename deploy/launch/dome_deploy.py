#!/usr/bin/env python3
"""DoMe one-button deploy to Fly.io: everything the "Deploy" GitHub Actions workflow does.

    dome_deploy.py gate     production only: refuse unless staging runs this commit, healthy (no secrets)
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
* Child processes do not inherit DOME_OIDC_CLIENT_SECRET or DOME_FALLBACK_DATABASE_URL; flyctl gets
  FLY_API_TOKEN because it needs it.
* The summary (``$GITHUB_STEP_SUMMARY``) names secrets and says what happened to them; it never holds a value.

Nothing in this program has run against a live Fly.io account yet. flyctl output shapes were read from the
flyctl v0.4.110 source and are parsed leniently; anything unexpected stops the run with a plain message
before it can change something it does not understand.
"""

from __future__ import annotations

import base64
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
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
ENVIRONMENTS_FILE = HERE / "environments.toml"
FLY_CONFIG = "deploy/fly.toml"
DOCKERFILE = "deploy/Dockerfile"

ENVIRONMENTS = ("staging", "production")
# Environment variables a child process never receives (flyctl reads FLY_API_TOKEN itself and keeps it).
CHILD_ENV_DROP = frozenset({"DOME_OIDC_CLIENT_SECRET", "DOME_FALLBACK_DATABASE_URL"})
GENERATED_SECRETS = ("DOME_SESSION_SECRET", "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64")
DATABASE_SECRET_NAMES = ("DATABASE_URL", "DOME_DATABASE_URL")
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
    database_cluster: str
    database_plan: str
    database_volume_gb: int
    pg_major_version: int
    requires_staging_commit: bool

    @property
    def title(self) -> str:
        return self.name.capitalize()

    @property
    def callback_url(self) -> str:
        return self.public_origin + "/v1/auth/callback"


def load_environments(path: Path = ENVIRONMENTS_FILE) -> dict[str, EnvConfig]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, EnvConfig] = {}
    for name in ENVIRONMENTS:
        section = data[name]
        out[name] = EnvConfig(name=name, **section)
    return out


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


def http_get(url: str, *, timeout: float = HTTP_TIMEOUT, headers: Mapping[str, str] | None = None) -> HttpResult:
    """GET with TLS verification. Non-2xx answers are results, not exceptions."""
    request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")  # noqa: S310 - https only
    if not url.startswith("https://"):
        raise ValueError("https only")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
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
                text=True,
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
            text=True,
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


@dataclass
class Inputs:
    cfg: EnvConfig
    app: str
    sha: str
    org: str
    issuer: str
    client_id: str
    client_secret: str
    fallback_database_url: str
    create_database: bool
    support_url: str
    support_expectation: str


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

    fallback_db = env.get("DOME_FALLBACK_DATABASE_URL", "").strip()
    if fallback_db:
        book.add(fallback_db, mask=False)
        if not fallback_db.startswith(("postgres://", "postgresql://")) or not importable(fallback_db):
            problems.append(
                "The DATABASE_URL secret does not look like a database address (it should start with "
                "postgresql:// and have no spaces). Copy the pooled URL again from Fly > Managed Postgres > "
                "your cluster > Connect."
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

    next_steps: list[str] = []
    if missing_secrets:
        next_steps.append(
            f"Open {page}, choose **{cfg.name}**, and under **Environment secrets** add: "
            + ", ".join(f"`{n}`" for n in missing_secrets)
            + ". They must be Environment secrets of this environment, not repository secrets."
        )
    if missing_vars:
        next_steps.append(
            f"Open {page}, choose **{cfg.name}**, and under **Environment variables** add: "
            + ", ".join(f"`{n}`" for n in missing_vars)
            + "."
        )
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
        fallback_database_url=fallback_db,
        create_database=env.get("CREATE_DATABASE", "").strip().lower() == "true",
        support_url=support_url,
        support_expectation=expectation,
    )


# ======================================================================================================
# Checks that need no secrets: OIDC discovery, healthz, DNS/TLS
# ======================================================================================================


def check_discovery(issuer: str) -> None:
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


def production_gate(env: Mapping[str, str], envs: Mapping[str, EnvConfig], sha: str, summary: Summary) -> None:
    staging = envs["staging"]
    staging_app = app_name_for(staging, env)
    base = f"https://{staging_app}.fly.dev"
    health = get_health(base)
    attempts = 1
    while not health.reachable and attempts < 3:
        sleep(SMOKE_INTERVAL)
        health = get_health(base)
        attempts += 1
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
    raise Stop(
        "you",
        f"Production only receives a commit that staging already runs and that is healthy there. Staging "
        f"({base}) is {describe_health(health, sha)}; this run is commit {sha[:12]}.",
        next_steps=[
            "Run Deploy with environment **staging** for this same commit and wait for it to finish green.",
            "Then run Deploy with environment **production** again.",
        ],
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
            f"In {zone_page}, first delete any existing A, AAAA or CNAME record named **{cfg.dns_name}**.",
            f"Then add: {record_text}. {grey} on each. Save.",
            "About 10 minutes later, run Deploy with action **status**.",
        ]
    if report.state == "proxied":
        return [
            f"In {zone_page}, the record(s) for **{cfg.dns_name}** are proxied (orange cloud). Click each and switch "
            f"Proxy status to **DNS only** (grey cloud): Fly cannot issue the certificate through Cloudflare's proxy.",
            "About 10 minutes later, run Deploy with action **status**.",
        ]
    if report.state == "elsewhere":
        return [
            f"{cfg.hostname} points at {', '.join(report.addresses)}, which is not this app. In {zone_page}, replace "
            f"the record(s) named **{cfg.dns_name}** with: {record_text or 'the records a Deploy run lists'}. {grey}.",
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
]


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
                "engineer",
                f"Could not read {what} from Fly ({'timed out' if result.timed_out else f'exit {result.code}'})."
                + (f" {hint}." if hint else ""),
            )
        return items

    def secret_names(self, app: str) -> set[str]:
        items = self.fly_list(f"the secrets of {app}", "secrets", "list", "-a", app, "--json")
        names = {str(field_of(i, "name") or "") for i in items}
        if "" in names:
            raise Stop("engineer", f"Fly listed a secret of {app} without a name; nothing was generated.")
        return names

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
        try:
            check_ref(self.env)
            cfg = read_environment_choice(self.env, self.envs)
            self.summary.title = f"{cfg.title} deploy"
            inputs = read_inputs(self.env, cfg, self.book, self.summary)
            self.summary.step("Inputs", "all present")
            self.deploy(inputs)
        except Stop as stop:
            self.summary.headline = HEADLINES[stop.kind]
            self.summary.problem = stop.message
            self.summary.next_steps = stop.next_steps + self.summary.next_steps
            said = stop.message + " ".join(stop.next_steps)
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
        check_discovery(inp.issuer)
        self.summary.step("Sign-in provider (OIDC discovery)", f"{inp.issuer} answers")

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
        has_database = bool(names & set(DATABASE_SECRET_NAMES))
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
        database_plan = "existing"
        if not has_database:
            cluster, database_plan = self.choose_database(inp)
        if has_database and inp.fallback_database_url:
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
        if database_plan == "fallback":
            staged.append(("DATABASE_URL", inp.fallback_database_url))
        self.log("Storing secrets in Fly (staged; they reach the app with this deploy)")
        self.stage(app, staged)
        self.summary.step("DOME_OIDC_CLIENT_SECRET", "updated from GitHub")

        self.build_and_deploy(inp)
        self.ensure_one_machine(app)
        records = self.ensure_certificate(inp, addresses)
        self.smoke(inp, addresses, records)

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
        clusters = self.list_clusters(inp)
        if clusters is not None:
            for c in clusters:
                if field_of(c, "name") == inp.cfg.database_cluster:
                    return c, "attach"
            if inp.create_database:
                return None, "create"
        if inp.fallback_database_url:
            return None, "fallback"
        if clusters is None:
            raise Stop(
                "you",
                "This environment has no database yet, and Fly would not let this token create or list Managed "
                "Postgres databases. Nothing was created or changed.",
                next_steps=dashboard_database_steps(inp),
            )
        raise Stop(
            "you",
            "This environment has no database yet. Fly charges about $38 a month for one (Managed Postgres, "
            "Basic plan). Nothing was created or changed.",
            next_steps=[
                "If you agree to that cost, run Deploy again with **create the database** ticked.",
            ],
        )

    def ensure_database(self, inp: Inputs, cluster: dict[str, Any] | None, plan: str) -> str:
        """Returns the plan that ended up applying: "existing", "attached" or "fallback" (stage the GitHub secret)."""
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
        if inp.fallback_database_url:
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
            raise Stop("you" if hint else "engineer", "Fly did not create the app" + (f" ({hint})." if hint else "."))
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
        argv = [
            "flyctl",
            "deploy",
            "--config",
            FLY_CONFIG,
            "--app",
            app,
            "--image",
            image,
            "--local-only",
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
        deployed = self.runner.stream(argv, timeout=min(DEPLOY_TIMEOUT, self.remaining() - 4 * 60.0))
        self.mutated = True
        if not deployed.ok:
            raise Stop(
                "engineer",
                "Fly did not finish the deploy (see the log above: a failing database migration or a start-up "
                "configuration error are the usual causes). The previous version, if any, keeps running.",
            )
        self.summary.step("Deploy", f"done ({app}, migrations ran first)")

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

    def smoke(self, inp: Inputs, addresses: set[str], records: list[tuple[str, str, str]]) -> None:
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
        if problems:
            raise Stop("engineer", "The new version is deployed but does not pass its checks: " + " ".join(problems))

        report = check_custom_host(cfg.hostname, addresses, inp.sha, production=cfg.dome_env == "production")
        self.summary.step(cfg.hostname, host_result_text(cfg, report))
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
    cfg = inp.cfg
    return [
        f"On fly.io, open the dashboard, then **Managed Postgres** > **Create new cluster**: name "
        f"`{cfg.database_cluster}`, region **{cfg.region}** (Ashburn, Virginia), plan **{cfg.database_plan}**. "
        "Wait until it says ready.",
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
        if cfg.requires_staging_commit:
            production_gate(env, envs, sha, summary)
            summary.headline = "Staging runs this commit and is healthy: production may be approved"
        else:
            summary.headline = "Staging needs no earlier environment: approve the deploy when GitHub asks"
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
        sha = env.get("GITHUB_SHA", "").strip().lower()
        base = f"https://{app}.fly.dev"
        health = get_health(base)
        summary.step(f"{app}.fly.dev /healthz", describe_health(health, sha))
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
            return 0
        if health.status_code != 200 or health.body.get("status") != "ok":
            summary.headline = f"{cfg.title} is deployed but not healthy ({describe_health(health, sha)})"
            summary.next_steps.append(
                "Run Deploy with action **status** again in a few minutes; if it stays like this, ask an engineer "
                "(Fly dashboard > the app > Monitoring shows its log)."
            )
            write_summary(summary, env, out)
            return 0
        if cfg.dome_env == "production":
            staging_app = app_name_for(envs["staging"], env)
            staging = get_health(f"https://{staging_app}.fly.dev")
            summary.step("Staging commit", (staging.commit or "unknown")[:12])
            summary.step("Production commit", (health.commit or "unknown")[:12])
        try:
            app_addresses = set(resolve(f"{app}.fly.dev"))
        except OSError:
            app_addresses = set()
        running_commit = health.commit or ""
        report = check_custom_host(cfg.hostname, app_addresses, health.commit, production=cfg.dome_env == "production")
        summary.step(cfg.hostname, host_result_text(cfg, report))
        records = dns_records(cfg, app, None, app_addresses)
        if report.state == "live" and health.body.get("forwarded_client") == "ok":
            summary.headline = (
                f"{cfg.title} is live at https://{cfg.hostname} (commit {running_commit[:12] or 'unknown'})"
            )
            summary.next_steps.append(f"Open https://{cfg.hostname} and sign in to check sign-in yourself.")
        else:
            summary.headline = f"{cfg.title} is running at {base}; {host_result_text(cfg, report)}"
            summary.next_steps += host_next_steps(cfg, report, records)
            if health.body.get("forwarded_client") not in (None, "ok"):
                summary.next_steps.append(
                    "The client-address self-check is not ok: ask an engineer (deploy/README.md 'Client addresses')."
                )
        summary.notes.append("This run only looked; it has no secrets and changed nothing.")
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
