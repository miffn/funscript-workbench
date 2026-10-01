from __future__ import annotations

import json
from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.config import Config, Root
from backend.es_posts import ESPosts, Inputs, import_preview_history, register_es_post_routes
from backend.previews import PreviewService
from backend.store import Store, now
from backend.work_links import WorkLinks


@pytest.fixture
def posts(tmp_path, monkeypatch):
    root = tmp_path / 'materials'
    root.mkdir()
    config = Config(data_dir=tmp_path / 'data', roots=(Root(root, str(root), 'materials'),),
                    preview_output_root=tmp_path / 'previews')
    store = Store(config.data_dir)
    app = FastAPI()
    previews = PreviewService(store, config)
    register_es_post_routes(app, store, config, previews)
    monkeypatch.setattr('backend.es_posts.subprocess.run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, '{"format":{"duration":"123.4"}}', ''))
    client = TestClient(app)
    return client, store, config, app.state.es_posts


def work(posts, script_id='S070', free=False, support='none', title='A Release'):
    client, store, config, service = posts
    directory = config.roots[0].path / script_id
    directory.mkdir()
    (directory / 'video.mp4').write_bytes(b'video')
    (directory / 'video.funscript').write_text('{"actions":[]}')
    with store.connection() as db:
        work_id = db.execute('INSERT INTO works(script_id,title,notes,created_at,updated_at) VALUES(?,?,?,?,?)',
                             (script_id, title, 'NEVER_PUBLISH_INTERNAL_SECRET', now(), now())).lastrowid
        directory_id = db.execute('INSERT INTO directories(work_id,path,windows_path,root_path,name) VALUES(?,?,?,?,?)',
                                 (work_id, str(directory), str(directory), str(config.roots[0].path), script_id)).lastrowid
        for name, kind in (('video.mp4', 'video'), ('video.funscript', 'script')):
            path = directory / name
            db.execute('INSERT INTO assets(directory_id,name,relative_path,kind,size,mtime_ns) VALUES(?,?,?,?,?,?)',
                       (directory_id, name, name, kind, path.stat().st_size, path.stat().st_mtime_ns))
        for category, name in (('author', 'Example Creator'), ('video_type', 'Anime'), ('axis_type', '多轴'),
                               ('release_type', 'Free Sample' if free else 'Paid'), ('tier', 'Free' if free else 'Extra Tier')):
            row = db.execute('SELECT id FROM tags WHERE category=? AND name_key=?', (category, name.lower())).fetchone()
            tag_id = row[0] if row else db.execute('INSERT INTO tags(category,name,name_key,support_status,support_url) VALUES(?,?,?,?,?)',
                                                 (category, name, name.lower(), support if category == 'author' else 'unknown',
                                                  'https://creator.example/support' if category == 'author' and support == 'url' else None)).lastrowid
            db.execute('INSERT INTO work_tags(work_id,tag_id) VALUES(?,?)', (work_id, tag_id))
    WorkLinks(store).update(work_id, {'video': 'https://video.example/watch', 'patreon': 'https://patreon.com/posts/example'}, 0)
    return work_id


def save(client, work_id, **changes):
    state = client.get(f'/api/works/{work_id}/es-post').json()
    inputs = {**state['inputs'], **changes}
    return client.put(f'/api/works/{work_id}/es-post', json={'inputs': inputs, 'expected_revision': state['revision']})


def generate(client, work_id):
    state = client.get(f'/api/works/{work_id}/es-post').json()
    return client.post(f'/api/works/{work_id}/es-post/generate', json={'expected_revision': state['revision']})


def test_paid_ready_preserves_upload_markdown_and_layout(posts):
    client, store, config, service = posts
    work_id = work(posts, script_id='S025_001', title='[Multi-axis] Exact Title', support='url')
    preview = '![First GIF|192x108](upload://first.gif)\n\n[clip.webm](upload://clip.webm)'
    save(client, work_id, preview_markdown=preview, intro_markdown='Intro for this release',
         heatmap_markdown='![Heatmap](upload://heatmap.png)')
    output = generate(client, work_id).json()['output']
    assert output['status'] == 'ready' and output['missing'] == []
    assert output['title'] == '【Extra Tier】【S025_001】【Multi-axis】 Exact Title'
    assert preview in output['body']
    assert '[center]\n' + preview not in output['body']
    assert 'NEVER_PUBLISH' not in json.dumps(output)
    assert output['title'] not in output['body']
    assert output['body'].startswith('[center][/center]')
    assert output['body'].index('Intro for this release') < output['body'].index(preview) < output['body'].index('Choose Your Path')
    assert 'width="50%"' in output['body'] and '<summary><strong>📊 Script Heatmaps' in output['body']
    assert 'View Patreon Release' in output['body'] and 'https://creator.example/support' in output['body']
    assert 'Script File' not in output['body'] and 'Download Script' not in output['body']
    with store.connection() as db:
        assert db.execute('SELECT upload_url FROM es_preview_history WHERE script_id=?', ('S025_001',)).fetchone()[0] == 'upload://first.gif'


def test_free_explicit_script_selection_and_es_attachment(posts):
    client, store, _, _ = posts
    work_id = work(posts, free=True)
    state = client.get(f'/api/works/{work_id}/es-post').json()
    script = state['sources']['scripts'][0]
    assert client.get(script['download_url']).content == b'{"actions":[]}'
    save(client, work_id, preview_markdown='![gif](upload://preview.gif)',
         attachment_markdown='[script.funscript](upload://script.funscript)')
    output = generate(client, work_id).json()['output']
    assert output['status'] == 'draft' and '请选择需要上传的免费脚本' in output['missing']
    save(client, work_id, selected_script_ids=[script['id']])
    result = generate(client, work_id).json()['output']
    assert result['status'] == 'ready'
    assert 'Script File' in result['body'] and 'upload://script.funscript' in result['body']
    assert 'View Patreon Release' not in result['body'] and 'Download Script' not in result['body']
    assert 'https://patreon.com/posts/example' not in result['body']


def test_drafts_never_record_placeholders_and_unknown_creator(posts):
    client, store, _, _ = posts
    work_id = work(posts, free=True, support='unknown', title='S070')
    result = generate(client, work_id).json()['output']
    assert result['status'] == 'draft'
    assert '[Preview will be inserted here]' in result['body']
    assert '[Script attachment will be inserted here]' in result['body']
    assert '请确认作者支持链接或明确没有链接' in result['missing']
    assert '请填写发布标题' in result['missing']
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM es_preview_history').fetchone()[0] == 0
    save(client, work_id, no_creator_link=True, release_title='Confirmed Title')
    output = generate(client, work_id).json()['output']
    assert '请确认作者支持链接或明确没有链接' not in output['missing']


def test_paid_missing_patreon_and_video_support_none(posts):
    client, store, _, _ = posts
    work_id = work(posts)
    WorkLinks(store).update(work_id, {'patreon': '', 'video': ''}, 1)
    save(client, work_id, preview_markdown='[video](upload://preview.webm)')
    output = generate(client, work_id).json()['output']
    assert output['status'] == 'draft'
    assert '付费作品缺少 Patreon 文章链接' in output['missing'] and '缺少视频链接' in output['missing']
    assert '请确认作者支持链接或明确没有链接' not in output['missing']


def test_template_validation_conflicts_and_stale_output(posts):
    client, _, _, _ = posts
    work_id = work(posts)
    save(client, work_id, preview_markdown='![gif](upload://preview.gif)')
    assert generate(client, work_id).json()['output']['stale'] is False
    template = client.get('/api/es-template').json()
    payload = {key: template[key] for key in ('name', 'body', 'config')}
    payload['expected_revision'] = template['revision']
    assert client.put('/api/es-template', json={**payload, 'body': '{{notes}}'}).status_code == 422
    assert client.put('/api/es-template', json={**payload, 'body': '{{ preview }'}).status_code == 422
    payload['config']['brandingFooterMarkdown'] = 'Final footer'
    assert client.put('/api/es-template', json=payload).status_code == 200
    assert client.put('/api/es-template', json=payload).status_code == 409
    assert client.get(f'/api/works/{work_id}/es-post').json()['output']['stale'] is True
    assert generate(client, work_id).json()['output']['body'].endswith('Final footer')
    state = client.get(f'/api/works/{work_id}/es-post').json()
    payload = {'inputs': state['inputs'], 'expected_revision': state['revision'] - 1}
    assert client.put(f'/api/works/{work_id}/es-post', json=payload).status_code == 409
    assert client.post(f'/api/works/{work_id}/es-post/generate', json={'expected_revision': state['revision'] - 1}).status_code == 409
    save(client, work_id, intro_markdown='A changed intro')
    assert client.get(f'/api/works/{work_id}/es-post').json()['output']['stale'] is True


@pytest.mark.parametrize('changes', [
    {'selected_script_ids': [999]}, {'video_asset_id': 999}, {'selected_preview_filenames': ['../evil.gif']},
    {'preview_markdown': '![gif](https://other.example/a.gif)'}, {'heatmap_markdown': '[video](upload://a.webm)'},
    {'preview_markdown': '![gif](upload://ok.gif) ![external](https://other.example/a.gif)'},
    {'preview_markdown': 'upload://bare.gif'}, {'attachment_markdown': 'upload://bare.funscript'},
    {'cover_markdown': '[video](upload://clip.webm)'}, {'cover_markdown': '![a](upload://a.gif) ![b](upload://b.gif)'},
    {'attachment_markdown': '[script](https://other.example/a.funscript)'}, {'selected_script_ids': [True]},
    {'no_creator_link': 'yes'}, {'private_notes': 'no'},
])
def test_rejects_invalid_sources_and_upload_inputs(posts, changes):
    client, _, _, _ = posts
    work_id = work(posts)
    assert save(client, work_id, **changes).status_code in (404, 422)


def test_download_limits_dynamic_roots_and_symlinks(posts, tmp_path):
    client, store, config, service = posts
    first = work(posts)
    second = work(posts, script_id='S071')
    script = client.get(f'/api/works/{first}/es-post').json()['sources']['scripts'][0]
    assert client.get(f'/api/works/{second}/es-post/scripts/{script["id"]}').status_code == 404
    folder = config.roots[0].path / 'S070'
    original = folder / 'video.funscript'
    original.unlink()
    target = tmp_path / 'private.funscript'
    target.write_text('private')
    original.symlink_to(target)
    assert client.get(script['download_url']).status_code == 403
    original.unlink()
    original.write_text('restored')
    with store.connection() as db:
        db.execute("UPDATE settings SET value=? WHERE key='root_catalog'", (json.dumps({'roots': [], 'revision': 3}),))
    assert client.get(script['download_url']).status_code == 403


def test_multiple_videos_need_selection_and_no_history_length_guess(posts):
    client, store, config, _ = posts
    work_id = work(posts)
    with store.connection() as db:
        row = db.execute('SELECT * FROM assets WHERE kind=\'video\'').fetchone()
        second = config.roots[0].path / 'S070' / 'second.mp4'
        second.write_bytes(b'video2')
        second_id = db.execute('INSERT INTO assets(directory_id,name,relative_path,kind,size,mtime_ns) VALUES(?,?,?,?,?,?)',
                              (row['directory_id'], 'second.mp4', 'second.mp4', 'video', 6, second.stat().st_mtime_ns)).lastrowid
        db.execute('UPDATE works SET metadata=? WHERE id=?', (json.dumps({'Length': '999:00'}), work_id))
    save(client, work_id, preview_markdown='![gif](upload://preview.gif)')
    result = generate(client, work_id).json()['output']
    assert '请选择用于发布的源视频' in result['missing'] and '999:00' not in result['body']
    save(client, work_id, video_asset_id=second_id)
    assert generate(client, work_id).json()['output']['status'] == 'ready'


def test_persistence_and_import_history_respects_workbench_covers(posts):
    client, store, config, service = posts
    work_id = work(posts)
    save(client, work_id, preview_markdown='![gif](upload://current.gif)')
    generated = generate(client, work_id).json()
    imported = import_preview_history(store, {'previews': {'S070': {'uploadUrl': 'upload://older.gif', 'markdown': ''},
                                                          'S999_001': {'uploadUrl': 'upload://archived.gif', 'markdown': ''},
                                                          'S998': {'uploadUrl': 'upload://video.webm', 'markdown': ''}}})
    assert imported == {'imported': ['S999_001'], 'skipped': ['S070', 'S998']}
    restarted = ESPosts(Store(config.data_dir), config, PreviewService(Store(config.data_dir), config))
    assert restarted.state(work_id)['inputs'] == generated['inputs']
    assert restarted.state(work_id)['output'] == generated['output']
    with store.connection() as db:
        assert db.execute("SELECT upload_url FROM es_preview_history WHERE script_id='S070'").fetchone()[0] == 'upload://current.gif'
        assert db.execute("SELECT COUNT(*) FROM es_preview_history WHERE script_id='S999_001'").fetchone()[0] == 1


def test_recent_four_actual_dates_pin_and_current_exclusion(posts):
    client, store, _, service = posts
    current = work(posts, script_id='S080')
    candidates = [work(posts, script_id=script_id) for script_id in ('S046', 'S071', 'S072', 'S073', 'S074', 'S075')]
    history = {'previews': {script_id: {'uploadUrl': f'upload://{script_id}.gif', 'markdown': ''}
                           for script_id in ('S046', 'S071', 'S072', 'S073', 'S074', 'S075', 'S080')}}
    import_preview_history(store, history)
    for work_id, day in zip(candidates, ('2025-01-01', '2026-09-20', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24')):
        WorkLinks(store).update(work_id, {'es': f'https://forum.example/t/{work_id}'}, 1, {'es_published_date': day})
    save(client, current, preview_markdown='![gif](upload://current.gif)')
    result = generate(client, current).json()['output']
    recent = result['body'].split('Recent Releases')[1]
    assert all(script_id in recent for script_id in ('S046', 'S073', 'S074', 'S075'))
    assert all(script_id not in recent for script_id in ('S071', 'S072', 'S080'))
    assert recent.index('S075') < recent.index('S074') < recent.index('S073') < recent.index('S046')
    with store.connection() as db:
        db.execute('UPDATE works SET es_published=0 WHERE id=?', (candidates[-1],))
    assert 'S075' not in generate(client, current).json()['output']['body'].split('Recent Releases')[1]


def test_history_prefills_only_unsaved_inputs_and_recent_changes_mark_stale(posts):
    client, store, _, _ = posts
    work_id = work(posts)
    other_id = work(posts, script_id='S071')
    history = {'previews': {'S070': {'uploadUrl': 'upload://old.gif', 'markdown': '![old](upload://old.gif)'},
                           'S071': {'uploadUrl': 'upload://recent.gif', 'markdown': '![other](upload://recent.gif)'}}}
    import_preview_history(store, history)
    state = client.get(f'/api/works/{work_id}/es-post').json()
    assert state['inputs']['preview_markdown'] == '![old](upload://old.gif)' and state['revision'] == 0
    assert generate(client, work_id).json()['output']['status'] == 'ready'
    WorkLinks(store).update(other_id, {'es': 'https://forum.example/new'}, 1, {'es_published_date': '2026-10-01'})
    assert client.get(f'/api/works/{work_id}/es-post').json()['output']['stale'] is True
    assert 'upload://recent.gif' in generate(client, work_id).json()['output']['body']
    save(client, work_id, preview_markdown='')
    assert client.get(f'/api/works/{work_id}/es-post').json()['inputs']['preview_markdown'] == ''


def test_valid_source_manifest_duration_avoids_probe(posts, monkeypatch):
    client, store, config, service = posts
    work_id = work(posts)
    output = service.previews.output_directory('S070')
    output.mkdir(parents=True)
    source = config.roots[0].path / 'S070' / 'video.mp4'
    manifest = {'work_id': 'S070', 'status': 'completed', 'outputs': [],
                'video': {'path': str(source), 'size': source.stat().st_size, 'duration_seconds': 321}}
    (output / 'manifest.json').write_text(json.dumps(manifest))
    prior_run = subprocess.run
    def no_probe(args, **kwargs):
        assert args[0] != config.ffprobe, 'should reuse known source duration'
        return prior_run(args, **kwargs)
    monkeypatch.setattr('backend.es_posts.subprocess.run', no_probe)
    save(client, work_id, preview_markdown='[webm](upload://video.webm)')
    result = generate(client, work_id).json()['output']
    assert result['status'] == 'ready' and '5:21' in result['body']
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM es_preview_history').fetchone()[0] == 0


def test_download_rejects_parent_escape(posts, tmp_path):
    client, store, _, _ = posts
    work_id = work(posts)
    with store.connection() as db:
        script_id = db.execute("SELECT id FROM assets WHERE kind='script'").fetchone()[0]
        db.execute('UPDATE assets SET relative_path=? WHERE id=?', ('../../private.funscript', script_id))
    (tmp_path / 'private.funscript').write_text('private')
    assert client.get(f'/api/works/{work_id}/es-post/scripts/{script_id}').status_code == 403


def test_missing_media_section_never_ready_and_wrong_template_types(posts):
    client, _, _, _ = posts
    work_id = work(posts, free=True)
    state = client.get(f'/api/works/{work_id}/es-post').json()
    save(client, work_id, selected_script_ids=[state['sources']['scripts'][0]['id']],
         preview_markdown='![gif](upload://preview.gif)', attachment_markdown='[script](upload://script.funscript)')
    template = client.get('/api/es-template').json()
    payload = {key: template[key] for key in ('name', 'body', 'config')}
    payload['expected_revision'] = template['revision']
    bad = {**payload, 'config': {**payload['config'], 'recentPinnedIds': ['S046'] * 5}}
    assert client.put('/api/es-template', json=bad).status_code == 422
    bad = {**payload, 'config': {**payload['config'], 'promoButtons': [{'id': 'evil', 'group': 'bottom', 'linkUrl': 'javascript:alert(1)'}]}}
    assert client.put('/api/es-template', json=bad).status_code == 422
    payload['body'] = '{{metadata}}\n{{navigation}}'
    assert client.put('/api/es-template', json=payload).status_code == 200
    result = generate(client, work_id).json()['output']
    assert result['status'] == 'draft'
    assert '模板必须包含预览区域 {{preview}}' in result['missing']
    assert '免费模板必须包含附件区域 {{attachments}}' in result['missing']


def test_explicit_cover_save_from_draft_is_work_scoped_and_persistent(posts):
    client, store, config, service = posts
    work_id = work(posts, title='S070', support='unknown')
    other_id = work(posts, script_id='S071')
    state = save(client, work_id, preview_markdown='[webm](upload://v.webm)\n![first](upload://one.gif)\n![second](upload://two.png)').json()
    assert generate(client, work_id).json()['output']['status'] == 'draft'
    state = client.get(f'/api/works/{work_id}/es-post').json()
    response = client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']})
    assert response.status_code == 200
    saved = response.json()
    assert saved['saved_cover_url'] == 'upload://one.gif' and saved['revision'] == state['revision'] + 1
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).status_code == 409
    assert client.get(f'/api/works/{other_id}/es-post').json()['saved_cover_url'] == ''
    restarted = ESPosts(Store(config.data_dir), config, PreviewService(Store(config.data_dir), config))
    assert restarted.state(work_id)['saved_cover_url'] == 'upload://one.gif'
    assert restarted.state(work_id)['inputs']['preview_markdown'] == saved['inputs']['preview_markdown']
    with store.connection() as db:
        record = db.execute('SELECT * FROM es_preview_history WHERE script_id=?', ('S070',)).fetchone()
        assert record['source'] == 'workbench-explicit' and record['markdown'] == saved['inputs']['preview_markdown']
    save(client, work_id, preview_markdown='[webm](upload://video.webm)')
    state = client.get(f'/api/works/{work_id}/es-post').json()
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).status_code == 422
    assert client.get(f'/api/works/{work_id}/es-post').json()['saved_cover_url'] == 'upload://one.gif'


def test_explicit_cover_rejects_no_preview_or_heatmap_only(posts):
    client, _, _, _ = posts
    work_id = work(posts)
    state = save(client, work_id, heatmap_markdown='![heatmap](upload://heatmap.png)').json()
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).status_code == 422
    assert client.get(f'/api/works/{work_id}/es-post').json()['saved_cover_url'] == ''


