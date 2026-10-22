"""Licence gate for DoMe's own FFmpeg + PyAV build (Screen view, roadmap #52; design §5.5 and §19 WP0.1).

    python -I licence_gate.py configure-summary ffmpeg-configure.log
    python -I licence_gate.py collect (--wheel-env VENV | --folder DIR) --out licence-report.json
    python -I licence_gate.py check licence-report.json [--dlls-only]
    python -I licence_gate.py notices licence-report.json --pacman pacman-qi.txt --ffmpeg-commit SHA --out FILE
    python -I licence_gate.py smoke --expect-version VERSION [--log FILE]

  The frozen app (Phase 1, WP1.6; windows-installer.yml and release.yml, after PyInstaller):

    python -I licence_gate.py shipping-av --pyproject pyproject.toml --lock uv.lock [--github-output FILE]
    python -I licence_gate.py check REPORT --frozen --screen present|absent [--strict]
    python -I licence_gate.py screen-self-test --exe dist/DoMe/DoMe.exe --expect present|absent --out FILE
    python    licence_gate.py bundle-notices --head THIRD_PARTY_NOTICES.txt --licenses DIR --out-dir dist/DoMe

Why it exists: DoMe is closed-source, so the FFmpeg it carries must stay LGPL-2.1-or-later. The stock PyAV
wheels on PyPI bundle x264/x265 (GPL) and report "LGPL version 3" (design §5.5), so one wrong wheel or one
wrong configure flag would quietly change what DoMe may distribute. This gate makes that a build failure.

It runs on the custom wheel built by ``.github/workflows/ffmpeg-lgpl.yml`` and, since Phase 1 (WP1.6), on the
frozen ``dist/DoMe`` folder (``collect --folder`` then ``check --frozen``, see "The frozen app" below). It is a
build-time tool: it never ships and nothing in ``dome_agent`` imports it. The design names it
``license_gate.py`` (§5.5, §15, §19); the file is spelled ``licence_gate.py`` and the design is to be reconciled.

Rules applied by :func:`evaluate` to a report (``check`` prints one line per violation and exits 1 on any):

- R0 report: the report has ``schema`` 1 and lists at least one DLL.
- R1 name-deny: no DLL name matches ``x264|x265|openh264|fdk|nonfree|postproc|rubberband|vidstab|xvid``.
- R2 allow-list: every DLL stem (delvewheel's ``-<32 hex>`` suffix removed) is one of ``avcodec-N``,
  ``avdevice-N``, ``avfilter-N``, ``avformat-N``, ``avutil-N``, ``swresample-N``, ``swscale-N``, ``libvpx-N``,
  ``libopus-N``, ``libgcc_s_seh-1``, ``libwinpthread-1``. Anything else is added to the allow-list and to the
  notices together, or removed.
- R3 import-deny: no DLL's import table names a library matching the R1 pattern.
- R4 export-deny: no DLL exports a symbol starting ``x264_``, ``x265_``, ``Wels``, ``aacEnc``, ``aacDec`` or ``pp_``.
- R5 configure-flags: FFmpeg's configuration has none of ``--enable-gpl``, ``--enable-version3``,
  ``--enable-nonfree``, ``--enable-libx264``, ``--enable-libx265``, ``--enable-libopenh264``,
  ``--enable-libfdk-aac``, ``--enable-nvenc``, ``--enable-amf``, ``--enable-libvpl``, and has
  ``--disable-network``, ``--disable-everything`` and ``--enable-decoder=wrapped_avframe``.
- R6 licence-strings: each of the seven libraries reports exactly "LGPL version 2.1 or later".
- R7 decoders: the decoder set is exactly ``{wrapped_avframe}`` (no bitstream parser reaches DoMe).
- R8 encoders: the encoder set is exactly ``{h264_mf, libvpx, libopus}``. These are FFmpeg's run-time codec
  names: the VP8 encoder is configured as ``libvpx_vp8`` but registers itself as ``libvpx`` (the name aiortc
  1.15.0 opens with ``CodecContext.create("libvpx", "w")``).
- R9 pe-tables: every DLL's import and export tables were read (``pefile``); a DLL that could not be read
  could hide an import, so it fails closed. Name rules R1/R2 still apply to it.
- R10 filters: the built libavfilter offers every filter DoMe's graphs use (``RUNTIME_FILTERS``): the four
  graph endpoints ``buffer``, ``buffersink``, ``abuffer``, ``abuffersink`` (FFmpeg always builds them; they
  cannot be selected with ``--enable-filter``) and ``ddagrab``, ``scale``, ``format``, ``testsrc``.

``configure-summary`` checks FFmpeg's own configure output before anything is compiled (licence line,
network off, the exact decoder/encoder/indev lists, every requested filter present, no protocols, demuxers,
muxers, parsers, bitstream filters, hwaccels or outdevs, no external library beyond libvpx, libopus and
Media Foundation, and no ``WARNING: Option --enable-… did not match anything``: configure only warns about an
option it ignores, so an ignored flag is a gate failure, never a quiet no-op). ``smoke`` runs inside the venv that has the wheel installed and proves the wheel works:
a ``lavfi`` ``testsrc`` frame decoded as NV12, a VP8 frame encoded, aiortc importable; the hardware H.264
encoder and Desktop Duplication are reported only, because the CI runner has no GPU or desktop session.

The frozen app. Screen view is in a Windows build only when ``pyproject.toml`` resolves ``av`` on Windows to
the shipping LGPL wheel (``shipping-av``: a ``[tool.uv.sources]`` entry with ``marker = "sys_platform ==
'win32'"`` and an https GitHub release-asset URL, locked in ``uv.lock`` with its SHA-256 and a ``+lgpl`` local
version); the workflows then install the ``screen`` extra, otherwise they build without it. The stock PyPI
wheel therefore never reaches a Windows build. ``check --frozen`` applies to every PE file in the folder
(``.dll``, ``.pyd``, ``.exe``) R1, R3, R4 and R9, and to every other file name R1, plus:

- F1 av.libs: every DLL in ``_internal/av.libs`` is on the R2 allow-list, and no FFmpeg, libvpx or libopus DLL
  lies anywhere else.
- F2 runtime DLLs: every other DLL matches :data:`FROZEN_DLL_STEMS` (the Python runtime, the Visual C++
  runtime, pywin32, google-crc32c), each covered by THIRD_PARTY_NOTICES.txt. An unknown one is a warning, and
  a failure with ``--strict`` (release.yml): the list was written without a Windows build at hand, so the
  first windows-installer.yml run names anything it missed without breaking the development build.
- F3 screen: ``--screen absent`` means no ``av`` package, no ``av.libs`` and no FFmpeg DLL at all;
  ``--screen present`` means all seven FFmpeg libraries plus libvpx and libopus in ``av.libs``.
- F4 notices: ``THIRD_PARTY_NOTICES.txt`` and every file of :data:`LICENCE_FILES` in ``licenses/``; a
  ``[NOT FOUND`` marker in the notices (a package without licence files) is a warning, a failure with
  ``--strict``.

``screen-self-test`` runs the frozen ``DoMe.exe screen-worker --self-test`` (with a throw-away state folder)
and checks its JSON report: with Screen view, aiortc 1.15.0 and the ``+lgpl`` PyAV import, the decoder set is
exactly ``{wrapped_avframe}`` (R7 on the frozen build), a ``lavfi`` ``testsrc`` frame decodes, the PLI hook point
exists and VP8 encodes (H.264 through Media Foundation is reported only); without it, the worker answers
and reports aiortc and av missing. Either way no crash log may appear. ``bundle-notices`` writes
``THIRD_PARTY_NOTICES.txt`` (the hand-written Part 1, then the Python runtime's licence and every installed
package's own licence files, read from the running interpreter: run it with the build venv's Python) and
copies ``licenses/*.txt`` next to it.

Standard library only; ``pefile`` is used by ``collect`` when importable. ``packaging/`` is outside ruff and
mypy on purpose, so the module keeps its own type hints and is unit-tested by ``tests/test_licence_gate.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

SCHEMA = 1
LGPL = "LGPL version 2.1 or later"
LIBRARIES = ("libavutil", "libavcodec", "libavformat", "libavdevice", "libavfilter", "libswscale", "libswresample")
EXPECTED_DECODERS = frozenset({"wrapped_avframe"})
# Run-time names (av.codecs_available); configure's summary calls the VP8 one libvpx_vp8 (see CONFIGURE_EXACT).
EXPECTED_ENCODERS = frozenset({"h264_mf", "libvpx", "libopus"})

DENY_NAME = re.compile(r"x264|x265|openh264|fdk|nonfree|postproc|rubberband|vidstab|xvid")
DENY_EXPORT = re.compile(r"^(x264_|x265_|Wels|aacEnc|aacDec|pp_)")
# delvewheel 1.13 renames each vendored DLL to <stem>-<32 hex content hash>.dll inside av.libs.
DELVEWHEEL_SUFFIX = re.compile(r"-[0-9a-f]{32}$")
ALLOWED_STEMS = tuple(
    re.compile(p)
    for p in (
        r"avcodec-\d+",
        r"avdevice-\d+",
        r"avfilter-\d+",
        r"avformat-\d+",
        r"avutil-\d+",
        r"swresample-\d+",
        r"swscale-\d+",
        r"libvpx-\d+",
        r"libopus-\d+",
        r"libgcc_s_seh-1",
        r"libwinpthread-1",
    )
)
FFMPEG_STEM = re.compile(r"(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale)-\d+")
FORBIDDEN_FLAGS = (
    "--enable-gpl",
    "--enable-version3",
    "--enable-nonfree",
    "--enable-libx264",
    "--enable-libx265",
    "--enable-libopenh264",
    "--enable-libfdk-aac",
    "--enable-nvenc",
    "--enable-amf",
    "--enable-libvpl",
)
REQUIRED_FLAGS = ("--disable-network", "--disable-everything", "--enable-decoder=wrapped_avframe")

# FFmpeg 8.1 configure summary: blocks that must hold exactly these names (configure's component names).
CONFIGURE_EXACT: dict[str, frozenset[str]] = {
    "decoders": frozenset({"wrapped_avframe"}),
    "encoders": frozenset({"h264_mf", "libvpx_vp8", "libopus"}),
    "indevs": frozenset({"lavfi"}),
    "hwaccels": frozenset(),
    "parsers": frozenset(),
    "demuxers": frozenset(),
    "muxers": frozenset(),
    "protocols": frozenset(),
    "bsfs": frozenset(),
    "outdevs": frozenset(),
    "external": frozenset({"libvpx", "libopus", "mediafoundation"}),
    "hwaccel_libraries": frozenset({"d3d11va"}),
}
# Every filter asked for by name: configure drops a filter silently when a dependency check fails
# (ddagrab needs d3d11va, IDXGIOutput1 and DXGI_OUTDUPL_FRAME_INFO), so absence is a failure. buffer,
# buffersink, abuffer and abuffersink are not here: FFmpeg always builds them (n8.1.3 declares them outside
# configure's filter list and configure adds them to filter_list.c by itself), so --enable-filter=buffer only
# prints "did not match anything" and the summary never lists them. R10 checks them in the built library.
CONFIGURE_FILTERS = frozenset(
    {
        "ddagrab",
        "scale_d3d11",
        "hwdownload",
        "format",
        "scale",
        "aresample",
        "testsrc",
        "color",
    }
)
# Filters DoMe's graphs need at run time (av.filter.filters_available), the always-built endpoints included.
RUNTIME_FILTERS = frozenset({"buffer", "buffersink", "abuffer", "abuffersink", "ddagrab", "scale", "format", "testsrc"})
# configure's only sign that it ignored an option it was given
_IGNORED_OPTION = re.compile(r"^WARNING: Option (--\S+) did not match anything")
_HEADINGS = {
    "External libraries:": "external",
    "External libraries providing hardware acceleration:": "hwaccel_libraries",
    "Libraries:": "libraries",
    "Programs:": "programs",
}
_ENABLED = re.compile(r"^Enabled (\w+):$")

OFFER = (
    "[OFFER: the FFmpeg source and this build script are available from DoMe on request for three years "
    "from the date of distribution]"
)

# --- the frozen app (dist/DoMe) ---
NOTICES_NAME = "THIRD_PARTY_NOTICES.txt"
NOT_FOUND = "[NOT FOUND"  # bundle-notices writes it where a licence text is missing; F4 looks for it
# Directories under _internal that only a build with Screen view has (PyInstaller 6 onedir layout).
SCREEN_DIRS = ("av", "av.libs")
# packaging/licenses/*.txt, each named in THIRD_PARTY_NOTICES.txt Part 1 (F4: all of them, in every build).
LICENCE_FILES = (
    "Apache-2.0.txt",
    "GCC-RUNTIME-LIBRARY-EXCEPTION-3.1.txt",
    "GPL-3.0.txt",
    "LGPL-2.1.txt",
    "libvpx-LICENSE.txt",
    "libvpx-PATENTS.txt",
    "opus-COPYING.txt",
    "winpthreads-COPYING.txt",
)
# DLLs a frozen DoMe may hold outside av.libs, with the THIRD_PARTY_NOTICES.txt part that covers each (F2).
# Written from CPython 3.12's Windows layout, pywin32 and google-crc32c without a Windows build at hand.
FROZEN_DLL_STEMS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), covered_by)
    for pattern, covered_by in (
        (r"python3\d*", "Part 1 section 2 (the Python runtime)"),
        (r"vcruntime140(_1)?|msvcp140(_\d+|_atomic_wait|_codecvt_ids)?", "Part 1 section 2 (Visual C++ runtime)"),
        (r"lib(crypto|ssl)-3(-x64)?", "Part 1 section 2 (OpenSSL 3)"),
        (r"libffi-\d+|sqlite3|tcl86t|tk86t|zlib1", "Part 1 section 2 (the Python runtime's own libraries)"),
        (r"pywintypes3\d+|pythoncom3\d+", "Part 2 (pywin32)"),
        (r"crc32c", "Part 2 (google-crc32c, Screen view)"),
    )
)
# FFmpeg and the codec libraries it links: allowed only inside av.libs (F1).
FFMPEG_FAMILY = re.compile(r"(avcodec|avdevice|avfilter|avformat|avutil|swresample|swscale|libvpx|libopus)-\d+")
SCREEN_LIBRARIES = (
    "avcodec",
    "avdevice",
    "avfilter",
    "avformat",
    "avutil",
    "swresample",
    "swscale",
    "libvpx",
    "libopus",
)
# shipping-av: the Windows source of av must be a release asset of a GitHub repository, as a win_amd64 wheel.
RELEASE_ASSET = re.compile(
    r"\Ahttps://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/releases/download/[^/\s]+/[^/\s]+-win_amd64\.whl\Z"
)
WIN32_MARKER = "sys_platform=='win32'"
SHA256_HASH = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
AIORTC_VERSION = "1.15.0"  # pyproject.toml pins aiortc==1.15.0: the PLI hook (design §5.3) depends on it
# Distributions in a build venv that never reach the frozen app (bundle-notices leaves them out).
BUILD_ONLY = frozenset(
    {
        "altgraph",
        "dome-agent",
        "dome-protocol",
        "macholib",
        "mypy",
        "mypy-extensions",
        "pefile",
        "pip",
        "pluggy",
        "iniconfig",
        "pyinstaller",
        "pyinstaller-hooks-contrib",
        "pytest",
        "pytest-asyncio",
        "pywin32-ctypes",
        "ruff",
    }
)
_LICENCE_NAME = re.compile(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    """One broken rule: ``rule`` is the id and name (``R1 name-deny``), ``subject`` what broke it."""

    rule: str
    subject: str
    detail: str

    def line(self) -> str:
        return f"FAIL {self.rule}: {self.subject}: {self.detail}"


# --- pure rules -------------------------------------------------------------------------------------


def dll_stem(name: str) -> str:
    """``avcodec-62-3f2a…(32 hex).dll`` → ``avcodec-62``; lower-cased, so the rules ignore Windows' case."""
    stem = name.lower()
    if stem.endswith(".dll"):
        stem = stem[: -len(".dll")]
    return DELVEWHEEL_SUFFIX.sub("", stem)


