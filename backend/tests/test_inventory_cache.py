from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.inventory_cache import InventoryCache, InventoryCacheError
from backend.main import create_app
from backend.store import COVER_COLUMNS, INVENTORY_TABLES, Store, inventory_revision, now


@pytest.fixture
def inventory(tmp_path):
    root = tmp_path / 'materials'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, r'D:\materials', 'Materials'),),
                    ffmpeg='missing', ffprobe='missing', preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    store = app.state.store
    with store.connection() as db:
        for work_id, date in ((1, '2026-10-03'), (2, '2026-10-01'), (3, None), (4, None)):
            db.execute('INSERT INTO works(id,script_id,title,es_published_date,created_at,updated_at) '
                       'VALUES(?,?,?,?,?,?)', (work_id, f'S00{work_id}', f'Title {work_id}', date, now(), now()))
            db.execute('INSERT INTO directories(id,work_id,path,windows_path,root_path,name) VALUES(?,?,?,?,?,?)',
                       (work_id, work_id, str(root / f'S00{work_id}'), rf'D:\materials\S00{work_id}', str(root), f'S00{work_id}'))
            db.execute('INSERT INTO assets(id,directory_id,name,relative_path,kind,size,mtime_ns) VALUES(?,?,?,?,?,?,?)',
                       (work_id, work_id, 'main.funscript', 'main.funscript', 'script', 10, 1))
        for tag_id, category, name in ((1, 'author', 'A'), (2, 'author', 'B'), (3, 'video_type', 'Real')):
            db.execute('INSERT INTO tags(id,category,name,name_key) VALUES(?,?,?,?)', (tag_id, category, name, name.casefold()))
        db.executemany('INSERT INTO work_tags(work_id,tag_id) VALUES(?,?)', ((1, 1), (1, 3), (2, 2), (2, 3)))
        db.execute('INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)',
                   (1, 'cached-fingerprint', str(config.data_dir / 'covers' / 'cached.jpg'), 'source', now()))
    with TestClient(app) as client:
        yield client, app, store


def test_append_uses_no_sql_or_storage_and_normal_pages_reuse_one_build(inventory, monkeypatch):
    client, app, store = inventory
    statements = []
    original = store.connection

    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, 'connection', traced)
    first = client.get('/api/works?page_size=2').json()
    assert [work['id'] for work in first['items']] == [1, 2]
    assert first['total'] == 4
    assert sum(statement.startswith('SELECT * FROM works') for statement in statements) == 1
    statements.clear()
    normal = client.get('/api/works?page_size=2&page=2').json()
    assert normal['snapshot_id'] == first['snapshot_id']
    assert [work['id'] for work in normal['items']] == [4, 3]
    assert all('inventory_state' in statement for statement in statements if statement.startswith('SELECT'))

    def unavailable(*args, **kwargs):
        raise AssertionError('An appended snapshot page opened SQLite or probed storage')

    monkeypatch.setattr(store, 'connection', unavailable)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', unavailable)
        patch.setattr(Path, 'resolve', unavailable)
        response = client.get('/api/works', params={'snapshot_id': first['snapshot_id'], 'page': 2, 'page_size': 2})
        assert response.status_code == 200
        assert response.json() == normal
        empty = client.get('/api/works', params={'snapshot_id': first['snapshot_id'], 'page': 20})
        assert empty.status_code == 200 and empty.json()['items'] == []


def test_changed_revision_gets_new_snapshot_while_old_pages_remain_immutable(inventory):
    client, _, store = inventory
    first = client.get('/api/works?page_size=1').json()
    with store.connection() as db:
        db.execute("UPDATE works SET title='Changed',es_published_date='2026-10-09' WHERE id=3")
    after = client.get('/api/works?page_size=1').json()
    assert after['snapshot_id'] != first['snapshot_id']
    assert after['inventory_revision'] > first['inventory_revision']
    assert after['items'][0]['id'] == 3 and after['items'][0]['title'] == 'Changed'
    old = client.get('/api/works', params={'snapshot_id': first['snapshot_id'], 'page_size': 4}).json()
    assert old['inventory_revision'] == first['inventory_revision']
    assert [work['id'] for work in old['items']] == [1, 2, 4, 3]
    assert old['items'][-1]['title'] == 'Title 3'
    first['items'][0]['tags'].clear()
    assert client.get('/api/works', params={'snapshot_id': first['snapshot_id']}).json()['items'][0]['tags']