def test_source_edits_during_duration_probe_cannot_mix_old_and_new_data(posts, monkeypatch):
    client, store, _, service = posts
    work_id = work(posts)
    save(client, work_id, preview_markdown='![gif](upload://preview.gif)')
    def change_sources(*args):
        with store.connection() as db:
            db.execute('UPDATE works SET title=? WHERE id=?', ('Changed during generation', work_id))
        return 123, None
    monkeypatch.setattr(service, 'duration', change_sources)
    result = generate(client, work_id)
    assert result.status_code == 409
    assert client.get(f'/api/works/{work_id}/es-post').json()['output'] is None


def test_video_with_gif_in_filename_is_not_an_image_cover(posts):
    client, store, _, _ = posts
    work_id = work(posts)
    state = save(client, work_id, preview_markdown='![video](upload://example.gif.webm)').json()
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).status_code == 422
    assert generate(client, work_id).json()['output']['status'] == 'ready'
    assert client.get(f'/api/works/{work_id}/es-post').json()['saved_cover_url'] == ''
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM es_preview_history').fetchone()[0] == 0
    save(client, work_id, preview_markdown='![video](upload://example.gif.webm)\n![real](upload://real.gif)')
    assert generate(client, work_id).json()['saved_cover_url'] == 'upload://real.gif'


