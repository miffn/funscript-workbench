import json

import pytest

from preview_generator.generator import clip_ranges
from preview_generator.scripts import ScriptError, discover_scripts, frame_values, load_script


def write_script(path, actions):
    path.write_text(json.dumps({"actions": actions}), encoding="utf-8")
    return path


def test_multi_axis_uses_absolute_time_and_independent_action_times(tmp_path):
    stroke = load_script(write_script(tmp_path / "v.funscript", [
        {"at": 10_000, "pos": 0}, {"at": 20_000, "pos": 100}]))
    pitch = load_script(write_script(tmp_path / "v.pitch.funscript", [
        {"at": 0, "pos": 100}, {"at": 15_000, "pos": 0}, {"at": 30_000, "pos": 100}]))
    frames = frame_values({"stroke": stroke, "pitch": pitch}, 12, 3, 1)
    assert frames[0] == pytest.approx([.2, .5, .5, .5, .5, .2])
    assert frames[2] == pytest.approx([.4, .5, .5, .5, .5, 1 / 15])


def test_normal_ofs_interpolation_and_boundary_bug_fix(tmp_path):
    script = load_script(write_script(tmp_path / "v.funscript", [
        {"at": 1000, "pos": 20}, {"at": 3000, "pos": 80}]))
    assert script.position(0) == .2  # intentional fix: OFS before-first fallthrough returns .8
    assert script.position(1) == .2
    assert script.position(2) == pytest.approx(.5)
    assert script.position(3) == .8
    assert script.position(9) == .8


@pytest.mark.parametrize("actions", [[], [{"at": -1, "pos": 50}], [{"at": 0, "pos": 101}],
                                     [{"at": True, "pos": 50}], [{"at": 0, "pos": float("nan")}],
                                     [{"at": 0, "pos": 1}, {"at": 0, "pos": 2}]])
def test_invalid_actions_are_not_silently_dropped(tmp_path, actions):
    with pytest.raises(ScriptError):
        load_script(write_script(tmp_path / "bad.funscript", actions))


def test_exact_association_ignores_other_videos_and_versions(tmp_path):
    video = tmp_path / "my video.mp4"
    video.touch()
    for name in ("my video.funscript", "my video.pitch.funscript", "other.pitch.funscript", "my video.v2.funscript"):
        (tmp_path / name).touch()
    selected = discover_scripts(video)
    assert set(selected) == {"stroke", "pitch"}
    assert selected["pitch"].name == "my video.pitch.funscript"


def test_case_insensitive_duplicate_is_ambiguous(tmp_path):
    video = tmp_path / "v.mp4"
    video.touch()
    (tmp_path / "v.funscript").touch()
    (tmp_path / "V.FUNSCRIPT").touch()
    with pytest.raises(ScriptError, match="Multiple scripts"):
        discover_scripts(video)


def test_clip_starts_and_short_boundaries():
    normal = clip_ranges(600, (.2, .4, .6, .8), 10)
    assert [c["start_seconds"] for c in normal] == [120, 240, 360, 480]
    shifted = clip_ranges(20, (.2, .4, .6, .8), 10)
    assert [c["start_seconds"] for c in shifted] == [4, 8, 10]
    assert [c["index"] for c in shifted] == [1, 2, 3]  # fourth duplicates shifted third
    short = clip_ranges(3, (.2, .4, .6, .8), 10)
    assert short == [{"index": 1, "percentage": .2, "start_seconds": 0, "duration_seconds": 3}]
