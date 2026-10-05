import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import threading
import subprocess
import time
import zlib

import pytest

from backend import heatmaps
from preview_generator import GenerationCancelled


def test_bundled_native_source_font_and_config(monkeypatch):
    from backend.config import Config
    monkeypatch.delenv('WORKBENCH_HEATMAP_TOOL', raising=False)
    assert Config.from_environment().heatmap_tool == heatmaps.DEFAULT_TOOL
    provenance = json.loads((heatmaps.DEFAULT_TOOL.parent / 'PROVENANCE.json').read_text())
    assert hashlib.sha256(heatmaps.DEFAULT_TOOL.read_bytes()).hexdigest() == provenance['sha256']
    font = heatmaps.DEFAULT_TOOL.parent / provenance['font']['path']
    assert hashlib.sha256(font.read_bytes()).hexdigest() == provenance['font']['sha256']
    assert 'SIL OPEN FONT LICENSE' in (font.parent / 'OFL.txt').read_text()


def test_tool_location_follows_relocated_module(tmp_path):
    relocated = tmp_path / 'moved-workbench' / 'backend'
    relocated.mkdir(parents=True)
    source = relocated / 'heatmaps.py'
    source.write_bytes(Path(heatmaps.__file__).read_bytes())
    spec = importlib.util.spec_from_file_location('relocated_heatmaps', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.DEFAULT_TOOL == relocated / 'tools' / 'heatmapgen' / 'heatmapgen.py'


def png(width=2048, height=1002):
    def chunk(kind, value):
        return struct.pack('!I', len(value)) + kind + value + struct.pack('!I', zlib.crc32(kind + value) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x00' * width * 3) * height))
            + chunk(b'IEND', b''))


@pytest.fixture
def files(tmp_path):
    tool = tmp_path / 'creator.py'
    tool.write_bytes(b'fake installed tool')
    source = tmp_path / 'original.funscript'
    source.write_text(json.dumps({'actions': [{'at': 0, 'pos': 0}, {'at': 1000, 'pos': 100}],
                                 'metadata': {'title': 'original', 'duration': 1}}))
    return tool, source, tmp_path / 'output'


@pytest.mark.parametrize('axes', [('stroke',), ('stroke', 'surge', 'pitch'), ('roll',)])
def test_explicit_axes_copies_and_full_duration(files, monkeypatch, axes):
    tool, source, output = files
    original = source.read_bytes()
    (source.parent / 'unselected.sway.funscript').write_bytes(original)
    seen = {}

    def execute(actual_tool, inputs, cancel_event):
        assert actual_tool == tool
        seen.update({p.name: json.loads(p.read_text()) for p in inputs.glob('*.funscript')})
        assert len(seen) == len(axes)
        for data in seen.values():
            assert data['actions'][-1] == {'at': 10000, 'pos': 100}
            assert data['metadata']['duration'] == 10
        (inputs / 'heatmaps').mkdir()
        (inputs / 'heatmaps' / 'preview_Axes.png').write_bytes(png())

    monkeypatch.setattr(heatmaps, '_run_tool', execute)
    result = heatmaps.generate_heatmap('S064', {axis: str(source) for axis in axes}, output, 10, tool_path=tool)
    assert set(seen) == {f"preview{'' if a == 'stroke' else '.' + a}.funscript" for a in axes}
    assert result['kind'] == 'heatmap' and result['clip_index'] == 0
    assert result['width'] == 2048 and result['height'] == 1002
    assert result['sha256'] == hashlib.sha256((output / '热力图.png').read_bytes()).hexdigest()
    assert source.read_bytes() == original
    assert list(output.iterdir()) == [output / '热力图.png']


def test_long_scripts_are_clamped_at_interpolated_video_end(files, monkeypatch):
    tool, source, output = files
    original = source.read_bytes()

    def execute(tool, inputs, event):
        data = json.loads((inputs / 'preview.funscript').read_text())
        assert data['actions'] == [{'at': 0, 'pos': 0}, {'at': 500, 'pos': 50}]
        (inputs / 'heatmaps').mkdir()
        (inputs / 'heatmaps' / 'preview_Axes.png').write_bytes(png())

    monkeypatch.setattr(heatmaps, '_run_tool', execute)
    heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, .5, tool_path=tool)
    assert source.read_bytes() == original


@pytest.mark.parametrize('duration', [0, -1, float('nan'), float('inf'), True])
def test_invalid_duration(files, duration):
    tool, source, output = files
    with pytest.raises(ValueError):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, duration, tool_path=tool)
    assert not output.exists()


