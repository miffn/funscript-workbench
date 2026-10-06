from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend import release_dates


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    class FrozenDateTime:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 6, 1, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(release_dates, 'datetime', FrozenDateTime)
    root = tmp_path / 'inventory'
    (root / 'S067').mkdir(parents=True)
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, r'D:\inventory', 'Inventory'),),
                    ffmpeg='missing', ffprobe='missing', preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    Scanner(app.state.store, config).scan()
    with TestClient(app) as client:
        work_id = client.get('/api/works').json()['items'][0]['id']
        yield config, app, client, work_id


def test_workspace_timezone_persists_validates_and_guards_revision(inventory):
    config, app, client, _ = inventory
    initial = client.get('/api/settings/timezone').json()
    assert initial['timezone'] == 'Asia/Shanghai' and initial['revision'] == 0
    assert {'Asia/Shanghai', 'America/Los_Angeles', 'UTC'} <= set(initial['choices'])
    for body in ({'timezone': '../etc/passwd', 'expected_revision': 0},
                 {'timezone': 'Mars/Base', 'expected_revision': 0},
                 {'timezone': 'UTC', 'expected_revision': True},
                 {'timezone': 'UTC', 'expected_revision': 0, 'extra': 'field'}):
        assert client.put('/api/settings/timezone', json=body).status_code == 422
    saved = client.put('/api/settings/timezone', json={'timezone': 'America/Los_Angeles', 'expected_revision': 0})
    assert saved.status_code == 200 and saved.json()['revision'] == 1
    assert client.put('/api/settings/timezone', json={'timezone': 'UTC', 'expected_revision': 0}).status_code == 409
    assert client.put('/api/settings/timezone', headers={'Origin': 'http://other.test'}, json={'timezone': 'UTC', 'expected_revision': 1}).status_code == 403
    assert release_dates.release_today(app.state.store) == '2026-10-05'
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get('/api/settings/timezone').json() == saved.json()


def test_new_platform_days_and_calendar_today_follow_timezone_but_history_never_moves(inventory):
    _, _, client, work_id = inventory
    path = f'/api/works/{work_id}'
    client.patch(path + '/links', json={'expected_revision': 0, 'links': {'es': 'https://example.test/es'}})
    assert client.get(path).json()['es_published_date'] == '2026-10-06'
    client.put('/api/settings/timezone', json={'timezone': 'America/Los_Angeles', 'expected_revision': 0})
    state = client.get(path + '/links').json()
    saved = client.patch(path + '/links', json={'expected_revision': state['links_revision'], 'links': {'patreon': 'https://example.test/patreon'}}).json()
    assert saved['patreon_published_date'] == '2026-10-05'
    assert saved['es_published_date'] == '2026-10-06'
    calendar = client.get('/api/release-calendar?month=2026-10').json()
    assert calendar['today'] == '2026-10-05'
    assert {event['platform']: event['date'] for event in calendar['events']} == {'es': '2026-10-06', 'patreon': '2026-10-05'}
    with client.app.state.store.connection() as db:
        before = dict(db.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone())
        jobs = [dict(row) for row in db.execute('SELECT * FROM jobs')]
    client.put('/api/settings/timezone', json={'timezone': 'UTC', 'expected_revision': 1})
    with client.app.state.store.connection() as db:
        assert dict(db.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone()) == before
        assert [dict(row) for row in db.execute('SELECT * FROM jobs')] == jobs
