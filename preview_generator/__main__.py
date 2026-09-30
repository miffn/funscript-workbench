"""JSON-lines CLI. Stdout is reserved for progress/result/error messages."""

import argparse
from dataclasses import fields
import json
from pathlib import Path
import signal
import sys
import threading

from .generator import Config, GenerationCancelled, GenerationError, generate
from .scripts import AXES


def _json_out(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def parser():
    result = argparse.ArgumentParser(description="Generate four standalone WebM/GIF pairs without opening OFS.")
    result.add_argument("--config", type=Path, help="JSON object containing Config fields; see README")
    result.add_argument("--work-id", help="Complete numbered-folder ID, e.g. S064")
    result.add_argument("--video", type=Path, help="Explicit original video file")
    result.add_argument("--script", action="append", default=[], metavar="AXIS=PATH",
                        help="Explicit axis mapping; repeat for all selected axes. Omit to use exact OFS filename siblings.")
    result.add_argument("--output-dir", type=Path)
    result.add_argument("--renderer", type=Path)
    result.add_argument("--model", help="GLB file, or builtin for the OFS default white cylinder (default)")
    result.add_argument("--hud-text-scale", type=float, help="OFS axis HUD text scale, 0.7..1.8 (default 1.1)")
    result.add_argument("--no-axis-hud", action="store_true", help="Hide axis value bars and stroke badge")
    result.add_argument("--cache-dir", type=Path)
    result.add_argument("--force", action="store_true", help="Re-encode instead of reusing verified results")
    result.add_argument("--clip-seconds", type=float)
    result.add_argument("--percentages", type=float, nargs="+", help="Fractions, e.g. 0.2 0.4 0.6 0.8")
    result.add_argument("--width", type=int)
    result.add_argument("--height", type=int)
    result.add_argument("--fps", type=int)
    result.add_argument("--gif-width", type=int)
    result.add_argument("--gif-height", type=int)
    result.add_argument("--gif-fps", type=int)
    result.add_argument("--simulator-width", type=int)
    result.add_argument("--simulator-height", type=int)
    result.add_argument("--margin", type=int)
    result.add_argument("--pitch-range", type=float)
    result.add_argument("--camera-yaw", type=float)
    result.add_argument("--camera-pitch", type=float)
    result.add_argument("--camera-distance", type=float)
    result.add_argument("--camera-fov", type=float)
    result.add_argument("--model-scale", type=float)
    result.add_argument("--threads", type=int)
    result.add_argument("--crf", type=int)
    result.add_argument("--no-audio", action="store_true")
    return result


def make_config(args):
    if args.config:
        value = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("Config JSON must be an object")
        unknown = set(value) - {f.name for f in fields(Config)}
        if unknown:
            raise ValueError(f"Unknown Config fields: {', '.join(sorted(unknown))}")
        # Explicit CLI overrides win, but no implicit parser default changes JSON.
    else:
        value = {}
    for name, item in vars(args).items():
        if name not in ("config", "script", "force", "no_audio", "no_axis_hud") and item is not None:
            value[name] = tuple(item) if name == "percentages" else item
    if args.script:
        mappings = {}
        for selection in args.script:
            axis, separator, path = selection.partition("=")
            if not separator or axis not in AXES or not path:
                raise ValueError(f"Invalid script selection: {selection}; use AXIS=PATH")
            if axis in mappings:
                raise ValueError(f"Multiple selections for {axis}; choose one script version")
            mappings[axis] = path
        value["scripts"] = mappings
    if args.force:
        value["force"] = True
    if args.no_audio:
        value["include_audio"] = False
    if args.no_axis_hud:
        value["show_axis_hud"] = False
    if "work_id" not in value or "video" not in value:
        raise ValueError("Provide --work-id and --video, or include both in --config")
    return Config(**value)


def main(argv=None):
    args = parser().parse_args(argv)
    cancellation = threading.Event()

    def request_cancel(_signum, _frame):
        cancellation.set()

    # CLI is normally the main thread. Library callers use their own Event.
    old_handlers = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, request_cancel)
    try:
        config = make_config(args)
        result = generate(config, on_progress=_json_out, cancel_event=cancellation)
        _json_out({"event": "result", "status": result["status"], "work_id": result["work_id"],
                   "manifest": str(Path(result["output_dir"]) / "manifest.json"),
                   "outputs": result["outputs"], "warnings": result["warnings"]})
        return 0
    except GenerationCancelled as exc:
        _json_out({"event": "error", "status": "cancelled", "message": str(exc)})
        return 130
    except (GenerationError, ValueError, OSError, TypeError) as exc:
        _json_out({"event": "error", "status": "failed", "message": str(exc)})
        return 1
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