def allowed(name: str) -> bool:
    stem = dll_stem(name)
    return any(p.fullmatch(stem) for p in ALLOWED_STEMS)


def config_tokens(configuration: str) -> list[str]:
    """Configure options as FFmpeg recorded them (``avcodec_configuration()``), quotes removed."""
    return [t.strip("'\"") for t in configuration.split()]


def evaluate(report: dict[str, Any], *, dlls_only: bool = False) -> list[Violation]:
    """Every violation in ``report`` (empty list = the gate passes). ``dlls_only`` skips R5–R8, for a
    ``collect --folder`` report that has no FFmpeg metadata (no ``av`` import)."""
    out: list[Violation] = []
    if report.get("schema") != SCHEMA:
        out.append(Violation("R0 report", "schema", f"expected {SCHEMA}, got {report.get('schema')!r}"))
    dlls = report.get("dlls") or []
    if not dlls:
        out.append(Violation("R0 report", "dlls", "no DLL was collected (is av.libs missing?)"))
    for dll in dlls:
        name = str(dll.get("name", ""))
        if DENY_NAME.search(name.lower()):
            out.append(Violation("R1 name-deny", name, "the name matches a GPL, non-free or patent-encumbered library"))
        if not allowed(name):
            out.append(
                Violation(
                    "R2 allow-list",
                    name,
                    f"stem {dll_stem(name)!r} is not on the allow-list; add it to the allow-list and to the "
                    "notices together, or remove it",
                )
            )
        for imp in dll.get("imports") or []:
            if DENY_NAME.search(str(imp).lower()):
                out.append(Violation("R3 import-deny", name, f"imports {imp}"))
        for exp in dll.get("exports") or []:
            if DENY_EXPORT.match(str(exp)):
                out.append(Violation("R4 export-deny", name, f"exports {exp}"))
        if not dll.get("pe_parsed"):
            out.append(Violation("R9 pe-tables", name, "the import and export tables could not be read"))
    if dlls_only:
        return out

    configuration = report.get("configuration")
    if not isinstance(configuration, str):
        out.append(Violation("R5 configure-flags", "configuration", "not collected"))
    else:
        tokens = config_tokens(configuration)
        for flag in FORBIDDEN_FLAGS:
            if any(t == flag or t.startswith(flag + "=") for t in tokens):
                out.append(Violation("R5 configure-flags", flag, "present in FFmpeg's configuration"))
        for flag in REQUIRED_FLAGS:
            if flag not in tokens:
                out.append(Violation("R5 configure-flags", flag, "missing from FFmpeg's configuration"))

    licenses = report.get("licenses")
    if not isinstance(licenses, dict):
        out.append(Violation("R6 licence-strings", "licenses", "not collected"))
    else:
        for lib in LIBRARIES:
            value = licenses.get(lib)
            if value is None:
                out.append(Violation("R6 licence-strings", lib, "missing"))
            elif value != LGPL:
                out.append(Violation("R6 licence-strings", lib, f"reports {value!r}, expected {LGPL!r}"))

    filters = report.get("filters")
    if not isinstance(filters, list):
        out.append(Violation("R10 filters", "filters", "not collected"))
    elif not RUNTIME_FILTERS <= set(filters):
        out.append(Violation("R10 filters", "filters", f"missing {sorted(RUNTIME_FILTERS - set(filters))}"))

    for rule, key, expected in (
        ("R7 decoders", "decoders", EXPECTED_DECODERS),
        ("R8 encoders", "encoders", EXPECTED_ENCODERS),
    ):
        value = report.get(key)
        if not isinstance(value, list):
            out.append(Violation(rule, key, "not collected"))
        elif set(value) != expected:
            extra = sorted(set(value) - expected)
            missing = sorted(expected - set(value))
            out.append(Violation(rule, key, f"expected exactly {sorted(expected)}; extra {extra}, missing {missing}"))
    return out


