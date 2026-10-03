from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.main import create_app
from backend.previews import CLIP_MEDIA_FILES as MEDIA_FILES, PreviewService
from backend.scanner import Scanner
from backend.scan_roots import ScanRoots
from backend.store import Store
import backend.previews as preview_module


AXES = ('stroke', 'surge', 'sway', 'twist', 'roll', 'pitch')
SCRIPT = json.dumps({'actions': [{'at': 0, 'pos': 0}, {'at': 10000, 'pos': 100}]})


@pytest.fixture
def matching(tmp_path, fake_heatmap_tool):
    root = tmp_path / 'workspace'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'data',
                    roots=(Root(root, r'D:\Media\workspace', 'workspace'),),
                    preview_output_root=root / '预览', open_mode='gateway',
                    ffmpeg='missing-test-ffmpeg', ffprobe='missing-test-ffprobe', heatmap_tool=fake_heatmap_tool)
    store = Store(config.data_dir)
    scanner = Scanner(store, config)
    return config, store, scanner


def source(root: Path, identifier='S070', stem='main'):
    folder = root / f'{identifier}_source'
    folder.mkdir(exist_ok=True)
    (folder / f'{stem}.mp4').write_bytes(b'test-video')
    (folder / f'{stem}.funscript').write_text(SCRIPT)
    return folder


def work_id(store, identifier='S070'):
    with store.connection() as db:
        return db.execute('SELECT id FROM works WHERE script_id=?', (identifier,)).fetchone()[0]


def asset_id(store, name, identifier='S070'):
    with store.connection() as db:
        return db.execute('SELECT a.id FROM assets a JOIN directories d ON d.id=a.directory_id '
                          'JOIN works w ON w.id=d.work_id WHERE a.name=? AND w.script_id=?',
                          (name, identifier)).fetchone()[0]


def endpoint(store, identifier='S070'):
    return f'/api/works/{work_id(store, identifier)}/preview-matching'


