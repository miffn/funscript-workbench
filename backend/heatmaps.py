"""Generate full-duration axes reports with bundled Python/Pillow in WSL."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib

from PIL import __version__ as PILLOW_VERSION
from preview_generator import GenerationCancelled
from preview_generator.scripts import AXES, load_script

DEFAULT_TOOL = Path(__file__).resolve().parent / 'tools' / 'heatmapgen' / 'heatmapgen.py'
TOOL_TIMEOUT_SECONDS = 120


def _check_cancel(event):
    if event is not None and event.is_set():
        raise GenerationCancelled('热力图生成已取消')


def renderer_fingerprint(tool_path):
    tool = Path(tool_path)
    if not tool.is_file():
        raise RuntimeError('未找到 WSL 热力图程序，请检查内置源码')
    if tool.suffix.lower() != '.py':
        raise RuntimeError('热力图需要 Python 源码程序，不能使用 Windows EXE')
    font = tool.parent / 'fonts' / 'NotoSansCJKsc-Regular.otf'
    if tool == DEFAULT_TOOL and not font.is_file():
        raise RuntimeError('热力图字体资源不可用，请检查内置工具')
    return {'runtime': 'python-pillow-wsl-v1',
            'source_sha256': hashlib.sha256(tool.read_bytes()).hexdigest(),
            'font_sha256': hashlib.sha256(font.read_bytes()).hexdigest() if font.is_file() else None,
            'pillow_version': PILLOW_VERSION}


def _stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def _run_tool(tool: Path, inputs: Path, cancel_event=None):
    """An isolated Linux process owns the render and its cancellation/timeout."""
    _check_cancel(cancel_event)
    command = [sys.executable, '-I', '-u', str(tool), str(inputs), '--mode', 'axes',
               '--out', str(inputs / 'heatmaps'), '--overwrite',
               '--speed-resolution', '8192', '--report-width', '2048']
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        started = time.monotonic()
        try:
            while True:
                _check_cancel(cancel_event)
                if time.monotonic() - started > TOOL_TIMEOUT_SECONDS:
                    raise RuntimeError('热力图生成超时')
                try:
                    result = process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            _stop_process(process)
        _check_cancel(cancel_event)
        if result:
            log.seek(max(0, log.tell() - 3000))
            detail = log.read().decode('utf-8', errors='replace').strip()
            raise RuntimeError(f'热力图工具执行失败（{result}）：{detail}')


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Validate the entire PNG chunk framing/CRCs, not only its extension."""
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise RuntimeError('热力图工具未生成有效 PNG')
    offset, dimensions, has_image, ended = 8, None, False, False
    while offset + 12 <= len(data):
        size = struct.unpack('!I', data[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(data):
            raise RuntimeError('热力图 PNG 不完整')
        kind = data[offset + 4:offset + 8]
        chunk = data[offset + 8:offset + 8 + size]
        crc = struct.unpack('!I', data[offset + 8 + size:end])[0]
        if zlib.crc32(kind + chunk) & 0xffffffff != crc:
            raise RuntimeError('热力图 PNG 校验失败')
        if offset == 8:
            if kind != b'IHDR' or size != 13:
                raise RuntimeError('热力图 PNG 头部无效')
            dimensions = struct.unpack('!II', chunk[:8])
        if kind == b'IDAT':
            has_image = True
        if kind == b'IEND':
            ended = size == 0 and end == len(data)
            break
        offset = end
    if not ended or not has_image or not dimensions or dimensions[0] != 2048 or dimensions[1] <= 0:
        raise RuntimeError('热力图 PNG 尺寸或内容无效（要求宽度 2048）')
    return dimensions


def generate_heatmap(script_id: str, scripts: dict[str, str], output_dir: Path,
                     duration_seconds: float, cancel_event: threading.Event | None = None,
                     tool_path: str | Path | None = None) -> dict:
    _check_cancel(cancel_event)
    if not scripts or set(scripts) - set(AXES):
        raise ValueError('热力图需要已匹配的有效轴脚本')
    if isinstance(duration_seconds, bool) or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError('热力图视频时长无效')
    tool = Path(tool_path or DEFAULT_TOOL)
    renderer_fingerprint(tool)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir, prefix='.heatmap-') as temporary:
        stage = Path(temporary)
        inputs = stage / 'inputs'
        inputs.mkdir()
        duration_ms = round(duration_seconds * 1000)
        for axis in AXES:
            if axis not in scripts:
                continue
            _check_cancel(cancel_event)
            original = Path(scripts[axis])
            parsed = load_script(original)
            document = json.loads(original.read_text(encoding='utf-8-sig'))
            # The renderer measures time from its final action, ignoring metadata.
            # Clamp/hold copies to video end to give every selected axis one timeline.
            document['actions'] = [{'at': round(t * 1000), 'pos': p * 100}
                                   for t, p in zip(parsed.times, parsed.positions)
                                   if round(t * 1000) < duration_ms]
            document['actions'].append({'at': duration_ms,
                                        'pos': parsed.position(duration_seconds) * 100})
            document.setdefault('metadata', {})
            if not isinstance(document['metadata'], dict):
                document['metadata'] = {}
            document['metadata']['duration'] = duration_seconds
            name = f"preview{'' if axis == 'stroke' else '.' + axis}.funscript"
            (inputs / name).write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        _run_tool(tool, inputs, cancel_event)
        _check_cancel(cancel_event)
        rendered = inputs / 'heatmaps' / 'preview_Axes.png'
        if not rendered.is_file():
            raise RuntimeError('热力图工具未输出预期的轴报告')
        width, height = _png_dimensions(rendered)
        target = output_dir / '热力图.png'
        rendered.replace(target)
    return {'kind': 'heatmap', 'clip_index': 0, 'filename': target.name,
            'path': str(target), 'width': width, 'height': height,
            'size': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