def parse_configure_summary(text: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """FFmpeg configure's closing summary → (name blocks, ``key value`` fields such as ``network support``).

    A block is a heading line (``Enabled filters:``, ``External libraries:`` …) followed by names in columns
    up to the next blank line; an empty block is the heading followed directly by a blank line."""
    blocks: dict[str, set[str]] = {}
    fields: dict[str, str] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r").rstrip()
        if current is not None:
            if not line:
                current = None
            else:
                blocks[current].update(line.split())
            continue
        if line.startswith("License: "):
            fields["license"] = line[len("License: ") :]
            continue
        heading = _HEADINGS.get(line)
        match = _ENABLED.match(line)
        if heading is None and match:
            heading = match.group(1)
        if heading is not None:
            current = heading
            blocks[current] = set()
            continue
        m = re.match(r"^([a-z][a-zA-Z0-9 ]*?)\s{2,}(\S.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2)
    return blocks, fields


def check_configure_summary(text: str) -> tuple[list[Violation], list[str]]:
    """Violations in a configure summary, plus notes (filters configure added on its own: reported, not failed)."""
    blocks, fields = parse_configure_summary(text)
    out: list[Violation] = []
    notes: list[str] = []
    if fields.get("license") != LGPL:
        out.append(Violation("C1 licence", "License", f"is {fields.get('license')!r}, expected {LGPL!r}"))
    if fields.get("network support") != "no":
        out.append(Violation("C2 network", "network support", f"is {fields.get('network support')!r}, expected 'no'"))
    for key, expected in CONFIGURE_EXACT.items():
        if key not in blocks:
            out.append(Violation("C3 components", key, "block not found in the configure summary"))
        elif blocks[key] != expected:
            extra = sorted(blocks[key] - expected)
            missing = sorted(expected - blocks[key])
            out.append(Violation("C3 components", key, f"extra {extra}, missing {missing}"))
    if "filters" not in blocks:
        out.append(Violation("C4 filters", "filters", "block not found in the configure summary"))
    else:
        missing = sorted(CONFIGURE_FILTERS - blocks["filters"])
        if missing:
            out.append(Violation("C4 filters", "filters", f"requested but not enabled: {missing}"))
        extra = sorted(blocks["filters"] - CONFIGURE_FILTERS)
        if extra:
            notes.append(f"note: configure also enabled the filters {extra} (selected by a dependency)")
    for raw in text.splitlines():
        ignored = _IGNORED_OPTION.match(raw.strip())
        if ignored:
            out.append(Violation("C5 ignored-option", ignored.group(1), "configure did not match it and ignored it"))
    return out, notes


# --- collect ----------------------------------------------------------------------------------------

# Runs inside the venv that has the wheel installed; prints the av part of the report as JSON.
_AV_PROBE = r"""
import json, os
import importlib.metadata as md
import av, av._core, av.filter
out = {
    "av_version": av.__version__,
    "configuration": av._core.library_meta["libavcodec"]["configuration"],
    "licenses": {k: v["license"] for k, v in av._core.library_meta.items()},
    "decoders": [], "encoders": [],
    "filters": sorted(av.filter.filters_available),
    "site_packages": os.path.dirname(os.path.dirname(os.path.abspath(av.__file__))),
    "packages": {},
}
for name in sorted(av.codecs_available):
    for mode, key in (("r", "decoders"), ("w", "encoders")):
        try:
            av.Codec(name, mode)
        except ValueError:  # av.codec.codec.UnknownCodecError
            continue
        out[key].append(name)
for dist in ("av", "aiortc", "aioice", "pylibsrtp"):
    try:
        out["packages"][dist] = md.version(dist)
    except md.PackageNotFoundError:
        out["packages"][dist] = None
print(json.dumps(out))
"""


def venv_python(venv: Path) -> Path:
    windows = venv / "Scripts" / "python.exe"
    return windows if windows.exists() else venv / "bin" / "python"


def pe_tables(path: Path) -> tuple[list[str], list[str]] | None:
    """(imports, exports) of a PE file, or None when ``pefile`` is missing or the file is not a readable PE."""
    try:
        import pefile  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(
            directories=[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"],
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
            ]
        )
    except Exception:  # pefile.PEFormatError and truncated files alike: fail closed through R9
        return None
    imports = [
        e.dll.decode("ascii", "replace")
        for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT")
        for e in getattr(pe, attr, [])
    ]
    export_dir = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    exports = [s.name.decode("ascii", "replace") for s in (export_dir.symbols if export_dir else []) if s.name]
    return imports, exports


def scan_dlls(folder: Path, suffixes: tuple[str, ...] = (".dll",)) -> list[dict[str, Any]]:
    """Every file under ``folder`` with one of ``suffixes``: its name, its path below ``folder`` and its PE tables."""
    out: list[dict[str, Any]] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in suffixes):
        tables = pe_tables(path)
        out.append(
            {
                "name": path.name,
                "path": path.relative_to(folder).as_posix(),
                "imports": tables[0] if tables else [],
                "exports": tables[1] if tables else [],
                "pe_parsed": tables is not None,
            }
        )
    return out


def collect_wheel_env(venv: Path) -> dict[str, Any]:
    python = venv_python(venv)
    done = subprocess.run([str(python), "-I", "-c", _AV_PROBE], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"importing av from {venv} failed (exit {done.returncode}):\n{done.stderr.strip()}")
    probe = json.loads(done.stdout)
    site_packages = Path(probe.pop("site_packages"))
    return {"schema": SCHEMA, "source": "wheel-env", **probe, "dlls": scan_dlls(site_packages / "av.libs")}


def collect_folder(folder: Path) -> dict[str, Any]:
    """A folder report: every DLL (``dlls``) and every other PE file (``binaries``: ``.pyd``, ``.exe``) with its
    tables; every file whose name matches the R1 pattern whatever its type (``denied_names``: a Linux freeze's
    ``libx264*.so`` too); which Screen view parts exist under ``_internal``; the notices and licence files."""
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    internal = folder / "_internal"
    notices = folder / NOTICES_NAME
    not_found: list[str] = []
    if notices.is_file():
        text = notices.read_text(encoding="utf-8", errors="replace")
        not_found = [line.strip() for line in text.splitlines() if NOT_FOUND in line]
    return {
        "schema": SCHEMA,
        "source": "folder",
        "dlls": scan_dlls(folder),
        "binaries": scan_dlls(folder, (".pyd", ".exe")),
        "denied_names": [p.relative_to(folder).as_posix() for p in files if DENY_NAME.search(p.name.lower())],
        "screen_dirs": {name: (internal / name).is_dir() for name in SCREEN_DIRS},
        "notices": {"present": notices.is_file(), "not_found": not_found},
        "licence_files": sorted(p.name for p in (folder / "licenses").glob("*.txt")),
    }


# --- notices ----------------------------------------------------------------------------------------


def pacman_versions(text: str) -> dict[str, dict[str, str]]:
    """``pacman -Qi`` output → {package name: {"version", "licenses"}}."""
    out: dict[str, dict[str, str]] = {}
    name: str | None = None
    for raw in text.splitlines():
        key, sep, value = raw.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "Name":
            name = value
            out[name] = {}
        elif name is not None and key in ("Version", "Licenses"):
            out[name][key.lower()] = value
    return out


def _pkg(pacman: dict[str, dict[str, str]], suffix: str) -> str:
    info = pacman.get(f"mingw-w64-x86_64-{suffix}", {})
    return f"MSYS2 package mingw-w64-x86_64-{suffix} {info.get('version', '[VERSION NOT RECORDED]')}" + (
        f" ({info['licenses']})" if "licenses" in info else ""
    )


def render_notices(report: dict[str, Any], *, report_name: str, pacman_text: str, ffmpeg_commit: str) -> str:
    """An inventory for THIRD_PARTY_NOTICES.txt: which licence covers each DLL and what must be inserted.
    It is not the final notice (``[INSERT TEXT]`` marks the licence texts to paste in after review)."""
    pacman = pacman_versions(pacman_text)
    dlls = sorted(str(d.get("name", "")) for d in report.get("dlls") or [])
    lines = [
        f"DRAFT — generated by licence_gate.py from {report_name}; not reviewed by counsel; not shipped.",
        "LGPL compliance checklist wording to be confirmed against ffmpeg.org/legal.html (the page could not be",
        "reached from the build sandbox on 2026-10-20).",
        "",
    ]
    ffmpeg = [d for d in dlls if FFMPEG_STEM.fullmatch(dll_stem(d))]
    if ffmpeg:
        licenses = report.get("licenses") or {}
        lines += [
            "== FFmpeg ==",
            "DLLs: " + ", ".join(ffmpeg),
            f"Licence: {LGPL} (LGPL-2.1-or-later). Reported by the libraries:",
            *(f"  {lib}: {licenses.get(lib, '[NOT COLLECTED]')}" for lib in LIBRARIES),
            f"Source: https://github.com/FFmpeg/FFmpeg commit {ffmpeg_commit}",
            f"Configure line: {report.get('configuration', '[NOT COLLECTED]')}",
            OFFER,
            "The DLLs are separate files in DoMe's install folder and may be replaced by the user.",
            "[INSERT TEXT: GNU Lesser General Public License version 2.1]",
            "",
        ]
    for name in dlls:
        stem = dll_stem(name)
        if FFMPEG_STEM.fullmatch(stem):
            continue
        if re.fullmatch(r"libvpx-\d+", stem):
            body = [
                "Licence: BSD-3-Clause, with Google's additional patent grant (royalty-free VP8).",
                _pkg(pacman, "libvpx"),
                "[INSERT TEXT: libvpx LICENSE and PATENTS]",
            ]
        elif re.fullmatch(r"libopus-\d+", stem):
            body = [
                "Licence: BSD-3-Clause; patent licences at https://opus-codec.org/license/",
                _pkg(pacman, "opus"),
                "[INSERT TEXT: Opus COPYING]",
            ]
        elif stem == "libgcc_s_seh-1":
            body = [
                "Licence: GPL-3.0-or-later WITH GCC-exception-3.1 (GCC Runtime Library Exception).",
                "[INSERT TEXT: GCC Runtime Library Exception 3.1]",
            ]
        elif stem == "libwinpthread-1":
            body = ["Licence: MIT and BSD (mingw-w64 winpthreads).", "[INSERT TEXT: winpthreads COPYING]"]
        else:
            body = ["Licence: UNKNOWN: not on the allow-list; the gate fails on this file."]
        lines += [f"== {name} ==", *body, ""]
    packages = report.get("packages") or {}
    lines += [
        "== Python packages to be listed with their versions ==",
        f"PyAV (av) {packages.get('av') or report.get('av_version') or '[VERSION]'}: BSD-3-Clause [INSERT TEXT]",
        *(
            f"{dist} {packages.get(dist) or '[VERSION]'}: BSD-3-Clause [INSERT TEXT]"
            for dist in ("aiortc", "aioice", "pylibsrtp")
        ),
        "",
    ]
    return "\n".join(lines)


# --- smoke --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SmokeRow:
    """One smoke check. ``required`` rows fail the job; the others are reported (no GPU or desktop on CI)."""

    name: str
    required: bool
    ok: bool
    detail: str

    @property
    def status(self) -> str:
        if self.required:
            return "PASS" if self.ok else "FAIL"
        return "REPORTED (ok)" if self.ok else "REPORTED (failed)"


def _first_frame(av: Any, graph: str) -> Any:
    container = av.open(graph, format="lavfi")
    try:
        for frame in container.decode(video=0):
            return frame
    finally:
        container.close()
    raise RuntimeError("the graph produced no frame")


def _encode_one(av: Any, codec: str, width: int, height: int, pix_fmt: str, options: dict[str, str]) -> int:
    ctx = av.CodecContext.create(codec, "w")
    ctx.width, ctx.height, ctx.pix_fmt = width, height, pix_fmt
    ctx.time_base = Fraction(1, 30)
    ctx.framerate = Fraction(30, 1)
    ctx.options = options
    frame = av.VideoFrame(width, height, pix_fmt)
    frame.pts = 0
    packets = list(ctx.encode(frame)) + list(ctx.encode(None))
    if not packets:
        raise RuntimeError("no packet")
    return len(packets)


def smoke_checks(av: Any, expect_version: str, import_aiortc: Callable[[], object]) -> list[SmokeRow]:
    """Run every check against an imported ``av`` module (a fake in the unit tests)."""
    meta: dict[str, Any] = av._core.library_meta

    def lgpl() -> str:
        bad = {k: v["license"] for k, v in meta.items() if v["license"] != LGPL}
        if bad or set(meta) != set(LIBRARIES):
            raise RuntimeError(f"not LGPL-2.1+: {bad or sorted(set(LIBRARIES) ^ set(meta))}")
        return "all seven libraries"

    def codec_names(mode: str) -> set[str]:
        names = set()
        for name in av.codecs_available:
            try:
                av.Codec(name, mode)
            except ValueError:
                continue
            names.add(name)
        return names

    def decoders() -> str:
        found = codec_names("r")
        if found != EXPECTED_DECODERS:
            raise RuntimeError(f"decoders {sorted(found)}")
        return "wrapped_avframe only"

    def encoders() -> str:
        found = codec_names("w")
        if not EXPECTED_ENCODERS <= found:
            raise RuntimeError(f"encoders {sorted(found)}")
        return " ".join(sorted(found))

    def version() -> str:
        if av.__version__ != expect_version:
            raise RuntimeError(f"av.__version__ is {av.__version__!r}")
        return av.__version__

    def filters_listed() -> str:
        missing = sorted(RUNTIME_FILTERS - set(av.filter.filters_available))
        if missing:
            raise RuntimeError(f"not among the filters: {missing}")
        return " ".join(sorted(RUNTIME_FILTERS))

    def lavfi_nv12() -> str:
        frame = _first_frame(av, "testsrc=size=64x64:rate=5,format=nv12")
        if frame.format.name != "nv12":
            raise RuntimeError(f"frame format {frame.format.name}")
        return f"{frame.width}x{frame.height} nv12 through lavfi + wrapped_avframe"

    def vp8() -> str:
        return f"{_encode_one(av, 'libvpx', 64, 64, 'yuv420p', {})} packet(s)"

    def aiortc() -> str:
        import_aiortc()
        return "imported"

    def h264_mf_hw() -> str:
        return f"{_encode_one(av, 'h264_mf', 1280, 720, 'nv12', {'hw_encoding': '1'})} packet(s)"

    def ddagrab_frame() -> str:
        frame = _first_frame(av, "ddagrab=output_idx=0:framerate=5,hwdownload,format=bgra")
        return f"{frame.width}x{frame.height}"

    checks: list[tuple[str, bool, Callable[[], str]]] = [
        ("av version is the local build", True, version),
        ("licence strings", True, lgpl),
        ("decoders == {wrapped_avframe}", True, decoders),
        ("encoders include h264_mf libvpx libopus", True, encoders),
        ("graph filters listed (buffer, buffersink, abuffer, abuffersink, ddagrab, ...)", True, filters_listed),
        ("lavfi testsrc -> one NV12 frame", True, lavfi_nv12),
        ("libvpx (VP8) encodes one frame", True, vp8),
        ("import aiortc", True, aiortc),
        ("h264_mf hw_encoding=1, one 1280x720 frame", False, h264_mf_hw),
        ("ddagrab one frame", False, ddagrab_frame),
    ]
    rows: list[SmokeRow] = []
    for name, required, fn in checks:
        try:
            rows.append(SmokeRow(name, required, True, fn()))
        except Exception as exc:  # every failure becomes a row; required ones fail the run below
            rows.append(SmokeRow(name, required, False, f"{type(exc).__name__}: {exc}"))
    return rows


def format_smoke(rows: Iterable[SmokeRow]) -> str:
    return "\n".join(f"{row.status:<18} {row.name}: {row.detail}" for row in rows)


# --- the frozen app: check --frozen ---------------------------------------------------------------------


def _frozen_dll_covered(name: str) -> str | None:
    stem = dll_stem(name)
    return next((covered for pattern, covered in FROZEN_DLL_STEMS if pattern.fullmatch(stem)), None)


def _in_av_libs(entry: dict[str, Any]) -> bool:
    return "av.libs" in str(entry.get("path", "")).split("/")[:-1]


def evaluate_frozen(
    report: dict[str, Any], *, screen: str, strict: bool = False
) -> tuple[list[Violation], list[Violation]]:
    """(failures, warnings) for a ``collect --folder`` report of ``dist/DoMe``. ``screen`` is what the workflow
    built (``present`` or ``absent``); ``strict`` turns every warning into a failure (release builds)."""
    if screen not in ("present", "absent"):
        raise ValueError(f"screen must be 'present' or 'absent', not {screen!r}")
    fail: list[Violation] = []
    warn: list[Violation] = []
    if report.get("schema") != SCHEMA or report.get("source") != "folder":
        fail.append(Violation("R0 report", "report", "not a schema-1 folder report (collect --folder)"))
    dlls = list(report.get("dlls") or [])
    binaries = list(report.get("binaries") or [])
    if not dlls and not binaries:
        fail.append(Violation("R0 report", "files", "no PE file was collected (is this the dist/DoMe folder?)"))

    for entry in dlls + binaries:
        name = str(entry.get("name", ""))
        where = str(entry.get("path", name))
        if DENY_NAME.search(name.lower()):
            fail.append(
                Violation("R1 name-deny", where, "the name matches a GPL, non-free or patent-encumbered library")
            )
        for imp in entry.get("imports") or []:
            if DENY_NAME.search(str(imp).lower()):
                fail.append(Violation("R3 import-deny", where, f"imports {imp}"))
        for exp in entry.get("exports") or []:
            if DENY_EXPORT.match(str(exp)):
                fail.append(Violation("R4 export-deny", where, f"exports {exp}"))
        if not entry.get("pe_parsed"):
            fail.append(Violation("R9 pe-tables", where, "the import and export tables could not be read"))
    pe_paths = {str(e.get("path", e.get("name", ""))) for e in dlls + binaries}
    for path in report.get("denied_names") or []:
        if path not in pe_paths:
            fail.append(
                Violation("R1 name-deny", str(path), "the name matches a GPL, non-free or patent-encumbered library")
            )

    av_libs = [e for e in dlls if _in_av_libs(e)]
    for entry in dlls:
        name, where = str(entry.get("name", "")), str(entry.get("path", entry.get("name", "")))
        if _in_av_libs(entry):
            if not allowed(name):
                fail.append(Violation("F1 av.libs", where, f"stem {dll_stem(name)!r} is not on the FFmpeg allow-list"))
        elif FFMPEG_FAMILY.fullmatch(dll_stem(name)):
            fail.append(Violation("F1 av.libs", where, "an FFmpeg or codec library outside _internal/av.libs"))
        elif _frozen_dll_covered(name) is None:
            target = fail if strict else warn
            target.append(
                Violation(
                    "F2 runtime-dlls",
                    where,
                    f"stem {dll_stem(name)!r} is not in FROZEN_DLL_STEMS: add it there and to THIRD_PARTY_NOTICES.txt "
                    "together, or keep it out of the build",
                )
            )

    dirs = report.get("screen_dirs") or {}
    if screen == "absent":
        present = sorted(name for name in SCREEN_DIRS if dirs.get(name))
        if present or av_libs:
            fail.append(
                Violation("F3 screen", "_internal", f"built without Screen view but holds {present or 'av.libs DLLs'}")
            )
    else:
        stems = {dll_stem(str(e.get("name", ""))).rsplit("-", 1)[0] for e in av_libs}
        missing = [lib for lib in SCREEN_LIBRARIES if lib not in stems]
        if not dirs.get("av.libs") or missing:
            fail.append(Violation("F3 screen", "_internal/av.libs", f"built with Screen view but missing {missing}"))

    notices = report.get("notices") or {}
    if not notices.get("present"):
        fail.append(Violation("F4 notices", NOTICES_NAME, "missing from the folder (bundle-notices writes it)"))
    for line in notices.get("not_found") or []:
        (fail if strict else warn).append(Violation("F4 notices", NOTICES_NAME, str(line)[:200]))
    missing_texts = sorted(set(LICENCE_FILES) - set(report.get("licence_files") or []))
    if missing_texts:
        fail.append(Violation("F4 notices", "licenses", f"missing {missing_texts}"))
    return fail, warn


# --- the frozen app: shipping-av ------------------------------------------------------------------------


class GateError(Exception):
    """A configuration the gate refuses outright (shipping-av); the message says what to change."""


def shipping_av(pyproject: dict[str, Any], lock: dict[str, Any]) -> tuple[str, str]:
    """("present", url) when ``pyproject.toml`` resolves ``av`` on Windows to a locked, hash-pinned ``+lgpl``
    release-asset wheel; ("absent", why) when it has no ``av`` source at all (Windows would get the stock PyPI
    wheel, so the build leaves Screen view out). Anything in between raises :class:`GateError`."""
    sources = ((pyproject.get("tool") or {}).get("uv") or {}).get("sources") or {}
    entry = sources.get("av")
    if entry is None:
        return (
            "absent",
            "pyproject.toml has no [tool.uv.sources] entry for av, so Windows would get the stock PyPI wheel",
        )
    entries = entry if isinstance(entry, list) else [entry]
    if len(entries) != 1 or not isinstance(entries[0], dict):
        raise GateError("[tool.uv.sources] av must be exactly one { url = ..., marker = ... } entry")
    source = entries[0]
    if set(source) != {"url", "marker"}:
        raise GateError(f"[tool.uv.sources] av must have exactly the keys url and marker, not {sorted(source)}")
    marker = re.sub(r"\s+", "", str(source["marker"])).replace('"', "'")
    if marker != WIN32_MARKER:
        raise GateError(
            f"[tool.uv.sources] av has marker {source['marker']!r}: it must be exactly sys_platform == 'win32', so "
            "Linux and macOS keep the PyPI wheel for development and only Windows builds get the LGPL wheel"
        )
    url = str(source["url"])
    if not RELEASE_ASSET.match(url):
        raise GateError(f"[tool.uv.sources] av url {url!r} is not an https GitHub release asset ending -win_amd64.whl")
    locked = [
        p for p in lock.get("package") or [] if p.get("name") == "av" and (p.get("source") or {}).get("url") == url
    ]
    if len(locked) != 1:
        raise GateError(
            "uv.lock does not resolve av to that url: run `uv lock` and commit pyproject.toml and uv.lock together"
        )
    package = locked[0]
    if "+lgpl" not in str(package.get("version", "")):
        raise GateError(f"the locked av is version {package.get('version')!r}, not DoMe's +lgpl build")
    wheels = [w for w in package.get("wheels") or [] if w.get("url") == url]
    if len(wheels) != 1 or not SHA256_HASH.match(str(wheels[0].get("hash", ""))):
        raise GateError("uv.lock does not pin that wheel by its SHA-256")
    return "present", url


# --- the frozen app: screen-self-test -------------------------------------------------------------------


def run_screen_self_test(exe: Path, state_dir: Path, *, timeout: float = 300) -> tuple[int, str, str]:
    """``DoMe.exe screen-worker --self-test`` with no ``DOME_*`` variable (never ``DOME_SCREEN_DEV_H264``) and a
    throw-away state folder, so a crash log lands there and the runner's own DoMe folder stays untouched."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("DOME_")}
    env["DOME_AGENT_STATE_DIR"] = str(state_dir)
    done = subprocess.run(
        [str(exe), "screen-worker", "--self-test"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        timeout=timeout,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def parse_self_test(stdout: str) -> dict[str, Any] | None:
    """The worker's report: the last stdout line that is a JSON object (logs go to stderr)."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


def evaluate_screen_self_test(
    report: dict[str, Any] | None, returncode: int, *, expect: str, crash_log: bool
) -> list[SmokeRow]:
    """Rows for the frozen worker's self-test. ``expect`` is ``present`` (a build with Screen view) or ``absent``."""
    if expect not in ("present", "absent"):
        raise ValueError(f"expect must be 'present' or 'absent', not {expect!r}")
    rows = [SmokeRow("the worker answered with its JSON report", True, report is not None, f"exit code {returncode}")]
    checks: dict[str, Any] = (report or {}).get("checks") or {}

    def check(name: str) -> dict[str, Any]:
        value = checks.get(name)
        return value if isinstance(value, dict) else {}

    def shown(item: dict[str, Any]) -> str:
        return str(item.get("value") if item.get("ok") else item.get("error", "not reported"))[:200]

    if report is not None and expect == "present":
        imports = check("imports")
        versions = imports.get("value") if isinstance(imports.get("value"), dict) else {}
        rows.append(
            SmokeRow(
                f"aiortc {AIORTC_VERSION} imports",
                True,
                imports.get("ok") is True and versions.get("aiortc") == AIORTC_VERSION,
                shown(imports),
            )
        )
        rows.append(
            SmokeRow("av is DoMe's LGPL build (+lgpl)", True, "+lgpl" in str(versions.get("av", "")), shown(imports))
        )
        decoders = check("decoders")
        value = decoders.get("value") if isinstance(decoders.get("value"), dict) else {}
        rows.append(
            SmokeRow(
                "decoders == {wrapped_avframe}",
                True,
                decoders.get("ok") is True and value.get("exactly_wrapped_avframe") is True,
                shown(decoders),
            )
        )
        frame = check("lavfi_testsrc_frame")
        rows.append(SmokeRow("lavfi testsrc -> one decoded frame", True, frame.get("ok") is True, shown(frame)))
        hook = check("pli_hook")
        rows.append(
            SmokeRow(
                "PLI hook point (RTCRtpSender._send_keyframe and its PLI dispatch)",
                True,
                hook.get("ok") is True and hook.get("value") is True,
                shown(hook),
            )
        )
        encoders = check("encoders")
        listed = [r for r in encoders.get("value") or [] if isinstance(r, dict)] if encoders.get("ok") else []
        summary = ", ".join(
            f"{r.get('encoder')}: {'yes' if r.get('available') else r.get('note') or 'no'}" for r in listed
        )
        vp8 = any(r.get("kind") == "vp8" and r.get("available") is True for r in listed)
        rows.append(SmokeRow("VP8 encoder available", True, vp8, summary or shown(encoders)))
        h264 = any(r.get("kind") == "h264" and r.get("available") is True for r in listed)
        rows.append(SmokeRow("H.264 through Media Foundation", False, h264, summary or shown(encoders)))
        rows.append(
            SmokeRow(
                "the worker's own verdict (exit code 0)",
                True,
                returncode == 0 and report.get("ok") is True,
                f"exit code {returncode}",
            )
        )
    elif report is not None:
        imports = check("imports")
        error = str(imports.get("error", ""))
        rows.append(
            SmokeRow(
                "Screen view is not in this build (aiortc and av cannot be imported)",
                True,
                imports.get("ok") is False and error.startswith(("ModuleNotFoundError", "ImportError")),
                shown(imports),
            )
        )
        rows.append(
            SmokeRow(
                "the worker says it is not ready (exit code 1)",
                True,
                returncode == 1 and report.get("ok") is False,
                f"exit code {returncode}",
            )
        )
    rows.append(SmokeRow("no crash log", True, not crash_log, "logs/crash.log was written" if crash_log else "none"))
    return rows


# --- the frozen app: bundle-notices ---------------------------------------------------------------------


@dataclass(frozen=True)
class PackageNotice:
    """One installed distribution and the licence files it ships (``texts``: file name and text)."""

    name: str
    version: str
    licence: str
    texts: tuple[tuple[str, str], ...]


def distribution_licence(meta: Any) -> str:
    """The licence a distribution declares: ``License-Expression``, a short ``License`` field, or its classifiers."""
    expression = str(meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    declared = str(meta.get("License") or "").strip()
    if declared and "\n" not in declared and len(declared) <= 80:
        return declared
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return "; ".join(classifiers) or "not declared (see its licence files below)"


def distribution_texts(dist: Any) -> tuple[tuple[str, str], ...]:
    """The licence files a distribution ships: its ``License-File`` entries (``.dist-info/licenses/`` or
    ``.dist-info/``), else any LICENSE/COPYING/NOTICE/AUTHORS file in its ``.dist-info``."""
    out: dict[str, str] = {}
    for name in dist.metadata.get_all("License-File") or []:
        for candidate in (f"licenses/{name}", name):
            text = dist.read_text(candidate)
            if text is not None:
                out[name] = text
                break
    if not out:
        for file in dist.files or []:
            parts = Path(str(file)).parts
            if len(parts) >= 2 and parts[0].endswith(".dist-info") and _LICENCE_NAME.search(parts[-1]):
                text = dist.read_text("/".join(parts[1:]))
                if text is not None:
                    out[parts[-1]] = text
    return tuple(sorted(out.items()))


def installed_packages(fallbacks: Path | None = None, exclude: frozenset[str] = BUILD_ONLY) -> list[PackageNotice]:
    """Every distribution in the running interpreter's environment except ``exclude`` and ``types-*`` stubs. A
    package that ships no licence file gets ``fallbacks/<normalised name>.txt`` (``packaging/licenses/python``:
    the text from the project's own repository at that version) when one exists."""
    import importlib.metadata as md

    seen: dict[str, PackageNotice] = {}
    for dist in md.distributions():
        name = str(dist.metadata.get("Name") or "").strip()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if not name or key in exclude or key.startswith("types-") or key in seen:
            continue
        texts = distribution_texts(dist)
        fallback = fallbacks / f"{key}.txt" if fallbacks is not None else None
        if not texts and fallback is not None and fallback.is_file():
            note = f"{fallback.name}, from the project's source repository (the package ships no licence file)"
            texts = ((note, fallback.read_text(encoding="utf-8")),)
        seen[key] = PackageNotice(name, str(dist.version), distribution_licence(dist.metadata), texts)
    return sorted(seen.values(), key=lambda p: p.name.lower())


def python_runtime_licence() -> tuple[str, str | None]:
    """(where, text) of the build interpreter's own LICENSE.txt, which names the libraries compiled into it."""
    import sysconfig

    base = Path(sys.base_prefix)
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for path in (base / "LICENSE.txt", base / "LICENSE", stdlib / "LICENSE.txt", stdlib / "LICENSE"):
        if path.is_file():
            return path.name, path.read_text(encoding="utf-8", errors="replace")
    return "LICENSE.txt", None


def render_bundle_notices(
    head: str, packages: Sequence[PackageNotice], runtime: tuple[str, str | None], python_version: str
) -> tuple[str, list[str]]:
    """THIRD_PARTY_NOTICES.txt for the frozen app: ``head`` (Part 1 and the PART 2 heading), the package list,
    the Python runtime's licence, then each package's licence files. Returns the text and what was not found."""
    missing: list[str] = []
    lines = [head.rstrip("\n"), "", "Python packages in this build (name, version, licence):"]
    lines += [f"  {p.name} {p.version}: {p.licence}" for p in packages]
    lines += ["", "", f"== Python runtime: CPython {python_version} ({runtime[0]}) =="]
    if runtime[1] is None:
        missing.append("the Python runtime's licence")
        lines.append(f"{NOT_FOUND}: the build interpreter has no LICENSE.txt; add CPython's licence text here]")
    else:
        lines.append(runtime[1].rstrip("\n"))
    for package in packages:
        lines += ["", "", f"== {package.name} {package.version}: {package.licence} =="]
        if not package.texts:
            missing.append(package.name)
            lines.append(f"{NOT_FOUND}: {package.name} {package.version} ships no licence file; add its licence text]")
        for file_name, text in package.texts:
            lines += [f"-- {file_name} --", text.rstrip("\n")]
    return "\n".join(lines) + "\n", missing


def bundle_notices(head_path: Path, licences: Path, out_dir: Path) -> list[str]:
    """Write ``out_dir/THIRD_PARTY_NOTICES.txt`` and copy ``licences/*.txt`` to ``out_dir/licenses``. Returns what
    was not found (also marked in the file, so ``check --frozen`` sees it)."""
    text, missing = render_bundle_notices(
        head_path.read_text(encoding="utf-8"),
        installed_packages(licences / "python"),
        python_runtime_licence(),
        ".".join(str(n) for n in sys.version_info[:3]),
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / NOTICES_NAME).write_text(text, encoding="utf-8")
    target = out_dir / "licenses"
    target.mkdir(exist_ok=True)
    for path in sorted(licences.glob("*.txt")):
        (target / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return missing


def _annotate(prefix: str, line: str) -> str:
    """A GitHub Actions annotation on the runner (``::warning::``), the plain line elsewhere."""
    return f"::{prefix} title=licence gate::{line}" if os.environ.get("GITHUB_ACTIONS") == "true" else line


# --- command line -------------------------------------------------------------------------------------


def _load(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: not a JSON object")
    return data


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="licence_gate.py", description=__doc__.split("\n\n")[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("configure-summary", help="check FFmpeg's configure output")
    p.add_argument("log")
    p = sub.add_parser("collect", help="write a report from a venv with the wheel, or from a folder of DLLs")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--wheel-env", type=Path)
    src.add_argument("--folder", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="apply R0-R10 (or, with --frozen, the frozen-app rules); exit 1 on any violation")
    p.add_argument("report")
    p.add_argument("--dlls-only", action="store_true", help="a --folder report: skip R5-R8 (no FFmpeg metadata)")
    p.add_argument("--frozen", action="store_true", help="a --folder report of dist/DoMe: R1 R3 R4 R9 and F1-F4")
    p.add_argument("--screen", choices=("present", "absent"), help="with --frozen: was Screen view built in")
    p.add_argument("--strict", action="store_true", help="with --frozen: warnings fail too (release builds)")
    p = sub.add_parser("notices", help="write the THIRD_PARTY_NOTICES draft inventory")
    p.add_argument("report")
    p.add_argument("--pacman", type=Path, required=True)
    p.add_argument("--ffmpeg-commit", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("smoke", help="functional checks inside the venv that has the wheel")
    p.add_argument("--expect-version", required=True)
    p.add_argument("--log", type=Path)
    p = sub.add_parser("shipping-av", help="is av pinned to DoMe's LGPL wheel on Windows? prints screen=present|absent")
    p.add_argument("--pyproject", type=Path, required=True)
    p.add_argument("--lock", type=Path, required=True)
    p.add_argument("--github-output", type=Path, help="also append screen=... to this file (GITHUB_OUTPUT)")
    p = sub.add_parser("screen-self-test", help="run the frozen DoMe.exe screen-worker --self-test and check it")
    p.add_argument("--exe", type=Path, required=True)
    p.add_argument("--expect", choices=("present", "absent"), required=True)
    p.add_argument("--out", type=Path, required=True, help="where to keep the worker's JSON report")
    p.add_argument("--timeout", type=float, default=300.0)
    p = sub.add_parser("bundle-notices", help="write THIRD_PARTY_NOTICES.txt and licenses/ for the frozen app")
    p.add_argument("--head", type=Path, required=True, help="packaging/THIRD_PARTY_NOTICES.txt (Part 1)")
    p.add_argument("--licenses", type=Path, required=True, help="packaging/licenses")
    p.add_argument("--out-dir", type=Path, required=True, help="dist/DoMe")
    args = parser.parse_args(argv)

    if args.command == "configure-summary":
        violations, notes = check_configure_summary(Path(args.log).read_text(encoding="utf-8", errors="replace"))
        for line in [v.line() for v in violations] + notes:
            print(line)
        print("configure summary: " + ("FAILED" if violations else "passed"))
        return 1 if violations else 0
    if args.command == "collect":
        report = collect_wheel_env(args.wheel_env) if args.wheel_env else collect_folder(args.folder)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {args.out} ({len(report['dlls'])} DLLs)")
        return 0
    if args.command == "check" and args.frozen:
        if args.screen is None:
            parser.error("check --frozen needs --screen present|absent")
        violations, warnings = evaluate_frozen(_load(args.report), screen=args.screen, strict=args.strict)
        for v in violations:
            print(_annotate("error", v.line()))
        for v in warnings:
            print(_annotate("warning", "WARN " + v.line().removeprefix("FAIL ")))
        print(
            f"licence gate (frozen app, Screen view {args.screen}{', strict' if args.strict else ''}): "
            f"{'FAILED' if violations else 'passed'} ({len(violations)} violation(s), {len(warnings)} warning(s))"
        )
        return 1 if violations else 0
    if args.command == "check":
        violations = evaluate(_load(args.report), dlls_only=args.dlls_only)
        for v in violations:
            print(v.line())
        print(f"licence gate: {'FAILED' if violations else 'passed'} ({len(violations)} violation(s))")
        return 1 if violations else 0
    if args.command == "shipping-av":
        try:
            screen, detail = shipping_av(
                tomllib.loads(args.pyproject.read_text(encoding="utf-8")),
                tomllib.loads(args.lock.read_text(encoding="utf-8")),
            )
        except GateError as exc:
            print(_annotate("error", f"shipping-av: FAILED: {exc}"))
            return 1
        print(f"screen={screen}")
        print(f"shipping-av: Screen view {'is built in' if screen == 'present' else 'is left out'}: {detail}")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as fh:
                fh.write(f"screen={screen}\n")
        return 0
    if args.command == "screen-self-test":
        with tempfile.TemporaryDirectory(prefix="dome-self-test-") as state:
            try:
                returncode, stdout, stderr = run_screen_self_test(args.exe, Path(state), timeout=args.timeout)
            except subprocess.TimeoutExpired:
                returncode, stdout, stderr = -1, "", f"timed out after {args.timeout:.0f} s"
            crash = Path(state) / "logs" / "crash.log"
            crash_text = crash.read_text(encoding="utf-8", errors="replace") if crash.is_file() else ""
        report = parse_self_test(stdout)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        rows = evaluate_screen_self_test(report, returncode, expect=args.expect, crash_log=bool(crash_text))
        print(format_smoke(rows))
        failed = any(r.required and not r.ok for r in rows)
        if failed:
            # The worker's stderr (redacted by its own log pipeline) and the crash log (masked by crashguard)
            # explain a failure; neither holds a frame or an address.
            print("--- worker stderr (last 40 lines) ---\n" + "\n".join(stderr.splitlines()[-40:]))
            if crash_text:
                print("--- crash.log (last 40 lines) ---\n" + "\n".join(crash_text.splitlines()[-40:]))
        print(f"screen-worker self-test (Screen view {args.expect}): {'FAILED' if failed else 'passed'}")
        return 1 if failed else 0
    if args.command == "bundle-notices":
        missing = bundle_notices(args.head, args.licenses, args.out_dir)
        for item in missing:
            print(_annotate("warning", f"WARN F4 notices: no licence text found for {item}"))
        print(f"wrote {args.out_dir / NOTICES_NAME} and {args.out_dir / 'licenses'} ({len(missing)} missing text(s))")
        return 0
    if args.command == "notices":
        text = render_notices(
            _load(args.report),
            report_name=Path(args.report).name,
            pacman_text=args.pacman.read_text(encoding="utf-8", errors="replace"),
            ffmpeg_commit=args.ffmpeg_commit,
        )
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    # smoke
    import av  # type: ignore[import-not-found]  # only in the venv that holds the wheel

    def import_aiortc() -> object:
        import aiortc  # type: ignore[import-not-found]

        return aiortc

    rows = smoke_checks(av, args.expect_version, import_aiortc)
    text = format_smoke(rows)
    print(text)
    if args.log:
        args.log.write_text(text + "\n", encoding="utf-8")
    return 1 if any(r.required and not r.ok for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
