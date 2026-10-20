"""Licence gate for DoMe's own FFmpeg + PyAV build (Screen view, roadmap #52; design §5.5 and §19 WP0.1).

    python -I licence_gate.py configure-summary ffmpeg-configure.log
    python -I licence_gate.py collect (--wheel-env VENV | --folder DIR) --out licence-report.json
    python -I licence_gate.py check licence-report.json [--dlls-only]
    python -I licence_gate.py notices licence-report.json --pacman pacman-qi.txt --ffmpeg-commit SHA --out FILE
    python -I licence_gate.py smoke --expect-version VERSION [--log FILE]

Why it exists: DoMe is closed-source, so the FFmpeg it carries must stay LGPL-2.1-or-later. The stock PyAV
wheels on PyPI bundle x264/x265 (GPL) and report "LGPL version 3" (design §5.5), so one wrong wheel or one
wrong configure flag would quietly change what DoMe may distribute. This gate makes that a build failure.

It runs now on the custom wheel built by ``.github/workflows/ffmpeg-lgpl.yml`` and, from Phase 1 (WP1.6), on
the frozen ``dist/DoMe`` folder (``collect --folder``; the allow-list then grows by the interpreter's own
DLLs, each added together with its notice). It is a build-time tool: it never ships and nothing in
``dome_agent`` imports it. The design names it ``license_gate.py`` (§5.5, §15, §19); the file is spelled
``licence_gate.py`` and the design is to be reconciled.

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

Standard library only; ``pefile`` is used by ``collect`` when importable. ``packaging/`` is outside ruff and
mypy on purpose, so the module keeps its own type hints and is unit-tested by ``tests/test_licence_gate.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
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


def scan_dlls(folder: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".dll"):
        tables = pe_tables(path)
        out.append(
            {
                "name": path.name,
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
    return {"schema": SCHEMA, "source": "folder", "dlls": scan_dlls(folder)}


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
    p = sub.add_parser("check", help="apply R0-R9 to a report; exit 1 on any violation")
    p.add_argument("report")
    p.add_argument("--dlls-only", action="store_true", help="a --folder report: skip R5-R8 (no FFmpeg metadata)")
    p = sub.add_parser("notices", help="write the THIRD_PARTY_NOTICES draft inventory")
    p.add_argument("report")
    p.add_argument("--pacman", type=Path, required=True)
    p.add_argument("--ffmpeg-commit", required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("smoke", help="functional checks inside the venv that has the wheel")
    p.add_argument("--expect-version", required=True)
    p.add_argument("--log", type=Path)
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
    if args.command == "check":
        violations = evaluate(_load(args.report), dlls_only=args.dlls_only)
        for v in violations:
            print(v.line())
        print(f"licence gate: {'FAILED' if violations else 'passed'} ({len(violations)} violation(s))")
        return 1 if violations else 0
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