def test_html_and_disourse_attachment_references_are_supported(posts):
    client, _, _, _ = posts
    work_id = work(posts)
    state = save(client, work_id, preview_markdown='<video src="upload://clip.webm"></video>\n<img src="upload://image.png">').json()
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).json()['saved_cover_url'] == 'upload://image.png'
    save(client, work_id, preview_markdown='[clip.webm|attachment](upload://clip.webm)\n![gif|192x108](upload://first.gif)')
    result = generate(client, work_id).json()
    assert result['saved_cover_url'] == 'upload://image.png'
    assert '![gif|192x108](upload://first.gif)' in result['output']['body']


def test_cover_and_body_preview_are_independent(posts):
    client, store, _, _ = posts
    work_id = work(posts)
    cover = '![cover A|192x108](upload://cover-a.gif)'
    body = '[body B.webm|attachment](upload://body-b.webm)'
    save(client, work_id, cover_markdown=cover, preview_markdown=body)
    first = generate(client, work_id).json()
    assert first['output']['status'] == 'ready' and first['saved_cover_url'] == 'upload://cover-a.gif'
    assert body in first['output']['body'] and 'upload://cover-a.gif' not in first['output']['body']
    save(client, work_id, preview_markdown='![body C](upload://body-c.png)')
    changed = generate(client, work_id).json()
    assert changed['saved_cover_url'] == 'upload://cover-a.gif'
    with store.connection() as db:
        history = db.execute('SELECT * FROM es_preview_history WHERE script_id=?', ('S070',)).fetchone()
        assert history['cover_markdown'] == cover
        assert history['markdown'] == '![body C](upload://body-c.png)'
    # An explicitly cleared cover field also cannot cause body media to replace an existing cover.
    save(client, work_id, cover_markdown='', preview_markdown='![body D](upload://body-d.png)')
    assert generate(client, work_id).json()['saved_cover_url'] == 'upload://cover-a.gif'