def get_matching(client, store, video=None, identifier='S070'):
    params = {} if video is None else {'video_asset_id': video}
    response = client.get(endpoint(store, identifier), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def save_matching(client, store, video, scripts, revision=None):
    if revision is None:
        revision = get_matching(client, store, video)['revision']
    return client.put(endpoint(store), json={'video_asset_id': video, 'script_asset_ids': scripts,
                                            'expected_revision': revision})


def rematch(client, app, store, identifier='S070'):
    response = client.post(f'/api/works/{work_id(store, identifier)}/rematch')
    assert response.status_code == 202, response.text
    job = response.json()
    assert job['type'] == 'rematch'
    app.state.worker.perform(job['id'])
    result = store.job(job['id'])
    assert result['status'] == 'completed', result['error']
    assert result['progress'] == 100
    return result


def manifest(output, marker):
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, (kind, index) in MEDIA_FILES.items():
        data = f'{marker}:{name}'.encode()
        (output / name).write_bytes(data)
        records.append({'filename': name, 'kind': kind, 'clip_index': index,
                        'width': 1920 if kind == 'video' else 192,
                        'height': 1080 if kind == 'video' else 108,
                        'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data),
                        'path': str(output / name)})
    result = {'schema_version': 1, 'status': 'completed', 'work_id': 'S070', 'outputs': records}
    (output / 'manifest.json').write_text(json.dumps(result))
    return result


def test_automatic_matching_reports_all_axes_and_candidates(matching):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    for axis in AXES[1:]:
        (folder / f'main.{axis}.funscript').write_text(SCRIPT)
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        state = get_matching(client, store)
        assert state['work_id'] == work_id(store)
        assert state['video_asset_id'] == asset_id(store, 'main.mp4')
        assert state['mode'] == 'auto' and state['job'] is None
        assert state['revision'] >= 0 and state['issues'] == []
        assert set(state['script_asset_ids']) == set(AXES)
        assert len(state['videos']) == 1 and len(state['scripts']) == 6


def test_manual_binding_persists_per_video_and_empty_map_is_intentional(matching):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    source(config.roots[0].path, stem='other')
    (folder / 'different-name.funscript').write_text(SCRIPT)
    scanner.scan()
    video = asset_id(store, 'main.mp4')
    other = asset_id(store, 'other.mp4')
    selected = {'stroke': asset_id(store, 'different-name.funscript')}
    with TestClient(create_app(config, start_worker=False)) as client:
        before = get_matching(client, store, video)
        saved = save_matching(client, store, video, selected)
        assert saved.status_code == 200, saved.text
        assert saved.json()['revision'] > before['revision']
        empty = save_matching(client, store, other, {})
        assert empty.status_code == 200, empty.text
    # Reopening the database/application must retain both independent choices.
    Store(config.data_dir)
    with TestClient(create_app(config, start_worker=False)) as client:
        state = get_matching(client, store, video)
        assert state['mode'] == 'manual' and state['script_asset_ids'] == selected
        state = get_matching(client, store, other)
        assert state['mode'] == 'manual' and state['script_asset_ids'] == {}
        assert state['issues']
        assert client.post(f'/api/works/{work_id(store)}/preview',
                           json={'video_asset_id': other}).status_code == 422


def test_manual_six_axis_assignment_reaches_generator(matching, monkeypatch):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    for axis in AXES:
        (folder / f'custom-{axis}.funscript').write_text(SCRIPT)
    scanner.scan()
    seen = []

    def generate(generator_config, **kwargs):
        seen.append(generator_config)
        return manifest(Path(generator_config.output_dir), 'new')

    monkeypatch.setattr(preview_module, 'generate', generate)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        scripts = {axis: asset_id(store, f'custom-{axis}.funscript') for axis in AXES}
        saved = save_matching(client, store, asset_id(store, 'main.mp4'), scripts)
        assert saved.status_code == 200, saved.text
        response = client.post(f'/api/works/{work_id(store)}/preview', json={'force': True})
        assert response.status_code == 202, response.text
        job = response.json()
        assert job['inputs']['force'] is True
        app.state.worker.perform_preview(job['id'])
        assert store.job(job['id'])['status'] == 'completed', store.job(job['id'])['error']
        assert seen[0].force is True
        assert set(seen[0].scripts) == set(AXES)
        for axis in AXES:
            assert Path(seen[0].scripts[axis]).name == f'custom-{axis}.funscript'


def test_stale_revision_cannot_replace_mapping(matching):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        video = asset_id(store, 'main.mp4')
        state = get_matching(client, store, video)
        selected = {'stroke': asset_id(store, 'main.funscript')}
        assert save_matching(client, store, video, selected, state['revision']).status_code == 200
        assert save_matching(client, store, video, {}, state['revision']).status_code == 409
        assert get_matching(client, store, video)['script_asset_ids'] == selected


@pytest.mark.parametrize('fault', ['foreign_video', 'foreign_script', 'script_as_video', 'video_as_script', 'unknown_axis'])
def test_manual_selection_rejects_foreign_assets_and_invalid_kinds(matching, fault):
    config, store, scanner = matching
    source(config.roots[0].path)
    source(config.roots[0].path, identifier='S071', stem='foreign')
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        video = asset_id(store, 'main.mp4')
        scripts = {'stroke': asset_id(store, 'main.funscript')}
        if fault == 'foreign_video':
            video = asset_id(store, 'foreign.mp4', 'S071')
        elif fault == 'foreign_script':
            scripts['stroke'] = asset_id(store, 'foreign.funscript', 'S071')
        elif fault == 'script_as_video':
            video = scripts['stroke']
        elif fault == 'video_as_script':
            scripts['stroke'] = video
        else:
            scripts = {'heave': scripts['stroke']}
        response = save_matching(client, store, video, scripts, revision=0)
        assert response.status_code in {403, 404, 422}, response.text


@pytest.mark.parametrize('fault', ['video_traversal', 'script_traversal', 'video_symlink', 'script_symlink'])
def test_manual_selection_checks_actual_path_before_persisting(matching, fault, tmp_path):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    scanner.scan()
    video = asset_id(store, 'main.mp4')
    script = asset_id(store, 'main.funscript')
    bad_id = video if fault.startswith('video') else script
    if fault.endswith('traversal'):
        with store.connection() as db:
            db.execute("UPDATE assets SET relative_path='../outside' WHERE id=?", (bad_id,))
    else:
        path = folder / ('main.mp4' if fault.startswith('video') else 'main.funscript')
        outside = tmp_path / path.name
        path.rename(outside)
        path.symlink_to(outside)
    with TestClient(create_app(config, start_worker=False)) as client:
        response = save_matching(client, store, video, {'stroke': script}, revision=0)
        assert response.status_code in {403, 404, 422}, response.text


@pytest.mark.parametrize('active', ['preview', 'rematch'])
def test_active_jobs_coalesce_and_prevent_other_operation_and_mapping_edits(matching, active):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        base = f'/api/works/{work_id(store)}'
        first = client.post(f'{base}/{active}')
        assert first.status_code == 202, first.text
        second = client.post(f'{base}/{active}')
        assert second.status_code == 202 and second.json()['id'] == first.json()['id']
        other = 'rematch' if active == 'preview' else 'preview'
        assert client.post(f'{base}/{other}').status_code == 409
        assert save_matching(client, store, asset_id(store, 'main.mp4'),
                             {'stroke': asset_id(store, 'main.funscript')}).status_code == 409
        state = get_matching(client, store)
        if active == 'rematch':
            assert state['job']['id'] == first.json()['id']
        else:
            assert client.get(f'{base}/preview').json()['job']['id'] == first.json()['id']


def test_active_work_operation_does_not_block_other_works(matching):
    config, store, scanner = matching
    source(config.roots[0].path)
    source(config.roots[0].path, identifier='S071', stem='foreign')
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        first = client.post(f'/api/works/{work_id(store)}/preview')
        assert first.status_code == 202, first.text
        other = client.post(f'/api/works/{work_id(store, "S071")}/rematch')
        assert other.status_code == 202, other.text
        assert first.json()['id'] != other.json()['id']


def test_force_option_is_strict_and_defaults_to_normal_generation(matching):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    with TestClient(create_app(config, start_worker=False)) as client:
        url = f'/api/works/{work_id(store)}/preview'
        assert client.post(url, json={'force': 'true'}).status_code == 422
        normal = client.post(url)
        assert normal.status_code == 202, normal.text
        assert normal.json()['inputs'].get('force', False) is False


def test_source_changes_warn_without_automatically_enqueuing_preview(matching, monkeypatch):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    scanner.scan()

    def generate(generator_config, **kwargs):
        result = manifest(Path(generator_config.output_dir), 'original')
        result['video'] = {'path': generator_config.video}
        result['scripts'] = {axis: {'path': path} for axis, path in generator_config.scripts.items()}
        return result

    monkeypatch.setattr(preview_module, 'generate', generate)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        job = client.post(f'/api/works/{work_id(store)}/preview').json()
        app.state.worker.perform_preview(job['id'])
        assert store.job(job['id'])['status'] == 'completed', store.job(job['id'])['error']
        assert get_matching(client, store)['source_changed'] is False
        (folder / 'main.funscript').write_text(json.dumps({
            'actions': [{'at': 0, 'pos': 25}, {'at': 5000, 'pos': 75}, {'at': 10000, 'pos': 0}]}))
        rematch(client, app, store)
        assert get_matching(client, store)['source_changed'] is True
        with store.connection() as db:
            assert db.execute("SELECT count(*) FROM jobs WHERE type='preview'").fetchone()[0] == 1


def test_queued_input_change_fails_without_calling_renderer(matching, monkeypatch):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    scanner.scan()

    def unexpected(*args, **kwargs):
        pytest.fail('Renderer must not run with changed queued inputs')

    monkeypatch.setattr(preview_module, 'generate', unexpected)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        response = client.post(f'/api/works/{work_id(store)}/preview')
        assert response.status_code == 202, response.text
        (folder / 'main.funscript').write_text(json.dumps({
            'actions': [{'at': 0, 'pos': 15}, {'at': 9000, 'pos': 95}]}))
        app.state.worker.perform_preview(response.json()['id'])
        job = store.job(response.json()['id'])
        assert job['status'] == 'failed'
        assert '变化' in job['error']


def test_targeted_rematch_updates_full_identifier_only_and_keeps_manual_data(matching):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    sibling = source(config.roots[0].path, identifier='S070_001', stem='part')
    other = source(config.roots[0].path, identifier='S071', stem='foreign')
    scanner.scan()
    app = create_app(config, start_worker=False)
    with store.connection() as db:
        db.execute('UPDATE works SET metadata=? WHERE id=?',
                   (json.dumps({'video_url': 'https://example.test/video'}), work_id(store)))
    with TestClient(app) as client:
        original = client.patch(f'/api/works/{work_id(store)}', json={
            'title': '人工标题', 'status': 'published', 'notes': '保留备注'})
        assert original.status_code == 200, original.text
        before = original.json()
        # Files changed in three works; refreshing S070 must touch only S070.
        (folder / 'new.pitch.funscript').write_text(SCRIPT)
        (sibling / 'part.mp4').unlink()
        (other / 'foreign.mp4').unlink()
        result = rematch(client, app, store)
        state = get_matching(client, store)
        assert any(asset['name'] == 'new.pitch.funscript' for asset in state['scripts'])
        assert asset_id(store, 'part.mp4', 'S070_001')
        assert asset_id(store, 'foreign.mp4', 'S071')
        after = client.get(f'/api/works/{work_id(store)}').json()
        for field in ('title', 'status', 'notes', 'metadata'):
            assert after[field] == before[field]
        assert result['inputs']['work_id'] == work_id(store)


def test_targeted_rematch_uses_only_enabled_roots(matching, tmp_path):
    config, store, scanner = matching
    source(config.roots[0].path)
    archive = tmp_path / '2026'
    archive.mkdir()
    config = replace(config, roots=(*config.roots, Root(archive, r'D:\Media\2026', '2026')))
    Scanner(store, config).scan()
    ScanRoots(store, config).update([str(config.roots[0].path)], expected_revision=0)
    # Same ID outside the selected scope must not become a new conflict.
    source(archive)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        rematch(client, app, store)
        state = get_matching(client, store)
        assert len(state['videos']) == 1
        assert state['issues'] == []
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM directories WHERE work_id=?', (work_id(store),)).fetchone()[0] == 1


def test_duplicate_full_identifier_blocks_generation_after_rematch(matching):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    second = config.roots[0].path / 'S070_duplicate'
    second.mkdir()
    (second / 'duplicate.mp4').write_bytes(b'duplicate-video')
    (second / 'duplicate.funscript').write_text(SCRIPT)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        rematch(client, app, store)
        state = get_matching(client, store)
        assert state['videos'] == [] and state['scripts'] == [] and state['issues']
        response = client.post(f'/api/works/{work_id(store)}/preview',
                               json={'video_asset_id': asset_id(store, 'main.mp4')})
        assert response.status_code in {409, 422}


@pytest.mark.parametrize('change', ['rename_script', 'move_directory'])
def test_manual_missing_binding_survives_rematch_and_database_reopen_without_auto_fallback(matching, change):
    config, store, scanner = matching
    folder = source(config.roots[0].path)
    (folder / 'custom.funscript').write_text(SCRIPT)
    scanner.scan()
    app = create_app(config, start_worker=False)
    video = asset_id(store, 'main.mp4')
    selected = {'stroke': asset_id(store, 'custom.funscript')}
    with TestClient(app) as client:
        assert save_matching(client, store, video, selected).status_code == 200
        if change == 'rename_script':
            (folder / 'custom.funscript').rename(folder / 'renamed.funscript')
        else:
            folder.rename(folder.parent / 'S070_moved')
        rematch(client, app, store)
    Store(config.data_dir)
    with TestClient(create_app(config, start_worker=False)) as client:
        state = get_matching(client, store, video)
        assert state['mode'] == 'manual'
        assert state['issues']
        assert state['script_asset_ids'].get('stroke') != asset_id(store, 'main.funscript')
        response = client.post(f'/api/works/{work_id(store)}/preview', json={'video_asset_id': video})
        assert response.status_code in {404, 409, 422}


@pytest.mark.parametrize('outcome', ['failure', 'incomplete', 'incomplete_manifest', 'success'])
def test_force_generation_stages_outputs_and_promotes_only_complete_success(matching, monkeypatch, outcome):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    destination = config.preview_output_root / 'S070'
    manifest(destination, 'old')
    old = {name: (destination / name).read_bytes() for name in MEDIA_FILES}
    old_manifest = (destination / 'manifest.json').read_bytes()
    seen = []

    def generate(generator_config, **kwargs):
        staging = Path(generator_config.output_dir)
        seen.append(staging)
        assert staging != destination
        assert generator_config.force is True
        (staging / '预览视频1.webm').write_bytes(b'partial-new-result')
        if outcome == 'failure':
            raise RuntimeError('模拟压制失败')
        new = manifest(staging, 'new')
        if outcome == 'incomplete':
            (staging / '预览gif4.gif').unlink()
        elif outcome == 'incomplete_manifest':
            new['outputs'] = new['outputs'][:-1]
        return new

    monkeypatch.setattr(preview_module, 'generate', generate)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        response = client.post(f'/api/works/{work_id(store)}/preview', json={'force': True})
        assert response.status_code == 202, response.text
        app.state.worker.perform_preview(response.json()['id'])
        job = store.job(response.json()['id'])
        assert job['status'] == ('completed' if outcome == 'success' else 'failed'), job['error']
        assert len(seen) == 1
        for name in MEDIA_FILES:
            result = (destination / name).read_bytes()
            assert result == (f'new:{name}'.encode() if outcome == 'success' else old[name])
        if outcome != 'success':
            assert (destination / 'manifest.json').read_bytes() == old_manifest
        state = client.get(f'/api/works/{work_id(store)}/preview').json()
        assert len(state['files']) == (9 if outcome == 'success' else 8)


def interrupted_publication(config, status='publishing', missing_previous=False):
    """Simulate a crash after two media replacements, with the recovery journal intact."""
    output = config.preview_output_root / 'S070'
    manifest(output, 'old')
    filenames = [*MEDIA_FILES, 'manifest.json']
    absent = '预览gif4.gif' if missing_previous else None
    if absent:
        (output / absent).unlink()
    old = {name: (output / name).read_bytes() for name in filenames if (output / name).exists()}
    stage = output / f'.workbench-stage-{uuid4()}'
    manifest(stage, 'new')
    prior = stage / 'prior'
    prior.mkdir()
    for name in old:
        shutil.copy2(output / name, prior / name)
    journal = {'status': status, 'filenames': filenames, 'previous': list(old)}
    (stage / 'publication.json').write_text(json.dumps(journal))
    changed = filenames if status == 'published' else filenames[:2] + ([absent] if absent else [])
    for name in changed:
        (stage / name).replace(output / name)
    lock = output / '.preview-generator.lock'
    lock.write_text(json.dumps({'pid': 987654321, 'workbench_stage': stage.name}))
    return output, stage, lock, old


def dead_process(pid, signal):
    assert pid == 987654321 and signal == 0
    raise ProcessLookupError()


@pytest.mark.parametrize('missing_previous', [False, True])
def test_stale_owned_publication_restores_previous_set_and_cleans_stage(matching, monkeypatch, missing_previous):
    config, store, _ = matching
    output, stage, lock, old = interrupted_publication(config, missing_previous=missing_previous)
    monkeypatch.setattr(preview_module.os, 'kill', dead_process)
    PreviewService(store, config).recover_stale_lock(output, 987654321)
    assert not lock.exists() and not stage.exists()
    for name in [*MEDIA_FILES, 'manifest.json']:
        if name in old:
            assert (output / name).read_bytes() == old[name]
        else:
            assert not (output / name).exists()


def test_stale_published_journal_keeps_complete_new_results(matching, monkeypatch):
    config, store, _ = matching
    output, stage, lock, old = interrupted_publication(config, status='published')
    new = {name: (output / name).read_bytes() for name in [*MEDIA_FILES, 'manifest.json']}
    assert all(new[name] != old[name] for name in new)
    monkeypatch.setattr(preview_module.os, 'kill', dead_process)
    PreviewService(store, config).recover_stale_lock(output, 987654321)
    assert not lock.exists() and not stage.exists()
    assert {name: (output / name).read_bytes() for name in new} == new


@pytest.mark.parametrize('owner', ['unknown', 'different_pid', 'alive', 'permission_denied'])
def test_recovery_never_claims_unowned_or_live_publication(matching, monkeypatch, owner):
    config, store, _ = matching
    output, stage, lock, old = interrupted_publication(config)
    before = {name: (output / name).read_bytes() for name in [*MEDIA_FILES, 'manifest.json']}

    def probe(pid, signal):
        if owner == 'alive':
            return
        if owner == 'permission_denied':
            raise PermissionError()
        pytest.fail('Unowned PID must not even be probed')

    monkeypatch.setattr(preview_module.os, 'kill', probe)
    expected = None if owner == 'unknown' else 1234 if owner == 'different_pid' else 987654321
    PreviewService(store, config).recover_stale_lock(output, expected)
    assert lock.exists() and stage.exists()
    assert {name: (output / name).read_bytes() for name in before} == before


@pytest.mark.parametrize('unsafe', ['traversal_stage', 'absolute_stage', 'symlink_stage', 'journal_traversal', 'symlink_prior'])
def test_recovery_refuses_unsafe_stage_or_journal_without_outside_tree_writes(matching, monkeypatch, tmp_path, unsafe):
    config, store, _ = matching
    output, stage, lock, old = interrupted_publication(config)
    before = {name: (output / name).read_bytes() for name in [*MEDIA_FILES, 'manifest.json']}
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep.txt'
    sentinel.write_bytes(b'outside-data-must-stay')
    lock_value = json.loads(lock.read_text())
    if unsafe == 'traversal_stage':
        lock_value['workbench_stage'] = '../../../outside'
    elif unsafe == 'absolute_stage':
        lock_value['workbench_stage'] = str(outside)
    elif unsafe == 'symlink_stage':
        original = stage
        hidden = output / 'held-stage'
        original.rename(hidden)
        original.symlink_to(outside, target_is_directory=True)
    elif unsafe == 'journal_traversal':
        journal = json.loads((stage / 'publication.json').read_text())
        journal['filenames'].append('../../../../outside/keep.txt')
        journal['previous'].append('../../../../outside/keep.txt')
        (stage / 'publication.json').write_text(json.dumps(journal))
    else:
        (stage / 'prior').rename(stage / 'held-prior')
        (stage / 'prior').symlink_to(outside, target_is_directory=True)
    lock.write_text(json.dumps(lock_value))
    monkeypatch.setattr(preview_module.os, 'kill', dead_process)
    PreviewService(store, config).recover_stale_lock(output, 987654321)
    assert sentinel.read_bytes() == b'outside-data-must-stay'
    assert {name: (output / name).read_bytes() for name in before} == before


def test_mid_promotion_replace_failure_rolls_back_original_eight_files_and_manifest(matching, monkeypatch):
    config, store, scanner = matching
    source(config.roots[0].path)
    scanner.scan()
    output = config.preview_output_root / 'S070'
    manifest(output, 'old')
    old = {name: (output / name).read_bytes() for name in [*MEDIA_FILES, 'manifest.json']}

    def generate(generator_config, **kwargs):
        return manifest(Path(generator_config.output_dir), 'new')

    original_replace = Path.replace
    replaced = []

    def replace_file(path, target):
        destination = Path(target)
        if path.parent.name.startswith('.workbench-stage-') and destination.parent == output and destination.name in MEDIA_FILES:
            if len(replaced) == 2:
                replaced.append('failed')
                raise OSError('模拟发布期间第三个文件替换失败')
            replaced.append(destination.name)
        return original_replace(path, target)

    monkeypatch.setattr(preview_module, 'generate', generate)
    monkeypatch.setattr(Path, 'replace', replace_file)
    app = create_app(config, start_worker=False)
    with TestClient(app) as client:
        response = client.post(f'/api/works/{work_id(store)}/preview', json={'force': True})
        assert response.status_code == 202, response.text
        app.state.worker.perform_preview(response.json()['id'])
        job = store.job(response.json()['id'])
        assert job['status'] == 'failed' and '第三个文件替换失败' in job['error']
        assert len(replaced) == 3
        assert {name: (output / name).read_bytes() for name in old} == old
        assert len(client.get(f'/api/works/{work_id(store)}/preview').json()['files']) == 8
    assert not (output / '.preview-generator.lock').exists()
    assert not list(output.glob('.workbench-stage-*'))