def test_query_normalization_and_token_mismatch_do_not_query_database(inventory, monkeypatch):
    client, _, store = inventory
    first = client.get('/api/works?tag_ids=3,2,1&q=%20%20').json()
    assert first['total'] == 2
    equivalent = client.get('/api/works?tag_id=1&tag_ids=2,3,1').json()
    assert equivalent['snapshot_id'] == first['snapshot_id']
    monkeypatch.setattr(store, 'connection', lambda: pytest.fail('Token validation must not open SQLite'))
    token = first['snapshot_id']
    assert client.get('/api/works', params={'snapshot_id': token, 'tag_ids': '1,3,2,1'}).status_code == 200
    for mismatch in ({'q': 'other'}, {'sort_platform': 'patreon'}, {'issues_only': True}, {'tag_ids': '1'}):
        assert client.get('/api/works', params={'snapshot_id': token, 'tag_ids': '1,2,3', **mismatch}).status_code == 422
    assert client.get('/api/works?snapshot_id=missing').status_code == 410


def test_unicode_query_keys_follow_sqlite_matching_instead_of_casefold(inventory):
    client, _, store = inventory
    with store.connection() as db:
        db.execute("UPDATE works SET title='Ä title' WHERE id=1")
    upper = client.get('/api/works', params={'q': 'Ä'}).json()
    lower = client.get('/api/works', params={'q': 'ä'}).json()
    assert upper['total'] == 1 and lower['total'] == 0
    assert upper['snapshot_id'] != lower['snapshot_id']


def test_snapshot_revision_rows_and_stats_share_one_read_transaction(inventory, monkeypatch):
    client, _, store = inventory
    with store.connection() as db:
        before = inventory_revision(db)
    original = store.connection
    changed = False

    @contextmanager
    def raced():
        with original() as db:
            def on_statement(statement):
                nonlocal changed
                if not changed and statement.startswith('SELECT * FROM works'):
                    changed = True
                    with original() as writer:
                        writer.execute("UPDATE works SET title='Concurrent change' WHERE id=1")
            db.set_trace_callback(on_statement)
            yield db

    monkeypatch.setattr(store, 'connection', raced)
    first = client.get('/api/works?q=Title%201').json()
    assert changed and first['inventory_revision'] == before
    assert first['total'] == 1 and first['items'][0]['title'] == 'Title 1'
    assert first['stats']['total'] == 4
    fresh = client.get('/api/works?q=Title%201').json()
    assert fresh['inventory_revision'] > before and fresh['total'] == 0


MUTATIONS = {
    'works': ("INSERT INTO works(id,title,created_at,updated_at) VALUES(99,'extra','a','a')", "UPDATE works SET title='new' WHERE id=99", 'DELETE FROM works WHERE id=99'),
    'directories': ("INSERT INTO directories(id,work_id,path,windows_path,root_path,name) VALUES(99,1,'extra','extra','root','extra')", "UPDATE directories SET available=0 WHERE id=99", 'DELETE FROM directories WHERE id=99'),
    'assets': ("INSERT INTO assets(id,directory_id,name,relative_path,kind,size,mtime_ns) VALUES(99,1,'extra','extra','video',1,1)", 'UPDATE assets SET size=2 WHERE id=99', 'DELETE FROM assets WHERE id=99'),
    'covers': ("INSERT INTO covers(work_id,fingerprint,source_path,updated_at) VALUES(2,'f','s','a')", 'UPDATE covers SET revision=1 WHERE work_id=2', 'DELETE FROM covers WHERE work_id=2'),
    'issues': ("INSERT INTO issues(id,type,message,work_id) VALUES(99,'test','test',1)", "UPDATE issues SET message='new' WHERE id=99", 'DELETE FROM issues WHERE id=99'),
    'tags': ("INSERT INTO tags(id,category,name,name_key) VALUES(99,'custom','Extra','extra')", "UPDATE tags SET name='New' WHERE id=99", 'DELETE FROM tags WHERE id=99'),
    'work_tags': ('INSERT INTO work_tags(work_id,tag_id) VALUES(4,3)', "UPDATE work_tags SET source='import' WHERE work_id=4", 'DELETE FROM work_tags WHERE work_id=4'),
    'work_tag_state': ('INSERT INTO work_tag_state(work_id,revision) VALUES(4,1)', 'UPDATE work_tag_state SET revision=2 WHERE work_id=4', 'DELETE FROM work_tag_state WHERE work_id=4'),
    'work_links': ("INSERT INTO work_links(work_id,overrides) VALUES(4,'{}')", 'UPDATE work_links SET revision=1 WHERE work_id=4', 'DELETE FROM work_links WHERE work_id=4'),
    'work_durations': ("INSERT INTO work_durations(work_id,total_seconds,updated_at) VALUES(4,60,'a')", 'UPDATE work_durations SET total_seconds=90 WHERE work_id=4', 'DELETE FROM work_durations WHERE work_id=4'),
    'release_calendar_plans': ("INSERT INTO release_calendar_plans(work_id,es_planned_date) VALUES(4,'2026-10-08')", "UPDATE release_calendar_plans SET es_planned_date='2026-10-09' WHERE work_id=4", 'DELETE FROM release_calendar_plans WHERE work_id=4'),
    'settings': ("INSERT INTO settings(key,value) VALUES('last_scan','{}')", "UPDATE settings SET value='{} ' WHERE key='last_scan'", "DELETE FROM settings WHERE key='last_scan'"),
}


