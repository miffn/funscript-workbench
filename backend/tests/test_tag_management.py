from concurrent.futures import ThreadPoolExecutor
import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.config import Config
from backend.durations import DurationService
from backend.import_tags import import_tags
from backend.main import create_app
from backend.store import SCHEMA, Store, add_folder_schema, add_inventory_revision_schema, inventory_revision, now
from backend.tags import CATEGORIES, TagService, sync_axis_tag


@pytest.fixture
def management(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=(), preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    with app.state.store.connection() as db:
        for work_id in (7, 8):
            db.execute('INSERT INTO works(id,script_id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                       (work_id, f'S00{work_id}', f'Work {work_id}', '保留备注', now(), now()))
    with TestClient(app) as client:
        yield client, app.state.store, config


def create_tag(client, category='custom', name='Tag'):
    response = client.post('/api/tags', json={'category': category, 'name': name, 'color_light': '#123456', 'bold': False})
    assert response.status_code == 201
    return response.json()


def delete(client, tag, revision=None):
    return client.request('DELETE', f'/api/tags/{tag["id"]}', json={'expected_revision': revision or tag['revision']})


def bind(client, work_id, ids, revision=0):
    result = client.put(f'/api/works/{work_id}/tags', json={'tag_ids': ids, 'expected_revision': revision})
    assert result.status_code == 200
    return result.json()


def test_custom_categories_create_rename_style_and_multi_selection_survive_restart(management):
    client, _, config = management
    category = client.post('/api/tag-categories', json={'name': '系列'}).json()
    key = category['category']
    assert re.fullmatch(r'custom_[1-9][0-9]*', key)
    assert category == {'category': key, 'name': '系列', 'is_custom': True, 'revision': 1}
    first = create_tag(client, key, 'Series A')
    second = create_tag(client, key, 'Series B')
    assert first['category_label'] == '系列' and first['category_style']['revision'] == 0
    assert len(bind(client, 7, [first['id'], second['id']])['tags']) == 2
    style = client.patch(f'/api/tag-category-styles/{key}', json={'expected_revision': 0, 'bold': True, 'color_dark': '#aabbcc'})
    assert style.status_code == 200 and style.json()['color_dark'] == '#AABBCC'
    renamed = client.patch(f'/api/tag-categories/{key}', json={'name': '场景', 'expected_revision': 1})
    assert renamed.status_code == 200 and renamed.json()['revision'] == 2
    assert client.patch(f'/api/tag-categories/{key}', json={'name': '旧名覆盖', 'expected_revision': 1}).status_code == 409
    catalog = client.get('/api/tags').json()
    assert catalog['categories'] == [*CATEGORIES, key]
    assert len(catalog['category_definitions']) == 8
    assert all(tag['category_label'] == '场景' for tag in catalog['items'])
    assert all(tag['category_style']['bold'] is True and tag['bold'] is False for tag in catalog['items'])
    with TestClient(create_app(config, start_worker=False)) as restarted:
        restored = restarted.get('/api/tags').json()
        assert restored['category_definitions'][-1] == renamed.json()
        assert len(restarted.get('/api/works/7/tags').json()['tags']) == 2


@pytest.mark.parametrize('name', ['', ' ', 'bad\nname', '作者', 1, None])
def test_invalid_category_names_are_rejected(management, name):
    client, _, _ = management
    assert client.post('/api/tag-categories', json={'name': name}).status_code == 422


def test_category_name_uniqueness_no_op_and_builtin_rename_guards(management):
    client, store, _ = management
    first = client.post('/api/tag-categories', json={'name': 'Series'}).json()
    assert client.post('/api/tag-categories', json={'name': 'series'}).status_code == 409
    second = client.post('/api/tag-categories', json={'name': 'Scene'}).json()
    assert client.patch(f'/api/tag-categories/{second["category"]}', json={'name': 'SERIES', 'expected_revision': 1}).status_code == 409
    with store.connection() as db:
        before = inventory_revision(db)
    same = client.patch(f'/api/tag-categories/{first["category"]}', json={'name': 'Series', 'expected_revision': 1}).json()
    assert same['revision'] == 1
    with store.connection() as db:
        assert inventory_revision(db) == before
    assert client.patch('/api/tag-categories/author', json={'name': '新作者类型', 'expected_revision': 1}).status_code == 422


@pytest.mark.parametrize('category', ['unknown', 'custom_0', 'custom_01', 'custom_999', 'custom_9223372036854775808'])
def test_unknown_category_rejected_for_tags_and_styles(management, category):
    client, _, _ = management
    assert client.post('/api/tags', json={'category': category, 'name': 'Bad'}).status_code == 422
    assert client.get(f'/api/tag-category-styles/{category}').status_code == 422


def test_shared_delete_hides_tag_without_destroying_bindings_and_restore_preserves_metadata(management):
    client, store, _ = management
    tag = create_tag(client)
    for work_id in (7, 8):
        bind(client, work_id, [tag['id']])
    with store.connection() as db:
        db.execute('UPDATE work_tag_state SET manual_edited=0')
    before = client.get('/api/works').json()
    response = delete(client, tag)
    assert response.status_code == 200
    removed = response.json()
    assert removed['affected_work_count'] == 2 and removed['tag']['deleted'] is True and removed['tag']['revision'] == 2
    assert client.get('/api/tags').json()['items'] == []
    recycled = client.get('/api/tags?include_deleted=1').json()['items'][0]
    assert recycled['id'] == tag['id'] and recycled['color_light'] == '#123456' and recycled['bold'] is False
    for work_id in (7, 8):
        state = client.get(f'/api/works/{work_id}/tags').json()
        assert state['tags'] == [] and state['tags_revision'] == 2
    assert client.get('/api/works?q=Tag').json()['total'] == 0
    assert client.get('/api/works?untagged_only=true').json()['total'] == 2
    assert client.get(f'/api/works?tag_id={tag["id"]}').status_code == 404
    assert client.put('/api/works/7/tags', json={'tag_ids': [tag['id']], 'expected_revision': 2}).status_code == 404
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM work_tags WHERE tag_id=?', (tag['id'],)).fetchone()[0] == 2
        assert [row[0] for row in db.execute('SELECT manual_edited FROM work_tag_state')] == [0, 0]
    after = client.get('/api/works').json()
    assert after['inventory_revision'] > before['inventory_revision']
    assert client.get('/api/works', params={'snapshot_id': before['snapshot_id']}).json()['items'][0]['tags'][0]['id'] == tag['id']
    restored = client.post(f'/api/tags/{tag["id"]}/restore', json={'expected_revision': 2})
    assert restored.status_code == 200 and restored.json()['affected_work_count'] == 2
    assert restored.json()['tag']['deleted'] is False and restored.json()['tag']['revision'] == 3
    assert client.get('/api/works/7/tags').json()['tags_revision'] == 3


def test_editing_other_tags_keeps_tombstone_relationships_for_later_restore(management):
    client, store, _ = management
    old = create_tag(client, name='Old')
    new = create_tag(client, name='New')
    bind(client, 7, [old['id']])
    delete(client, old).raise_for_status()
    bind(client, 7, [new['id']], revision=2)
    with store.connection() as db:
        assert {row[0] for row in db.execute('SELECT tag_id FROM work_tags WHERE work_id=7')} == {old['id'], new['id']}
    client.post(f'/api/tags/{old["id"]}/restore', json={'expected_revision': 2}).raise_for_status()
    assert len(client.get('/api/works/7/tags').json()['tags']) == 2


def test_delete_restore_expected_revision_no_op_and_name_tombstone_guards(management):
    client, store, _ = management
    tag = create_tag(client)
    delete(client, tag).raise_for_status()
    assert delete(client, tag).status_code == 409
    with store.connection() as db:
        before = inventory_revision(db)
    assert delete(client, tag, revision=2).json()['tag']['revision'] == 2
    with store.connection() as db:
        assert inventory_revision(db) == before
    assert client.post('/api/tags', json={'category': 'custom', 'name': 'Tag'}).status_code == 409
    assert client.patch(f'/api/tags/{tag["id"]}', json={'expected_revision': 2, 'name': 'Hidden renamed'}).status_code == 409
    assert client.post(f'/api/tags/{tag["id"]}/restore', json={'expected_revision': 1}).status_code == 409
    client.post(f'/api/tags/{tag["id"]}/restore', json={'expected_revision': 2}).raise_for_status()
    assert client.post(f'/api/tags/{tag["id"]}/restore', json={'expected_revision': 3}).json()['tag']['revision'] == 3


def test_restore_rejects_singleton_collision_atomically(management):
    client, _, _ = management
    old = create_tag(client, 'author', 'A')
    new = create_tag(client, 'author', 'B')
    bind(client, 7, [old['id']])
    delete(client, old).raise_for_status()
    bind(client, 7, [new['id']], revision=2)
    response = client.post(f'/api/tags/{old["id"]}/restore', json={'expected_revision': 2})
    assert response.status_code == 409 and '冲突' in response.json()['detail']
    assert client.get('/api/works/7/tags').json()['tags'][0]['id'] == new['id']
    assert next(tag for tag in client.get('/api/tags?include_deleted=1').json()['items'] if tag['id'] == old['id'])['revision'] == 2
    bind(client, 7, [], revision=3)
    client.post(f'/api/tags/{old["id"]}/restore', json={'expected_revision': 2}).raise_for_status()


def test_duration_and_axis_scan_tombstones_are_not_rebuilt_or_revised_repeatedly(management):
    client, store, config = management
    duration = DurationService(store, config)
    with store.connection() as db:
        duration.sync_tag(db, 7, 18)
        db.execute("UPDATE works SET metadata='{\"axis_type\":\"单轴\"}' WHERE id=8")
        sync_axis_tag(db, 8, initialize=True)
    catalog = client.get('/api/tags').json()['items']
    automatic = [tag for tag in catalog if tag['category'] in {'duration', 'axis_type'}]
    assert len(automatic) == 2
    for tag in automatic:
        delete(client, tag).raise_for_status()
    with store.connection() as db:
        before = inventory_revision(db)
        for _ in range(3):
            duration.sync_tag(db, 7, 18)
            sync_axis_tag(db, 8, initialize=True)
        assert inventory_revision(db) == before
        assert db.execute('SELECT count(*) FROM tags').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM work_tags').fetchone()[0] == 2
    assert client.get('/api/tags').json()['items'] == []
    assert all(tag['deleted'] and tag['revision'] == 2 for tag in client.get('/api/tags?include_deleted=1').json()['items'])


def test_import_does_not_reanimate_deleted_author_or_destroy_its_binding(management):
    client, store, config = management
    author = create_tag(client, 'author', 'Deleted Author')
    with store.connection() as db:
        db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(7,?,'import')", (author['id'],))
    delete(client, author).raise_for_status()
    source = {'rows': [{'Script ID': 'S007', 'Creator': 'Deleted Author', 'Support Creator URL': 'https://example.test/changed'}]}
    import_tags(config.data_dir, {}, source)
    with store.connection() as db:
        assert db.execute('SELECT deleted,revision FROM tags WHERE id=?', (author['id'],)).fetchone()[:] == (1, 2)
        assert db.execute('SELECT count(*) FROM work_tags WHERE work_id=7 AND tag_id=?', (author['id'],)).fetchone()[0] == 1


def test_legacy_category_check_and_deleted_migration_preserve_styles_ids_bindings_and_triggers(tmp_path):
    legacy = '\n'.join(line for line in SCHEMA.splitlines() if not line.lstrip().startswith('deleted '))
    legacy = legacy.replace(" OR category GLOB 'custom_[0-9]*'", '')
    legacy = re.sub(r'CREATE TABLE IF NOT EXISTS tag_categories \([\s\S]+?\);', '', legacy)
    db = sqlite3.connect(tmp_path / 'workbench.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript(legacy)
    add_folder_schema(db)
    add_inventory_revision_schema(db)
    db.execute("INSERT INTO works(id,title,notes,created_at,updated_at) VALUES(7,'Original','保留备注','a','a')")
    db.execute("INSERT INTO tags(id,category,name,name_key,revision,color_light,bold) VALUES(13,'author','Creator','creator',4,'#112233',0)")
    db.execute('INSERT INTO work_tags(work_id,tag_id) VALUES(7,13)')
    db.execute('INSERT INTO work_tag_state(work_id,revision) VALUES(7,8)')
    db.execute("INSERT INTO tag_category_styles(category,color_dark,bold,revision) VALUES('author','#AABBCC',1,3)")
    for key in ('production_confirmation_initialized', 'axis_tags_initialized'):
        db.execute('INSERT INTO settings(key,value) VALUES(?,?)', (key, 'true'))
    db.commit(); db.close()
    store = Store(tmp_path)
    service = TagService(store)
    category = service.create_category('系列')
    created = service.create({'category': category['category'], 'name': 'New'})
    assert created['category_label'] == '系列'
    with store.connection() as db:
        tag = service.tag(db, 13)
        assert tag['revision'] == 4 and not tag['deleted'] and tag['bold'] is False and tag['color_light'] == '#112233'
        assert tag['category_style']['revision'] == 3 and tag['category_style']['bold'] is True
        assert service.work_state(db, 7)['tags_revision'] == 8
        before = inventory_revision(db)
        db.execute('UPDATE tags SET deleted=1 WHERE id=13')
        assert inventory_revision(db) == before + 1
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    Store(tmp_path)
    assert service.catalog()['category_definitions'][-1]['category'] == category['category']
