"""Storage keys remain a narrow filename, never a path chosen by a caller."""
import pytest

from preview_generator.generator import Config, GenerationError, _validate_config


@pytest.mark.parametrize('key', [None, True, 42, '', 'work-0', 'work-01', 'work--1',
                               '../work-42', 'work-42/other', 'work-42\\other', 's064'])
def test_invalid_storage_keys_are_rejected_before_reading_materials(key, tmp_path):
    with pytest.raises(GenerationError, match='work_id must be'):
        _validate_config(Config(work_id=key, video=tmp_path / 'nonexistent-video.mp4'))
