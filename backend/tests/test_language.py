from fastapi.testclient import TestClient

from backend.config import Config
from backend.main import create_app
from backend.store import Store


def test_language_persists_across_restart_and_preserves_profile(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=())
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get('/api/settings/language').json() == {'language': 'zh-CN', 'revision': 0}
        profile = client.put('/api/profile', json={'name': '自定义姓名', 'bio': '原有简介', 'avatar': None, 'expected_revision': 0}).json()
        saved = client.put('/api/settings/language', json={'language': 'en', 'expected_revision': 0})
        assert saved.status_code == 200
        assert saved.json() == {'language': 'en', 'revision': 1}
        assert client.get('/api/profile').json() == profile
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get('/api/settings/language').json() == {'language': 'en', 'revision': 1}
        assert client.get('/api/profile').json() == profile


def test_language_validates_input_and_detects_stale_writes(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=())
    with TestClient(create_app(config, start_worker=False)) as client:
        for payload in ({'language': 'fr', 'expected_revision': 0},
                        {'language': 'en', 'expected_revision': True},
                        {'language': 'en', 'expected_revision': 0, 'unknown': 'value'}):
            assert client.put('/api/settings/language', json=payload).status_code == 422
        assert client.put('/api/settings/language', json={'language': 'en', 'expected_revision': 0}).status_code == 200
        assert client.put('/api/settings/language', json={'language': 'zh-CN', 'expected_revision': 0}).status_code == 409
        assert client.get('/api/settings/language').json()['language'] == 'en'
        assert client.put('/api/settings/language', headers={'Origin': 'http://other.example'}, json={'language': 'zh-CN', 'expected_revision': 1}).status_code == 403
    with Store(config.data_dir).connection() as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
