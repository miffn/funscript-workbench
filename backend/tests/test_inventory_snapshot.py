"""Inventory reads must stay responsive without touching source or cover storage."""
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import Store, now


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / 'materials'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, 'Z:\\materials', 'NAS', 'folder'),),
                    ffmpeg='missing-ffmpeg', ffprobe='missing-ffprobe', preview_output_root=tmp_path / 'previews')
    for name, script in [('S701', True), ('S702', False), ('S703', True), ('Unnumbered', True)]:
        folder = root / name
        folder.mkdir()
        (folder / 'video.mp4').write_bytes(b'video')
        if script:
            (folder / 'video.funscript').write_text('{"actions":[]}')
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    scanner.scan()
    with store.connection() as db:
        db.execute("UPDATE works SET es_published=1,patreon_published=1,status='published' WHERE script_id='S703'")
        work_id = db.execute("SELECT id FROM works WHERE script_id='S701'").fetchone()[0]
        # A stale local cover record is still a URL; the media endpoint checks the file.
        db.execute('INSERT INTO covers(work_id,fingerprint,path,source_path,updated_at) VALUES(?,?,?,?,?)',
                   (work_id, 'cached-cover', str(config.data_dir / 'covers' / 'cached.jpg'), 'source', now()))
    return config, store, scanner, work_id


def test_lists_filters_pagination_and_stats_never_probe_storage(snapshot, monkeypatch):
    config, store, _, work_id = snapshot
    with TestClient(create_app(config, start_worker=False)) as client:
        baseline = client.get('/api/works').json()
        assert baseline['stats']['pending'] == 2
        assert baseline['stats']['to_make'] == 1
        with store.connection() as db:
            tag_id = db.execute("SELECT tag_id FROM work_tags WHERE work_id=? LIMIT 1", (work_id,)).fetchone()[0]
            before = '\n'.join(db.iterdump())
        watched = (config.roots[0].path, config.data_dir / 'covers')

        def guard(original):
            def checked(path, *args, **kwargs):
                candidate = Path(path)
                if any(candidate.is_relative_to(root) for root in watched):
                    raise AssertionError(f'Inventory queried storage: {candidate}')
                return original(path, *args, **kwargs)
            return checked

        # These also intercept is_dir/is_file/is_symlink and root enumeration.
        with monkeypatch.context() as patch:
            patch.setattr(Path, 'stat', guard(Path.stat))
            patch.setattr(Path, 'resolve', guard(Path.resolve))
            patch.setattr(os, 'scandir', guard(os.scandir))
            for query in ('', '?status=pending', '?status=to_make', '?status=published',
                          '?status=es_published', '?status=patreon_published', '?q=S701',
                          '?issues_only=true', '?untagged_only=true', f'?tag_id={tag_id}',
                          '?page_size=1&page=1', '?page_size=1&page=2'):
                response = client.get('/api/works' + query)
                assert response.status_code == 200
                assert response.json()['stats'] == baseline['stats']
            assert client.get('/api/works').json() == baseline
        work = next(item for item in baseline['items'] if item['id'] == work_id)
        assert work['cover_url'].startswith(f'/api/covers/{work_id}?v=')
        assert client.get(work['cover_url']).status_code == 404
        with store.connection() as db:
            assert '\n'.join(db.iterdump()) == before


def test_list_stays_at_last_scan_while_details_check_live_then_scan_updates(snapshot):
    config, store, scanner, work_id = snapshot
    with TestClient(create_app(config, start_worker=False)) as client:
        before = client.get('/api/works').json()
        (config.roots[0].path / 'S701').rename(config.roots[0].path / '.moved')
        assert client.get('/api/works').json() == before
        assert client.get(f'/api/works/{work_id}').json()['association_status'] == 'missing'
        # A detail check does not mutate the last scan or start background scanning.
        assert client.get('/api/works').json() == before
        assert not client.get('/api/jobs').json()['items']
        scanner.scan()
        after = client.get('/api/works').json()
        work = next(item for item in after['items'] if item['id'] == work_id)
        assert work['association_status'] == 'missing'
        assert work['video_count'] == work['script_count'] == 0
        assert after['stats']['pending'] == 1
        assert after['stats']['to_make'] == 1


def test_unavailable_root_keeps_cached_counts_and_categories(snapshot):
    config, _, scanner, work_id = snapshot
    with TestClient(create_app(config, start_worker=False)) as client:
        before = client.get('/api/works').json()
        config.roots[0].path.rename(config.roots[0].path.with_name('offline'))
        scanner.scan()
        after = client.get('/api/works').json()
        assert after['stats']['pending'] == before['stats']['pending']
        assert after['stats']['to_make'] == before['stats']['to_make']
        assert all(item['association_status'] == 'unavailable' for item in after['items'])
        work = next(item for item in after['items'] if item['id'] == work_id)
        assert not work['directories'][0]['available']
        assert work['video_count'] == work['script_count'] == 1
        assert work['cover_url']


def test_conflicting_sources_remain_excluded_from_production_categories(snapshot):
    config, _, scanner, work_id = snapshot
    copy = config.roots[0].path / 'S701_copy'
    copy.mkdir()
    (copy / 'video.funscript').write_text('{"actions":[]}')
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        result = client.get('/api/works').json()
        work = next(item for item in result['items'] if item['id'] == work_id)
        assert work['association_status'] == 'conflict'
        assert work['directories'] == []
        assert work['video_count'] == work['script_count'] == 0
        assert result['stats']['pending'] == 1
        assert result['stats']['to_make'] == 1
