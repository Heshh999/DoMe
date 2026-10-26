"""Build DoMe for Windows: PyInstaller folder, then the Inno Setup installer.

Usage (Windows, from pc-agent, after ``uv sync --locked --extra build``)::

    uv run --no-sync python packaging/installer/build_installer.py [--iscc PATH] [--release]

Steps:

1. Reads the version from ``pyproject.toml`` (X.Y.Z) and checks it equals ``dome_agent.__version__``.
2. Runs PyInstaller on ``packaging/dome-agent.spec``: ``dist/DoMe/`` holds ``DoMe.exe``,
   ``dome-native-host.exe`` and ``_internal/``. Screen view is built in when the environment has the ``screen``
   extra (aiortc, av; the workflows install it only once ``licence_gate.py shipping-av`` says the LGPL av wheel
   is pinned for Windows). Then writes ``THIRD_PARTY_NOTICES.txt`` and ``licenses/`` next to the programs
   (``licence_gate.py bundle-notices``: the hand-written Part 1 in ``packaging/THIRD_PARTY_NOTICES.txt`` and
   every installed package's own licence files).
3. Checks that folder: both programs, the protocol contract, the tray icon, the notices and licence texts,
   pystray (LGPL-3.0) as replaceable ``.py`` files, and NO test fixtures or private keys (the old spec shipped
   ``shared/protocol/fixtures/test-controller-key.pem``). Where PyInstaller is importable it also reads the
   programs' own archives: ``aiortc.contrib`` and ``dome_agent.testing`` are never inside, ``DoMe.exe`` always
   carries the ``screen-worker`` dispatch, aiortc is inside exactly when Screen view is (with
   ``aiortc/rtcrtpsender.py`` as source for the worker's PLI hook check), and the browser helper carries no
   Screen view code. A Windows build with Screen view also carries windows-capture's native module
   (``_internal/windows_capture/windows_capture.pyd``, the Windows.Graphics.Capture fallback), and no build carries
   ``cv2`` or ``numpy``. The licence gate on the folder and the worker's ``--self-test`` are separate workflow
   steps (``licence_gate.py check --frozen`` and ``screen-self-test``).
4. Compiles ``packaging/installer/DoMe.iss`` with ISCC (Inno Setup 6) into
   ``dist/installer/DoMe-Setup-<version>.exe`` and writes ``SHA256SUMS`` and ``BUILD-INFO.txt`` next to it.
   The build number (``GITHUB_RUN_NUMBER``) and commit go into the file's version information and
   BUILD-INFO.txt, so two builds of the same version can be told apart. The commit is ``BUILD_COMMIT``
   when set (the workflow passes the branch commit of a pull request: ``GITHUB_SHA`` is then GitHub's
   temporary merge commit, which is on no branch), else ``GITHUB_SHA``, else ``git rev-parse HEAD``; when
   ``GITHUB_SHA`` differs, BUILD-INFO.txt names it too, because that merge is what was built.
   ``BUILD_EVENT`` and ``BUILD_BRANCH`` (set by the workflow) say where the build came from.

By default this is a DEVELOPMENT build: its Setup accepts ``/ApiUrl=https://...`` (a staging server) and
``/ExtensionId=<32 a-p>`` (a development browser add-on) and remembers a staging address for upgrades.
``--release`` builds a Setup that accepts neither and always writes https://domeremote.com. A release is
never unsigned: ``--release`` needs ``--sign-artifact-signing`` or ``--sign-thumbprint`` and ``--publisher``.
While ``dome_agent.bridge.manifest.PRODUCTION_EXTENSION_IDS`` is empty (no store listing yet) ``--release``
also needs ``--without-browser-addon`` (``/DNoAddon``): Setup then registers no browser bridge and says
that YouTube control in Chrome or Edge is not part of this version (a release Setup must never re-register
a development add-on left on the PC by a test install). ``--pyinstaller-only`` stops after step 3 (it
also works on Linux, to check the spec).

Signing goes through Inno Setup's ``SignTool`` directive, so one command signs DoMe.exe,
dome-native-host.exe, Setup and the uninstaller:

* ``--sign-artifact-signing METADATA_JSON --dlib Azure.CodeSigning.Dlib.dll``: Azure Artifact Signing
  (formerly Trusted Signing) through signtool's ``/dlib``/``/dmdf`` with Microsoft's RFC 3161 timestamp
  (the certificates live 3 days, so the timestamp is what keeps a signature valid). Authentication is
  the dlib's own (in the release workflow: the Azure CLI login of an OIDC token). Used by
  ``.github/workflows/release.yml``.
* ``--sign-thumbprint`` (or ``SIGN_THUMBPRINT``): a certificate already in the user's certificate store,
  by its SHA-1 thumbprint.

After ISCC every signature is checked with ``signtool verify /pa /v`` (exit code 0 and "Successfully
verified"); ``signed=true`` is reported only then. No password or key ever appears on a command line.
A release build also writes ``latest.json`` (the version information the agent's update check and the
website read; signed afterwards by ``packaging/release/release_files.py sign``) and ``RELEASE-NOTES.md``.

Results for GitHub Actions: ``GITHUB_OUTPUT`` gets version, setup (absolute path), sha256, signed,
build_tag and artifact_name, and for a release also manifest, browser_addon and publisher;
``GITHUB_STEP_SUMMARY`` gets a short note.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

PC_AGENT = Path(__file__).resolve().parents[2]
REPO = PC_AGENT.parent
SPEC = PC_AGENT / "packaging" / "dome-agent.spec"
ISS = PC_AGENT / "packaging" / "installer" / "DoMe.iss"
ICON = REPO / "brand" / "exports" / "windows" / "dome.ico"
MANIFEST_MODULE = PC_AGENT / "dome_agent" / "bridge" / "manifest.py"
LICENCE_GATE = PC_AGENT / "packaging" / "licence_gate.py"
NOTICES_HEAD = PC_AGENT / "packaging" / "THIRD_PARTY_NOTICES.txt"
LICENCES = PC_AGENT / "packaging" / "licenses"
# The Windows.Graphics.Capture fallback's native module (dome-agent.spec bundles it alone, without the package's
# Python wrapper, which needs cv2 and numpy; neither may be in the folder: OpenCV carries its own FFmpeg).
WGC_NATIVE = Path("_internal") / "windows_capture" / "windows_capture.pyd"
NEVER_SHIPPED = ("cv2", "numpy")
TIMESTAMP_URL = "http://timestamp.digicert.com"
# Azure Artifact Signing's timestamp service (vendor docs, how-to-signing-integrations, read 2026-10-20).
ARTIFACT_SIGNING_TIMESTAMP_URL = "http://timestamp.acs.microsoft.com"
DEFAULT_REPO = "Heshh999/DoMe"
REPO_RE = re.compile(r"\A[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}\Z")
# SignTool for the Artifact Signing dlib must come from Windows SDK 10.0.22621 or later (the 20348 SDK is
# not supported, per the same vendor page).
MIN_SIGNTOOL_SDK_BUILD = 22621
NO_ADDON_NOTE = "not included in this version (no store listing yet)"
VERSION_RE = re.compile(r"\A\d+\.\d+\.\d+\Z")
THUMBPRINT_RE = re.compile(r"\A[0-9A-Fa-f]{40}\Z")
LABEL_RE = re.compile(r"[^A-Za-z0-9._/+-]")


def fail(message: str) -> int:
    print(f"build_installer: {message}", file=sys.stderr)
    return 1


def read_version() -> str:
    """X.Y.Z from pyproject.toml, which must equal dome_agent.__version__."""
    project = tomllib.loads((PC_AGENT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = str(project["version"])
    if not VERSION_RE.match(version):
        raise SystemExit(f"build_installer: pyproject.toml version {version!r} is not X.Y.Z")
    init = (PC_AGENT / "dome_agent" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"', init, re.MULTILINE)
    if match is None or match.group(1) != version:
        found = match.group(1) if match else "missing"
        raise SystemExit(f"build_installer: pyproject.toml says {version}, dome_agent.__version__ says {found}")
    return version


def default_iscc() -> str:
    env = os.environ.get("ISCC", "")
    if env:
        return env
    found = shutil.which("iscc")
    if found:
        return found
    return str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe")


def default_signtool() -> str:
    kits = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Kits" / "10" / "bin"
    candidates = sorted(
        kits.glob("*/x64/signtool.exe"), key=lambda p: [int(x) for x in re.findall(r"\d+", p.parts[-3])]
    )
    return str(candidates[-1]) if candidates else "signtool.exe"


def production_extension_ids() -> tuple[str, ...]:
    """``PRODUCTION_EXTENSION_IDS`` as written in ``dome_agent/bridge/manifest.py`` (read, not imported)."""
    tree = ast.parse(MANIFEST_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id == "PRODUCTION_EXTENSION_IDS" and value is not None:
            return tuple(str(v) for v in ast.literal_eval(value))
    raise SystemExit(f"build_installer: PRODUCTION_EXTENSION_IDS not found in {MANIFEST_MODULE}")


def label(value: str) -> str:
    """A short ASCII label for BUILD-INFO.txt (branch names are free text)."""
    return LABEL_RE.sub("_", value.strip())[:100]


def git_commit() -> str:
    for name in ("BUILD_COMMIT", "GITHUB_SHA"):
        env = os.environ.get(name, "").strip()
        if env:
            return label(env)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - git from PATH; only a label for the build
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def exe_name(stem: str) -> str:
    return f"{stem}.exe" if os.name == "nt" else stem


def licence_gate() -> ModuleType:
    """``packaging/licence_gate.py`` (a script, not a package): loaded once by path."""
    loaded = sys.modules.get("licence_gate")
    if loaded is not None and getattr(loaded, "__file__", None) == str(LICENCE_GATE):
        return loaded
    import importlib.util

    spec = importlib.util.spec_from_file_location("licence_gate", LICENCE_GATE)
    if spec is None or spec.loader is None:
        raise SystemExit(f"build_installer: cannot load {LICENCE_GATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["licence_gate"] = module  # dataclasses look their module up while the file executes
    spec.loader.exec_module(module)
    return module


def write_notices(app: Path) -> None:
    """``THIRD_PARTY_NOTICES.txt`` and ``licenses/`` for the folder, from this (the build) environment."""
    missing = licence_gate().bundle_notices(NOTICES_HEAD, LICENCES, app)
    for item in missing:
        print(f"build_installer: WARNING: no licence text found for {item} (THIRD_PARTY_NOTICES.txt says so)")
    print(f"notices written: {app / 'THIRD_PARTY_NOTICES.txt'} ({len(missing)} licence text(s) not found)")


def screen_view_included(app: Path) -> bool:
    """Screen view is in the folder when PyAV is (``_internal/av``); the spec adds both together."""
    return (app / "_internal" / "av").is_dir()


def frozen_modules(program: Path) -> set[str] | None:
    """Every entry of a PyInstaller program's archive and of its PYZ (module names), or None where PyInstaller
    is not installed (the release sign job reuses a folder the build job already checked)."""
    try:
        from PyInstaller.archive.readers import pkg_archive_contents  # type: ignore[import-not-found]
    except ImportError:
        return None
    return set(pkg_archive_contents(str(program)))


def _under(modules: Iterable[str], package: str) -> list[str]:
    return sorted(m for m in modules if m == package or m.startswith(package + "."))


def check_frozen_modules(agent: set[str], host: set[str], *, screen: bool) -> list[str]:
    """Problems with what the two programs carry (design §5.1, §17.2): never aiortc.contrib or the test doubles;
    the screen-worker dispatch always; aiortc exactly when Screen view is built in; no Screen view in the host."""
    problems: list[str] = []
    for package in ("aiortc.contrib", "dome_agent.testing"):
        if _under(agent, package):
            problems.append(f"DoMe must not carry {package}: {_under(agent, package)[:5]}")
    if "dome_agent.screen.worker.main" not in agent:
        problems.append("DoMe lacks dome_agent.screen.worker.main (the screen-worker dispatch)")
    if screen:
        for module in ("aiortc.rtcrtpsender", "dome_agent.screen.worker.rtc", "dome_agent.screen.worker.track"):
            if module not in agent:
                problems.append(f"Screen view is built in but DoMe lacks {module}")
    elif _under(agent, "aiortc"):
        problems.append("Screen view is left out but DoMe carries aiortc")
    for package in ("aiortc", "av", "dome_agent.screen"):
        if _under(host, package):
            problems.append(f"the browser helper must not carry {package}")
    return problems


def run_pyinstaller(dist: Path, work: Path) -> None:
    if not ICON.is_file():
        raise SystemExit(f"build_installer: brand icon missing: {ICON}")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(dist)]
    cmd += ["--workpath", str(work), str(SPEC)]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def check_folder(app: Path, *, windows: bool | None = None) -> list[str]:
    """Problems with the PyInstaller folder (empty when it is complete and carries no test material). ``windows``
    (default: this machine is Windows) adds the checks only a Windows build can pass (windows-capture's native module
    is a Windows-only dependency; ``--pyinstaller-only`` also runs on Linux)."""
    windows = os.name == "nt" if windows is None else windows
    problems: list[str] = []
    contract = app / "_internal" / "dome_protocol" / "_contract"
    required = [
        app / exe_name("DoMe"),
        app / exe_name("dome-native-host"),
        contract / "actions.json",
        contract / "errors.json",
        contract / "plans.json",
        contract / "version.json",
        app / "_internal" / "dome_agent" / "assets" / "tray-256.png",
    ]
    problems += [f"missing: {p}" for p in required if not p.exists()]
    if not (contract / "schemas").is_dir():
        problems.append(f"missing: {contract / 'schemas'}")
    if (contract / "fixtures").exists():
        problems.append(f"test fixtures must not be shipped: {contract / 'fixtures'}")
    for path in app.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".pem", ".key") and b"PRIVATE KEY-----" in path.read_bytes():
            problems.append(f"a private key must not be shipped: {path}")
    gate = licence_gate()
    notices = [app / gate.NOTICES_NAME, *(app / "licenses" / name for name in gate.LICENCE_FILES)]
    problems += [f"missing: {p}" for p in notices if not p.is_file()]
    if not (app / "_internal" / "pystray" / "__init__.py").is_file():
        problems.append("pystray (LGPL-3.0) must be shipped as replaceable .py files in _internal/pystray (spec)")
    screen = screen_view_included(app)
    if screen and not (app / "_internal" / "aiortc" / "rtcrtpsender.py").is_file():
        problems.append("Screen view is built in but _internal/aiortc/rtcrtpsender.py (the PLI hook check) is missing")
    if screen and windows and not (app / WGC_NATIVE).is_file():
        problems.append(
            f"Screen view is built in but {WGC_NATIVE.as_posix()} (the Windows.Graphics.Capture fallback) is missing"
        )
    for name in NEVER_SHIPPED:
        if (app / "_internal" / name).exists():
            problems.append(f"_internal/{name} must not be shipped (only windows-capture's native module is used)")
    agent, host = frozen_modules(app / exe_name("DoMe")), frozen_modules(app / exe_name("dome-native-host"))
    if agent is not None and host is not None:
        problems += check_frozen_modules(agent, host, screen=screen)
    else:
        print("PyInstaller is not installed here: the programs' archives were checked when the folder was built")
    return problems


def folder_size(app: Path) -> int:
    return sum(p.stat().st_size for p in app.rglob("*") if p.is_file())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def append(env_name: str, text: str) -> None:
    target = os.environ.get(env_name, "")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(text)


@dataclass(frozen=True)
class Signing:
    """How Inno signs: the ``/Sdomesign=`` command and the BUILD-INFO.txt line once verified."""

    kind: str  # "artifact-signing" or "thumbprint"
    command: str
    note: str


def sign_tool_command(
    kind: str,
    *,
    signtool: str,
    thumbprint: str | None = None,
    dlib: str | None = None,
    metadata: str | None = None,
    verbose: bool = False,
) -> str:
    """The value of ISCC's ``/Sdomesign=``. ``$q`` is a quote and ``$f`` the quoted file Inno signs."""
    for path in (signtool, dlib or "", metadata or ""):
        if "$" in path or '"' in path:
            raise ValueError(f"a signing path may not contain $ or a quote: {path}")
    if kind == "thumbprint":
        if not thumbprint or not THUMBPRINT_RE.match(thumbprint):
            raise ValueError("a 40-character SHA-1 thumbprint is needed")
        return f"$q{signtool}$q sign /sha1 {thumbprint} /fd sha256 /tr {TIMESTAMP_URL} /td sha256 $f"
    if kind == "artifact-signing":
        if not dlib or not metadata:
            raise ValueError("Artifact Signing needs the dlib and the metadata file")
        noisy = " /v /debug" if verbose else ""
        return (
            f"$q{signtool}$q sign{noisy} /fd SHA256 /tr {ARTIFACT_SIGNING_TIMESTAMP_URL} /td SHA256 "
            f"/dlib $q{dlib}$q /dmdf $q{metadata}$q $f"
        )
    raise ValueError(f"unknown signing kind: {kind}")


