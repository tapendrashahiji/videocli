"""Input transforms: bring tagged, HDR or log sources into the Rec.709 working space before grading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from videocli.core.errors import UsageError

KNOWN = ("none", "auto", "hlg_to_rec709", "pq_to_rec709", "bt601_to_rec709", "bt2020_to_rec709")
LOG_HELP = {
    "s-log": "Sony S-Log: supply the matching camera LUT (e.g. SLog3SGamut3.Cine_To_LC-709.cube)",
    "c-log": "Canon C-Log: supply Canon's C-Log to BT.709 LUT",
    "v-log": "Panasonic V-Log: supply the VLog_to_V709 LUT",
    "log-c": "ARRI LogC: supply ARRI's LogC to Rec.709 LUT",
    "f-log": "Fujifilm F-Log: supply the F-Log to WDR/ETERNA LUT",
    "n-log": "Nikon N-Log: supply Nikon's N-Log 3D LUT",
    "d-log": "DJI D-Log: supply DJI's D-Log to Rec.709 LUT",
}


def choose_auto(probe: Any) -> tuple[str, list[dict[str, str]]]:
    """Pick a transform from probe colour metadata (decision: never guess silently)."""
    warnings = []
    v = probe.video
    if v is None:
        return "none", warnings
    c = v.color
    if c.hdr == "hlg":
        return "hlg_to_rec709", warnings
    if c.hdr == "pq":
        return "pq_to_rec709", warnings
    if c.log_guess:
        raise UsageError("BAD_ARGUMENT", f"Source looks like camera log ({c.log_guess}); an explicit LUT is required.",
                         hint=f"{LOG_HELP.get(c.log_guess, 'Supply the camera vendor LUT')}, then: videocli grade input NAME --source ID --transform FILE.cube")
    if c.primaries == "bt2020":
        return "bt2020_to_rec709", warnings
    if c.matrix in ("bt470bg", "smpte170m") or c.primaries in ("bt470bg", "smpte170m"):
        return "bt601_to_rec709", warnings
    if not (c.primaries or c.transfer or c.matrix):
        warnings.append({"code": "UNTAGGED_COLOR", "message": "Source has no colour tags; assuming Rec.709."})
    return "none", warnings


def esc(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    p = str(Path(path).resolve()).replace("\\", "/")
    return "'" + p.replace("'", r"'\''").replace(":", r"\:") + "'"


def filter_for(transform: str | None, tags: dict[str, Any] | None = None) -> str | None:
    """ffmpeg filter chain for a transform (applied right after decoding)."""
    if not transform or transform == "none":
        return None
    tin = {"hlg_to_rec709": "arib-std-b67", "pq_to_rec709": "smpte2084"}
    if transform in tin:
        return (f"zscale=tin={tin[transform]}:pin=bt2020:min=bt2020nc:t=linear:npl=203,format=gbrpf32le,"
                "zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p10le")
    if transform == "bt2020_to_rec709":
        return "zscale=pin=bt2020:min=bt2020nc:tin=bt709:p=bt709:m=bt709:t=bt709,format=yuv420p10le"
    if transform == "bt601_to_rec709":
        return "colorspace=all=bt709:iall=bt601-6-625:format=yuv420p10"
    if transform.startswith("lut:"):
        return f"format=gbrpf32le,lut3d=file={esc(transform[4:])}:interp=tetrahedral,format=yuv420p10le"
    raise UsageError("BAD_ARGUMENT", f"Unknown input transform {transform!r}.", hint=f"Use one of {', '.join(KNOWN)} or a .cube file.")
