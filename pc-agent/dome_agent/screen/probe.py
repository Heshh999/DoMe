"""Screen probe (Screen view, roadmap #52; design §18 and §19 WP0.2).

Development tool for the Phase 0 spike. Not a product feature; not reachable from the app; prints numbers
only, never pixels.

What it is for: before Phase 1 is built, the founder's own PC must show that capture (Desktop Duplication
through FFmpeg's ``ddagrab``, or Windows.Graphics.Capture) and encoding (Media Foundation H.264 in hardware
or software, or VP8) are fast and cheap enough. The probe captures 5 s per source × encoder into memory and
prints frames per second, CPU, encode latency and failures. It writes no file and keeps no frame.

What exists in Phase 0 (this file): argument parsing, the result table, the §18 go/no-go evaluation from a
JSON file the device round fills in by hand, and the :class:`ProbeBackend` seam with a fake. What does not
exist yet: any capture or encode code. That arrives with the device round as a Windows backend; until then
``matrix`` without an injected backend explains that and exits 2. This module must not import ``av``,
``aiortc`` or anything Windows-only (a test checks it), so it type-checks and runs on the Linux CI.

    python -m dome_agent.screen.probe matrix [--seconds N] [--size WxH] [--fps N] [--only SOURCE:ENCODER ...] [--json]
    python -m dome_agent.screen.probe gate PATH.json [--json]

Evidence: the logic is unit-tested with the fake; capture, encoding and every number are not yet verified
on a device.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Literal, Protocol, TextIO, get_args

Source = Literal["ddagrab", "wgc"]
Encoder = Literal["h264_mf_hw", "h264_mf_sw", "vp8"]
GateStatus = Literal["pass", "fail", "not yet verified on a device", "informational"]
Decision = Literal["go", "no-go", "incomplete"]

SOURCES: tuple[Source, ...] = get_args(Source)
ENCODERS: tuple[Encoder, ...] = get_args(Encoder)

NOT_VERIFIED: GateStatus = "not yet verified on a device"
TABLE_FOOTER = (
    "No image was written. These are runner/PC numbers, not device evidence until recorded in docs/ACCEPTANCE.md."
)
NEEDS_WINDOWS = (
    "The screen probe's capture and encode backend is not written yet (WP0.2), and it will need Windows "
    "(Desktop Duplication, Media Foundation). Only `gate` works in this build."
)
NO_WINDOWS_BACKEND = (
    "The Windows capture and encode backend of the screen probe is not written yet (WP0.2 device round). "
    "Only `gate` works in this build."
)


@dataclass(frozen=True)
class ProbeSpec:
    """One cell of the matrix: capture ``seconds`` of ``source`` at ``width``×``height`` and ``fps`` through ``encoder``."""

    source: Source
    encoder: Encoder
    width: int
    height: int
    fps: int
    seconds: float


@dataclass(frozen=True)
class ProbeResult:
    """What one cell measured. ``error`` set means the cell failed, and every number is then 0 or None:
    a failed cell never reports a partial figure that could be read as a result."""

    spec: ProbeSpec
    frames: int
    achieved_fps: float
    cpu_percent: float | None
    encode_latency_ms_p50: float | None
    encode_latency_ms_p95: float | None
    first_frame_ms: float | None
    error: str | None

    @classmethod
    def failed(cls, spec: ProbeSpec, error: str) -> ProbeResult:
        return cls(spec, 0, 0.0, None, None, None, None, error)


class ProbeBackend(Protocol):
    """The seam between the probe and the machine. The real backend (Windows only) arrives with the device
    round; tests use :class:`FakeProbeBackend`."""

    def run(self, spec: ProbeSpec) -> ProbeResult: ...


def default_specs() -> list[ProbeSpec]:
    """The §19 matrix: both sources × the three encoders at 1280×720, 30 fps, 5 s each."""
    return [ProbeSpec(s, e, 1280, 720, 30, 5.0) for s in SOURCES for e in ENCODERS]


def run_matrix(backend: ProbeBackend, specs: Sequence[ProbeSpec]) -> list[ProbeResult]:
    """Run every spec in order. A backend that raises becomes a failed row, so one broken encoder never
    hides the rest of the table."""
    results: list[ProbeResult] = []
    for spec in specs:
        try:
            results.append(backend.run(spec))
        except Exception as exc:  # any failure of one cell is that cell's result
            results.append(ProbeResult.failed(spec, f"{type(exc).__name__}: {exc}"))
    return results


def _num(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_table(results: Sequence[ProbeResult]) -> str:
    """Fixed-width table, one line per spec, then the footer that says what these numbers are not."""
    header = ("source", "encoder", "size", "fps", "cpu%", "p50ms", "p95ms", "first-frame", "result")
    widths = (8, 11, 10, 6, 6, 7, 7, 12)
    rows: list[tuple[str, ...]] = [header]
    for r in results:
        s = r.spec
        failed = r.error is not None
        rows.append(
            (
                s.source,
                s.encoder,
                f"{s.width}x{s.height}",
                "-" if failed else _num(r.achieved_fps),
                "-" if failed else _num(r.cpu_percent),
                "-" if failed else _num(r.encode_latency_ms_p50),
                "-" if failed else _num(r.encode_latency_ms_p95),
                "-" if failed else (_num(r.first_frame_ms, 0) + (" ms" if r.first_frame_ms is not None else "")),
                f"failed: {r.error}" if failed else f"ok ({r.frames} frames in {s.seconds:g} s)",
            )
        )
    lines = [" ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=False)) + " " + row[-1] for row in rows]
    return "\n".join(line.rstrip() for line in lines) + "\n\n" + TABLE_FOOTER


# --- the §18 go/no-go gate ------------------------------------------------------------------------------


@dataclass(frozen=True)
class GateInput:
    """The §18 measurements, filled in by hand after the device round. None = not measured yet."""

    ios_playback_safari: bool | None = None
    ios_playback_home_screen: bool | None = None
    first_frame_s: float | None = None
    glass_to_glass_ms: float | None = None
    pc_cpu_percent: float | None = None
    relayed_turn_works: bool | None = None
    mdns_direct_works: bool | None = None


@dataclass(frozen=True)
class GateVerdict:
    criterion: str
    threshold: str
    value: str
    status: GateStatus


# §18: every limit is strict ("< 4 s"), so a value equal to the limit fails.
GATE_THRESHOLDS: dict[str, float] = {"first_frame_s": 4.0, "glass_to_glass_ms": 250.0, "pc_cpu_percent": 10.0}
_BOOL_FIELDS = ("ios_playback_safari", "ios_playback_home_screen", "relayed_turn_works", "mdns_direct_works")
_NUMBER_FIELDS = tuple(GATE_THRESHOLDS)


def _yes_no(value: bool | None) -> str:
    return "not recorded" if value is None else ("yes" if value else "no")


def _bool_verdict(criterion: str, value: bool | None) -> GateVerdict:
    status: GateStatus = NOT_VERIFIED if value is None else ("pass" if value else "fail")
    return GateVerdict(criterion, "yes", _yes_no(value), status)


def _below_verdict(criterion: str, key: str, unit: str, value: float | None) -> GateVerdict:
    limit = GATE_THRESHOLDS[key]
    threshold = f"< {limit:g}{unit}"
    if value is None:
        return GateVerdict(criterion, threshold, "not recorded", NOT_VERIFIED)
    return GateVerdict(criterion, threshold, f"{value:g}{unit}", "pass" if value < limit else "fail")


def evaluate_gate(data: GateInput) -> list[GateVerdict]:
    """One row per §18 criterion, in the design's order."""
    return [
        _bool_verdict("Plays in Safari on iOS 18 and 26", data.ios_playback_safari),
        _bool_verdict("Plays in the home-screen app on iOS 18 and 26", data.ios_playback_home_screen),
        _below_verdict('First frame after "Show screen"', "first_frame_s", " s", data.first_frame_s),
        _below_verdict("Same-Wi-Fi glass-to-glass", "glass_to_glass_ms", " ms", data.glass_to_glass_ms),
        _below_verdict(
            "PC CPU at 720p30 with hardware encoding (worker + agent)", "pc_cpu_percent", " %", data.pc_cpu_percent
        ),
        _bool_verdict("Works relayed through Cloudflare TURN (relay forced)", data.relayed_turn_works),
        GateVerdict(
            "Same-Wi-Fi direct via mDNS from a standard-user process",
            "informational",
            _yes_no(data.mdns_direct_works),
            "informational",
        ),
    ]


