import json

import pytest

from preview_generator.__main__ import main, make_config, parser
from preview_generator.generator import Config, renderer_arguments
from pathlib import Path


def test_cli_explicit_multi_axis_and_builtin():
    config = make_config(parser().parse_args(["--work-id", "S064", "--video", "v.mp4",
        "--script", "stroke=v.funscript", "--script", "pitch=v.pitch.funscript", "--model", "builtin"]))
    assert config.scripts == {"stroke": "v.funscript", "pitch": "v.pitch.funscript"}
    assert config.model == "builtin"
    assert config.width == 1920 and config.gif_width == 192


def test_default_model_is_source_builtin_and_hud_is_visible():
    config = make_config(parser().parse_args(["--work-id", "S064", "--video", "v.mp4"]))
    assert config.model == "builtin"
    assert config.show_axis_hud is True and config.hud_text_scale == 1.1


def test_cli_hud_switches_override_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"work_id": "S064", "video": "v.mp4", "hud_text_scale": .8,
                                "show_axis_hud": True}), encoding="utf-8")
    config = make_config(parser().parse_args(["--config", str(path), "--no-axis-hud", "--hud-text-scale", "1.2"]))
    assert config.show_axis_hud is False and config.hud_text_scale == 1.2


def test_renderer_protocol_only_marks_associated_axes_present():
    config = Config("S064", "v.mp4")
    command = renderer_arguments(config, Path("renderer"), Path("axes.json"), Path("sim.rgba"),
                                 {"pitch": Path("pitch.funscript"), "stroke": Path("v.funscript")})
    assert command[command.index("--axes-present") + 1] == "stroke,pitch"
    assert command[command.index("--hud-text-scale") + 1] == 1.1
    assert "--model" not in command and "--no-axis-hud" not in command
    assert command[command.index("--camera-distance") + 1] == 8.4


def test_renderer_protocol_can_hide_hud_without_changing_axis_inputs():
    command = renderer_arguments(Config("S064", "v.mp4", show_axis_hud=False),
                                 Path("renderer"), Path("axes.json"), Path("sim.rgba"), ("stroke", "pitch"))
    assert "--no-axis-hud" in command
    assert command[command.index("--axes-present") + 1] == "stroke,pitch"


def test_explicit_glb_option_remains_available(tmp_path):
    model = tmp_path / "optional.glb"
    command = renderer_arguments(Config("S064", "v.mp4", model=model),
                                 Path("renderer"), Path("axes.json"), Path("sim.rgba"), ("stroke",))
    assert command[command.index("--model") + 1] == model.resolve()


def test_cli_conflicting_axis_is_rejected():
    args = parser().parse_args(["--work-id", "S064", "--video", "v.mp4",
        "--script", "stroke=a", "--script", "stroke=b"])
    with pytest.raises(ValueError, match="Multiple selections"):
        make_config(args)


def test_json_config_and_explicit_override(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"work_id": "S064", "video": "v.mp4", "fps": 24, "include_audio": False}), encoding="utf-8")
    config = make_config(parser().parse_args(["--config", str(path), "--fps", "30"]))
    assert config.fps == 30 and not config.include_audio


def test_json_error_contract(capsys):
    assert main(["--work-id", "S064"]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["event"] == "error" and error["status"] == "failed"
    assert "video" in error["message"]


def test_unknown_config_field_fails_instead_of_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"work_id": "S064", "video": "v.mp4", "fpz": 30}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown Config fields"):
        make_config(parser().parse_args(["--config", str(path)]))