def test_save_independent_cover_without_body_preserves_previous_body(posts):
    client, store, _, _ = posts
    work_id = work(posts)
    state = save(client, work_id, cover_markdown='![A](upload://cover-a.png)').json()
    saved = client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).json()
    assert saved['saved_cover_url'] == 'upload://cover-a.png'
    assert saved['inputs']['preview_markdown'] == ''
    assert generate(client, work_id).json()['output']['status'] == 'draft'
    save(client, work_id, preview_markdown='[B](upload://body-b.webm)')
    generate(client, work_id)
    state = save(client, work_id, preview_markdown='', cover_markdown='![C](upload://cover-c.gif)').json()
    assert client.post(f'/api/works/{work_id}/es-post/cover', json={'expected_revision': state['revision']}).json()['saved_cover_url'] == 'upload://cover-c.gif'
    with store.connection() as db:
        assert db.execute('SELECT markdown FROM es_preview_history WHERE script_id=?', ('S070',)).fetchone()[0] == '[B](upload://body-b.webm)'


def test_old_schema_and_inputs_are_migrated_without_overwriting_manual_clears(posts):
    client, store, config, service = posts
    work_id = work(posts)
    inputs = Inputs().model_dump()
    inputs.pop('cover_markdown')
    inputs['preview_markdown'] = '![manual body](upload://body.gif)'
    with store.connection() as db:
        db.execute('DROP TABLE es_preview_history')
        db.execute('CREATE TABLE es_preview_history(script_id TEXT PRIMARY KEY,upload_url TEXT NOT NULL,markdown TEXT NOT NULL,source TEXT NOT NULL,updated_at TEXT NOT NULL)')
        db.execute('INSERT INTO es_preview_history VALUES(?,?,?,?,?)', ('S070', 'upload://history-cover.gif', '![historic body](upload://historic.gif)', 'skill-history', now()))
        db.execute('INSERT INTO es_posts(work_id,inputs,updated_at) VALUES(?,?,?)', (work_id, json.dumps(inputs), now()))
    restarted = ESPosts(store, config, service.previews)
    state = restarted.state(work_id)
    assert state['inputs']['cover_markdown'] == '![Preview cover](upload://history-cover.gif)'
    assert state['inputs']['preview_markdown'] == '![manual body](upload://body.gif)'
    with store.connection() as db:
        stored = {**inputs, 'cover_markdown': '', 'preview_markdown': ''}
        db.execute('UPDATE es_posts SET inputs=? WHERE work_id=?', (json.dumps(stored), work_id))
    state = restarted.state(work_id)
    assert state['inputs']['cover_markdown'] == '' and state['inputs']['preview_markdown'] == ''
