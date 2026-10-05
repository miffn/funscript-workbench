"""Directory identity, recovery safety and transactional nullable-code migration."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.scan_candidates import ScanCandidates, ScanCandidatesError
from backend.scan_roots import ScanRoots, ScanRootsError
from backend.scanner import Scanner, existing_path_owner
from backend.store import Store, SCHEMA, now
from backend.work_directory import current_directory
import backend.store as store_module


@pytest.fixture
def context(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    first.mkdir(); second.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(first, 'D:\\first', 'first', 'folder'), Root(second, 'D:\\second', 'second', 'folder')),
                    ffmpeg='missing-ffmpeg', ffprobe='missing-ffprobe', preview_output_root=tmp_path / 'previews')
    store = Store(config.data_dir)
    return config, store, Scanner(store, config), ScanCandidates(store, config)


def material(root, name, script=True):
    directory = root / name
    directory.mkdir()
    (directory / 'video.mp4').write_bytes(b'fake-video')
    if script:
        (directory / 'video.funscript').write_text('{"actions":[]}')
    return directory


def works(store):
    with store.connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM works ORDER BY id')]


def test_folder_mode_same_name_distinct_and_numbered_mode_backwards(context):
    config, store, scanner, candidates = context
    material(config.roots[0].path, 'Same title')
    material(config.roots[1].path, 'Same title')
    scanner.scan()
    rows = works(store)
    assert len(rows) == 2 and all(row['script_id'] is None for row in rows)
    assert rows[0]['id'] != rows[1]['id']
    assert not candidates.catalog()['items']
    scanner.scan()
    assert len(works(store)) == 2
    roots = ScanRoots(store, config)
    with store.connection() as db:
        state = roots.state(db)
    roots.replace([{**root, 'identification': 'numbered'} for root in state['roots']], state['revision'])
    material(config.roots[0].path, 'New uncoded')
    material(config.roots[0].path, 'S025_001_child')
    scanner.scan()
    assert len(works(store)) == 3 and works(store)[-1]['script_id'] == 'S025_001'


def test_confirmed_missing_uncoded_rename_to_code_preserves_identity_and_user_data(context):
    config, store, scanner, candidates = context
    original = material(config.roots[0].path, 'Original')
    scanner.scan()
    row = works(store)[0]
    with store.connection() as db:
        db.execute("UPDATE works SET title='Manual title',notes='Manual note',es_published=1,es_published_date='2026-10-02',production_confirmed_at='saved-confirmation' WHERE id=?", (row['id'],))
        directory = current_directory(db, row['id'])
        db.execute('INSERT INTO preview_bindings(work_id,video_path,scripts) VALUES(?,?,?)', (row['id'], str(original / 'video.mp4'), '{}'))
    original.rename(config.roots[1].path / 'S099_Renamed')
    scanner.scan()
    assert len(works(store)) == 1
    catalog = candidates.catalog()
    assert len(catalog['items']) == 1 and catalog['items'][0]['script_id'] == 'S099'
    candidate, missing = catalog['items'][0], catalog['works'][0]
    resolved = candidates.resolve(candidate['id'], 'associate', candidate['revision'], row['id'], missing['association_revision'])
    assert resolved['work_id'] == row['id']
    recovered = works(store)[0]
    assert recovered['script_id'] == 'S099' and recovered['title'] == 'Manual title' and recovered['notes'] == 'Manual note'
    assert recovered['es_published'] == 1 and recovered['es_published_date'] == '2026-10-02'
    assert recovered['production_confirmed_at'] == 'saved-confirmation'
    assert recovered['preview_key'] == f"work-{row['id']}" and recovered['preview_stale'] == 1
    with store.connection() as db:
        assert not db.execute('SELECT 1 FROM preview_bindings WHERE work_id=?', (row['id'],)).fetchone()
    assert not candidates.catalog()['items']
    scanner.scan()
    assert len(works(store)) == 1


def test_unavailable_root_is_not_missing_evidence_and_no_false_candidate(context):
    config, store, scanner, candidates = context
    material(config.roots[0].path, 'S071_source')
    scanner.scan()
    config.roots[0].path.rename(config.roots[0].path.with_name('unmounted'))
    material(config.roots[1].path, 'Unrelated new work')
    scanner.scan()
    assert len(works(store)) == 2 and not candidates.catalog()['items']
    assert not candidates.catalog()['works']
    old = works(store)[0]
    assert old['association_missing'] == 0


def test_case_sensitive_linux_directories_are_not_merged(context):
    config, store, scanner, candidates = context
    upper = material(config.roots[0].path, 'Foo')
    lower = material(config.roots[0].path, 'foo')
    assert not upper.samefile(lower)
    assert existing_path_owner(lower, {str(upper): 1}) is None
    scanner.scan()
    assert len(works(store)) == 2 and all(row['script_id'] is None for row in works(store))
    upper.rename(config.roots[0].path / 'FOO')
    # The old spelling does not exist on Linux, so this is a candidate, not alias matching.
    assert existing_path_owner(config.roots[0].path / 'FOO', {str(upper): 1}) is None
    scanner.scan()
    assert len(works(store)) == 2 and len(candidates.catalog()['items']) == 1


def test_exact_code_move_auto_recovers_then_duplicate_conflicts(context):
    config, store, scanner, candidates = context
    original = material(config.roots[0].path, 'S025_001_original')
    scanner.scan()
    row = works(store)[0]
    original.rename(config.roots[1].path / 'S025_001_moved')
    scanner.scan()
    assert len(works(store)) == 1 and works(store)[0]['id'] == row['id']
    assert not candidates.catalog()['items'] and works(store)[0]['preview_stale'] == 1
    material(config.roots[0].path, 'S025_001_copy')
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        detail = client.get(f'/api/works/{row["id"]}').json()
        assert detail['association_status'] == 'conflict' and detail['directories'] == []
        assert len(detail['issues']) and detail['script_id'] == 'S025_001'


def test_unique_code_from_readable_root_supersedes_offline_cached_directory(context):
    config, store, scanner, candidates = context
    original = material(config.roots[0].path, 'S064_original')
    scanner.scan()
    original_work = works(store)[0]
    original.rename(config.roots[1].path / 'S064_moved')
    config.roots[0].path.rename(config.roots[0].path.with_name('temporarily-unavailable'))
    scanner.scan()
    with store.connection() as db:
        active = db.execute('SELECT * FROM directories WHERE work_id=? AND available=1', (original_work['id'],)).fetchall()
        assert len(active) == 1 and active[0]['root_path'] == str(config.roots[1].path)
        assert db.execute('SELECT count(*) FROM works').fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM issues WHERE work_id=? AND type='duplicate_identifier'", (original_work['id'],)).fetchone()
    assert works(store)[0]['id'] == original_work['id']
    assert works(store)[0]['association_missing'] == 0
    assert not candidates.catalog()['items']
    # A second actually readable copy remains a real conflict.
    config.roots[0].path.with_name('temporarily-unavailable').rename(config.roots[0].path)
    material(config.roots[0].path, 'S064_copy')
    scanner.scan()
    with store.connection() as db:
        assert db.execute("SELECT 1 FROM issues WHERE work_id=? AND type='duplicate_identifier'", (original_work['id'],)).fetchone()


def test_candidate_task_cas_fingerprint_and_parallel_resolution(context):
    config, store, scanner, candidates = context
    original = material(config.roots[0].path, 'Original')
    scanner.scan()
    original.rename(config.roots[1].path / 'New directory')
    scanner.scan()
    candidate = candidates.catalog()['items'][0]
    with store.connection() as db:
        db.execute("INSERT INTO jobs(type,status,trigger,created_at) VALUES('preview','queued','test',?)", (now(),))
    with pytest.raises(ScanCandidatesError) as busy:
        candidates.resolve(candidate['id'], 'create', candidate['revision'])
    assert busy.value.status_code == 409
    with store.connection() as db:
        db.execute("UPDATE jobs SET status='completed'")
    (Path(candidate['path']) / 'new.funscript').write_text('{"actions":[]}')
    with pytest.raises(ScanCandidatesError) as changed:
        candidates.resolve(candidate['id'], 'create', candidate['revision'])
    assert changed.value.status_code == 409
    scanner.scan()
    candidate = candidates.catalog()['items'][0]
    def resolve(_):
        try:
            return candidates.resolve(candidate['id'], 'create', candidate['revision'])
        except ScanCandidatesError as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, [0, 1]))
    assert sum(isinstance(result, dict) for result in results) == 1 and 409 in results
    assert len(works(store)) == 2


def test_detail_detects_lost_directory_readonly_then_scan_allows_recovery(context):
    config, store, scanner, candidates = context
    original = material(config.roots[0].path, 'Original')
    scanner.scan()
    work = works(store)[0]
    original.rename(config.roots[1].path / 'Renamed')
    with store.connection() as db:
        before = '\n'.join(db.iterdump())
    with TestClient(create_app(config, start_worker=False)) as client:
        assert client.get(f'/api/works/{work["id"]}').json()['association_status'] == 'missing'
    # App boot registers settings, so compare the discovery fields themselves.
    current = works(store)[0]
    assert current['association_missing'] == 0 and current['association_revision'] == work['association_revision']
    assert candidates.catalog()['works'] == []
    scanner.scan()
    assert candidates.catalog()['works'][0]['id'] == work['id']


@pytest.mark.parametrize('nullable', [False, True])
def test_folder_feature_migration_failure_rolls_back_entire_schema_and_rows(tmp_path, monkeypatch, nullable):
    data = tmp_path / 'legacy'
    data.mkdir()
    database = data / 'workbench.sqlite3'
    schema = SCHEMA.replace('script_id TEXT UNIQUE', 'script_id TEXT UNIQUE' if nullable else 'script_id TEXT NOT NULL UNIQUE')
    schema = schema.replace(' association_revision INTEGER NOT NULL DEFAULT 0, association_missing INTEGER NOT NULL DEFAULT 0,\n', '')
    schema = schema.replace(' preview_key TEXT, preview_stale INTEGER NOT NULL DEFAULT 0 CHECK(preview_stale IN (0,1)),\n', '')
    start = schema.index('CREATE TABLE IF NOT EXISTS scan_candidates')
    end = schema.index('CREATE INDEX IF NOT EXISTS idx_assets_directory')
    schema = schema[:start] + schema[end:]
    db = sqlite3.connect(database)
    db.executescript(schema)
    db.execute('INSERT INTO works(script_id,title,created_at,updated_at) VALUES(?,?,?,?)', ('S025', 'Original', now(), now()))
    db.commit()
    original_sql = db.execute("SELECT sql FROM sqlite_master WHERE name='works'").fetchone()[0]
    original_rows = db.execute('SELECT * FROM works').fetchall()
    db.close()
    add = store_module.add_folder_schema
    def fail(connection):
        add(connection)
        connection.execute('CREATE INDEX test_broken ON works(nonexistent_column)')
    monkeypatch.setattr(store_module, 'add_folder_schema', fail)
    with pytest.raises(sqlite3.OperationalError):
        Store(data)
    db = sqlite3.connect(database)
    assert db.execute("SELECT sql FROM sqlite_master WHERE name='works'").fetchone()[0] == original_sql
    assert db.execute('SELECT * FROM works').fetchall() == original_rows
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('idx_works_preview_key','scan_candidates')").fetchall()
    assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    db.close()
    backup = next((data / 'backups').glob('before-folder-identification-*.sqlite3'))
    db = sqlite3.connect(backup)
    assert db.execute('SELECT * FROM works').fetchall() == original_rows
    db.close()
