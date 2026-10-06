from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner


@pytest.fixture
def client(tmp_path):
    root = tmp_path / 'inventory'
    for number in range(1, 5):
        folder = root / f'S00{number}'
        folder.mkdir(parents=True)
        (folder / 'main.funscript').write_text('{"actions":[]}')
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, r'D:\inventory', 'Inventory'),),
                    ffmpeg='missing', ffprobe='missing', preview_output_root=tmp_path / 'previews')
    app = create_app(config, start_worker=False)
    Scanner(app.state.store, config).scan()
    with TestClient(app) as client:
        yield client


def test_actual_date_sort_is_before_pagination_and_unknown_dates_last(client):
    rows = client.get('/api/works').json()['items']
    ids = {work['script_id']: work['id'] for work in rows}
    dates = {'S001': ('2026-10-05', '2026-10-01'), 'S002': ('2026-10-01', '2026-10-05'),
             'S003': ('2026-10-03', '2026-10-02')}
    for name, (es, patreon) in dates.items():
        assert client.patch(f'/api/works/{ids[name]}', json={'es_published_date': es, 'patreon_published_date': patreon}).status_code == 200
    def names(**query):
        return [work['script_id'] for work in client.get('/api/works', params=query).json()['items']]
    assert names() == ['S001', 'S003', 'S002', 'S004']
    assert names(page_size=1, page=2) == ['S003']
    assert names(sort_direction='asc') == ['S002', 'S003', 'S001', 'S004']
    assert names(sort_platform='patreon') == ['S002', 'S003', 'S001', 'S004']
    assert client.get('/api/works?sort_platform=unknown').status_code == 422
    assert client.get('/api/works?sort_direction=unknown').status_code == 422


def test_tag_categories_or_within_and_between_and_legacy_filters(client):
    works = {work['script_id']: work for work in client.get('/api/works').json()['items']}
    tags = []
    for category, name in [('author', 'A'), ('author', 'B'), ('video_type', 'Real'), ('video_type', '3DCG')]:
        tags.append(client.post('/api/tags', json={'category': category, 'name': name}).json()['id'])
    for name, assigned in [('S001', [tags[0], tags[2]]), ('S002', [tags[1], tags[2]]), ('S003', [tags[1], tags[3]])]:
        work = works[name]
        assert client.put(f'/api/works/{work["id"]}/tags', json={'tag_ids': assigned, 'expected_revision': work['tags_revision']}).status_code == 200
    query = {'tag_ids': ','.join(str(value) for value in tags[:3])}
    result = client.get('/api/works', params=query).json()
    assert result['total'] == 2 and {work['script_id'] for work in result['items']} == {'S001', 'S002'}
    assert client.get('/api/works', params={**query, 'q': 'S002'}).json()['total'] == 1
    assert client.get('/api/works', params={'tag_id': tags[0]}).json()['total'] == 1
    assert client.get('/api/works', params={**query, 'untagged_only': 'true'}).status_code == 422
    assert client.get('/api/works?tag_ids=1,wat').status_code == 422
    assert client.get('/api/works?tag_ids=999999').status_code == 404


def test_atomic_release_editor_and_calendar_conflict_preserve_links(client, monkeypatch):
    monkeypatch.setattr('backend.work_links.release_today', lambda: '2026-10-06')
    work_id = client.get('/api/works').json()['items'][0]['id']
    path = f'/api/works/{work_id}/links'
    before = client.get(path).json()
    response = client.patch(path, json={'expected_revision': before['links_revision'],
        'expected_publication_revision': before['publication_revision'],
        'links': {'patreon': 'https://example.test/post'}, 'es_planned_date': '2026-10-09'})
    assert response.status_code == 200
    after = response.json()
    assert after['patreon_published'] is True and after['patreon_published_date'] == '2026-10-06'
    assert after['es_published'] is False and after['es_planned_date'] == '2026-10-09'
    calendar = client.get('/api/release-calendar?month=2026-10').json()
    work = next(item for item in calendar['works'] if item['id'] == work_id)
    assert work['revision'] == after['publication_revision']
    assert client.post('/api/release-calendar', json={'work_id': work_id, 'platforms': ['es'],
        'mode': 'planned', 'date': '2026-10-10', 'expected_revision': work['revision']}).status_code == 200
    stale = client.patch(path, json={'expected_revision': after['links_revision'],
        'expected_publication_revision': after['publication_revision'], 'links': {'video': 'https://example.test/new'},
        'es_planned_date': '2026-10-11'})
    assert stale.status_code == 409
    final = client.get(path).json()
    assert final['links']['video'] == '' and final['es_planned_date'] == '2026-10-10'


def test_unpublished_platform_new_link_sets_today_but_replacement_preserves_date(client, monkeypatch):
    monkeypatch.setattr('backend.work_links.release_today', lambda: '2026-10-06')
    work_id = client.get('/api/works').json()['items'][0]['id']
    path = f'/api/works/{work_id}'
    client.patch(path, json={'patreon_published_date': '2026-01-01'})
    state = client.get(path + '/links').json()
    state = client.patch(path + '/links', json={'expected_revision': state['links_revision'],
        'links': {'patreon': 'https://example.test/first'}}).json()
    assert state['patreon_published'] is True and state['patreon_published_date'] == '2026-10-06'
    monkeypatch.setattr('backend.work_links.release_today', lambda: '2026-10-07')
    state = client.patch(path + '/links', json={'expected_revision': state['links_revision'],
        'links': {'patreon': 'https://example.test/second', 'video': 'https://example.test/video'}}).json()
    assert state['patreon_published_date'] == '2026-10-06' and state['es_published'] is False
    assert client.patch(path + '/links', json={'expected_revision': state['links_revision'], 'es_published': True}).status_code == 422
