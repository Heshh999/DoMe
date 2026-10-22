"""The licence gate of pc-agent/packaging/licence_gate.py (Screen view WP0.1, design §5.5), on Linux: the rules
against hand-built reports (planted libx264, libx265, libopenh264 and non-free DLLs, a second decoder, GPL,
version3, x264, x265, openh264 and fdk-aac flags, licence strings, imports, exports, delvewheel's mangled names,
the run-time filters), the configure-summary check against real FFmpeg n8.1.3 configure output, the notices
draft and the smoke checks against a fake ``av``. Nothing here builds FFmpeg or imports PyAV; the workflow does
that on Windows.

``fixtures/ffmpeg-n8.1.3-configure.log`` is configure's output for n8.1.3 (commit 1041abdc, the workflow's pin)
with the workflow's flags, captured on Linux by cross-compiling with x86_64-w64-mingw32 (hence the closing
pkg-config warning). libvpx and libopus were not installed there, so their names were added by hand to the
"External libraries" and "Enabled encoders" lines; everything else is as configure printed it."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

GATE_PATH = Path(__file__).resolve().parents[1] / "packaging" / "licence_gate.py"
HASH = "3f2a9c0d4b5e6f708192a3b4c5d6e7f8"
CONFIGURATION = (
    "--prefix=/c/ffmpeg-lgpl --target-os=mingw32 --arch=x86_64 --enable-shared --disable-static --disable-gpl "
    "--disable-nonfree --disable-version3 --disable-autodetect --disable-network --disable-everything "
    "--enable-d3d11va --enable-mediafoundation --enable-libvpx --enable-libopus --enable-indev=lavfi "
    "--enable-decoder=wrapped_avframe --enable-encoder=h264_mf --enable-encoder=libvpx_vp8 --enable-encoder=libopus"
)
LGPL = "LGPL version 2.1 or later"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("licence_gate", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["licence_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = load()


def dll(name: str, imports: list[str] | None = None, exports: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "imports": imports or ["KERNEL32.dll"], "exports": exports or [], "pe_parsed": True}


@pytest.fixture
def good_report() -> dict[str, Any]:
    names = ["avcodec-62", "avdevice-62", "avfilter-11", "avformat-62", "avutil-60", "swresample-6", "swscale-9"]
    dlls = [dll(f"{n}-{HASH}.dll", exports=[f"{n.split('-')[0]}_version"]) for n in names]
    dlls += [
        dll(f"libvpx-1-{HASH}.dll", exports=["vpx_codec_encode"]),
        dll(f"libopus-0-{HASH}.dll", exports=["opus_encode"]),
        dll(f"libgcc_s_seh-1-{HASH}.dll"),
        dll(f"libwinpthread-1-{HASH}.dll", exports=["pthread_create"]),
    ]
    return {
        "schema": 1,
        "source": "wheel-env",
        "av_version": "17.1.0+lgpl.ffmpeg8.1.3",
        "configuration": CONFIGURATION,
        "licenses": dict.fromkeys(gate.LIBRARIES, LGPL),
        "decoders": ["wrapped_avframe"],
        "encoders": ["h264_mf", "libopus", "libvpx"],
        "filters": [
            "abuffer",
            "abuffersink",
            "aresample",
            "buffer",
            "buffersink",
            "ddagrab",
            "format",
            "scale",
            "testsrc",
        ],
        "packages": {"av": "17.1.0+lgpl.ffmpeg8.1.3", "aiortc": "1.15.0", "aioice": "0.10.2", "pylibsrtp": "1.0.0"},
        "dlls": dlls,
    }


def rules(violations: list[Any]) -> set[str]:
    return {v.rule.split()[0] for v in violations}


def test_good_report_passes(good_report: dict[str, Any]) -> None:
    assert gate.evaluate(good_report) == []


def test_planted_libx264_in_a_folder_fails_name_and_allow_list(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "libx264-165.dll").write_bytes(b"")
    report = gate.collect_folder(tmp_path)
    [planted] = report["dlls"]
    assert planted == {
        "name": "libx264-165.dll",
        "path": "sub/libx264-165.dll",
        "imports": [],
        "exports": [],
        "pe_parsed": False,
    }
    found = rules(gate.evaluate(report, dlls_only=True))
    assert {"R1", "R2"} <= found
    assert "R9" in found  # an unreadable PE fails closed too
    # without --dlls-only a folder report also fails for the FFmpeg metadata it could not collect
    assert {"R5", "R6", "R7", "R8"} <= rules(gate.evaluate(report))


@pytest.mark.parametrize("name", ["libx265.dll", "libopenh264-7.dll", "avcodec-nonfree-62.dll"])
def test_planted_x265_openh264_or_nonfree_fails_name_and_allow_list(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"")
    assert {"R1", "R2"} <= rules(gate.evaluate(gate.collect_folder(tmp_path), dlls_only=True))


def test_second_decoder_fails_r7_only(good_report: dict[str, Any]) -> None:
    good_report["configuration"] = CONFIGURATION + " --enable-decoder=h264"
    good_report["decoders"] = ["wrapped_avframe", "h264"]
    assert rules(gate.evaluate(good_report)) == {"R7"}


@pytest.mark.parametrize(
    "flag",
    [
        "--enable-gpl",
        "--enable-version3",
        "--enable-nonfree",
        "--enable-libx264",
        "--enable-libx265",
        "--enable-libopenh264",
        "--enable-libfdk-aac",
    ],
)
def test_forbidden_configure_flags_fail_r5(good_report: dict[str, Any], flag: str) -> None:
    good_report["configuration"] = CONFIGURATION + " " + flag
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R5 configure-flags" and violation.subject == flag


def test_missing_disable_network_fails_r5(good_report: dict[str, Any]) -> None:
    good_report["configuration"] = CONFIGURATION.replace("--disable-network ", "")
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R5 configure-flags" and violation.subject == "--disable-network"


def test_disable_flags_do_not_read_as_enable(good_report: dict[str, Any]) -> None:
    assert "--disable-gpl" in good_report["configuration"]  # the substring "-gpl" alone must not trip R5
    assert gate.evaluate(good_report) == []


def test_lgpl3_licence_string_fails_r6(good_report: dict[str, Any]) -> None:
    good_report["licenses"]["libavutil"] = "LGPL version 3 or later"
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R6 licence-strings" and violation.subject == "libavutil"


def test_missing_library_fails_r6(good_report: dict[str, Any]) -> None:
    del good_report["licenses"]["libswscale"]
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R6 licence-strings" and violation.detail == "missing"


def test_import_of_libopenh264_fails_r3(good_report: dict[str, Any]) -> None:
    good_report["dlls"][0]["imports"].append("libopenh264-7.dll")
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R3 import-deny" and "libopenh264-7.dll" in violation.detail


def test_missing_graph_filters_fail_r10(good_report: dict[str, Any]) -> None:
    """buffer/buffersink/abuffer/abuffersink cannot be checked in configure's summary (always built, never
    listed), so the built library must offer them."""
    good_report["filters"] = [f for f in good_report["filters"] if f not in ("buffer", "abuffersink")]
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R10 filters" and "abuffersink" in violation.detail and "buffer" in violation.detail
    del good_report["filters"]
    assert rules(gate.evaluate(good_report)) == {"R10"}


def test_import_of_libx264_fails_r3(good_report: dict[str, Any]) -> None:
    good_report["dlls"][0]["imports"].append("libx264-165.dll")
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R3 import-deny" and "libx264-165.dll" in violation.detail


@pytest.mark.parametrize("symbol", ["x264_encoder_open_165", "WelsCreateSVCEncoder", "pp_get_context"])
def test_denied_exports_fail_r4(good_report: dict[str, Any], symbol: str) -> None:
    good_report["dlls"][0]["exports"].append(symbol)
    [violation] = gate.evaluate(good_report)
    assert violation.rule == "R4 export-deny" and symbol in violation.detail


def test_mangled_names_pass_and_libstdcxx_is_not_allowed(good_report: dict[str, Any]) -> None:
    assert gate.dll_stem(f"AVCODEC-62-{HASH}.DLL") == "avcodec-62"
    assert gate.allowed(f"avcodec-62-{HASH}.dll") and gate.allowed("avcodec-62.dll")
    assert not gate.allowed(f"avcodec-62-{HASH[:-1]}.dll-extra")
    good_report["dlls"].append(dll(f"libstdc++-6-{HASH}.dll"))
    [violation] = gate.evaluate(good_report)
    assert (
        violation.rule == "R2 allow-list" and "add it to the allow-list and to the notices together" in violation.detail
    )


@pytest.mark.parametrize(
    "encoders",
    [["h264_mf", "libvpx", "libopus", "libx264"], ["h264_mf", "libvpx"], ["h264_mf", "libvpx_vp8", "libopus"]],
)
def test_encoder_set_must_be_exact(good_report: dict[str, Any], encoders: list[str]) -> None:
    # the last case: configure's component name is libvpx_vp8, but FFmpeg registers the encoder as "libvpx"
    good_report["encoders"] = encoders
    assert rules(gate.evaluate(good_report)) == {"R8"}


def test_empty_report_fails_closed() -> None:
    assert {"R0", "R5", "R6", "R7", "R8"} <= rules(gate.evaluate({"schema": 2}))


def test_notices_draft_is_an_honest_inventory(good_report: dict[str, Any]) -> None:
    pacman = (
        "Name            : mingw-w64-x86_64-libvpx\nVersion         : 1.17.0-1\nLicenses        : spdx:BSD-3-Clause\n\n"
        "Name            : mingw-w64-x86_64-opus\nVersion         : 1.6.1-1\nLicenses        : spdx:BSD-3-Clause\n"
    )
    text = gate.render_notices(
        good_report, report_name="licence-report.json", pacman_text=pacman, ffmpeg_commit="1041abdc"
    )
    assert text.startswith("DRAFT")
    assert "not reviewed by counsel" in text
    assert LGPL in text and CONFIGURATION in text and "commit 1041abdc" in text
    assert "[OFFER:" in text
    vpx = text.split(f"== libvpx-1-{HASH}.dll ==")[1].split("==")[0]
    opus = text.split(f"== libopus-0-{HASH}.dll ==")[1].split("==")[0]
    assert "BSD-3-Clause" in vpx and "1.17.0-1" in vpx
    assert "BSD-3-Clause" in opus and "1.6.1-1" in opus
    assert "GCC-exception-3.1" in text and "winpthreads" in text
    assert "aiortc 1.15.0" in text and "PyAV (av) 17.1.0+lgpl.ffmpeg8.1.3" in text
    # nothing reads as a shipped product: "ship" only in "not shipped", "available" only inside the offer
    assert re.findall(r"\bship\w*", text.lower()) == ["shipped"] and "not shipped" in text
    offer = text[text.index("[OFFER:") : text.index("]", text.index("[OFFER:"))]
    assert text.count("available") == offer.count("available") == 1


def test_check_cli_exit_codes(tmp_path: Path, good_report: dict[str, Any]) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps(good_report), encoding="utf-8")
    good_report["decoders"].append("h264")
    good_report["dlls"].append(dll("libx264-165.dll"))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(good_report), encoding="utf-8")

    def run(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-I", str(GATE_PATH), "check", str(path)], capture_output=True, text=True, check=False
        )

    ok = run(good)
    assert ok.returncode == 0 and "licence gate: passed" in ok.stdout
    failed = run(bad)
    assert failed.returncode == 1
    for rule in ("R1 name-deny", "R2 allow-list", "R7 decoders"):
        assert f"FAIL {rule}:" in failed.stdout


# --- configure summary ------------------------------------------------------------------------------


CONFIGURE_LOG = Path(__file__).resolve().parent / "fixtures" / "ffmpeg-n8.1.3-configure.log"


def summary(**overrides: str) -> str:
    """The captured n8.1.3 configure output, with whole blocks replaced: ``{"Enabled protocols:": "file"}``."""
    text = CONFIGURE_LOG.read_text(encoding="utf-8")
    for heading, body in overrides.items():
        pattern = re.compile(rf"^{re.escape(heading)}\n(?:.+\n)*", re.MULTILINE)
        assert pattern.search(text), heading
        text = pattern.sub(lambda _m, h=heading, b=body: h + "\n" + (b + "\n" if b else ""), text, count=1)
    return text


def test_configure_summary_passes() -> None:
    violations, notes = gate.check_configure_summary(summary())
    assert violations == [] and notes == []
    assert "buffer" not in summary()  # n8.1.3 never lists the always-built graph endpoints


def test_an_option_configure_ignored_is_a_failure() -> None:
    """With --enable-filter=buffer (and the three others) n8.1.3's configure only warns and never lists them; the
    gate must fail on the warning itself, not pass or fail by accident on the filter list."""
    ignored = [
        f"WARNING: Option --enable-filter={f} did not match anything"
        for f in ("buffer", "buffersink", "abuffer", "abuffersink")
    ]
    as_captured = summary().replace("\nWARNING: x86_64", "\n" + "\n".join(ignored) + "\nWARNING: x86_64")
    violations, _notes = gate.check_configure_summary(as_captured)
    assert [v.rule for v in violations] == ["C5 ignored-option"] * 4
    assert [v.subject for v in violations] == [
        f"--enable-filter={f}" for f in ("buffer", "buffersink", "abuffer", "abuffersink")
    ]
    # any other warning is not an ignored option
    assert gate.check_configure_summary(summary())[0] == []
    assert "WARNING: x86_64-w64-mingw32-pkg-config not found" in summary()


def test_configure_summary_failures() -> None:
    gpl = summary().replace(f"License: {LGPL}", "License: GPL version 2 or later")
    assert [v.rule for v in gate.check_configure_summary(gpl)[0]] == ["C1 licence"]
    # --enable-version3 turns the licence line into LGPL v3: not shippable either
    v3 = summary().replace(f"License: {LGPL}", "License: LGPL version 3 or later")
    [lgpl3] = gate.check_configure_summary(v3)[0]
    assert lgpl3.rule == "C1 licence" and "version 3" in lgpl3.detail
    for lib in ("libx265", "libopenh264"):
        [external] = gate.check_configure_summary(
            summary(**{"External libraries:": f"{lib}                 libopus                 libvpx\nmediafoundation"})
        )[0]
        assert external.rule == "C3 components" and external.subject == "external" and lib in external.detail
    net = summary().replace("network support           no", "network support           yes")
    assert [v.rule for v in gate.check_configure_summary(net)[0]] == ["C2 network"]
    [second] = gate.check_configure_summary(
        summary(**{"Enabled decoders:": "h264                    wrapped_avframe"})
    )[0]
    assert second.subject == "decoders" and "h264" in second.detail
    [proto] = gate.check_configure_summary(summary(**{"Enabled protocols:": "file"}))[0]
    assert proto.subject == "protocols"
    [ext] = gate.check_configure_summary(summary(**{"External libraries:": "libvpx  libopus  mediafoundation  zlib"}))[
        0
    ]
    assert ext.subject == "external" and "zlib" in ext.detail
    # ddagrab silently dropped by configure (a failed dependency check) is a failure, not a note
    [dropped] = gate.check_configure_summary(summary(**{"Enabled filters:": "aresample color format scale"}))[0]
    assert dropped.rule == "C4 filters" and "ddagrab" in dropped.detail
    extra, notes = gate.check_configure_summary(summary(**{"Enabled filters:": summary_filters() + " null"}))
    assert extra == [] and notes and "null" in notes[0]
    missing = "\n".join(line for line in summary().splitlines() if line != "Enabled indevs:")
    assert [v.subject for v in gate.check_configure_summary(missing)[0]] == ["indevs"]


def summary_filters() -> str:
    return " ".join(sorted(gate.CONFIGURE_FILTERS))


def test_configure_summary_cli(tmp_path: Path) -> None:
    log = tmp_path / "configure.log"
    log.write_text(summary(), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-I", str(GATE_PATH), "configure-summary", str(log)], capture_output=True, text=True
    )
    assert done.returncode == 0 and "configure summary: passed" in done.stdout


# --- smoke --------------------------------------------------------------------------------------------


class FakeContext:
    def __init__(self, name: str, packets: int) -> None:
        self.name, self.packets, self.options = name, packets, {}

    def encode(self, frame: Any) -> list[object]:
        return [object()] * self.packets if frame is None else []


def fake_av(
    *,
    version: str = "17.1.0+lgpl.ffmpeg8.1.3",
    decoders: tuple[str, ...] = ("wrapped_avframe",),
    filters: frozenset[str] = gate.RUNTIME_FILTERS | {"aresample", "color", "hwdownload", "scale_d3d11"},
) -> Any:
    encoders = ("h264_mf", "libvpx", "libopus")

    def codec(name: str, mode: str = "r") -> object:
        if name not in (decoders if mode == "r" else encoders):
            raise ValueError(name)
        return object()

    def open_(graph: str, format: str) -> Any:
        assert format == "lavfi"
        if graph.startswith("ddagrab"):
            raise OSError("no desktop on the runner")
        pix = graph.rsplit("format=", 1)[1]
        frame = SimpleNamespace(width=64, height=64, format=SimpleNamespace(name=pix))
        return SimpleNamespace(decode=lambda video: iter([frame]), close=lambda: None)

    def create(name: str, mode: str) -> FakeContext:
        if name == "h264_mf":
            raise RuntimeError("no hardware encoder")
        return FakeContext(name, 1)

    return SimpleNamespace(
        __version__=version,
        _core=SimpleNamespace(library_meta={lib: {"license": LGPL} for lib in gate.LIBRARIES}),
        codecs_available=set(decoders) | set(encoders),
        Codec=codec,
        filter=SimpleNamespace(filters_available=set(filters)),
        open=open_,
        CodecContext=SimpleNamespace(create=create),
        VideoFrame=lambda w, h, fmt: SimpleNamespace(pts=None),
    )


def test_smoke_required_pass_and_runner_limits_are_reported_only() -> None:
    rows = gate.smoke_checks(fake_av(), "17.1.0+lgpl.ffmpeg8.1.3", lambda: object())
    required = [r for r in rows if r.required]
    assert required and all(r.ok for r in required)
    reported = {r.name: r for r in rows if not r.required}
    assert set(reported) == {"h264_mf hw_encoding=1, one 1280x720 frame", "ddagrab one frame"}
    assert all(r.status == "REPORTED (failed)" for r in reported.values())
    table = gate.format_smoke(rows)
    assert "PASS" in table and "lavfi testsrc -> one NV12 frame" in table and "FAIL" not in table


def test_smoke_fails_when_a_graph_filter_is_missing() -> None:
    rows = gate.smoke_checks(
        fake_av(filters=frozenset({"ddagrab", "scale", "format", "testsrc"})),
        "17.1.0+lgpl.ffmpeg8.1.3",
        lambda: object(),
    )
    [failed] = [r for r in rows if r.required and not r.ok]
    assert (
        failed.name.startswith("graph filters listed") and "abuffer" in failed.detail and "buffersink" in failed.detail
    )


def test_smoke_fails_on_wrong_version_second_decoder_and_missing_aiortc() -> None:
    def no_aiortc() -> object:
        raise ImportError("No module named 'aiortc'")

    rows = gate.smoke_checks(
        fake_av(version="17.1.0", decoders=("wrapped_avframe", "h264")), "17.1.0+lgpl.ffmpeg8.1.3", no_aiortc
    )
    failed = {r.name for r in rows if r.required and not r.ok}
    assert failed == {"av version is the local build", "decoders == {wrapped_avframe}", "import aiortc"}
    assert all(r.status == "FAIL" for r in rows if r.name in failed)


def test_the_workflow_configures_what_the_gate_expects() -> None:
    """ffmpeg-lgpl.yml's configure line and the gate's rules are one decision written twice; keep them equal."""
    workflow = (Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ffmpeg-lgpl.yml").read_text("utf-8")
    configure = workflow.split("./configure", 1)[1].split("> ../logs/ffmpeg-configure.log", 1)[0]
    flags = set(configure.replace("\\", " ").split())
    assert set(gate.REQUIRED_FLAGS) <= flags
    assert not {f for f in flags if any(f == x or f.startswith(x + "=") for x in gate.FORBIDDEN_FLAGS)}
    assert {f.split("=", 1)[1] for f in flags if f.startswith("--enable-filter=")} == set(gate.CONFIGURE_FILTERS)
    # FFmpeg always builds the graph endpoints and configure ignores them by name (n8.1.3)
    assert not {"buffer", "buffersink", "abuffer", "abuffersink"} & set(gate.CONFIGURE_FILTERS)
    assert not {f for f in flags if f.split("=", 1)[-1] in ("buffer", "buffersink", "abuffer", "abuffersink")}
    assert {"buffer", "buffersink", "abuffer", "abuffersink"} <= gate.RUNTIME_FILTERS
    assert {f.split("=", 1)[1] for f in flags if f.startswith("--enable-encoder=")} == set(
        gate.CONFIGURE_EXACT["encoders"]
    )
    assert {f.split("=", 1)[1] for f in flags if f.startswith("--enable-decoder=")} == set(gate.EXPECTED_DECODERS)
    assert {f.split("=", 1)[1] for f in flags if f.startswith("--enable-indev=")} == set(gate.CONFIGURE_EXACT["indevs"])
    assert "--disable-postproc" not in flags  # FFmpeg 8 removed libpostproc; configure rejects unknown options
    # the gate, the notices and the smoke test all run from the workflow; it holds no secret and writes nothing
    for command in ("configure-summary", "collect --wheel-env", "notices", "check", "smoke --expect-version"):
        assert f"licence_gate.py {command}" in workflow
    assert "secrets." not in workflow and "contents: write" not in workflow
    assert "Spike output: not shipped, not device-tested." in workflow
