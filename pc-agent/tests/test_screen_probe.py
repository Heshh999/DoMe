"""The Screen view Phase 0 probe skeleton (dome_agent/screen/probe.py, design §18/§19 WP0.2) with the fake
backend: the matrix, the result table, the §18 go/no-go evaluation and its strict JSON, the command line, and
that importing the probe pulls in no media or Windows module. No capture or encode code exists to test."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dome_agent.screen import probe
from dome_agent.screen.probe import FakeProbeBackend, GateInput, ProbeResult, ProbeSpec


def gate_file(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "gate.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


ALL_MET = {
    "ios_playback_safari": True,
    "ios_playback_home_screen": True,
    "first_frame_s": 2.5,
    "glass_to_glass_ms": 140,
    "pc_cpu_percent": 6.5,
    "relayed_turn_works": True,
    "mdns_direct_works": True,
}


def test_default_specs_are_the_2x3_matrix() -> None:
    specs = probe.default_specs()
    assert [(s.source, s.encoder) for s in specs] == [
        ("ddagrab", "h264_mf_hw"),
        ("ddagrab", "h264_mf_sw"),
        ("ddagrab", "vp8"),
        ("wgc", "h264_mf_hw"),
        ("wgc", "h264_mf_sw"),
        ("wgc", "vp8"),
    ]
    assert {(s.width, s.height, s.fps, s.seconds) for s in specs} == {(1280, 720, 30, 5.0)}


class RaisingBackend(FakeProbeBackend):
    def run(self, spec: ProbeSpec) -> ProbeResult:
        if spec.encoder == "vp8":
            self.calls.append(spec)
            raise RuntimeError("encoder vanished")
        return super().run(spec)


def test_run_matrix_asks_for_every_spec_in_order_and_keeps_failures() -> None:
    fake = RaisingBackend({("wgc", "h264_mf_hw"): "MF_E_TRANSFORM_NOT_POSSIBLE_FOR_CURRENT_MEDIATYPE"})
    specs = probe.default_specs()
    results = probe.run_matrix(fake, specs)
    assert fake.calls == specs
    assert [r.spec for r in results] == specs
    failed = {(r.spec.source, r.spec.encoder): r.error for r in results if r.error}
    assert failed == {
        ("ddagrab", "vp8"): "RuntimeError: encoder vanished",
        ("wgc", "h264_mf_hw"): "MF_E_TRANSFORM_NOT_POSSIBLE_FOR_CURRENT_MEDIATYPE",
        ("wgc", "vp8"): "RuntimeError: encoder vanished",
    }
    for r in results:
        if r.error:  # a failed row carries no number that could be read as a result
            assert (r.frames, r.achieved_fps, r.cpu_percent, r.encode_latency_ms_p50) == (0, 0.0, None, None)


def test_format_table_one_line_per_spec_and_the_footer() -> None:
    specs = probe.default_specs()
    results = probe.run_matrix(FakeProbeBackend({("ddagrab", "vp8"): "libvpx not available"}), specs)
    text = probe.format_table(results)
    body, footer = text.split("\n\n")
    lines = body.splitlines()
    assert len(lines) == 1 + len(specs)
    assert lines[0].split() == ["source", "encoder", "size", "fps", "cpu%", "p50ms", "p95ms", "first-frame", "result"]
    [failed] = [line for line in lines if "failed:" in line]
    assert failed.split()[:3] == ["ddagrab", "vp8", "1280x720"] and failed.endswith("failed: libvpx not available")
    assert "ok (150 frames in 5 s)" in lines[1] and "30.0" in lines[1]
    assert text.endswith(probe.TABLE_FOOTER) and footer.startswith("No image was written.")


def test_gate_all_unknown_is_incomplete() -> None:
    verdicts = probe.evaluate_gate(GateInput())
    assert len(verdicts) == 7
    assert [v.status for v in verdicts[:-1]] == ["not yet verified on a device"] * 6
    assert verdicts[-1].status == "informational"
    assert probe.gate_decision(verdicts) == "incomplete"


def test_gate_all_met_is_go(tmp_path: Path) -> None:
    verdicts = probe.evaluate_gate(probe.load_gate_input(gate_file(tmp_path, ALL_MET)))
    assert all(v.status in ("pass", "informational") for v in verdicts)
    assert probe.gate_decision(verdicts) == "go"


@pytest.mark.parametrize(
    ("key", "failing", "passing"),
    [("first_frame_s", 4.0, 3.9), ("glass_to_glass_ms", 250.0, 249.0), ("pc_cpu_percent", 10.0, 9.9)],
)
def test_gate_limits_are_strict(key: str, failing: float, passing: float) -> None:
    over = GateInput(**{**ALL_MET, key: failing})  # type: ignore[arg-type]
    under = GateInput(**{**ALL_MET, key: passing})  # type: ignore[arg-type]
    assert probe.gate_decision(probe.evaluate_gate(over)) == "no-go"
    assert probe.gate_decision(probe.evaluate_gate(under)) == "go"


def test_one_fail_beats_unknowns_and_a_false_bool_fails() -> None:
    assert probe.gate_decision(probe.evaluate_gate(GateInput(relayed_turn_works=False))) == "no-go"
    assert probe.gate_decision(probe.evaluate_gate(GateInput(ios_playback_safari=True))) == "incomplete"


def test_mdns_is_informational_and_never_flips_the_decision() -> None:
    verdicts = probe.evaluate_gate(GateInput(**{**ALL_MET, "mdns_direct_works": False}))  # type: ignore[arg-type]
    assert verdicts[-1].status == "informational" and verdicts[-1].value == "no"
    assert probe.gate_decision(verdicts) == "go"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"first_frame": 2.0}, "unknown key 'first_frame'"),
        ({"glass_to_glass_ms": "140"}, "'glass_to_glass_ms' must be a number"),
        ({"pc_cpu_percent": True}, "'pc_cpu_percent' must be a number"),
        ({"relayed_turn_works": "yes"}, "'relayed_turn_works' must be true, false or null"),
        ({"first_frame_s": -1}, "'first_frame_s' must not be negative"),
        ([1, 2], "expected a JSON object"),
    ],
)
def test_load_gate_input_is_strict(tmp_path: Path, data: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        probe.load_gate_input(gate_file(tmp_path, data))


def test_load_gate_input_missing_and_null_mean_not_measured(tmp_path: Path) -> None:
    loaded = probe.load_gate_input(gate_file(tmp_path, {"first_frame_s": 3, "glass_to_glass_ms": None}))
    assert loaded == GateInput(first_frame_s=3.0)


def test_main_gate_table_and_json(tmp_path: Path) -> None:
    path = gate_file(tmp_path, {"first_frame_s": 3.1})
    out = io.StringIO()
    assert probe.main(["gate", str(path)], out=out) == 0
    text = out.getvalue()
    assert "First frame after" in text and "3.1 s" in text and "Decision: incomplete" in text
    out = io.StringIO()
    assert probe.main(["gate", str(path), "--json"], out=out) == 0
    data = json.loads(out.getvalue())
    assert data["decision"] == "incomplete" and len(data["verdicts"]) == 7
    assert data["verdicts"][2] == {
        "criterion": 'First frame after "Show screen"',
        "threshold": "< 4 s",
        "value": "3.1 s",
        "status": "pass",
    }


def test_main_gate_bad_file_exits_2(tmp_path: Path) -> None:
    out = io.StringIO()
    assert probe.main(["gate", str(gate_file(tmp_path, {"oops": 1}))], out=out) == 2
    assert "unknown key 'oops'" in out.getvalue()


def test_main_matrix_with_the_fake_backend() -> None:
    fake = FakeProbeBackend()
    out = io.StringIO()
    assert probe.main(["matrix"], backend=fake, out=out) == 0
    assert fake.calls == probe.default_specs()
    assert out.getvalue().rstrip().endswith(probe.TABLE_FOOTER)
    fake = FakeProbeBackend()
    out = io.StringIO()
    argv = ["matrix", "--seconds", "2", "--size", "1920x1080", "--fps", "15", "--only", "wgc:vp8", "--json"]
    assert probe.main(argv, backend=fake, out=out) == 0
    assert fake.calls == [ProbeSpec("wgc", "vp8", 1920, 1080, 15, 2.0)]
    [row] = json.loads(out.getvalue())
    assert row["spec"]["encoder"] == "vp8" and row["frames"] == 30 and row["error"] is None


@pytest.mark.parametrize(
    "argv", [["matrix", "--only", "dxgi:vp8"], ["matrix", "--size", "720p"], ["matrix", "--fps", "0"]]
)
def test_main_matrix_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        probe.main(argv, backend=FakeProbeBackend(), out=io.StringIO())
    assert exc.value.code == 2


def test_main_matrix_without_a_backend_needs_windows() -> None:
    """No capture or encode backend exists on any platform yet: every message and the help say so."""
    out = io.StringIO()
    assert probe.main(["matrix"], out=out) == 2
    message = out.getvalue().strip()
    assert message == (probe.NEEDS_WINDOWS if sys.platform != "win32" else probe.NO_WINDOWS_BACKEND)
    for text in (probe.NEEDS_WINDOWS, probe.NO_WINDOWS_BACKEND, probe.build_parser().format_help()):
        assert "not written yet" in text, text
    assert "Windows" in probe.NEEDS_WINDOWS and "Only `gate` works" in probe.NEEDS_WINDOWS


def test_importing_the_probe_pulls_in_no_media_or_windows_module() -> None:
    # a fresh interpreter: this test process may already hold modules other tests imported
    code = (
        "import sys, dome_agent.screen.probe; "
        "print(sorted(m for m in ('av', 'aiortc', 'win32api', 'pywintypes', 'comtypes') if m in sys.modules))"
    )
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=root)
    assert done.stdout.strip() == "[]"
