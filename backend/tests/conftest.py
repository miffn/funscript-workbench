"""Even backend.main's module-level app must initialize in disposable test storage."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import struct
import zlib
import pytest


_test_app_data = TemporaryDirectory(prefix="workbench-test-app-")
_prior = {name: os.environ.get(name) for name in (
    "WORKBENCH_DATA_DIR", "WORKBENCH_ROOTS_JSON", "WORKBENCH_PREVIEW_OUTPUT_ROOT",
)}
os.environ["WORKBENCH_DATA_DIR"] = str(Path(_test_app_data.name) / "data")
os.environ["WORKBENCH_ROOTS_JSON"] = "[]"
os.environ["WORKBENCH_PREVIEW_OUTPUT_ROOT"] = str(Path(_test_app_data.name) / "previews")


@pytest.fixture
def fake_heatmap_tool(tmp_path, monkeypatch):
    """Service tests isolate the Windows executable; adapter tests cover its protocol."""
    from backend import previews
    tool = tmp_path / 'heatmap-tool.exe'
    tool.write_bytes(b'test-heatmap-tool')
    def render(script_id, scripts, output_dir, duration_seconds, **kwargs):
        def chunk(kind, payload):
            return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload))
        width, height = 2048, 170
        data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress((b'\0' + b'\x18\x18\x18' * width) * height)) + chunk(b'IEND', b''))
        target = Path(output_dir) / '热力图.png'
        target.write_bytes(data)
        return {'filename': target.name, 'path': str(target), 'kind': 'heatmap', 'clip_index': 0,
                'width': width, 'height': height, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(previews, 'generate_heatmap', render)
    return tool


def pytest_sessionfinish(session, exitstatus):
    for name, value in _prior.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _test_app_data.cleanup()
