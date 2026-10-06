from __future__ import annotations

from decimal import Decimal
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.durations import DurationError, DurationService, duration_fields, is_source_video, refresh_all_duration, round_minutes
from backend.main import create_app
from backend.scanner import Scanner
from backend.store import SCHEMA, Store
from backend.tags import TagError, TagService


@pytest.fixture
def durations(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, str(root), 'workspace'),),
                    preview_output_root=root / '预览')
    store = Store(config.data_dir)
    values, calls = {}, []
    def probe(self, path):
        calls.append(str(path))
        value = values.get(path.name)
        if value is None:
            raise DurationError('测试无法读取时长')
        return Decimal(str(value))
    monkeypatch.setattr(DurationService, 'probe', probe)
    return config, store, Scanner(store, config), values, calls


def add_videos(durations, folder='S070', names=('main.mp4',)):
    config, *_ = durations
    directory = config.roots[0].path / folder
    directory.mkdir()
    for name in names:
        (directory / name).write_bytes(b'read-only-source')
    (directory / 'main.funscript').write_text('{"actions":[]}')
    return directory


def state(store, script_id='S070'):
    with store.connection() as db:
        work_id = db.execute('SELECT id FROM works WHERE script_id=?', (script_id,)).fetchone()[0]
        return {'id': work_id, **duration_fields(db, work_id),
                'tags': [dict(row) for row in db.execute('SELECT t.*,wt.source FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=?', (work_id,))]}


@pytest.mark.parametrize('seconds,minutes', [('29', 0), ('30', 1), ('89', 1), ('90', 2), ('149.9999', 2), ('150', 3)])
def test_half_up_boundaries(seconds, minutes):
    assert round_minutes(Decimal(seconds)) == minutes


def test_all_videos_sum_before_rounding_and_tag_persistence(durations):
    config, store, scanner, values, calls = durations
    directory = add_videos(durations, names=('a.mp4', 'b.mp4', 'c.mp4'))
    values.update({'a.mp4': 5, 'b.mp4': 20, 'c.mp4': 20})
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    result = scanner.scan()
    current = state(store)
    assert result['durations']['ready'] == 1
    assert current['duration_seconds'] == 45 and current['duration_minutes'] == 1
    assert current['duration_status'] == 'ready'
    time = next(tag for tag in current['tags'] if tag['category'] == 'duration')
    assert time['name'] == '1 分钟' and time['source'] == 'duration'
    restarted = Store(config.data_dir)
    assert state(restarted)['duration_minutes'] == 1
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before
    assert len(calls) == 3


def test_cache_reuse_then_changed_file_reprobe_and_round_once(durations):
    _, store, scanner, values, calls = durations
    directory = add_videos(durations)
    values['main.mp4'] = 89
    scanner.scan()
    assert state(store)['duration_minutes'] == 1
    first = state(store)
    scanner.scan()
    assert len(calls) == 1
    with store.connection() as db:
        revision = db.execute('SELECT revision FROM work_tag_state WHERE work_id=?', (first['id'],)).fetchone()[0]
    refresh_all_duration(store, scanner.config)
    with store.connection() as db:
        assert db.execute('SELECT revision FROM work_tag_state WHERE work_id=?', (first['id'],)).fetchone()[0] == revision
    values['main.mp4'] = 90
    (directory / 'main.mp4').write_bytes(b'changed-video-source')
    scanner.scan(target_id='S070')
    assert len(calls) == 2 and state(store)['duration_minutes'] == 2
    values['main.mp4'] = 149
    previous = (directory / 'main.mp4').stat()
    os.utime(directory / 'main.mp4', ns=(previous.st_atime_ns, previous.st_mtime_ns+1000000))
    scanner.scan()
    assert len(calls) == 3 and state(store)['duration_minutes'] == 2