@pytest.mark.parametrize('table', (*INVENTORY_TABLES, 'settings'))
def test_each_inventory_table_insert_update_delete_advances_revision(inventory, table):
    _, _, store = inventory
    with store.connection() as db:
        revision = inventory_revision(db)
        for statement in MUTATIONS[table]:
            db.execute(statement)
            assert inventory_revision(db) == revision + 1
            revision += 1


def test_jobs_unrelated_settings_and_no_op_updates_do_not_invalidate(inventory):
    client, _, store = inventory
    first = client.get('/api/works').json()
    with store.connection() as db:
        before = inventory_revision(db)
        db.execute("INSERT INTO jobs(type,status,trigger,created_at) VALUES('scan','running','manual','a')")
        db.execute("UPDATE jobs SET progress=75,message='progress'")
        db.execute("INSERT INTO settings(key,value) VALUES('workspace_timezone','{}')")
        db.execute("UPDATE settings SET value='{} ' WHERE key='workspace_timezone'")
        db.execute("DELETE FROM settings WHERE key='workspace_timezone'")
        db.execute('UPDATE works SET title=title WHERE id=1')
        assert inventory_revision(db) == before
    assert client.get('/api/works').json()['snapshot_id'] == first['snapshot_id']
    assert client.get('/api/inventory-revision').json() == {'inventory_revision': before, 'scan_active': True}
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    assert client.get('/api/inventory-revision').json() == {'inventory_revision': before, 'scan_active': False}


def test_legacy_cover_migration_preserves_original_and_manual_restore_metadata(tmp_path):
    store = Store(tmp_path)
    with store.connection() as db:
        db.execute("INSERT INTO works(id,title,created_at,updated_at) VALUES(1,'one','a','a')")
        db.execute('DROP TABLE covers')
        db.execute('CREATE TABLE covers(work_id INTEGER PRIMARY KEY,fingerprint TEXT NOT NULL,path TEXT,error TEXT,source_path TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute("INSERT INTO covers VALUES(1,'original','automatic.jpg',NULL,'source.mp4','a')")
    store = Store(tmp_path)
    with store.connection() as db:
        cover = dict(db.execute('SELECT * FROM covers').fetchone())
        assert {field for field, _ in COVER_COLUMNS} <= cover.keys()
        assert cover['mode'] == 'automatic' and cover['revision'] == 0
        assert (cover['automatic_path'], cover['automatic_fingerprint'], cover['automatic_source_path']) == ('automatic.jpg', 'original', 'source.mp4')
        db.execute("UPDATE covers SET mode='manual',revision=1,path='manual.jpg',fingerprint='manual' WHERE work_id=1")
        revision = inventory_revision(db)
    store = Store(tmp_path)
    with store.connection() as db:
        assert inventory_revision(db) == revision
        cover = dict(db.execute('SELECT * FROM covers').fetchone())
        assert cover['path'] == 'manual.jpg' and cover['automatic_path'] == 'automatic.jpg'


def test_cache_ttl_lru_and_immutable_serialization():
    current = [0.0]
    cache = InventoryCache(capacity=2, clock=lambda: current[0])
    data = {'items': [{'id': 1, 'tags': [1]}], 'stats': {'total': 1}, 'last_scan': None}
    first = cache.get_or_create(('first',), 1, lambda: data)
    second = cache.get_or_create(('second',), 1, lambda: data)
    first.page(1, 1)['items'][0]['tags'].clear()
    assert first.page(1, 1)['items'][0]['tags'] == [1]
    with pytest.raises(FrozenInstanceError):
        first.revision = 2
    cache.get(first.token, ('first',))
    cache.get_or_create(('third',), 1, lambda: data)
    with pytest.raises(InventoryCacheError) as error:
        cache.get(second.token, ('second',))
    assert error.value.status_code == 410
    current[0] = 1800
    with pytest.raises(InventoryCacheError) as error:
        cache.get(first.token, ('first',))
    assert error.value.status_code == 410


def test_concurrent_queries_build_one_snapshot():
    cache = InventoryCache()
    builds = []
    def build():
        builds.append(True)
        time.sleep(.02)
        return {'items': [], 'stats': {'total': 0}, 'last_scan': None}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: cache.get_or_create(('all',), 1, build).token, range(4)))
    assert len(set(tokens)) == 1 and len(builds) == 1
