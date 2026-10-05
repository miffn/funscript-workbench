from __future__ import annotations

import base64
import subprocess
from pathlib import PureWindowsPath

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store


@pytest.mark.parametrize('open_mode', ['native', 'gateway'])
def test_backend_delegates_source_and_preview_folders_to_gateway(tmp_path, monkeypatch, open_mode):
    root = tmp_path / 'library'
    source = root / 'S010'
    source.mkdir(parents=True)
    output_root = root / 'previews'
    (output_root / 'S010').mkdir(parents=True)
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, 'D:\\library', 'library'),),
                    preview_output_root=output_root, open_mode=open_mode,
                    ffprobe='missing-test-ffprobe')
    store = Store(config.data_dir)
    Scanner(store, config).scan()
    with store.connection() as db:
        work_id = db.execute("SELECT id FROM works WHERE script_id='S010'").fetchone()[0]
    (config.data_dir / 'host.key').write_text('test-host-key')
    headers = {'Host': 'localhost:8788', 'Origin': 'http://localhost:8788',
               'X-Workbench-Host-Key': 'test-host-key'}

    def no_process(*args, **kwargs):
        raise AssertionError('The backend must not start a desktop process')

    monkeypatch.setattr(subprocess, 'Popen', no_process)
    with TestClient(create_app(config, start_worker=False)) as client:
        for endpoint, expected_path, expected_root in [
            ('open-folder', r'D:\library\S010', r'D:\library'),
            ('preview/open-folder', r'D:\library\previews\S010', r'D:\library\previews'),
        ]:
            response = client.post(f'/api/works/{work_id}/{endpoint}', headers=headers)
            assert response.status_code == 200
            assert response.json() == {'message': '已发送打开请求'}
            path = base64.urlsafe_b64decode(response.headers['X-Workbench-Open-Folder']).decode()
            folder_root = base64.urlsafe_b64decode(response.headers['X-Workbench-Folder-Root']).decode()
            assert PureWindowsPath(path) == PureWindowsPath(expected_path)
            assert PureWindowsPath(folder_root) == PureWindowsPath(expected_root)