def test_unknown_partial_and_stale_do_not_publish_inaccurate_tags(durations):
    config, store, scanner, values, _ = durations
    directory = add_videos(durations, names=('a.mp4', 'b.mp4'))
    values.update({'a.mp4': 60, 'b.mp4': 120})
    scanner.scan()
    assert state(store)['duration_minutes'] == 3
    (directory / 'b.mp4').write_bytes(b'new-but-broken')
    values.pop('b.mp4')
    scanner.scan()
    partial = state(store)
    assert partial['duration_status'] == 'partial' and partial['duration_minutes'] is None
    assert partial['duration_last_known_minutes'] == 3
    assert not any(tag['category'] == 'duration' for tag in partial['tags'])
    assert 'b.mp4' in partial['duration_error']
    directory.rename(config.roots[0].path / 'unnumbered')
    scanner.scan()
    stale = state(store)
    assert stale['duration_status'] == 'stale' and stale['duration_minutes'] is None
    assert stale['duration_last_known_minutes'] == 3
    add_videos(durations, folder='S071', names=())
    scanner.scan()
    unknown = state(store, 'S071')
    assert unknown['duration_status'] == 'unknown' and unknown['duration_minutes'] is None
    assert not any(tag['category'] == 'duration' for tag in unknown['tags'])


def test_full_ids_remain_independent_and_same_real_path_counts_once(durations):
    _, store, scanner, values, calls = durations
    add_videos(durations, folder='S025', names=('parent.mp4',))
    add_videos(durations, folder='S025_001', names=('child.mp4',))
    values.update({'parent.mp4': 30, 'child.mp4': 150})
    scanner.scan()
    parent, child = state(store, 'S025'), state(store, 'S025_001')
    assert parent['duration_minutes'] == 1 and child['duration_minutes'] == 3
    with store.connection() as db:
        original = dict(db.execute("SELECT * FROM assets WHERE name='parent.mp4'").fetchone())
        db.execute('INSERT INTO assets(directory_id,name,relative_path,kind,size,mtime_ns) VALUES(?,?,?,?,?,?)',
                   (original['directory_id'], original['name'], './parent.mp4', 'video', original['size'], original['mtime_ns']))
    refresh_all_duration(store, scanner.config, [parent['id']])
    assert state(store, 'S025')['duration_seconds'] == 30 and len(calls) == 2


def test_automatic_duration_cannot_be_manually_created_modified_or_rebound(durations):
    config, store, scanner, values, _ = durations
    add_videos(durations)
    add_videos(durations, folder='S071', names=('other.mp4',))
    values.update({'main.mp4': 30, 'other.mp4': 90})
    scanner.scan()
    service = TagService(store)
    first, second = state(store), state(store, 'S071')
    duration_tag = next(tag for tag in first['tags'] if tag['category'] == 'duration')
    other_time = next(tag for tag in second['tags'] if tag['category'] == 'duration')
    with pytest.raises(TagError):
        service.create({'category': 'duration', 'name': '999 分钟'})
    with pytest.raises(TagError):
        service.update(duration_tag['id'], {'name': '999 分钟', 'expected_revision': duration_tag['revision']})
    with store.connection() as db:
        revision = service.work_state(db, first['id'])['tags_revision']
    with pytest.raises(TagError):
        service.replace_work(first['id'], [other_time['id']], revision)
    replaced = service.replace_work(first['id'], [], revision)
    assert [tag['category'] for tag in replaced['tags']] == ['duration']
    assert replaced['tags'][0]['id'] == duration_tag['id']
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.post('/api/tags', json={'category': 'duration', 'name': '100 分钟'}).status_code == 422
        assert client.patch(f'/api/tags/{duration_tag["id"]}', json={'name': '100 分钟', 'expected_revision': duration_tag['revision']}).status_code == 422
        assert client.delete(f'/api/tags/{duration_tag["id"]}').status_code in (404, 405)


