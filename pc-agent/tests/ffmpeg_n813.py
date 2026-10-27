"""The option names of the filters in the worker's Desktop Duplication graph, as FFmpeg n8.1.3 declares them (the
build DoMe ships; read from the n8.1.3 sources, commit 1041abdc, the ffmpeg-lgpl.yml pin):

* ``libavfilter/vsrc_ddagrab.c`` ``ddagrab_options[]`` (l.112-131): ``output_idx``, ``draw_mouse``, ``framerate``,
  ``video_size``, ``offset_x``, ``offset_y``, ``output_fmt``, ``allow_fallback``, ``force_fmt``, ``dup_frames``;
* ``libavfilter/vf_scale_d3d11.c`` ``scale_d3d11_options[]`` (l.434-438): ``width``, ``height``, ``format`` (no ``w`` or
  ``h``: those are ``scale``'s; device round 2 failed on "Could not set non-existent option 'w'");
* ``libavfilter/vf_hwdownload.c``: no options;
* ``libavfilter/vf_format.c`` ``options[]`` (l.184-188): ``pix_fmts``, ``color_spaces``, ``color_ranges``,
  ``alpha_modes``; one positional value is ``pix_fmts``.

Plain data, no PyAV: the licence-gate tests use it too."""

from __future__ import annotations

N813_FILTER_OPTIONS: dict[str, frozenset[str]] = {
    "ddagrab": frozenset(
        {
            "output_idx",
            "draw_mouse",
            "framerate",
            "video_size",
            "offset_x",
            "offset_y",
            "output_fmt",
            "allow_fallback",
            "force_fmt",
            "dup_frames",
        }
    ),
    "scale_d3d11": frozenset({"width", "height", "format"}),
    "hwdownload": frozenset(),
    "format": frozenset({"pix_fmts", "color_spaces", "color_ranges", "alpha_modes"}),
}
# filters whose first argument may be given without a name (vf_format.c's shorthand is pix_fmts)
N813_POSITIONAL: dict[str, str] = {"format": "pix_fmts"}


def unknown_options(name: str, args: str | None) -> list[str]:
    """The option names in ``args`` that filter ``name`` does not have in n8.1.3 (a positional value counts as the
    filter's shorthand option, when it has one). Raises KeyError for a filter outside the table."""
    known = N813_FILTER_OPTIONS[name]
    bad: list[str] = []
    for index, item in enumerate((args or "").split(":") if args else []):
        key, sep, _value = item.partition("=")
        if not sep:
            if index == 0 and name in N813_POSITIONAL:
                continue
            bad.append(f"<positional {key}>")
        elif key not in known:
            bad.append(key)
    return bad
