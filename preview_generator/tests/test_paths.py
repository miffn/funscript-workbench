"""Validate portable output boundaries without running media tools."""
from dataclasses import replace

import pytest

from preview_generator.generator import Config, GenerationError, OUTPUT_ROOT, PROJECT, _validate_config


@pytest.fixture
def config(tmp_path):
    video_dir = tmp_path / 'materials' / 'S067_001'
    script_dir = tmp_path / 'scripts' / 'S067_001'
    video_dir.mkdir(parents=True)
    script_dir.mkdir(parents=True)
    video = video_dir / 'source.mp4'
    script = script_dir / 'source.funscript'
    video.write_bytes(b'read-only-video-fixture')
    script.write_text('{"actions":[]}')
    renderer = tmp_path / 'renderer'
    renderer.write_text('#!/bin/sh\nexit 0\n')
    renderer.chmod(0o755)
    return Config('S067_001', video, scripts={'stroke': script}, renderer=renderer,
                  output_dir=tmp_path / 'output', cache_dir=tmp_path / 'cache')


def test_default_output_and_cache_are_project_local_without_creating_directories(config):
    default = Config(config.work_id, config.video, scripts=config.scripts, renderer=config.renderer)
    assert OUTPUT_ROOT == PROJECT / 'data/previews'
    assert default.cache_dir == PROJECT / 'data/preview-generator'
    assert _validate_config(default)[1] == (OUTPUT_ROOT / config.work_id).resolve()


@pytest.mark.parametrize('which', ['video', 'script'])
@pytest.mark.parametrize('target', ['same', 'descendant', 'ancestor'])
def test_output_and_cache_cannot_overlap_either_actual_source_directory(config, which, target):
    source = config.video.parent if which == 'video' else config.scripts['stroke'].parent
    directory = source if target == 'same' else source / 'generated' if target == 'descendant' else source.parent
    originals = {config.video: config.video.read_bytes(), config.scripts['stroke']: config.scripts['stroke'].read_bytes()}
    for field, label in [('output_dir', 'Output directory'), ('cache_dir', 'Intermediate cache')]:
        with pytest.raises(GenerationError, match=f'{label} overlaps'):
            _validate_config(replace(config, **{field: directory}))
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert not config.output_dir.exists() and not config.cache_dir.exists()


def test_symlink_output_and_cache_aliases_cannot_bypass_source_protection(config, tmp_path):
    alias = tmp_path / 'source-alias'
    alias.symlink_to(config.video.parent, target_is_directory=True)
    for field in ('output_dir', 'cache_dir'):
        with pytest.raises(GenerationError, match='overlaps'):
            _validate_config(replace(config, **{field: alias / 'generated'}))
    assert not (config.video.parent / 'generated').exists()


def test_default_preview_root_is_scoped_to_work_and_one_level_of_staging(config):
    stage = OUTPUT_ROOT / config.work_id / '.workbench-stage-abcd1234'
    assert _validate_config(replace(config, output_dir=stage))[1] == stage.resolve()
    for forbidden in (stage / 'nested', OUTPUT_ROOT / 'S068' / stage.name,
                      OUTPUT_ROOT / config.work_id / 'other-output'):
        with pytest.raises(GenerationError, match='Preview output must'):
            _validate_config(replace(config, output_dir=forbidden))


def test_backend_explicit_preview_staging_outside_source_folders_remains_supported(config):
    stage = config.video.parent.parent / 'previews' / config.work_id / '.workbench-stage-abcd1234'
    assert _validate_config(replace(config, output_dir=stage))[1] == stage.resolve()
    assert not stage.exists()
