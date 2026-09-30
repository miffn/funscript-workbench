"""OFS axis association and absolute-time sampling, with strict input checks."""

from bisect import bisect_left
from dataclasses import dataclass
import json
import math
from pathlib import Path

AXES = ("stroke", "surge", "sway", "twist", "roll", "pitch")


class ScriptError(ValueError):
    pass


@dataclass(frozen=True)
class Script:
    path: Path
    times: tuple[float, ...]
    positions: tuple[float, ...]

    def position(self, seconds: float) -> float:
        """OFS linear interpolation, normalized to 0..1, with endpoint clamping.

        OFS times before the first action fall through to the LAST position.
        Fix that boundary bug here by holding the first position; interior
        interpolation stays identical, and callers report coverage warnings.
        Duplicate timestamps are rejected on load (the source set's ordering differs
        between versions, and a simultaneous conflicting position is ambiguous).
        """
        if len(self.times) == 1:
            return self.positions[0]
        index = bisect_left(self.times, seconds)
        if index < len(self.times) and self.times[index] == seconds:
            return self.positions[index]
        if 0 < index < len(self.times):
            fraction = (seconds - self.times[index - 1]) / (self.times[index] - self.times[index - 1])
            return self.positions[index - 1] + fraction * (self.positions[index] - self.positions[index - 1])
        return self.positions[0] if index == 0 else self.positions[-1]


def load_script(path: Path) -> Script:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScriptError(f"Cannot read script {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list) or not data["actions"]:
        raise ScriptError(f"Script has no actions: {path}")
    actions = []
    for index, action in enumerate(data["actions"]):
        if not isinstance(action, dict):
            raise ScriptError(f"Invalid action {index}: {path}")
        at, pos = action.get("at"), action.get("pos")
        if (isinstance(at, bool) or isinstance(pos, bool) or not isinstance(at, (float, int))
                or not isinstance(pos, (float, int)) or not math.isfinite(at)
                or not math.isfinite(pos) or at < 0 or not 0 <= pos <= 100):
            raise ScriptError(f"Invalid timestamp/position at action {index}: {path}")
        actions.append((float(at) / 1000, float(pos) / 100))
    actions.sort()
    if any(a[0] == b[0] for a, b in zip(actions, actions[1:])):
        raise ScriptError(f"Duplicate action timestamps: {path}")
    return Script(path, tuple(a[0] for a in actions), tuple(a[1] for a in actions))


def discover_scripts(video: Path) -> dict[str, Path]:
    """Associate only exact OFS filename conventions, never unrelated versions."""
    expected = {f"{video.stem}{'' if axis == 'stroke' else '.' + axis}.funscript".casefold(): axis
                for axis in AXES}
    result = {}
    for path in sorted(video.parent.iterdir()):
        if not path.is_file():
            continue
        axis = expected.get(path.name.casefold())
        if axis:
            if axis in result:
                raise ScriptError(f"Multiple scripts for {axis}: {result[axis]} and {path}; select explicitly")
            result[axis] = path.resolve()
    if not result:
        raise ScriptError(f"No exact-name scripts associated with {video}; select --script axis=path explicitly")
    return result


def frame_values(scripts: dict[str, Script], start: float, frame_count: int, fps: int) -> list[list[float]]:
    return [[scripts[axis].position(start + frame / fps) if axis in scripts else 0.5
             for axis in AXES] for frame in range(frame_count)]
