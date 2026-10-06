from concurrent.futures import ThreadPoolExecutor
import re
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.config import Config
from backend.main import create_app
from backend.store import SCHEMA, Store, add_folder_schema, inventory_revision, now
from backend.tags import CATEGORIES, TagError, TagService


@pytest.fixture
def category_client(tmp_path):
    config = Config(data_dir=tmp_path / 'data', roots=(), preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    with app.state.store.connection() as db:
        for work_id in (7, 8):
            db.execute('INSERT INTO works(id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                       (work_id, f'Work {work_id}', '保留备注', now(), now()))
    with TestClient(app) as client:
        yield client, app.state.store, config


def test_catalog_initializes_all_nullable_category_defaults_at_revision_zero(category_client):
    client, store, _ = category_client
    catalog = client.get('/api/tags').json()
    assert {style['category'] for style in catalog['category_styles']} == set(CATEGORIES)
    for style in catalog['category_styles']:
        assert style == {'category': style['category'], 'color_light': None, 'color_dark': None, 'bold': None, 'revision': 0}
        assert client.get(f'/api/tag-category-styles/{style["category"]}').json() == style
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM tag_category_styles').fetchone()[0] == len(CATEGORIES)


def test_category_defaults_are_shared_without_overwriting_tag_overrides_or_classification(category_client):
    client, store, _ = category_client
    custom = client.post('/api/tags', json={'category': 'author', 'name': 'Override', 'color_light': '#102030', 'bold': False}).json()
    inherited = client.post('/api/tags', json={'category': 'author', 'name': 'Inherited'}).json()
    for work_id, tag in ((7, custom), (8, inherited)):
        client.put(f'/api/works/{work_id}/tags', json={'expected_revision': 0, 'tag_ids': [tag['id']]}).raise_for_status()
    with store.connection() as db:
        db.execute('UPDATE work_tag_state SET manual_edited=0')
    before = client.get('/api/works').json()
    style = client.patch('/api/tag-category-styles/author', json={'expected_revision': 0,
                         'color_light': '#aabbcc', 'color_dark': '#ddeeFF', 'bold': True})
    assert style.status_code == 200
    expected = {'category': 'author', 'color_light': '#AABBCC', 'color_dark': '#DDEEFF', 'bold': True, 'revision': 1}
    assert style.json() == expected
    for work_id in (7, 8):
        state = client.get(f'/api/works/{work_id}/tags').json()
        assert state['tags_revision'] == 1 and state['tags'][0]['revision'] == 1
        assert state['tags'][0]['category_style'] == expected
    catalog = client.get('/api/tags').json()
    overridden = next(tag for tag in catalog['items'] if tag['id'] == custom['id'])
    assert overridden['color_light'] == '#102030' and overridden['bold'] is False
    default = next(tag for tag in catalog['items'] if tag['id'] == inherited['id'])
    assert default['color_light'] is default['bold'] is None
    assert next(style for style in catalog['category_styles'] if style['category'] == 'author') == expected
    with store.connection() as db:
        assert [row[0] for row in db.execute('SELECT manual_edited FROM work_tag_state')] == [0, 0]
    after = client.get('/api/works').json()
    assert after['inventory_revision'] == before['inventory_revision'] + 1
    assert after['snapshot_id'] != before['snapshot_id']
    assert all(work['tags'][0]['category_style']['revision'] == 1 for work in after['items'])
    historical = client.get('/api/works', params={'snapshot_id': before['snapshot_id']}).json()
    assert all(work['tags'][0]['category_style']['revision'] == 0 for work in historical['items'])


def test_category_partial_reset_no_op_conflict_and_restart(category_client):
    client, store, config = category_client
    path = '/api/tag-category-styles/video_type'
    client.patch(path, json={'expected_revision': 0, 'color_light': '#112233', 'color_dark': '#aabbcc', 'bold': True}).raise_for_status()
    with store.connection() as db:
        before = inventory_revision(db)
    same = client.patch(path, json={'expected_revision': 1, 'color_dark': '#aabbcc'}).json()
    assert same['revision'] == 1
    with store.connection() as db:
        assert inventory_revision(db) == before
    assert client.patch(path, json={'expected_revision': 0, 'bold': False}).status_code == 409
    partial = client.patch(path, json={'expected_revision': 1, 'color_light': None}).json()
    assert partial == {'category': 'video_type', 'color_light': None, 'color_dark': '#AABBCC', 'bold': True, 'revision': 2}
    with TestClient(create_app(config, start_worker=False)) as restarted:
        assert restarted.get(path).json() == partial
        reset = restarted.patch(path, json={'expected_revision': 2, 'color_dark': None, 'bold': None}).json()
        assert reset['revision'] == 3 and reset['color_light'] is reset['color_dark'] is reset['bold'] is None


@pytest.mark.parametrize('fields', [{'bold': 1}, {'bold': 0}, {'bold': 'false'}, {'bold': []},
                                   {'color_light': '#ABC'}, {'color_dark': 'red'}, {'color_dark': 123456},
                                   {'expected_revision': True}, {'expected_revision': -1}, {'unknown': True}])
def test_invalid_category_style_input_is_atomic(category_client, fields):
    client, store, _ = category_client
    with store.connection() as db:
        before = inventory_revision(db)
    assert client.patch('/api/tag-category-styles/author', json={'expected_revision': 0, **fields}).status_code == 422
    assert client.get('/api/tag-category-styles/author').json()['revision'] == 0
    with store.connection() as db:
        assert inventory_revision(db) == before


def test_unknown_category_rejected_and_duration_category_display_is_editable(category_client):
    client, _, _ = category_client
    assert client.get('/api/tag-category-styles/unknown').status_code == 422
    assert client.patch('/api/tag-category-styles/unknown', json={'expected_revision': 0, 'bold': True}).status_code == 422
    response = client.patch('/api/tag-category-styles/duration', json={'expected_revision': 0, 'bold': True, 'color_light': '#102030'})
    assert response.status_code == 200 and response.json()['revision'] == 1


def test_single_tag_bold_is_nullable_strict_and_invalidates_only_its_bound_tag_state(category_client):
    client, store, _ = category_client
    client.patch('/api/tag-category-styles/author', json={'expected_revision': 0, 'bold': True}).raise_for_status()
    tag = client.post('/api/tags', json={'category': 'author', 'name': 'Author', 'bold': False}).json()
    assert tag['bold'] is False and tag['category_style']['bold'] is True
    client.put('/api/works/7/tags', json={'tag_ids': [tag['id']], 'expected_revision': 0}).raise_for_status()
    path = f'/api/tags/{tag["id"]}'
    renamed = client.patch(path, json={'expected_revision': 1, 'name': 'Author renamed'}).json()
    assert renamed['bold'] is False and renamed['revision'] == 2
    reset = client.patch(path, json={'expected_revision': 2, 'bold': None}).json()
    assert reset['bold'] is None and reset['category_style']['bold'] is True and reset['revision'] == 3
    assert client.get('/api/works/7/tags').json()['tags_revision'] == 3
    for bad in (1, 0, 'true', 'false', [], {}):
        assert client.patch(path, json={'expected_revision': 3, 'bold': bad}).status_code == 422
        assert client.post('/api/tags', json={'category': 'author', 'name': 'Bad', 'bold': bad}).status_code == 422
    with pytest.raises(TagError):
        TagService(store).update_category_style('author', {'expected_revision': 1, 'bold': 1})


def test_parallel_category_style_edits_have_one_winner(category_client):
    client, store, _ = category_client
    service = TagService(store)
    def edit(color):
        try:
            return service.update_category_style('author', {'expected_revision': 0, 'color_light': color})
        except TagError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['#112233', '#445566']))
    assert results.count(409) == 1
    winner = next(result for result in results if isinstance(result, dict))
    assert client.get('/api/tag-category-styles/author').json() == winner


