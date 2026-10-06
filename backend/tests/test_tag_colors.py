from concurrent.futures import ThreadPoolExecutor
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.config import Config
from backend.main import create_app
from backend.store import SCHEMA, Store, add_folder_schema, add_inventory_revision_schema, inventory_revision, now
from backend.tags import TagError, TagService


@pytest.fixture
def colored_tags(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=(), preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    with app.state.store.connection() as db:
        for work_id in (7, 8):
            db.execute('INSERT INTO works(id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (work_id, f'Work {work_id}', '保留备注', now(), now()))
    with TestClient(app) as client:
        yield client, app.state.store, config


def create_color(client, **fields):
    result = client.post('/api/tags', json={'category': 'custom', 'name': 'Colored', **fields})
    assert result.status_code == 201
    return result.json()


def test_colors_persist_in_catalog_bindings_inventory_and_restart(colored_tags):
    client, store, config = colored_tags
    tag = create_color(client, color_light='#a1b2c3', color_dark='#d4e5f6')
    assert tag['color_light'] == '#A1B2C3' and tag['color_dark'] == '#D4E5F6'
    for work_id in (7, 8):
        binding = client.put(f'/api/works/{work_id}/tags', json={'tag_ids': [tag['id']], 'expected_revision': 0})
        assert binding.status_code == 200 and binding.json()['tags'][0]['color_light'] == '#A1B2C3'
    assert client.get('/api/tags').json()['items'][0]['color_dark'] == '#D4E5F6'
    items = client.get('/api/works').json()['items']
    assert len(items) == 2 and all(work['tags'][0]['color_light'] == '#A1B2C3' for work in items)
    with TestClient(create_app(config, start_worker=False)) as restarted:
        restored = restarted.get('/api/tags').json()['items'][0]
        assert restored['id'] == tag['id'] and restored['color_dark'] == '#D4E5F6'
        assert restarted.get('/api/works/7/tags').json()['tags'][0]['color_light'] == '#A1B2C3'


def test_partial_edits_null_reset_and_equivalent_colors_are_no_op(colored_tags):
    client, store, _ = colored_tags
    tag = create_color(client, color_light='#102030', color_dark='#aabbcc')
    path = f'/api/tags/{tag["id"]}'
    with store.connection() as db:
        before = inventory_revision(db)
    same = client.patch(path, json={'expected_revision': 1, 'color_dark': '#aabbcc'}).json()
    assert same['revision'] == 1
    with store.connection() as db:
        assert inventory_revision(db) == before
    cleared = client.patch(path, json={'expected_revision': 1, 'color_light': None})
    assert cleared.status_code == 200
    assert cleared.json()['color_light'] is None and cleared.json()['color_dark'] == '#AABBCC'
    renamed = client.patch(path, json={'expected_revision': 2, 'name': 'Renamed'}).json()
    assert renamed['color_light'] is None and renamed['color_dark'] == '#AABBCC'
    restored = client.patch(path, json={'expected_revision': 3, 'color_dark': None}).json()
    assert restored['color_light'] is restored['color_dark'] is None
    assert restored['revision'] == 4


@pytest.mark.parametrize('field', ['color_light', 'color_dark'])
@pytest.mark.parametrize('value', ['red', '#ABC', '#12345678', '#GGGGGG', 'rgb(1,2,3)', '',
                                   ' #123456', '#123456\n', '#12 456', 123456, True, {}, []])
def test_invalid_create_and_update_colors_are_rejected_without_mutation(colored_tags, field, value):
    client, store, _ = colored_tags
    tag = create_color(client, color_light='#123456')
    with store.connection() as db:
        before = inventory_revision(db)
    invalid_create = client.post('/api/tags', json={'category': 'author', 'name': 'Bad color', field: value})
    assert invalid_create.status_code == 422
    invalid_update = client.patch(f'/api/tags/{tag["id"]}', json={'expected_revision': 1, field: value})
    assert invalid_update.status_code == 422
    current = client.get('/api/tags').json()['items']
    assert len(current) == 1 and current[0]['revision'] == 1 and current[0]['color_light'] == '#123456'
    with store.connection() as db:
        assert inventory_revision(db) == before


def test_service_validation_is_strict_and_database_rejects_non_hex(colored_tags):
    client, store, _ = colored_tags
    tag = create_color(client)
    service = TagService(store)
    with pytest.raises(TagError):
        service.create({'category': 'custom', 'name': 'Bad', 'color_light': 'var(--primary)'})
    with pytest.raises(TagError):
        service.update(tag['id'], {'expected_revision': 1, 'color_dark': 123456})
    with store.connection() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE tags SET color_light='red' WHERE id=?", (tag['id'],))
        assert db.execute('SELECT color_light,color_dark,revision FROM tags WHERE id=?', (tag['id'],)).fetchone()[:] == (None, None, 1)


def test_color_edits_invalidate_bindings_and_inventory_without_protecting_scanned_categories(colored_tags):
    client, store, _ = colored_tags
    tag = create_color(client, color_light='#112233')
    for work_id in (7, 8):
        client.put(f'/api/works/{work_id}/tags', json={'tag_ids': [tag['id']], 'expected_revision': 0}).raise_for_status()
    with store.connection() as db:
        db.execute('UPDATE work_tag_state SET manual_edited=0')
        db.execute("UPDATE work_tags SET source='scan'")
    before = client.get('/api/works').json()
    result = client.patch(f'/api/tags/{tag["id"]}', json={'expected_revision': 1, 'color_light': '#445566'})
    assert result.status_code == 200 and result.json()['revision'] == 2
    for work_id in (7, 8):
        current = client.get(f'/api/works/{work_id}/tags').json()
        assert current['tags_revision'] == 2 and current['tags'][0]['color_light'] == '#445566'
        stale = client.put(f'/api/works/{work_id}/tags', json={'tag_ids': [], 'expected_revision': 1})
        assert stale.status_code == 409
        with store.connection() as db:
            assert db.execute('SELECT manual_edited FROM work_tag_state WHERE work_id=?', (work_id,)).fetchone()[0] == 0
            assert db.execute('SELECT notes FROM works WHERE id=?', (work_id,)).fetchone()[0] == '保留备注'
    after = client.get('/api/works').json()
    assert after['inventory_revision'] > before['inventory_revision']
    assert after['snapshot_id'] != before['snapshot_id']
    assert after['items'][0]['tags'][0]['color_light'] == '#445566'
    historical = client.get('/api/works', params={'snapshot_id': before['snapshot_id']}).json()
    assert historical['items'][0]['tags'][0]['color_light'] == '#112233'


def test_stale_color_edits_do_not_override_current_palette(colored_tags):
    client, store, _ = colored_tags
    tag = create_color(client)
    path = f'/api/tags/{tag["id"]}'
    client.patch(path, json={'expected_revision': 1, 'color_light': '#112233'}).raise_for_status()
    before = client.get('/api/inventory-revision').json()['inventory_revision']
    assert client.patch(path, json={'expected_revision': 1, 'color_dark': '#445566'}).status_code == 409
    current = client.get('/api/tags').json()['items'][0]
    assert current['revision'] == 2 and current['color_dark'] is None
    assert client.get('/api/inventory-revision').json()['inventory_revision'] == before


def test_cosmetic_enum_colors_do_not_rewrite_or_require_repair_of_legacy_classification(colored_tags):
    client, store, _ = colored_tags
    release = client.post('/api/tags', json={'category': 'release_type', 'name': 'Paid'}).json()
    tier = client.post('/api/tags', json={'category': 'tier', 'name': 'Free'}).json()
    # Historical rows can contain a combination which the modern editor will reject.
    with store.connection() as db:
        db.executemany('INSERT INTO work_tags(work_id,tag_id,source) VALUES(7,?,?)',
                       [(release['id'], 'import'), (tier['id'], 'import')])
    changed = client.patch(f'/api/tags/{release["id"]}', json={'expected_revision': 1, 'color_light': '#112233'})
    assert changed.status_code == 200 and changed.json()['name'] == 'Paid'
    current = client.get('/api/works/7/tags').json()
    assert {tag['id'] for tag in current['tags']} == {release['id'], tier['id']}
    assert current['tags_revision'] == 1


def test_parallel_color_edits_have_one_winner(colored_tags):
    client, store, _ = colored_tags
    tag = create_color(client)
    service = TagService(store)
    def edit(color):
        try:
            return service.update(tag['id'], {'expected_revision': 1, 'color_light': color})
        except TagError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['#112233', '#445566']))
    assert results.count(409) == 1
    winner = next(value for value in results if isinstance(value, dict))
    assert winner['revision'] == 2
    assert client.get('/api/tags').json()['items'][0]['color_light'] == winner['color_light']


