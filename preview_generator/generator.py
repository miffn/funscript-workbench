"""Portable orchestration for an offscreen OFS renderer and FFmpeg.

Public entry point: generate(Config(...), on_progress=callback,
                            cancel_event=threading.Event()).
The callback receives JSON-serializable dictionaries; return value is the saved
manifest. No application state, web server, GUI, shell command, or database.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Callable, Mapping
import uuid

from .scripts import AXES, ScriptError, discover_scripts, frame_values, load_script

VERSION = "1.0.0"
PROJECT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = Path("/mnt/d/Media/workspace/预览")
OFS_SOURCE = {"repository": "https://github.com/miffn/OFS-custom",
              "commit": "d341a387649a3a9cdd5757ad9f0b1a3ca83af7d8"}


class GenerationError(RuntimeError):
    pass


class GenerationCancelled(GenerationError):
    pass


@dataclass(frozen=True)
class Config:
    work_id: str
    video: str | Path
    scripts: Mapping[str, str | Path] | None = None
    output_dir: str | Path | None = None
    renderer: str | Path = PROJECT / "preview_generator/build/ofs-preview-renderer"
    model: str | Path = "builtin"
    percentages: tuple[float, ...] = (0.2, 0.4, 0.6, 0.8)
    clip_seconds: float = 10.0
    width: int = 1920
    height: int = 1080
    fps: int = 30
    gif_width: int = 192
    gif_height: int = 108
    gif_fps: int = 10
    simulator_width: int = 480
    simulator_height: int = 480
    margin: int = 32
    include_audio: bool = True
    pitch_range: float = 60.0
    camera_yaw: float = 0.0
    camera_pitch: float = 0.0
    camera_distance: float = 8.4
    camera_fov: float = 45.0
    model_scale: float = 0.8
    show_axis_hud: bool = True
    hud_text_scale: float = 1.1
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    crf: int = 32
    threads: int = 4
    force: bool = False
    cache_dir: str | Path = PROJECT / "data/preview-generator"


def sha256_file(path: Path, check_cancel: Callable[[], None] = lambda: None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(4 * 1024 * 1024):
            check_cancel()
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clip_ranges(duration: float, percentages: tuple[float, ...], seconds: float) -> list[dict]:
    """Percentages are start times. Shift at EOF; duplicate starts are omitted."""
    length = min(duration, seconds)
    clips, starts = [], set()
    for index, percentage in enumerate(percentages, 1):
        start = max(0.0, min(duration * percentage, duration - length))
        # Quantization avoids floating-point duplicate starts after EOF clamping.
        start = round(start, 6)
        if start in starts:
            continue
        starts.add(start)
        clips.append({"index": index, "percentage": percentage, "start_seconds": start,
                      "duration_seconds": length})
    return clips


def _validate_config(config: Config) -> tuple[Path, Path, Path, dict[str, Path]]:
    if not isinstance(config.work_id, str) or not re.fullmatch(r"(?:S\d{3,}(?:_\d{3,})?|work-[1-9]\d*)", config.work_id):
        raise GenerationError("work_id must be a complete ID such as S064 or a stable key such as work-123")
    video = Path(config.video).expanduser().resolve()
    output = Path(config.output_dir or OUTPUT_ROOT / config.work_id).expanduser().resolve()
    renderer = Path(config.renderer).expanduser().resolve()
    if not video.is_file():
        raise GenerationError(f"Video does not exist: {video}")
    if not renderer.is_file() or not os.access(renderer, os.X_OK):
        raise GenerationError(f"Renderer executable missing: {renderer}; build it first")
    if str(config.model) != "builtin" and not Path(config.model).is_file():
        raise GenerationError(f"Model does not exist: {config.model}")
    # Prevent an output typo from modifying source materials. The user authorized
    # only workspace/预览/<ID> as a generated-file exception inside D: inventory.
    inventory_root = Path("/mnt/d/Media").resolve()
    authorized_output = (OUTPUT_ROOT / config.work_id).resolve()
    staging_output = output.parent == authorized_output and re.fullmatch(r"\.workbench-stage-[a-z0-9_]{8}", output.name)
    if output.is_relative_to(inventory_root) and output != authorized_output and not staging_output:
        raise GenerationError(f"Inventory output must be {OUTPUT_ROOT / config.work_id}")
    if output == video.parent or output.is_relative_to(video.parent) and not output.is_relative_to(OUTPUT_ROOT):
        raise GenerationError("Output directory overlaps the source material directory")
    for name in ("width", "height", "fps", "gif_width", "gif_height", "gif_fps",
                 "simulator_width", "simulator_height", "threads"):
        value = getattr(config, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise GenerationError(f"{name} must be a positive integer")
    if config.width % 2 or config.height % 2:
        raise GenerationError("WebM dimensions must be even for yuv420p")
    if (isinstance(config.margin, bool) or not isinstance(config.margin, int) or config.margin < 0
            or config.simulator_width + config.margin > config.width
            or config.simulator_height + config.margin > config.height):
        raise GenerationError("Simulator and margin must fit inside the preview canvas")
    if not 16 <= config.simulator_width <= 4096 or not 16 <= config.simulator_height <= 4096:
        raise GenerationError("Simulator dimensions must be in [16,4096]")
    if (not math.isfinite(config.clip_seconds) or config.clip_seconds <= 0
            or not config.percentages or any(not math.isfinite(p) or not 0 <= p < 1 for p in config.percentages)):
        raise GenerationError("Clip duration must be positive and percentages must be in [0,1)")
    for name in ("pitch_range", "camera_yaw", "camera_pitch", "camera_distance", "camera_fov", "model_scale"):
        if not math.isfinite(getattr(config, name)):
            raise GenerationError(f"{name} must be finite")
    if (not 0 <= config.pitch_range <= 90 or not 1.5 <= config.camera_distance <= 20
            or not 15 <= config.camera_fov <= 80 or not .1 <= config.model_scale <= 5
            or not -180 <= config.camera_yaw <= 180 or not -180 <= config.camera_pitch <= 180):
        raise GenerationError("Invalid camera/model parameters")
    if isinstance(config.crf, bool) or not isinstance(config.crf, int) or not 0 <= config.crf <= 63:
        raise GenerationError("VP9 crf must be in [0,63]")
    if (not isinstance(config.include_audio, bool) or not isinstance(config.force, bool)
            or not isinstance(config.show_axis_hud, bool)):
        raise GenerationError("include_audio, force and show_axis_hud must be booleans")
    if (isinstance(config.hud_text_scale, bool) or not isinstance(config.hud_text_scale, (int, float))
            or not math.isfinite(config.hud_text_scale) or not .7 <= config.hud_text_scale <= 1.8):
        raise GenerationError("hud_text_scale must be a finite number in [0.7,1.8]")
    if config.scripts is not None and not isinstance(config.scripts, Mapping):
        raise GenerationError("scripts must be an axis-to-path mapping")
    try:
        scripts = ({axis: Path(path).expanduser().resolve() for axis, path in config.scripts.items()}
                   if config.scripts is not None else discover_scripts(video))
    except ScriptError as exc:
        raise GenerationError(str(exc)) from exc
    if not scripts or any(axis not in AXES for axis in scripts):
        raise GenerationError(f"Select at least one script; axes are {', '.join(AXES)}")
    if len(set(scripts.values())) != len(scripts):
        raise GenerationError("The same file cannot be assigned to multiple axes")
    if any(not path.is_file() for path in scripts.values()):
        raise GenerationError("A selected script does not exist")
    return video, output, renderer, scripts


def renderer_arguments(config: Config, renderer: Path, values_path: Path, raw_path: Path,
                       axes_present: Mapping | list | tuple) -> list:
    """Explicit renderer protocol; only axes with associated input files get HUD."""
    command = [renderer, "--frames-json", values_path, "--output", raw_path,
               "--width", config.simulator_width, "--height", config.simulator_height,
               "--axes-present", ",".join(axis for axis in AXES if axis in axes_present),
               "--hud-text-scale", config.hud_text_scale]
    if str(config.model) != "builtin":
        command += ["--model", Path(config.model).resolve()]
    if not config.show_axis_hud:
        command += ["--no-axis-hud"]
    for option in ("pitch_range", "camera_yaw", "camera_pitch", "camera_distance", "camera_fov", "model_scale"):
        command += ["--" + option.replace("_", "-"), getattr(config, option)]
    return command


def generate(config: Config, on_progress: Callable[[dict], None] | None = None,
             cancel_event=None) -> dict:
    """Generate validated media atomically and return the persisted manifest.

    Progress callback: {event, work_id, stage, progress (0..1), message,
                        elapsed_seconds, clip_index?}. Cancellation event only
    needs is_set(). Failed/cancelled tasks terminate children and retain previous
    successful files. Reuse requires matching complete input/config/tool hashes,
    an intact output checksum, and a successful prior step.
    """
    started = time.monotonic()
    latest_progress = 0.0

    def check_cancel():
        if cancel_event is not None and cancel_event.is_set():
            raise GenerationCancelled("Generation cancelled")

    def emit(stage, progress, message, **extra):
        nonlocal latest_progress
        check_cancel()
        latest_progress = max(latest_progress, progress)
        if on_progress:
            on_progress({"event": "progress", "work_id": config.work_id, "stage": stage,
                         "progress": latest_progress, "message": message,
                         "elapsed_seconds": round(time.monotonic() - started, 3), **extra})

    manifest = None
    manifest_path = None
    scratch = None
    lock_path = None
    staged_files = []
    emit("check", 0.0, "Checking inputs and tools")
    video, output, renderer, script_paths = _validate_config(config)
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".preview-generator.lock"
    try:
        lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise GenerationError(f"Another generator owns {lock_path}; verify its PID before removing a stale lock") from exc
    try:
        with os.fdopen(lock, "w") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at": _utc_now()}))
    except BaseException:
        lock_path.unlink(missing_ok=True)
        raise

    def run(args, stage, progress, *, timeout=None):
        """No shell; child stderr goes to a bounded diagnostic tail on error."""
        check_cancel()
        with tempfile.TemporaryFile(mode="w+b") as diagnostic:
            try:
                proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=diagnostic)
            except OSError as exc:
                raise GenerationError(f"Cannot start {args[0]}: {exc}") from exc
            command_started = time.monotonic()
            heartbeat = command_started
            try:
                while True:
                    check_cancel()
                    try:
                        stdout, _ = proc.communicate(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        if timeout is not None and time.monotonic() - command_started > timeout:
                            raise GenerationError(f"Timed out during {stage}")
                        if time.monotonic() - heartbeat > 5:
                            emit(stage, progress, f"Working: {stage}")
                            heartbeat = time.monotonic()
            except BaseException:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                raise
            if proc.returncode:
                diagnostic.seek(0, os.SEEK_END)
                diagnostic.seek(max(0, diagnostic.tell() - 6000))
                detail = diagnostic.read().decode("utf-8", errors="replace")
                raise GenerationError(f"{stage} failed (exit {proc.returncode}): {detail}")
            return stdout.decode("utf-8", errors="replace")

    def probe(path):
        raw = run([config.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                  "probe", 0.01, timeout=60)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GenerationError(f"ffprobe returned invalid JSON for {path}") from exc

    def inspect_media(path, expected_width, expected_height, expected_duration, kind):
        info = probe(path)
        streams = info.get("streams", [])
        stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not stream or stream.get("width") != expected_width or stream.get("height") != expected_height:
            raise GenerationError(f"Unexpected generated {kind} dimensions: {path}")
        if stream.get("codec_name") != ("vp9" if kind == "video" else "gif"):
            raise GenerationError(f"Unexpected generated {kind} codec: {path}")
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or abs(duration - expected_duration) > 0.25:
            raise GenerationError(f"Unexpected generated {kind} duration {duration}; expected {expected_duration}: {path}")
        if kind == "gif" and any(s.get("codec_type") == "audio" for s in streams):
            raise GenerationError("GIF unexpectedly contains audio")
        return {"width": expected_width, "height": expected_height, "duration_seconds": duration,
                "codec": stream.get("codec_name"), "frame_rate": stream.get("avg_frame_rate"),
                "audio": any(s.get("codec_type") == "audio" for s in streams)}

    try:
        model = Path(config.model).resolve() if str(config.model) != "builtin" else None
        input_paths = [video, renderer, *script_paths.values()] + ([model] if model else [])
        input_stats = {path: (path.stat().st_size, path.stat().st_mtime_ns) for path in input_paths}

        def check_inputs_unchanged():
            for path, recorded in input_stats.items():
                try:
                    current = (path.stat().st_size, path.stat().st_mtime_ns)
                except OSError as exc:
                    raise GenerationError(f"Input disappeared during generation: {path}") from exc
                if current != recorded:
                    raise GenerationError(f"Input changed during generation; retry with stable files: {path}")

        info = probe(video)
        videos = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        if len(videos) != 1 or videos[0].get("disposition", {}).get("attached_pic", 0):
            raise GenerationError("Input must contain exactly one moving video stream")
        duration = float(videos[0].get("duration") or info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise GenerationError("Input has no positive finite video duration")
        try:
            scripts = {axis: load_script(path) for axis, path in script_paths.items()}
        except ScriptError as exc:
            raise GenerationError(str(exc)) from exc
        clips = clip_ranges(duration, config.percentages, config.clip_seconds)
        warnings = []
        if len(clips) < len(config.percentages):
            warnings.append("Short video: duplicate clamped clip ranges were omitted")
        for axis, script in scripts.items():
            if any(clip["start_seconds"] < script.times[0] or
                   clip["start_seconds"] + clip["duration_seconds"] > script.times[-1] for clip in clips):
                warnings.append(f"{axis} action coverage does not span every clip; endpoint positions are held")
        emit("fingerprint", 0.02, "Hashing complete video, scripts, renderer and model")
        video_input = {"path": str(video), "sha256": sha256_file(video, check_cancel),
                       "size": video.stat().st_size, "duration_seconds": duration}
        script_inputs = {axis: {"path": str(script_paths[axis]), "sha256": sha256_file(script_paths[axis], check_cancel),
                               "actions": len(script.times), "first_seconds": script.times[0],
                               "last_seconds": script.times[-1]} for axis, script in scripts.items()}
        tools = {"renderer": {"path": str(renderer), "sha256": sha256_file(renderer, check_cancel)},
                 "ffmpeg": run([config.ffmpeg, "-version"], "tools", 0.04, timeout=20).splitlines()[0],
                 "ffprobe": run([config.ffprobe, "-version"], "tools", 0.04, timeout=20).splitlines()[0],
                 "python_sources": {path.name: sha256_file(path, check_cancel)
                                    for path in (Path(__file__), Path(__file__).with_name("scripts.py"))}}
        # Explicit models participate in cache invalidation; builtin is implemented
        # by the fingerprinted renderer, sourced from the pinned OFS revision.
        model_input = {"path": str(model), "sha256": sha256_file(model, check_cancel)} if model else {"builtin": True}
        model_source = ({"kind": "explicit_glb", "renderer_source": dict(OFS_SOURCE)} if model
                        else {"kind": "ofs_builtin_cylinder", **OFS_SOURCE})
        hud = {"enabled": config.show_axis_hud, "text_scale": config.hud_text_scale,
               "axes_present": [axis for axis in AXES if axis in scripts], "source": dict(OFS_SOURCE)}
        check_inputs_unchanged()
        options = asdict(config)
        for key in ("video", "scripts", "output_dir", "renderer", "model", "force", "cache_dir", "ffmpeg", "ffprobe"):
            options.pop(key)
        fingerprint = hashlib.sha256(json.dumps({"version": VERSION, "video": video_input,
            "scripts": script_inputs, "model": model_input, "model_source": model_source,
            "hud": hud, "tools": tools, "options": options},
            sort_keys=True, allow_nan=False).encode()).hexdigest()
        manifest_path = output / "manifest.json"
        previous = {}
        if not config.force and manifest_path.is_file():
            try:
                previous = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(previous, dict) or previous.get("fingerprint") != fingerprint:
                    previous = {}
            except (ValueError, OSError):
                previous = {}
        manifest = {"schema_version": 1, "generator_version": VERSION, "work_id": config.work_id,
                    "fingerprint": fingerprint, "status": "running", "started_at": _utc_now(),
                    "video": video_input, "scripts": script_inputs, "missing_axes": [a for a in AXES if a not in scripts],
                    "axis_order": list(AXES), "model": model_input, "model_source": model_source,
                    "hud": hud, "tools": tools, "options": options,
                    "clips": clips, "warnings": warnings, "outputs": [], "output_dir": str(output)}
        _atomic_json(manifest_path, manifest)
        cache = Path(config.cache_dir).resolve()
        if cache.is_relative_to(Path("/mnt/d/Media").resolve()):
            raise GenerationError("Intermediate cache must not be in the inventory tree")
        cache.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f"{config.work_id}-", dir=cache))
        old_outputs = {item.get("filename"): item for item in previous.get("outputs", [])
                       if isinstance(item, dict)}
        tasks = len(clips) * 2
        completed = 0
        for clip in clips:
            index, clip_start, clip_duration = clip["index"], clip["start_seconds"], clip["duration_seconds"]
            for kind in ("video", "gif"):
                check_cancel()
                check_inputs_unchanged()
                progress = 0.05 + 0.9 * completed / tasks
                name = f"预览视频{index}.webm" if kind == "video" else f"预览gif{index}.gif"
                final_path = output / name
                old = old_outputs.get(name)
                if old and final_path.is_file() and sha256_file(final_path, check_cancel) == old.get("sha256"):
                    # Do not trust a pathname alone. Output hashes verify persisted media.
                    record = dict(old, reused=True, path=str(final_path))
                    manifest["outputs"].append(record)
                    completed += 1
                    _atomic_json(manifest_path, manifest)
                    emit("reuse", 0.05 + 0.9 * completed / tasks, f"Reused {name}", clip_index=index)
                    continue
                temporary_path = output / f".{Path(name).stem}.{uuid.uuid4().hex}{Path(name).suffix}"
                staged_files.append(temporary_path)
                base = [config.ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
                        "-threads", config.threads, "-filter_complex_threads", "1", "-filter_threads", "1"]
                # Seek in absolute video time and reset PTS, preserving decode accuracy
                # (FFmpeg's default accurate_seek applies when transcoding).
                if kind == "video":
                    count = math.ceil(clip_duration * config.fps - 1e-9)
                    values_path = scratch / f"axes-{index}.json"
                    values_path.write_text(json.dumps(frame_values(scripts, clip_start, count, config.fps)), encoding="utf-8")
                    raw_path = scratch / f"simulator-{index}.rgba"
                    emit("render", progress, f"Rendering simulator clip {index}", clip_index=index)
                    renderer_command = renderer_arguments(config, renderer, values_path, raw_path, scripts)
                    run(renderer_command, "render", progress)
                    expected_bytes = count * config.simulator_width * config.simulator_height * 4
                    if not raw_path.is_file() or raw_path.stat().st_size != expected_bytes:
                        raise GenerationError(f"Renderer frame count/size mismatch: expected {expected_bytes} bytes")
                    video_filter = (f"[0:v:0]setpts=PTS-STARTPTS,fps={config.fps},"
                                    f"scale={config.width}:{config.height}:force_original_aspect_ratio=decrease,"
                                    f"pad={config.width}:{config.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[base];"
                                    f"[1:v]setpts=PTS-STARTPTS[sim];"
                                    f"[base][sim]overlay=x=W-w-{config.margin}:y=H-h-{config.margin}:"
                                    "shortest=1:format=auto,format=yuv420p[out]")
                    command = base + ["-ss", f"{clip_start:.6f}", "-i", video,
                        "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size", f"{config.simulator_width}x{config.simulator_height}",
                        "-framerate", config.fps, "-i", raw_path, "-filter_complex", video_filter,
                        "-map", "[out]"]
                    if config.include_audio:
                        command += ["-map", "0:a:0?", "-af", "asetpts=PTS-STARTPTS", "-c:a", "libopus", "-b:a", "96k"]
                    else:
                        command += ["-an"]
                    command += ["-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-crf", config.crf,
                                "-b:v", "0", "-row-mt", "1", "-threads", config.threads, "-t", f"{clip_duration:.6f}", temporary_path]
                    emit("encode_video", progress, f"Encoding {name}", clip_index=index)
                    run(command, "encode_video", progress)
                    metadata = inspect_media(temporary_path, config.width, config.height, clip_duration, kind)
                    source_audio = any(stream.get("codec_type") == "audio" for stream in info.get("streams", []))
                    if metadata["audio"] != bool(config.include_audio and source_audio):
                        raise GenerationError("Generated preview audio does not match requested source-audio policy")
                    raw_path.unlink()
                    values_path.unlink()
                else:
                    gif_filter = (f"[0:v:0]trim=duration={clip_duration:.6f},setpts=PTS-STARTPTS,fps={config.gif_fps},"
                                  f"scale={config.gif_width}:{config.gif_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                                  f"pad={config.gif_width}:{config.gif_height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                                  "split[palette_in][image_in];[palette_in]palettegen=stats_mode=diff[palette];"
                                  "[image_in][palette]paletteuse=dither=sierra2_4a:diff_mode=rectangle[out]")
                    command = base + ["-ss", f"{clip_start:.6f}", "-i", video, "-filter_complex", gif_filter,
                                      "-map", "[out]", "-an", "-loop", "0", "-t", f"{clip_duration:.6f}", temporary_path]
                    emit("encode_gif", progress, f"Encoding {name}", clip_index=index)
                    run(command, "encode_gif", progress)
                    metadata = inspect_media(temporary_path, config.gif_width, config.gif_height, clip_duration, kind)
                checksum = sha256_file(temporary_path, check_cancel)
                with temporary_path.open("rb") as handle:
                    os.fsync(handle.fileno())
                check_cancel()
                check_inputs_unchanged()
                os.replace(temporary_path, final_path)
                record = {"kind": kind, "clip_index": index, "filename": name, "path": str(final_path),
                          "sha256": checksum, "size": final_path.stat().st_size, "reused": False, **metadata}
                manifest["outputs"].append(record)
                completed += 1
                _atomic_json(manifest_path, manifest)
                emit("saved", 0.05 + 0.9 * completed / tasks, f"Saved {name}", clip_index=index)
        manifest.update(status="completed", finished_at=_utc_now(), elapsed_seconds=round(time.monotonic() - started, 3))
        _atomic_json(manifest_path, manifest)
        emit("completed", 1.0, "All previews generated", manifest=str(manifest_path), output_dir=str(output))
        return manifest
    except BaseException as exc:
        if manifest is not None and manifest_path is not None:
            manifest.update(status="cancelled" if isinstance(exc, GenerationCancelled) else "failed",
                            error=str(exc), finished_at=_utc_now())
            _atomic_json(manifest_path, manifest)
        raise
    finally:
        for path in staged_files:
            path.unlink(missing_ok=True)
        if scratch:
            shutil.rmtree(scratch)
        if lock_path:
            lock_path.unlink(missing_ok=True)