def test_legacy_tag_bold_migration_preserves_metadata_and_refreshes_old_revision_trigger(tmp_path):
    legacy = '\n'.join(line for line in SCHEMA.splitlines() if not line.lstrip().startswith('bold '))
    legacy = re.sub(r'CREATE TABLE IF NOT EXISTS tag_category_styles \([\s\S]+?\);', '', legacy)
    db = sqlite3.connect(tmp_path / 'workbench.sqlite3')
    db.row_factory = sqlite3.Row
    db.executescript(legacy)
    add_folder_schema(db)
    db.execute('CREATE TABLE inventory_state(id INTEGER PRIMARY KEY, revision INTEGER NOT NULL)')
    db.execute('INSERT INTO inventory_state VALUES(1,7)')
    db.execute('CREATE TRIGGER inventory_tags_update AFTER UPDATE ON tags WHEN OLD.revision IS NOT NEW.revision '
               'BEGIN UPDATE inventory_state SET revision=revision+1 WHERE id=1; END')
    db.execute("INSERT INTO works(id,title,notes,created_at,updated_at) VALUES(7,'Original','保留备注','a','a')")
    db.execute("INSERT INTO tags(id,category,name,name_key,revision,color_light,support_status,support_url) VALUES(13,'author','Creator','creator',4,'#112233','url','https://example.test/creator')")
    db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(7,13,'import')")
    db.execute('INSERT INTO work_tag_state(work_id,revision) VALUES(7,8)')
    for key in ('production_confirmation_initialized', 'axis_tags_initialized'):
        db.execute('INSERT INTO settings(key,value) VALUES(?,?)', (key, 'true'))
    db.commit(); db.close()
    store = Store(tmp_path)
    service = TagService(store)
    with store.connection() as db:
        tag = service.tag(db, 13)
        assert tag['bold'] is None and tag['revision'] == 4 and tag['color_light'] == '#112233'
        assert tag['support_url'] == 'https://example.test/creator'
        assert tag['category_style']['revision'] == 0
        assert service.work_state(db, 7)['tags_revision'] == 8
        assert inventory_revision(db) == 7
        db.execute('UPDATE tags SET bold=1 WHERE id=13')
        assert inventory_revision(db) == 8
        db.execute("UPDATE tag_category_styles SET bold=1,revision=1 WHERE category='author'")
        assert inventory_revision(db) == 9
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    Store(tmp_path)
    with store.connection() as db:
        assert inventory_revision(db) == 9
        assert service.tag(db, 13)['bold'] is True
        assert service.tag(db, 13)['category_style']['bold'] is True