@pytest.mark.parametrize('legacy_categories', [False, True])
def test_legacy_migration_preserves_tag_ids_bindings_metadata_and_updates_revision_trigger(tmp_path, legacy_categories):
    legacy_schema = '\n'.join(line for line in SCHEMA.splitlines() if not line.lstrip().startswith(('color_light ', 'color_dark ')))
    if legacy_categories:
        legacy_schema = legacy_schema.replace("'axis_type',", '').replace("'duration',", '')
    db = sqlite3.connect(tmp_path / 'workbench.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript(legacy_schema)
    add_folder_schema(db)
    add_inventory_revision_schema(db)
    db.execute("INSERT INTO works(id,title,notes,created_at,updated_at) VALUES(7,'Original','保留备注','a','a')")
    db.execute("INSERT INTO tags(id,category,name,name_key,revision,support_status,support_url) VALUES(13,'author','Creator','creator',4,'url','https://example.test/creator')")
    db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(7,13,'import')")
    db.execute('INSERT INTO work_tag_state(work_id,revision) VALUES(7,8)')
    for key in ('production_confirmation_initialized', 'axis_tags_initialized'):
        db.execute('INSERT INTO settings(key,value) VALUES(?,?)', (key, 'true'))
    before = inventory_revision(db)
    assert 'color_light' not in db.execute("SELECT sql FROM sqlite_master WHERE name='inventory_tags_update'").fetchone()[0]
    db.commit(); db.close()
    store = Store(tmp_path)
    service = TagService(store)
    with store.connection() as db:
        current = service.work_state(db, 7)
        assert current['tags_revision'] == 8
        assert current['tags'][0]['id'] == 13 and current['tags'][0]['revision'] == 4
        assert current['tags'][0]['support_url'] == 'https://example.test/creator'
        assert current['tags'][0]['color_light'] is current['tags'][0]['color_dark'] is None
        assert inventory_revision(db) == before
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name='inventory_tags_update'").fetchone()[0]
        assert 'OLD."color_light"' in trigger and 'OLD."color_dark"' in trigger
        db.execute("UPDATE tags SET color_dark='#A0B0C0' WHERE id=13")
        assert inventory_revision(db) == before + 1
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    Store(tmp_path)
    with store.connection() as db:
        assert service.tag(db, 13)['color_dark'] == '#A0B0C0'
        assert service.work_state(db, 7)['tags_revision'] == 8