def test_duration_filter_search_and_untagged_semantics(durations):
    config, store, scanner, values, _ = durations
    add_videos(durations)
    values['main.mp4'] = 90
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        item = client.get('/api/works').json()['items'][0]
        time = next(tag for tag in item['tags'] if tag['category'] == 'duration')
        assert item['duration_minutes'] == 2
        assert client.get('/api/works', params={'tag_id': time['id']}).json()['total'] == 1
        assert client.get('/api/works', params={'q': '2 分钟'}).json()['total'] == 1
        assert client.get('/api/works', params={'untagged_only': 'true'}).json()['total'] == 1
        assert 'duration' in client.get('/api/tags').json()['categories']


@pytest.mark.parametrize('quoted,has_axis', [(False, False), (True, False), (True, True)])
def test_legacy_tag_constraint_migration_preserves_manual_data(tmp_path, quoted, has_axis):
    data = tmp_path / 'data'
    data.mkdir()
    legacy = SCHEMA.replace(",'duration'", '')
    if not has_axis:
        legacy = legacy.replace("'video_type','axis_type'", "'video_type'")
    if quoted:
        legacy = legacy.replace('CREATE TABLE IF NOT EXISTS tags (', 'CREATE TABLE IF NOT EXISTS "tags" (')
    with sqlite3.connect(data / 'workbench.sqlite3') as db:
        db.executescript(legacy)
        db.execute("INSERT INTO works(id,script_id,title,notes,es_published,es_published_date,created_at,updated_at) VALUES(1,'S001','Manual Title','Private Notes',1,'2026-09-01','x','x')")
        db.execute("INSERT INTO tags(id,category,name,name_key,revision) VALUES(42,'author','Creator','creator',7)")
        db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(1,42,'manual')")
        db.execute('INSERT INTO work_tag_state VALUES(1,9,1)')
    store = Store(data)
    with store.connection() as db:
        db.execute("INSERT INTO tags(category,name,name_key) VALUES('duration','1 分钟','1 分钟')")
        assert tuple(db.execute('SELECT title,notes,es_published,es_published_date FROM works').fetchone()) == ('Manual Title', 'Private Notes', 1, '2026-09-01')
        assert tuple(db.execute('SELECT tag_id,source FROM work_tags').fetchone()) == (42, 'manual')
        assert tuple(db.execute('SELECT revision,manual_edited FROM work_tag_state').fetchone()) == (9, 1)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_probes_run_outside_write_lock_and_cache_survives_restart(durations, monkeypatch):
    config, store, scanner, values, calls = durations
    add_videos(durations)
    def probe(self, path):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR REPLACE INTO settings VALUES('duration-lock-probe','passed')")
        return Decimal('90')
    monkeypatch.setattr(DurationService, 'probe', probe)
    scanner.scan()
    monkeypatch.setattr(DurationService, 'probe', lambda *args: (_ for _ in ()).throw(AssertionError('cached result must be reused')))
    assert refresh_all_duration(Store(config.data_dir), config)['ready'] == 1


def test_untrusted_symlink_or_removed_root_never_uses_cached_duration(durations, tmp_path):
    config, store, scanner, values, calls = durations
    directory = add_videos(durations)
    values['main.mp4'] = 90
    scanner.scan()
    original = directory / 'main.mp4'
    original.unlink()
    target = tmp_path / 'private.mp4'
    target.write_bytes(b'private')
    original.symlink_to(target)
    assert refresh_all_duration(store, config)['stale'] == 1
    assert len(calls) == 1
    original.unlink()
    original.write_bytes(b'read-only-source')
    with store.connection() as db:
        db.execute("UPDATE settings SET value=? WHERE key='root_catalog'", (json.dumps({'roots': [], 'revision': 5}),))
    assert refresh_all_duration(store, config)['stale'] == 1
    assert len(calls) == 1


