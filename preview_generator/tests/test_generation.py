from dataclasses import replace
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from preview_generator.generator import Config, GenerationCancelled, GenerationError, generate, sha256_file

PROJECT = Path(__file__).resolve().parents[2]
RENDERER = PROJECT / "preview_generator/build/ofs-preview-renderer"
pytestmark = pytest.mark.skipif(not RENDERER.is_file() or not shutil.which("ffmpeg"),
                                reason="Requires built renderer and FFmpeg")


@pytest.fixture
def config(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    video = source / "fixture.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=red:size=192x108:rate=30:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)], check=True)
    for suffix, positions in (("", (0, 100)), (".pitch", (100, 0))):
        (source / f"fixture{suffix}.funscript").write_text(json.dumps({"actions": [
            {"at": 0, "pos": positions[0]}, {"at": 2000, "pos": positions[1]}]}), encoding="utf-8")
    return Config("S999", video, model="builtin", output_dir=tmp_path / "outputs",
                  cache_dir=tmp_path / "cache", width=192, height=108, fps=10, gif_width=96,
                  gif_height=54, gif_fps=10, simulator_width=64, simulator_height=64, margin=4,
                  clip_seconds=.2, threads=1)


def test_real_offscreen_multiaxis_four_pairs_audio_and_reuse(config):
    original = {p: sha256_file(p) for p in config.video.parent.iterdir()}
    progress = []
    result = generate(config, progress.append)
    assert result["status"] == "completed" and len(result["outputs"]) == 8
    assert set(result["scripts"]) == {"stroke", "pitch"}
    assert result["model"]["builtin"] is True
    assert result["model_source"]["kind"] == "ofs_builtin_cylinder"
    assert result["model_source"]["repository"] == "https://github.com/miffn/OFS-custom"
    assert result["model_source"]["commit"] == "d341a387649a3a9cdd5757ad9f0b1a3ca83af7d8"
    assert result["hud"]["axes_present"] == ["stroke", "pitch"] and result["hud"]["enabled"]
    assert len(result["missing_axes"]) == 4
    assert all(item["audio"] for item in result["outputs"] if item["kind"] == "video")
    assert all(not item["audio"] for item in result["outputs"] if item["kind"] == "gif")
    assert progress[-1]["progress"] == 1
    assert all(0 <= p["progress"] <= 1 for p in progress)
    assert [p["progress"] for p in progress] == sorted(p["progress"] for p in progress)
    # GIF remains plain source footage: a solid-red input cannot contain model pixels.
    gif = next(item for item in result["outputs"] if item["kind"] == "gif")
    rgb = subprocess.check_output(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", gif["path"],
                                   "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    pixels = [rgb[i:i + 3] for i in range(0, len(rgb), 3)]
    assert all(pixel[0] > 240 and pixel[1] < 15 and pixel[2] < 15 for pixel in pixels)
    rerun = generate(config)
    assert all(item["reused"] for item in rerun["outputs"])
    assert rerun["fingerprint"] == result["fingerprint"]
    assert all(sha256_file(path) == checksum for path, checksum in original.items())
    assert not list(Path(config.output_dir).glob(".*"))
    assert not list(Path(config.cache_dir).iterdir())


def test_failed_reencode_preserves_prior_success_and_records_failure(config, tmp_path):
    good = generate(replace(config, percentages=(.2,)))
    originals = {Path(item["path"]): item["sha256"] for item in good["outputs"]}
    failing_ffmpeg = tmp_path / "fail-ffmpeg"
    failing_ffmpeg.write_text("#!/usr/bin/env python3\nimport sys\n"
        "if '-version' in sys.argv: print('ffmpeg fake'); sys.exit(0)\n"
        "sys.stderr.write('intentional encoder failure'); sys.exit(2)\n")
    failing_ffmpeg.chmod(0o755)
    with pytest.raises(GenerationError, match="intentional encoder failure"):
        generate(replace(config, percentages=(.2,), ffmpeg=str(failing_ffmpeg)))
    assert all(sha256_file(path) == checksum for path, checksum in originals.items())
    manifest = json.loads((Path(config.output_dir) / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert not list(Path(config.output_dir).glob(".*"))
    assert not list(Path(config.cache_dir).iterdir())


def test_cancel_terminates_and_retains_old_media(config):
    good = generate(replace(config, percentages=(.2,)))
    original = {Path(item["path"]): item["sha256"] for item in good["outputs"]}
    cancellation = threading.Event()

    def progress(value):
        if value["stage"] == "render":
            cancellation.set()

    with pytest.raises(GenerationCancelled):
        generate(replace(config, percentages=(.2,), force=True), progress, cancellation)
    assert all(sha256_file(path) == checksum for path, checksum in original.items())
    assert json.loads((Path(config.output_dir) / "manifest.json").read_text())["status"] == "cancelled"
    assert not list(Path(config.output_dir).glob(".*"))


def test_existing_lock_prevents_simultaneous_writes(config):
    output = Path(config.output_dir)
    output.mkdir()
    (output / ".preview-generator.lock").write_text('{"pid": 1}')
    with pytest.raises(GenerationError, match="Another generator"):
        generate(config)
    assert (output / ".preview-generator.lock").is_file()


def test_source_folder_is_not_valid_output(config):
    with pytest.raises(GenerationError, match="overlaps"):
        generate(replace(config, output_dir=config.video.parent))


@pytest.mark.parametrize("overrides", [{"margin": True}, {"crf": True}, {"camera_distance": 1},
    {"camera_fov": 90}, {"camera_pitch": 181}, {"model_scale": .05}, {"simulator_width": 15},
    {"include_audio": 1}, {"scripts": []}, {"show_axis_hud": 1}, {"show_axis_hud": "false"},
    {"hud_text_scale": True}, {"hud_text_scale": .6}, {"hud_text_scale": 1.9},
    {"hud_text_scale": float("nan")}, {"hud_text_scale": "1.1"}])
def test_invalid_parameters_are_rejected_before_tool_work(config, overrides):
    with pytest.raises(GenerationError):
        generate(replace(config, **overrides))
    assert not Path(config.output_dir).exists()


def test_inventory_output_permission_is_narrow(config):
    with pytest.raises(GenerationError, match="Inventory output must"):
        generate(replace(config, output_dir="/mnt/d/Media/workspace/S999"))


def test_input_change_during_encode_aborts_atomic_commit(config):
    config = replace(config, percentages=(.2,))
    result = generate(config)
    original = {Path(item["path"]): item["sha256"] for item in result["outputs"]}

    def mutate(event):
        if event["stage"] == "encode_video":
            pitch = config.video.parent / "fixture.pitch.funscript"
            pitch.write_text(json.dumps({"actions": [{"at": 0, "pos": 99}, {"at": 2000, "pos": 0}]}))

    with pytest.raises(GenerationError, match="Input changed"):
        generate(replace(config, force=True), mutate)
    assert all(sha256_file(path) == checksum for path, checksum in original.items())


def test_damaged_output_is_not_reused(config):
    config = replace(config, percentages=(.2,))
    result = generate(config)
    gif = next(item for item in result["outputs"] if item["kind"] == "gif")
    Path(gif["path"]).write_bytes(b"damaged")
    result = generate(config)
    assert next(item for item in result["outputs"] if item["kind"] == "video")["reused"]
    regenerated = next(item for item in result["outputs"] if item["kind"] == "gif")
    assert not regenerated["reused"] and regenerated["size"] > 7


def test_any_axis_change_invalidates_preview_group(config):
    config = replace(config, percentages=(.2,))
    old = generate(config)
    pitch = config.video.parent / "fixture.pitch.funscript"
    pitch.write_text(json.dumps({"actions": [{"at": 0, "pos": 50}, {"at": 2000, "pos": 50}]}))
    new = generate(config)
    assert new["fingerprint"] != old["fingerprint"]
    assert all(not item["reused"] for item in new["outputs"])


def test_hud_settings_change_invalidates_cached_group(config):
    config = replace(config, percentages=(.2,))
    old = generate(config)
    new = generate(replace(config, show_axis_hud=False, hud_text_scale=1.2))
    assert new["fingerprint"] != old["fingerprint"]
    assert new["hud"]["enabled"] is False and new["hud"]["text_scale"] == 1.2
    assert new["hud"]["axes_present"] == ["stroke", "pitch"]
    assert all(not item["reused"] for item in new["outputs"])