@pytest.mark.parametrize('scripts', [{}, {'unrecognized': 'script.funscript'}])
def test_invalid_axes(files, scripts):
    tool, source, output = files
    with pytest.raises(ValueError):
        heatmaps.generate_heatmap('S064', scripts, output, 10, tool_path=tool)


def test_cancellation_before_start(files, monkeypatch):
    tool, source, output = files
    event = threading.Event()
    event.set()
    monkeypatch.setattr(heatmaps, '_run_tool', lambda *args: pytest.fail('must not start'))
    with pytest.raises(GenerationCancelled):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, event, tool)
    assert not output.exists()


def test_cancel_after_tool_exit_keeps_old_result(files, monkeypatch):
    tool, source, output = files
    event = threading.Event()
    output.mkdir()
    old = output / '热力图.png'
    old.write_bytes(b'keep previous result')
    monkeypatch.setattr(heatmaps, '_run_tool', lambda *_: event.set())
    with pytest.raises(GenerationCancelled):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, event, tool)
    assert old.read_bytes() == b'keep previous result'
    assert len(list(output.iterdir())) == 1


@pytest.mark.parametrize('content', [b'not png', png(1024), png()[:-8]])
def test_bad_output_keeps_previous_png(files, monkeypatch, content):
    tool, source, output = files
    output.mkdir()
    target = output / '热力图.png'
    target.write_bytes(b'old')

    def execute(tool, inputs, event):
        (inputs / 'heatmaps').mkdir()
        (inputs / 'heatmaps' / 'preview_Axes.png').write_bytes(content)

    monkeypatch.setattr(heatmaps, '_run_tool', execute)
    with pytest.raises(RuntimeError):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, tool_path=tool)
    assert target.read_bytes() == b'old'
    assert len(list(output.iterdir())) == 1


def test_failure_cleans_copies_without_touching_source(files, monkeypatch):
    tool, source, output = files
    original = source.read_bytes()

    def fail(*args):
        raise RuntimeError('tool failed')

    monkeypatch.setattr(heatmaps, '_run_tool', fail)
    with pytest.raises(RuntimeError, match='tool failed'):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, tool_path=tool)
    assert source.read_bytes() == original
    assert not list(output.iterdir())


def test_missing_tool(files):
    tool, source, output = files
    with pytest.raises(RuntimeError, match='未找到'):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, tool_path=tool.parent / 'missing')


def test_native_render_works_without_windows_interop_and_keeps_sources(files, monkeypatch):
    _, source, output = files
    monkeypatch.delenv('WSL_INTEROP', raising=False)
    before = source.read_bytes()
    result = heatmaps.generate_heatmap('S064', {'stroke': str(source), 'pitch': str(source)}, output, 10)
    assert (result['width'], result['height']) == (2048, 690)
    from PIL import Image
    with Image.open(output / '热力图.png') as report:
        assert report.size == (2048, 690)
        assert report.getpixel((40, 52)) != report.getpixel((40, 156))
    assert source.read_bytes() == before
    assert list(output.iterdir()) == [output / '热力图.png']


def test_windows_executable_is_rejected(files):
    tool, source, output = files
    exe = tool.with_suffix('.exe')
    exe.write_bytes(b'old tool')
    with pytest.raises(RuntimeError, match='Windows EXE'):
        heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, tool_path=exe)
    assert not output.exists()


def test_fingerprint_includes_font_changes(files):
    tool, _, _ = files
    before = heatmaps.renderer_fingerprint(tool)
    (tool.parent / 'fonts').mkdir()
    font = tool.parent / 'fonts/NotoSansCJKsc-Regular.otf'
    font.write_bytes(b'font one')
    first = heatmaps.renderer_fingerprint(tool)
    font.write_bytes(b'font two')
    second = heatmaps.renderer_fingerprint(tool)
    assert before != first != second


@pytest.mark.parametrize('cancel', [True, False])
def test_native_process_cancellation_and_timeout_clean_stage(files, monkeypatch, cancel):
    tool, source, output = files
    tool.write_text('import time\ntime.sleep(60)\n')
    event = threading.Event()
    if cancel:
        timer = threading.Timer(.15, event.set)
        timer.start()
    else:
        monkeypatch.setattr(heatmaps, 'TOOL_TIMEOUT_SECONDS', .15)
    started = time.monotonic()
    try:
        with pytest.raises(GenerationCancelled if cancel else RuntimeError):
            heatmaps.generate_heatmap('S064', {'stroke': str(source)}, output, 10, event, tool)
    finally:
        if cancel:
            timer.cancel()
    assert time.monotonic() - started < 5
    assert list(output.iterdir()) == []