def signtool_sdk_build(signtool: str) -> int | None:
    """The Windows SDK build of a ``...\\Windows Kits\\10\\bin\\10.0.NNNNN.0\\x64\\signtool.exe`` path."""
    match = re.search(r"[\\/]10\.0\.(\d{5})\.\d+[\\/]", signtool)
    return int(match.group(1)) if match else None


def iscc_command(
    *,
    iscc: str,
    version: str,
    build_number: str,
    build_tag: str,
    app: Path,
    out_dir: Path,
    release: bool,
    no_addon: bool = False,
    sign_command: str | None = None,
) -> list[str]:
    """The ISCC command line. A development build without signing is exactly what windows-installer.yml
    has always run."""
    cmd = [
        iscc,
        f"/DAppVersion={version}",
        f"/DBuildNumber={build_number}",
        f"/DBuildTag={build_tag} (build {build_number})",
        f"/DAppFiles={app}",
        f"/DOutputDir={out_dir}",
        f"/DBrandIcon={ICON}",
    ]
    if not release:
        cmd.append("/DDevParams")
    if no_addon:
        cmd.append("/DNoAddon")
    if sign_command:
        cmd += ["/DSign", f"/Sdomesign={sign_command}"]
    cmd.append(str(ISS))
    return cmd


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def verify_signatures(signtool: str, paths: Sequence[Path], *, run: Runner = subprocess.run) -> list[str]:
    """Problems with the Authenticode signatures of ``paths`` (empty when ``signtool verify /pa /v`` exits
    0 and says "Successfully verified" for every file)."""
    problems: list[str] = []
    for path in paths:
        out = run([signtool, "verify", "/pa", "/v", str(path)], capture_output=True, text=True, errors="replace")
        if out.returncode != 0 or "Successfully verified" not in (out.stdout or ""):
            tail = ((out.stdout or "") + (out.stderr or "")).strip()[-600:]
            problems.append(f"{path.name}: signtool verify exit code {out.returncode}: {tail}")
        else:
            print(f"signature verified: {path}")
    return problems