def gate_decision(verdicts: Sequence[GateVerdict]) -> Decision:
    """``go`` only when every non-informational row passes; any fail is ``no-go``; otherwise ``incomplete``."""
    statuses = [v.status for v in verdicts if v.status != "informational"]
    if "fail" in statuses:
        return "no-go"
    if all(s == "pass" for s in statuses):
        return "go"
    return "incomplete"


def load_gate_input(path: Path) -> GateInput:
    """Read the device round's JSON strictly: an unknown key or a wrong type raises ValueError naming the key
    (a typo must not silently read as "not yet verified"). A missing key or ``null`` means not measured."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    known = {f.name for f in fields(GateInput)}
    for key in data:
        if key not in known:
            raise ValueError(f"{path}: unknown key {key!r}")
    values: dict[str, bool | float | None] = {}
    for key, value in data.items():
        if value is None:
            values[key] = None
        elif key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{path}: {key!r} must be true, false or null")
            values[key] = value
        else:
            if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
                raise ValueError(f"{path}: {key!r} must be a number or null")
            if value < 0:
                raise ValueError(f"{path}: {key!r} must not be negative")
            values[key] = float(value)
    return GateInput(**values)  # type: ignore[arg-type]  # each key's type was checked above


def format_gate(verdicts: Sequence[GateVerdict]) -> str:
    widths = (58, 15, 14)
    rows = [("criterion", "threshold", "value", "status")] + [
        (v.criterion, v.threshold, v.value, v.status) for v in verdicts
    ]
    lines = [" ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=False)) + " " + row[-1] for row in rows]
    return "\n".join(line.rstrip() for line in lines) + f"\n\nDecision: {gate_decision(verdicts)}"


# --- Fakes ------------------------------------------------------------------------------------------------


class FakeProbeBackend:
    """Returns scripted results per (source, encoder); records every spec it was asked to run; never
    allocates a frame. A scripted string is an error for that cell; an unscripted cell succeeds with
    synthetic numbers derived from its spec (test data only: the real command never uses this class)."""

    def __init__(self, script: Mapping[tuple[Source, Encoder], ProbeResult | str] | None = None) -> None:
        self.script = dict(script or {})
        self.calls: list[ProbeSpec] = []

    def run(self, spec: ProbeSpec) -> ProbeResult:
        self.calls.append(spec)
        scripted = self.script.get((spec.source, spec.encoder))
        if isinstance(scripted, str):
            return ProbeResult.failed(spec, scripted)
        if scripted is not None:
            return replace(scripted, spec=spec)
        frames = int(spec.fps * spec.seconds)
        return ProbeResult(spec, frames, float(spec.fps), 5.0, 4.0, 7.0, 120.0, None)


# --- command line -----------------------------------------------------------------------------------------


def _size(text: str) -> tuple[int, int]:
    width, sep, height = text.lower().partition("x")
    if not sep or not width.isdigit() or not height.isdigit() or int(width) <= 0 or int(height) <= 0:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}")
    return int(width), int(height)


def _cell(text: str) -> tuple[Source, Encoder]:
    source, sep, encoder = text.partition(":")
    if not sep or source not in SOURCES or encoder not in ENCODERS:
        raise argparse.ArgumentTypeError(
            f"expected SOURCE:ENCODER with SOURCE in {list(SOURCES)} and ENCODER in {list(ENCODERS)}, got {text!r}"
        )
    return source, encoder


def _positive(kind: type[int] | type[float]) -> Callable[[str], int | float]:
    def parse(text: str) -> int | float:
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"expected a positive number, got {text!r}") from None
        if value <= 0 or not math.isfinite(value):
            raise argparse.ArgumentTypeError(f"expected a positive number, got {text!r}")
        return value

    return parse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screen-probe",
        description="Screen view Phase 0 probe (development only; prints numbers, never pixels).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    matrix = sub.add_parser(
        "matrix", help="capture and encode per source x encoder (not written yet: WP0.2 device round; Windows only)"
    )
    matrix.add_argument("--seconds", type=_positive(float), default=5.0)
    matrix.add_argument("--size", type=_size, default=(1280, 720), metavar="WxH")
    matrix.add_argument("--fps", type=_positive(int), default=30)
    matrix.add_argument("--only", type=_cell, nargs="+", action="extend", metavar="SOURCE:ENCODER")
    matrix.add_argument("--json", action="store_true", help="print the results as JSON")
    gate = sub.add_parser("gate", help="evaluate the §18 go/no-go table from a JSON file")
    gate.add_argument("path", type=Path)
    gate.add_argument("--json", action="store_true", help="print the verdicts as JSON")
    return parser


def main(argv: list[str] | None = None, *, backend: ProbeBackend | None = None, out: TextIO = sys.stdout) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "gate":
        try:
            verdicts = evaluate_gate(load_gate_input(args.path))
        except (OSError, ValueError) as exc:
            print(f"screen-probe gate: {exc}", file=out)
            return 2
        if args.json:
            print(
                json.dumps({"decision": gate_decision(verdicts), "verdicts": [asdict(v) for v in verdicts]}), file=out
            )
        else:
            print(format_gate(verdicts), file=out)
        return 0

    if backend is None:
        # TODO(WP0.2 device round): the Windows backend lives in dome_agent/platform/windows/screen.py (§15);
        # it is imported here, behind sys.platform == "win32", once it exists. Nothing is imported in Phase 0.
        print(NEEDS_WINDOWS if sys.platform != "win32" else NO_WINDOWS_BACKEND, file=out)
        return 2
    width, height = args.size
    cells = args.only or [(s, e) for s in SOURCES for e in ENCODERS]
    specs = [ProbeSpec(s, e, width, height, args.fps, args.seconds) for s, e in cells]
    results = run_matrix(backend, specs)
    if args.json:
        print(json.dumps([asdict(r) for r in results]), file=out)
    else:
        print(format_table(results), file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