def test_confirmed_vanished_directory_is_not_counted_as_current_source(durations):
    _, store, scanner, values, _ = durations
    add_videos(durations, folder='S070_first', names=('a.mp4',))
    second = add_videos(durations, folder='S070_second', names=('b.mp4',))
    values.update({'a.mp4': 60, 'b.mp4': 120})
    scanner.scan()
    assert state(store)['duration_minutes'] is None
    assert '目录冲突' in state(store)['duration_error']
    second.rename(second.parent / 'other-unidentified')
    scanner.scan()
    current = state(store)
    assert current['duration_status'] == 'ready' and current['duration_minutes'] == 1
    assert current['duration_seconds'] == 60
    assert next(tag for tag in current['tags'] if tag['category'] == 'duration')['name'] == '1 分钟'


def test_moved_workspace_to_archive_is_ready_and_never_double_counted(durations, tmp_path):
    config, store, _, values, calls = durations
    original = add_videos(durations)
    archive = tmp_path / '2026'
    archive.mkdir()
    config = replace(config, roots=(*config.roots, Root(archive, str(archive), '2026')))
    scanner = Scanner(store, config)
    values['main.mp4'] = 90
    scanner.scan()
    original.rename(archive / 'S070')
    scanner.scan()
    current = state(store)
    assert current['duration_status'] == 'ready'
    assert current['duration_seconds'] == 90 and current['duration_minutes'] == 2
    assert current['duration_error'] is None and len(calls) == 2
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM directories WHERE work_id=?', (current['id'],)).fetchone()[0] == 2
        assert db.execute('SELECT COUNT(*) FROM directories WHERE work_id=? AND available=1', (current['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('root_state', ['offline', 'offline_rematch', 'removed'])
def test_uncertain_conflicting_root_cannot_establish_single_binding(durations, tmp_path, root_state):
    config, store, _, values, _ = durations
    add_videos(durations, names=('a.mp4',))
    archive = tmp_path / '2026'
    archive.mkdir()
    second = archive / 'S070'
    second.mkdir()
    (second / 'b.mp4').write_bytes(b'another-source')
    config = replace(config, roots=(*config.roots, Root(archive, str(archive), '2026')))
    scanner = Scanner(store, config)
    values.update({'a.mp4': 60, 'b.mp4': 120})
    scanner.scan()
    assert state(store)['duration_minutes'] is None
    assert '目录冲突' in state(store)['duration_error']
    if root_state == 'removed':
        with store.connection() as db:
            catalog = json.loads(db.execute("SELECT value FROM settings WHERE key='root_catalog'").fetchone()[0])
            catalog['roots'] = [root for root in catalog['roots'] if root['path'] != str(archive)]
            catalog['revision'] += 1
            db.execute("UPDATE settings SET value=? WHERE key='root_catalog'", (json.dumps(catalog),))
        refresh_all_duration(store, config)
    else:
        archive.rename(tmp_path / 'disconnected-drive')
        scanner.scan(target_id='S070' if root_state == 'offline_rematch' else None)
    current = state(store)
    assert current['duration_status'] == 'partial' and current['duration_minutes'] is None
    assert current['duration_last_known_minutes'] is None
    assert '目录冲突' in current['duration_error']
    assert not any(tag['category'] == 'duration' for tag in current['tags'])
    # Repeated scans and a service restart must not silently accept the readable copy.
    Scanner(Store(config.data_dir), config).scan()
    current = state(store)
    assert current['duration_status'] == 'partial' and current['duration_minutes'] is None
    assert current['duration_last_known_minutes'] is None
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM directories WHERE work_id=? AND available=1', (current['id'],)).fetchone()[0] == 2
        assert db.execute("SELECT 1 FROM issues WHERE work_id=? AND type='duplicate_identifier'", (current['id'],)).fetchone()
    if root_state != 'removed':
        (tmp_path / 'disconnected-drive').rename(archive)
        scanner.scan()
        assert state(store)['duration_status'] == 'partial'
        # A readable root that confirms the duplicate has vanished resolves the conflict.
        second.rename(archive / 'unidentified')
        scanner.scan()
        current = state(store)
        assert current['duration_status'] == 'ready' and current['duration_seconds'] == 60
        assert current['duration_error'] is None
        assert next(tag for tag in current['tags'] if tag['category'] == 'duration')['name'] == '1 分钟'


def test_unreadable_current_directory_without_video_records_blocks_accurate_sum(durations, monkeypatch):
    _, store, scanner, values, _ = durations
    unreadable = add_videos(durations, folder='S070_unreadable', names=())
    values['a.mp4'] = 60
    original_walk = os.walk
    def incomplete_walk(path, **kwargs):
        if Path(path) == unreadable:
            kwargs['onerror'](PermissionError('directory listing unavailable'))
            return iter(())
        return original_walk(path, **kwargs)
    monkeypatch.setattr('backend.scanner.os.walk', incomplete_walk)
    scanner.scan()
    current = state(store)
    assert current['duration_status'] == 'partial'
    assert current['duration_minutes'] is None and current['duration_seconds'] is None
    assert '编号文件夹中有素材无法读取' in current['duration_error']
    assert not any(tag['category'] == 'duration' for tag in current['tags'])
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.path=? AND a.kind=\'video\'', (str(unreadable),)).fetchone()[0] == 0


def test_generated_previews_and_partial_downloads_are_not_source_duration(durations):
    _, store, scanner, values, calls = durations
    directory = add_videos(durations, names=('a.mp4', 'b.mp4', 'work_preview_10-20.webm', 'download.partial.mp4'))
    for subdir in ('Preview', '预览'):
        folder = directory / subdir
        folder.mkdir()
        (folder / 'zero-byte.webm').write_bytes(b'')
    values.update({'a.mp4': 20, 'b.mp4': 20})
    scanner.scan()
    current = state(store)
    assert current['duration_status'] == 'ready'
    assert current['duration_seconds'] == 40 and current['duration_minutes'] == 1
    assert {Path(path).name for path in calls} == {'a.mp4', 'b.mp4'}
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM assets WHERE kind='video'").fetchone()[0] == 6


@pytest.mark.parametrize('name,expected', [
    ('Preview/generated.webm', False), ('nested/预览/gif-source.mp4', False),
    ('WORK_preview_10-20.WEBM', False), ('movie.partial.mp4', False),
    ('preview.mp4', True), ('my-preview.mp4', True), ('preview_original/movie.mp4', True),
    ('video_preview_original.webm', True), ('movie.partial.final.mp4', True), ('partial.mp4', True),
])
def test_source_filter_uses_explicit_patterns_only(name, expected):
    assert is_source_video(name) is expected


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'Infinity', '1e999', True, None, 'broken'])
def test_invalid_probe_results_are_not_cached_as_accurate_durations(tmp_path, monkeypatch, value):
    config = Config(data_dir=tmp_path / 'data', roots=())
    store = Store(config.data_dir)
    service = DurationService(store, config)
    def run(args, **kwargs):
        assert args[0] == config.ffprobe and kwargs['timeout'] == 10 and kwargs['check'] is True
        return subprocess.CompletedProcess(args, 0, json.dumps({'format': {'duration': value}}), '')
    monkeypatch.setattr('backend.durations.subprocess.run', run)
    with pytest.raises(DurationError):
        service.probe(tmp_path / 'video.mp4')
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM video_duration_cache').fetchone()[0] == 0


def test_reading_file_that_changes_during_probe_never_publishes_new_total(durations, monkeypatch):
    config, store, scanner, values, calls = durations
    directory = add_videos(durations)
    values['main.mp4'] = 90
    scanner.scan()
    original = directory / 'main.mp4'
    original.write_bytes(b'changed-source')
    def changing_probe(self, path):
        path.write_bytes(b'changed-during-read')  # Simulate an external producer, not service writes.
        return Decimal('150')
    monkeypatch.setattr(DurationService, 'probe', changing_probe)
    result = refresh_all_duration(store, config)
    assert result['stale'] == 1
    current = state(store)
    assert current['duration_minutes'] is None and current['duration_last_known_minutes'] == 2
    assert '视频发生变化' in current['duration_error']
