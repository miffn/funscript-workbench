"""Run the installed Windows heatmap creator against isolated selected-axis copies.

The original drag entry renders a 2048px axes report, including empty extra axes.
No generator source or private inventory data is embedded in this adapter.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import zlib

from preview_generator import GenerationCancelled
from preview_generator.scripts import AXES, load_script

DEFAULT_TOOL = Path(__file__).resolve().parent / 'tools' / 'heatmapcreatorv1.0.exe'
POWERSHELL = Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')


def _check_cancel(event):
    if event is not None and event.is_set():
        raise GenerationCancelled('热力图生成已取消')


def _windows_path(path: Path) -> str:
    path = path.absolute()
    if len(path.parts) >= 3 and path.parts[1] == 'mnt' and len(path.parts[2]) == 1:
        return path.parts[2].upper() + ':\\' + '\\'.join(path.parts[3:])
    return subprocess.check_output(['wslpath', '-w', str(path)], text=True).strip()


def _interop_environment() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get('WSL_INTEROP')
    if not current or not Path(current).exists():
        candidates = [Path('/run/WSL/1_interop'), *sorted(Path('/run/WSL').glob('*_interop'))]
        for candidate in candidates:
            if candidate.exists():
                env['WSL_INTEROP'] = str(candidate)
                break
        else:
            raise RuntimeError('Windows 热力图工具不可用：WSL interop 未启动')
    return env


def _run_tool(tool: Path, inputs: Path, cancel_event=None):
    """A hidden Windows supervisor owns the executable and kills its whole tree.

    Values are JSON/base64 data, never PowerShell source or shell arguments.
    Cancellation uses a private marker so the Windows owner can stop the child.
    """
    cancel_file = inputs.parent / 'cancel'
    settings = {'tool': _windows_path(tool), 'inputs': _windows_path(inputs),
                'cancel': _windows_path(cancel_file)}
    encoded_settings = base64.b64encode(json.dumps(settings).encode()).decode()
    source = r'''
$ErrorActionPreference = 'Stop'
$settings = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('SETTINGS')) | ConvertFrom-Json
$info = New-Object Diagnostics.ProcessStartInfo
$info.FileName = $settings.tool
$info.Arguments = '"' + $settings.inputs + '"'
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardInput = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$process = New-Object Diagnostics.Process
$process.StartInfo = $info
[void]$process.Start()
$process.StandardInput.Close()
$out = $process.StandardOutput.ReadToEndAsync()
$err = $process.StandardError.ReadToEndAsync()
$watch = [Diagnostics.Stopwatch]::StartNew()
$stopped = $false
while (!$process.WaitForExit(100)) {
    if ([IO.File]::Exists($settings.cancel) -or $watch.Elapsed.TotalSeconds -gt 120) {
        $killer = New-Object Diagnostics.ProcessStartInfo
        $killer.FileName = "$env:SystemRoot\System32\taskkill.exe"
        $killer.Arguments = '/PID ' + $process.Id + ' /T /F'
        $killer.UseShellExecute = $false
        $killer.CreateNoWindow = $true
        $killProcess = [Diagnostics.Process]::Start($killer)
        $killProcess.WaitForExit()
        $process.WaitForExit()
        $stopped = $true
        break
    }
}
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
[Console]::Write($out.Result)
[Console]::Error.Write($err.Result)
if ($stopped) { [Console]::Error.Write('Heatmap execution cancelled or timed out.'); exit 124 }
exit $process.ExitCode
'''.replace('SETTINGS', encoded_settings)
    command = [str(POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive',
               '-EncodedCommand', base64.b64encode(source.encode('utf-16le')).decode()]
    # Files keep diagnostics bounded in memory and avoid pipe deadlocks.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, env=_interop_environment())
        while True:
            if cancel_event is not None and cancel_event.is_set():
                cancel_file.touch(exist_ok=True)
            try:
                result = process.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue
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
    if not tool.is_file():
        raise RuntimeError('未找到 Windows 热力图工具，请检查工具路径')
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
            # This exe measures time from its final action, ignoring metadata.
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