def manifest_urls(repo: str, version: str) -> tuple[str, str]:
    """The installer's download address and the release page of ``vX.Y.Z``."""
    base = f"https://github.com/{repo}/releases"
    return f"{base}/download/v{version}/DoMe-Setup-{version}.exe", f"{base}/tag/v{version}"


def write_manifest(
    out_dir: Path,
    *,
    version: str,
    setup: Path,
    digest: str,
    size: int,
    publisher: str,
    browser_addon: bool,
    repo: str,
    now: datetime,
) -> Path:
    """``latest.json`` (schema 1): what the agent's update check and the website's Download page read once
    it is signed. ``published_at`` is the UTC time the build wrote it, just before it is signed (the GitHub
    release appears minutes later). Keys sorted, two-space indent, one trailing newline: the signature
    covers these exact bytes."""
    url, page = manifest_urls(repo, version)
    if setup.name != f"DoMe-Setup-{version}.exe":
        raise ValueError(f"unexpected installer name {setup.name}")
    obj: dict[str, Any] = {
        "browser_addon": browser_addon,
        "product": "DoMe for Windows",
        "published_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publisher": publisher,
        "release_page": page,
        "schema": 1,
        "sha256": digest,
        "size": size,
        "url": url,
        "version": version,
    }
    path = out_dir / "latest.json"
    path.write_bytes((json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def human_size(size: int) -> str:
    return f"{size / 1_000_000:.0f} MB"


def screen_view_note(app: Path) -> str:
    """The BUILD-INFO.txt and release-notes wording: honest about what was and was not verified."""
    if screen_view_included(app):
        return "included (beta, not yet verified on a device)"
    return "not included in this build"


# Smart App Control (Windows 11) checks every file Windows loads; only DoMe.exe, the browser helper, Setup and the
# uninstaller are signed, not the bundled libraries (docs/SECURITY.md T12). The same words as the Download page.
SMART_APP_CONTROL_NOTE = (
    "- Not yet verified on Windows 11 PCs with Smart App Control on; if it blocks DoMe, do not turn it off. "
    "See docs/WINDOWS_INSTALL.md (Smart App Control) for what to do."
)


def write_release_notes(
    out_dir: Path,
    *,
    version: str,
    setup: Path,
    digest: str,
    size: int,
    publisher: str,
    browser_addon: bool,
    repo: str,
    method: str = "Azure Artifact Signing",
    screen_note: str = "",
) -> Path:
    """``RELEASE-NOTES.md``, the GitHub release's text (it stays editable after publishing: an acceptance
    record from a real PC is added there by hand)."""
    addon = (
        "- YouTube control in Chrome or Edge: included.\n"
        if browser_addon
        else "- YouTube control in Chrome or Edge: **not included in this version.** Everything else in DoMe is "
        "included; a later version adds it.\n"
    )
    screen = f"- Screen view (a live picture of the PC on the phone): {screen_note}.\n" if screen_note else ""
    text = (
        f"# DoMe for Windows {version}\n\n"
        f"- File: `{setup.name}` ({human_size(size)}, {size} bytes)\n"
        f"- SHA-256: `{digest}`\n"
        f"- Signed for Windows by **{publisher}** through {method}. Windows may still show "
        "\"Windows protected your PC\" while this version is new: check that the publisher name is "
        f"{publisher} before you continue. DoMe never asks you to turn off antivirus or other protection.\n"
        f"{SMART_APP_CONTROL_NOTE}\n"
        f"{addon}"
        f"{screen}"
        f"- How to install, check the file and uninstall: https://github.com/{repo}/blob/HEAD/docs/WINDOWS_INSTALL.md\n\n"
        "## What was tested\n\n"
        "Tested: install, upgrade, uninstall and the signatures on GitHub's Windows Server runner "
        "(administrator, UAC off). Not tested on this run: any Windows 10/11 PC as a standard user.\n"
    )
    path = out_dir / "RELEASE-NOTES.md"
    path.write_bytes(text.encode("utf-8"))
    return path


def build_info(
    *,
    setup: Path,
    version: str,
    build_tag: str,
    build_number: str,
    commit: str,
    built_from: str,
    source: str,
    digest: str,
    kind: str,
    signed_note: str,
    addon_note: str,
    screen_note: str = "",
) -> str:
    screen = f"screen view: {screen_note}\n" if screen_note else ""
    return (
        f"{setup.name}\nversion: {version}\nbuild: {build_tag} (build {build_number})\ncommit: {commit}\n"
        f"{built_from}{source}sha256: {digest}\nkind: {kind}\nsignature: {signed_note}\n"
        f"browser add-on: {addon_note}\n"
        f"{screen}"
        "note: every test build of the same version (X.Y.Z) can replace any other; this file tells them apart\n"
    )


def finish(
    *,
    out_dir: Path,
    setup: Path,
    app: Path,
    version: str,
    build_tag: str,
    build_number: str,
    commit: str,
    release: bool,
    no_addon: bool,
    signing: Signing | None,
    signtool: str,
    publisher: str,
    repo: str,
    now: datetime,
    run: Runner = subprocess.run,
) -> int:
    """Everything after ISCC: verify the signatures, then write SHA256SUMS, BUILD-INFO.txt and (release)
    latest.json and RELEASE-NOTES.md, and report to GitHub Actions."""
    if signing is not None:
        problems = verify_signatures(signtool, [setup, app / "DoMe.exe", app / "dome-native-host.exe"], run=run)
        if problems:
            return fail("a signature did not verify, so nothing is reported as signed:\n  " + "\n  ".join(problems))
    digest = sha256(setup)
    size = setup.stat().st_size
    (out_dir / "SHA256SUMS").write_bytes(f"{digest}  {setup.name}\n".encode("ascii"))
    if release:
        kind = "release build without the browser add-on" if no_addon else "release build"
    else:
        kind = "development build (accepts /ApiUrl= and /ExtensionId=)"
    if signing is None:
        signed_note = "NOT signed: Windows SmartScreen will warn (development build)"
    else:
        signed_note = signing.note
    if no_addon:
        addon_note = NO_ADDON_NOTE
    elif release:
        addon_note = f"included (production id {', '.join(production_extension_ids())})"
    else:
        addon_note = "development build (the add-on given by /ExtensionId=, or one already registered on the PC)"
    merged = label(os.environ.get("GITHUB_SHA", ""))
    built_from = (
        f"built from: {merged} (GitHub's test merge of this pull request into its target branch)\n"
        if merged and merged != commit
        else ""
    )
    event, branch = label(os.environ.get("BUILD_EVENT", "")), label(os.environ.get("BUILD_BRANCH", ""))
    source = f"source: {event or 'local build'}{f' on branch {branch}' if branch else ''}\n"
    info = build_info(
        setup=setup,
        version=version,
        build_tag=build_tag,
        build_number=build_number,
        commit=commit,
        built_from=built_from,
        source=source,
        digest=digest,
        kind=kind,
        signed_note=signed_note,
        addon_note=addon_note,
        screen_note=screen_view_note(app),
    )
    (out_dir / "BUILD-INFO.txt").write_bytes(info.encode("utf-8"))
    print(info)
    outputs = (
        f"version={version}\nsetup={setup}\nsha256={digest}\nsigned={'true' if signing else 'false'}\n"
        f"build_tag={build_tag}\nartifact_name=DoMe-Setup-{build_tag}\n"
    )
    summary = (
        f"### DoMe Setup {build_tag} (build {build_number})\n\n"
        f"- File: `{setup.name}`\n- SHA-256: `{digest}`\n- Kind: {kind}\n- Signature: {signed_note}\n"
        f"- Screen view: {screen_view_note(app)}\n"
    )
    if release:
        common: dict[str, Any] = {
            "version": version,
            "setup": setup,
            "digest": digest,
            "size": size,
            "publisher": publisher,
            "browser_addon": not no_addon,
            "repo": repo,
        }
        manifest = write_manifest(out_dir, now=now, **common)
        method = "Azure Artifact Signing" if signing and signing.kind == "artifact-signing" else "a code-signing certificate"
        write_release_notes(out_dir, method=method, screen_note=screen_view_note(app), **common)
        outputs += f"manifest={manifest}\nbrowser_addon={'false' if no_addon else 'true'}\npublisher={publisher}\n"
        summary += f"- Browser add-on: {addon_note}\n- Version information: `{manifest.name}` (not signed yet)\n"
    append("GITHUB_OUTPUT", outputs)
    append("GITHUB_STEP_SUMMARY", summary)
    return 0


def check_options(args: argparse.Namespace, production_ids: tuple[str, ...]) -> str | None:
    """Why these options cannot build anything (None when they can)."""
    thumbprint = str(args.sign_thumbprint).strip()
    artifact = args.sign_artifact_signing
    if thumbprint and artifact:
        return "choose one signing option: --sign-artifact-signing or --sign-thumbprint, not both"
    if thumbprint and not THUMBPRINT_RE.match(thumbprint):
        return "--sign-thumbprint must be a 40-character SHA-1 thumbprint"
    if artifact and not args.dlib:
        return "--sign-artifact-signing needs --dlib (Azure.CodeSigning.Dlib.dll from Microsoft.ArtifactSigning.Client)"
    if args.dlib and not artifact:
        return "--dlib is only used with --sign-artifact-signing"
    if not REPO_RE.match(args.repo):
        return "--repo must be OWNER/REPO"
    publisher = str(args.publisher or "")
    if publisher and (len(publisher) > 200 or any(ord(ch) < 32 or ord(ch) == 127 or ch == '"' for ch in publisher)):
        return "--publisher must be the certificate's name: at most 200 characters, no quotes or control characters"
    if args.without_browser_addon and not args.release:
        return "--without-browser-addon is only for --release (a development build takes /ExtensionId= instead)"
    if not args.release or args.pyinstaller_only:
        return None
    if not thumbprint and not artifact:
        return (
            "--release needs --sign-artifact-signing or --sign-thumbprint: DoMe never publishes an unsigned release "
            "build (development builds stay unsigned; drop --release)"
        )
    if not publisher.strip():
        return "--release needs --publisher: the certificate's name, exactly as Windows will show it"
    if args.without_browser_addon and production_ids:
        return "--without-browser-addon: the production add-on id exists in PRODUCTION_EXTENSION_IDS; drop the flag"
    if not production_ids and not args.without_browser_addon:
        return (
            "--release needs the production browser add-on id in dome_agent/bridge/manifest.py "
            "(PRODUCTION_EXTENSION_IDS is empty). Without it a release Setup would keep re-registering a "
            "development add-on left on the PC by a test install; or pass --without-browser-addon to ship a "
            "release without YouTube control."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--iscc", default=None, help="ISCC.exe (default: ISCC env, iscc on PATH, Inno Setup 6)")
    parser.add_argument("--sign-thumbprint", default=os.environ.get("SIGN_THUMBPRINT", ""))
    parser.add_argument(
        "--sign-artifact-signing",
        metavar="METADATA_JSON",
        default=None,
        help="sign with Azure Artifact Signing: the dlib's metadata.json (endpoint, account, certificate profile)",
    )
    parser.add_argument("--dlib", default=None, help="Azure.CodeSigning.Dlib.dll (with --sign-artifact-signing)")
    parser.add_argument("--sign-verbose", action="store_true", help="signtool /v /debug (noisy in Inno's log)")
    parser.add_argument("--signtool", default=None, help="signtool.exe (default: the newest Windows 10 SDK one)")
    parser.add_argument("--publisher", default="", help="the certificate's name as Windows shows it (release)")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="OWNER/REPO for the release addresses in latest.json")
    parser.add_argument("--release", action="store_true", help="a Setup without /ApiUrl= and /ExtensionId=")
    parser.add_argument(
        "--without-browser-addon",
        action="store_true",
        help="release only, while there is no store listing: register no browser bridge (/DNoAddon)",
    )
    parser.add_argument("--pyinstaller-only", action="store_true", help="stop after checking the PyInstaller folder")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse an existing dist/DoMe folder")
    parser.add_argument("--build-number", default=os.environ.get("GITHUB_RUN_NUMBER", "0"))
    parser.add_argument("--dist", type=Path, default=PC_AGENT / "dist")
    parser.add_argument("--work", type=Path, default=PC_AGENT / "build" / "pyinstaller")
    args = parser.parse_args(argv)

    version = read_version()
    problem = check_options(args, production_extension_ids() if args.release else ())
    if problem:
        return fail(problem)
    thumbprint = str(args.sign_thumbprint).strip()
    build_number = str(min(int(args.build_number), 65535)) if str(args.build_number).isdigit() else "0"
    commit = git_commit()
    build_tag = f"{version}+{commit[:7]}" if commit != "unknown" else version
    dist: Path = args.dist.resolve()
    app = dist / "DoMe"

    if not args.skip_pyinstaller:
        run_pyinstaller(dist, args.work.resolve())
        write_notices(app)
    problems = check_folder(app)
    if problems:
        return fail("the PyInstaller folder is not right:\n  " + "\n  ".join(problems))
    print(f"PyInstaller folder OK: {app} ({folder_size(app) / 1_000_000:.1f} MB)")
    print(f"Screen view: {screen_view_note(app)}")
    append("GITHUB_OUTPUT", f"screen_view={'included' if screen_view_included(app) else 'absent'}\n")
    if args.pyinstaller_only:
        return 0
    if os.name != "nt":
        return fail("the installer can only be compiled on Windows (Inno Setup); use --pyinstaller-only here")

    out_dir = dist / "installer"
    shutil.rmtree(out_dir, ignore_errors=True)
    iscc = args.iscc or default_iscc()
    signtool = args.signtool or default_signtool()
    publisher = str(args.publisher).strip()
    signing: Signing | None = None
    if args.sign_artifact_signing:
        sdk = signtool_sdk_build(signtool)
        if sdk is not None and sdk < MIN_SIGNTOOL_SDK_BUILD:
            return fail(f"{signtool} is from Windows SDK build {sdk}; Artifact Signing needs {MIN_SIGNTOOL_SDK_BUILD}+")
        command = sign_tool_command(
            "artifact-signing",
            signtool=signtool,
            dlib=str(Path(args.dlib).resolve()),
            metadata=str(Path(args.sign_artifact_signing).resolve()),
            verbose=args.sign_verbose,
        )
        who = f', publisher "{publisher}"' if publisher else ""
        signing = Signing("artifact-signing", command, f"Azure Artifact Signing{who}, timestamped")
    elif thumbprint:
        command = sign_tool_command("thumbprint", signtool=signtool, thumbprint=thumbprint)
        who = f', publisher "{publisher}"' if publisher else ""
        signing = Signing("thumbprint", command, f"signed with certificate {thumbprint.upper()}{who}, timestamped")
    if signing is not None:
        print(f"signtool: {signtool}")
    cmd = iscc_command(
        iscc=iscc,
        version=version,
        build_number=build_number,
        build_tag=build_tag,
        app=app,
        out_dir=out_dir,
        release=args.release,
        no_addon=args.without_browser_addon,
        sign_command=signing.command if signing else None,
    )
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

    setup = out_dir / f"DoMe-Setup-{version}.exe"
    if not setup.is_file():
        return fail(f"ISCC finished but {setup} does not exist")
    return finish(
        out_dir=out_dir,
        setup=setup,
        app=app,
        version=version,
        build_tag=build_tag,
        build_number=build_number,
        commit=commit,
        release=args.release,
        no_addon=args.without_browser_addon,
        signing=signing,
        signtool=signtool,
        publisher=publisher,
        repo=args.repo,
        now=datetime.now(UTC),
    )


if __name__ == "__main__":
    sys.exit(main())
