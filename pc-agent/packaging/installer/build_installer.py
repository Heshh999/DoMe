"""Build DoMe for Windows: PyInstaller folder, then the Inno Setup installer.

Usage (Windows, from pc-agent, after ``uv sync --locked --extra build``)::

    uv run --no-sync python packaging/installer/build_installer.py [--iscc PATH] [--release]

Steps:

1. Reads the version from ``pyproject.toml`` (X.Y.Z) and checks it equals ``dome_agent.__version__``.
2. Runs PyInstaller on ``packaging/dome-agent.spec``: ``dist/DoMe/`` holds ``DoMe.exe``,
   ``dome-native-host.exe`` and ``_internal/``.
3. Checks that folder: both programs, the protocol contract, the tray icon, and NO test fixtures or
   private keys (the old spec shipped ``shared/protocol/fixtures/test-controller-key.pem``).
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
``--release`` builds a Setup that accepts neither and always writes https://domeremote.com; it is refused
while ``dome_agent.bridge.manifest.PRODUCTION_EXTENSION_IDS`` is empty (a release Setup would otherwise
re-register whatever development add-on an earlier test install left on the PC). ``--pyinstaller-only``
stops after step 3 (it also works on Linux, to check the spec).

Signing (not wired into CI yet): ``--sign-thumbprint`` (or ``SIGN_THUMBPRINT``) names a code-signing
certificate already in the user's certificate store by its SHA-1 thumbprint; Inno then signs DoMe.exe,
dome-native-host.exe, Setup and the uninstaller with signtool and an RFC 3161 timestamp, and this
script checks every signature with ``signtool verify /pa``. No password ever appears on a command line.

Results for GitHub Actions: ``GITHUB_OUTPUT`` gets version, setup (absolute path), sha256, signed,
build_tag and artifact_name; ``GITHUB_STEP_SUMMARY`` gets a short note.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

PC_AGENT = Path(__file__).resolve().parents[2]
REPO = PC_AGENT.parent
SPEC = PC_AGENT / "packaging" / "dome-agent.spec"
ISS = PC_AGENT / "packaging" / "installer" / "DoMe.iss"
ICON = REPO / "brand" / "exports" / "windows" / "dome.ico"
MANIFEST_MODULE = PC_AGENT / "dome_agent" / "bridge" / "manifest.py"
TIMESTAMP_URL = "http://timestamp.digicert.com"
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


def run_pyinstaller(dist: Path, work: Path) -> None:
    if not ICON.is_file():
        raise SystemExit(f"build_installer: brand icon missing: {ICON}")
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(dist)]
    cmd += ["--workpath", str(work), str(SPEC)]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def check_folder(app: Path) -> list[str]:
    """Problems with the PyInstaller folder (empty when it is complete and carries no test material)."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--iscc", default=None, help="ISCC.exe (default: ISCC env, iscc on PATH, Inno Setup 6)")
    parser.add_argument("--sign-thumbprint", default=os.environ.get("SIGN_THUMBPRINT", ""))
    parser.add_argument("--signtool", default=None, help="signtool.exe (default: the newest Windows 10 SDK one)")
    parser.add_argument("--release", action="store_true", help="a Setup without /ApiUrl= and /ExtensionId=")
    parser.add_argument("--pyinstaller-only", action="store_true", help="stop after checking the PyInstaller folder")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="reuse an existing dist/DoMe folder")
    parser.add_argument("--build-number", default=os.environ.get("GITHUB_RUN_NUMBER", "0"))
    parser.add_argument("--dist", type=Path, default=PC_AGENT / "dist")
    parser.add_argument("--work", type=Path, default=PC_AGENT / "build" / "pyinstaller")
    args = parser.parse_args(argv)

    version = read_version()
    if args.release and not args.pyinstaller_only and not production_extension_ids():
        return fail(
            "--release needs the production browser add-on id in dome_agent/bridge/manifest.py "
            "(PRODUCTION_EXTENSION_IDS is empty). Without it a release Setup would keep re-registering a "
            "development add-on left on the PC by a test install."
        )
    thumbprint = str(args.sign_thumbprint).strip()
    if thumbprint and not THUMBPRINT_RE.match(thumbprint):
        return fail("--sign-thumbprint must be a 40-character SHA-1 thumbprint")
    build_number = str(min(int(args.build_number), 65535)) if str(args.build_number).isdigit() else "0"
    commit = git_commit()
    build_tag = f"{version}+{commit[:7]}" if commit != "unknown" else version
    dist: Path = args.dist.resolve()
    app = dist / "DoMe"

    if not args.skip_pyinstaller:
        run_pyinstaller(dist, args.work.resolve())
    problems = check_folder(app)
    if problems:
        return fail("the PyInstaller folder is not right:\n  " + "\n  ".join(problems))
    print(f"PyInstaller folder OK: {app} ({folder_size(app) / 1_000_000:.1f} MB)")
    if args.pyinstaller_only:
        return 0
    if os.name != "nt":
        return fail("the installer can only be compiled on Windows (Inno Setup); use --pyinstaller-only here")

    out_dir = dist / "installer"
    shutil.rmtree(out_dir, ignore_errors=True)
    iscc = args.iscc or default_iscc()
    cmd = [
        iscc,
        f"/DAppVersion={version}",
        f"/DBuildNumber={build_number}",
        f"/DBuildTag={build_tag} (build {build_number})",
        f"/DAppFiles={app}",
        f"/DOutputDir={out_dir}",
        f"/DBrandIcon={ICON}",
    ]
    if not args.release:
        cmd.append("/DDevParams")
    signtool = args.signtool or default_signtool()
    if thumbprint:
        sign = f"$q{signtool}$q sign /sha1 {thumbprint} /fd sha256 /tr {TIMESTAMP_URL} /td sha256 $f"
        cmd += ["/DSign", f"/Sdomesign={sign}"]
    cmd.append(str(ISS))
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)

    setup = out_dir / f"DoMe-Setup-{version}.exe"
    if not setup.is_file():
        return fail(f"ISCC finished but {setup} does not exist")
    if thumbprint:
        for signed_file in (setup, app / "DoMe.exe", app / "dome-native-host.exe"):
            subprocess.run([signtool, "verify", "/pa", str(signed_file)], check=True)
    digest = sha256(setup)
    (out_dir / "SHA256SUMS").write_bytes(f"{digest}  {setup.name}\n".encode("ascii"))
    kind = "release build" if args.release else "development build (accepts /ApiUrl= and /ExtensionId=)"
    signed_note = "signed" if thumbprint else "NOT signed: Windows SmartScreen will warn (development build)"
    merged = label(os.environ.get("GITHUB_SHA", ""))
    built_from = (
        f"built from: {merged} (GitHub's test merge of this pull request into its target branch)\n"
        if merged and merged != commit
        else ""
    )
    event, branch = label(os.environ.get("BUILD_EVENT", "")), label(os.environ.get("BUILD_BRANCH", ""))
    source = f"source: {event or 'local build'}{f' on branch {branch}' if branch else ''}\n"
    info = (
        f"{setup.name}\nversion: {version}\nbuild: {build_tag} (build {build_number})\ncommit: {commit}\n"
        f"{built_from}{source}sha256: {digest}\nkind: {kind}\nsignature: {signed_note}\n"
        "note: every test build of the same version (X.Y.Z) can replace any other; this file tells them apart\n"
    )
    (out_dir / "BUILD-INFO.txt").write_bytes(info.encode("ascii"))
    print(info)
    append(
        "GITHUB_OUTPUT",
        f"version={version}\nsetup={setup}\nsha256={digest}\nsigned={'true' if thumbprint else 'false'}\n"
        f"build_tag={build_tag}\nartifact_name=DoMe-Setup-{build_tag}\n",
    )
    append(
        "GITHUB_STEP_SUMMARY",
        f"### DoMe Setup {build_tag} (build {build_number})\n\n"
        f"- File: `{setup.name}`\n- SHA-256: `{digest}`\n- Kind: {kind}\n- Signature: {signed_note}\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
