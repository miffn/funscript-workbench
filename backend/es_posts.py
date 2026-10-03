"""Database-backed, local ES drafts. This module never contacts a publishing site."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Annotated, Any

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .previews import PreviewError
from .store import now
from .work_links import WorkLinks, WorkLinksError, validate_link
from .work_directory import current_directory


TOKENS = {'header', 'intro', 'metadata', 'preview', 'heatmaps', 'attachments', 'navigation', 'motion', 'recent', 'footer'}
DEFAULT_BODY = '\n\n'.join('{{' + token + '}}' for token in
                          ('header', 'intro', 'metadata', 'preview', 'heatmaps', 'attachments', 'navigation', 'motion', 'recent', 'footer'))
DEFAULT_CONFIG = {
    'version': 4, 'theme': 'garden-journal', 'brandingHeaderMarkdown': '[center][/center]',
    'brandingFooterMarkdown': '', 'showFeaturedHeading': False, 'showTitleInBody': False,
    'showPicks': False, 'tierNoticeMarkdown': '', 'footerText': '', 'recentPinnedIds': ['S046'],
    'promoButtons': [
        {'id': 'video-link', 'group': 'action', 'label': 'Video Link', 'linkSource': 'videoLink', 'imageUrl': '', 'width': '300'},
        {'id': 'paid-script', 'group': 'action-paid', 'label': 'View Patreon Release', 'linkSource': 'patreonLink', 'imageUrl': '', 'width': '300'},
        {'id': 'all-script-videos', 'group': 'bottom', 'label': 'Video Archive', 'linkUrl': '', 'imageUrl': '', 'width': '300'},
        {'id': 'support-video-creator', 'group': 'bottom', 'label': 'Support Video Creator', 'linkSource': 'supportCreatorLink', 'imageUrl': '', 'width': '300'},
        {'id': 'browse-scripts', 'group': 'bottom', 'label': 'View Portfolio', 'linkUrl': '', 'imageUrl': '', 'width': '300'},
        {'id': 'support-patreon', 'group': 'bottom', 'label': 'Support on Patreon', 'linkUrl': '', 'imageUrl': '', 'width': '300'},
    ],
}
UPLOAD_RE = re.compile(r'upload://[^\s)\"\'<>]+', re.I)
IMAGE_RE = re.compile(r'upload://[^\s)\"\'<>]+\.(?:gif|jpe?g|png|webp)(?:\?[^\s)\"\'<>]*)?', re.I)
MEDIA_RE = re.compile(r'upload://[^\s)\"\'<>]+\.(?:gif|jpe?g|png|webp|webm|mp4|mov)(?:\?[^\s)\"\'<>]*)?', re.I)
SCRIPT_RE = re.compile(r'upload://[^\s)\"\'<>]+\.(?:funscript|zip|7z)(?:\?[^\s)\"\'<>]*)?', re.I)
ID_RE = re.compile(r'S\d{3,}(?:_\d{3,})?')


class Inputs(BaseModel):
    model_config = ConfigDict(extra='forbid')
    release_title: str = Field(default='', strict=True, max_length=500)
    intro_markdown: str = Field(default='', strict=True, max_length=20000)
    cover_markdown: str = Field(default='', strict=True, max_length=30000)
    preview_markdown: str = Field(default='', strict=True, max_length=30000)
    heatmap_markdown: str = Field(default='', strict=True, max_length=30000)
    attachment_markdown: str = Field(default='', strict=True, max_length=30000)
    selected_script_ids: list[Annotated[int, Field(strict=True, gt=0)]] = Field(default_factory=list, max_length=100)
    selected_preview_filenames: list[Annotated[str, Field(strict=True, min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)
    no_creator_link: bool = Field(default=False, strict=True)
    video_asset_id: int | None = Field(default=None, strict=True, gt=0)


class InputsEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    inputs: Inputs
    expected_revision: int = Field(strict=True, ge=0)


class GenerateEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: int = Field(strict=True, ge=0)


class TemplateEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(strict=True, min_length=1, max_length=120)
    body: str = Field(strict=True, min_length=1, max_length=100000)
    config: dict[str, Any]
    expected_revision: int = Field(strict=True, ge=1)


def reject(message: str, status: int = 422):
    raise HTTPException(status, message)


def uploaded(markdown: str, pattern) -> bool:
    urls = UPLOAD_RE.findall(markdown)
    # Only ES-uploaded media belongs in these fields. Keep the original Markdown,
    # but reject externally hosted images alongside a token that merely looks uploaded.
    references = media_references(markdown)
    return bool(references) and bool(urls) and all(pattern.fullmatch(url) for url in urls) and all(pattern.fullmatch(url) for url in references)


def media_references(markdown: str) -> list[str]:
    """Return whole link targets in source order, preserving Discourse media syntax."""
    matches = list(re.finditer(r'\[[^\]]*\]\(\s*([^\s)]+)(?:\s+[\"\'][^\"\']*[\"\'])?\s*\)', markdown))
    matches += list(re.finditer(r'<(?:img|video|source|a)\b[^>]*\b(?:src|href)=[\"\']([^\"\']+)[\"\']', markdown, re.I))
    matches.sort(key=lambda match: match.start())
    return [match[1] for match in matches]


def first_image(markdown: str) -> str:
    # Match the complete upload token: a .gif.webm video must never become a .gif card.
    referenced = set(media_references(markdown))
    return next((url for url in UPLOAD_RE.findall(markdown) if url in referenced and IMAGE_RE.fullmatch(url)), '')


def safe_link(value: str) -> str:
    try:
        return validate_link(value)
    except WorkLinksError:
        return ''


def validate_template(body: str, config: dict) -> dict:
    variables = re.findall(r'{{\s*([^{}]+?)\s*}}', body)
    unknown = set(variables) - TOKENS
    if unknown:
        reject('未知模板变量：' + '、'.join(sorted(unknown)))
    remainder = re.sub(r'{{\s*[^{}]+?\s*}}', '', body)
    if '{{' in remainder or '}}' in remainder:
        reject('模板变量必须使用 {{变量名}} 格式')
    if len(json.dumps(config, ensure_ascii=False)) > 100000:
        reject('模板配置过大')
    if set(config) - DEFAULT_CONFIG.keys():
        reject('模板配置包含不支持的字段：' + '、'.join(sorted(set(config) - DEFAULT_CONFIG.keys())))
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(config)
    for key in ('theme', 'brandingHeaderMarkdown', 'brandingFooterMarkdown', 'tierNoticeMarkdown', 'footerText'):
        if not isinstance(result[key], str) or len(result[key]) > 30000:
            reject(f'{key} 必须是长度不超过 30000 的文本')
    for key in ('showFeaturedHeading', 'showTitleInBody', 'showPicks'):
        if not isinstance(result[key], bool):
            reject(f'{key} 必须为布尔值')
    if type(result['version']) is not int or not 1 <= result['version'] <= 100:
        reject('模板版本无效')
    pinned = result['recentPinnedIds']
    if not isinstance(pinned, list) or len(pinned) > 4 or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in pinned):
        reject('置顶编号最多 4 个，必须使用完整编号')
    buttons = result['promoButtons']
    if not isinstance(buttons, list) or len(buttons) > 20:
        reject('导航按钮最多 20 个')
    ids = set()
    for button in buttons:
        if not isinstance(button, dict) or set(button) - {'id', 'group', 'label', 'imageUrl', 'linkUrl', 'linkSource', 'localAsset', 'width'}:
            reject('导航按钮配置无效')
        if any(not isinstance(value, str) or len(value) > 4000 for value in button.values()):
            reject('导航按钮字段必须是文本')
        if not button.get('id') or button['id'] in ids:
            reject('导航按钮必须有唯一 ID')
        ids.add(button['id'])
        if button.get('group') not in {'action', 'action-paid', 'bottom'}:
            reject('导航按钮分组无效')
        if button.get('linkSource', '') not in {'', 'videoLink', 'patreonLink', 'supportCreatorLink'}:
            reject('导航按钮链接来源无效')
        link = button.get('linkUrl', '')
        if link and not safe_link(link):
            reject('公共链接必须为有效 HTTP 或 HTTPS 地址')
        image = button.get('imageUrl', '')
        if image and not IMAGE_RE.fullmatch(image):
            reject('按钮图片必须为 ES upload:// 图片地址')
        if not re.fullmatch(r'\d{1,4}%?', button.get('width', '300')):
            reject('按钮图片宽度无效')
    return result


class ESPosts:
    def __init__(self, store, config, previews):
        self.store, self.config, self.previews = store, config, previews
        with store.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS es_templates (
                    id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL, body TEXT NOT NULL,
                    config TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS es_posts (
                    work_id INTEGER PRIMARY KEY REFERENCES works(id), revision INTEGER NOT NULL DEFAULT 0,
                    inputs TEXT NOT NULL, output TEXT, source_fingerprint TEXT,
                    cover_url TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS es_preview_history (
                    script_id TEXT PRIMARY KEY, upload_url TEXT NOT NULL, markdown TEXT NOT NULL,
                    source TEXT NOT NULL, updated_at TEXT NOT NULL,
                    cover_markdown TEXT NOT NULL DEFAULT '');
            ''')
            if 'cover_markdown' not in {row[1] for row in db.execute('PRAGMA table_info(es_preview_history)')}:
                db.execute("ALTER TABLE es_preview_history ADD COLUMN cover_markdown TEXT NOT NULL DEFAULT ''")
            db.execute('INSERT OR IGNORE INTO es_templates(id,name,body,config) VALUES(1,?,?,?)',
                       ('ES 发布模板', DEFAULT_BODY, json.dumps(DEFAULT_CONFIG, ensure_ascii=False)))

    def template(self, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.template(connection)
        row = dict(db.execute('SELECT name,body,config,revision FROM es_templates WHERE id=1').fetchone())
        row['config'] = json.loads(row['config'])
        return row

    def save_template(self, payload: TemplateEdit):
        if not payload.name.strip():
            reject('模板名称不能为空')
        config = validate_template(payload.body, payload.config)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self.template(db)['revision'] != payload.expected_revision:
                reject('模板已被其他操作更新，请刷新后重试', 409)
            db.execute('UPDATE es_templates SET name=?,body=?,config=?,revision=revision+1 WHERE id=1',
                       (payload.name.strip(), payload.body, json.dumps(config, ensure_ascii=False)))
            return self.template(db)

    def records(self, db, work_id):
        work = db.execute('SELECT id,script_id,title,es_published_date,patreon_published_date FROM works WHERE id=?', (work_id,)).fetchone()
        if work is None:
            reject('库存编号不存在', 404)
        tags = [dict(row) for row in db.execute('SELECT t.* FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? ORDER BY t.id', (work_id,))]
        links = WorkLinks(self.store).state(db, work_id)
        directory = current_directory(db, work_id)
        assets = [dict(row) for row in db.execute('SELECT a.*,d.work_id,d.path,d.root_path,d.available FROM assets a JOIN directories d ON d.id=a.directory_id WHERE d.id=? ORDER BY a.id', (directory['id'],))] if directory is not None else []
        return dict(work), tags, links['links'], assets

    def sources(self, work_id):
        with self.store.connection() as db:
            work, tags, _, assets = self.records(db, work_id)
        authors = [tag for tag in tags if tag['category'] == 'author']
        author = authors[0] if len(authors) == 1 else None
        support = {'name': author['name'] if author else '', 'status': author['support_status'] if author else 'unknown',
                   'url': author['support_url'] if author else None}
        for asset in assets:
            if asset['kind'] == 'script':
                asset['download_url'] = f"/api/works/{work_id}/es-post/scripts/{asset['id']}"
        try:
            files = self.previews.state(work_id)['files']
        except (PreviewError, OSError):
            files = []
        return {'scripts': [item for item in assets if item['kind'] == 'script'],
                'videos': [item for item in assets if item['kind'] == 'video'],
                'previews': files, 'author_support': support}

    def state(self, work_id):
        sources = self.sources(work_id)
        with self.store.connection() as db:
            return self._state(db, work_id, sources)

    def fingerprint(self, db, work_id, inputs):
        work, tags, links, assets = self.records(db, work_id)
        relevant = {'work': work, 'tags': [{k: tag[k] for k in ('category', 'name', 'support_status', 'support_url')} for tag in tags],
                    'links': links, 'assets': [{k: asset[k] for k in ('id', 'mtime_ns', 'size', 'available')} for asset in assets],
                    'inputs': inputs, 'template_revision': self.template(db)['revision'],
                    'recent': self.recent(db, work_id, self.template(db)['config'])}
        return hashlib.sha256(json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def _state(self, db, work_id, sources):
        self.records(db, work_id)
        row = db.execute('SELECT * FROM es_posts WHERE work_id=?', (work_id,)).fetchone()
        stored_inputs = json.loads(row['inputs']) if row else {}
        inputs = {**Inputs().model_dump(), **stored_inputs}
        historical = db.execute('SELECT upload_url,markdown,cover_markdown FROM es_preview_history WHERE script_id=(SELECT script_id FROM works WHERE id=?)', (work_id,)).fetchone()
        if row is None:
            if historical and uploaded(historical['markdown'], MEDIA_RE):
                inputs['preview_markdown'] = historical['markdown']
        if 'cover_markdown' not in stored_inputs and historical:
            cover = historical['cover_markdown']
            inputs['cover_markdown'] = cover if uploaded(cover, IMAGE_RE) else f'![Preview cover]({historical["upload_url"]})'
        output = json.loads(row['output']) if row and row['output'] else None
        if output:
            output['stale'] = row['source_fingerprint'] != self.fingerprint(db, work_id, inputs)
        saved_cover = db.execute('SELECT upload_url FROM es_preview_history WHERE script_id=(SELECT script_id FROM works WHERE id=?)', (work_id,)).fetchone()
        return {'work_id': work_id, 'revision': row['revision'] if row else 0,
                'inputs': inputs, 'output': output, 'sources': sources,
                'saved_cover_url': saved_cover[0] if saved_cover else ''}

    def validate_inputs(self, inputs, sources):
        scripts = {asset['id'] for asset in sources['scripts']}
        videos = {asset['id'] for asset in sources['videos']}
        filenames = {file['filename'] for file in sources['previews']}
        if len(inputs['selected_script_ids']) != len(set(inputs['selected_script_ids'])):
            reject('选择的脚本文件重复')
        if not set(inputs['selected_script_ids']) <= scripts:
            reject('所选脚本不属于当前作品', 404)
        if inputs['video_asset_id'] is not None and inputs['video_asset_id'] not in videos:
            reject('所选视频不属于当前作品', 404)
        if len(inputs['selected_preview_filenames']) != len(set(inputs['selected_preview_filenames'])):
            reject('选择的预览文件重复')
        if not set(inputs['selected_preview_filenames']) <= filenames:
            reject('所选预览不存在或已失效', 404)
        for key, pattern in (('cover_markdown', IMAGE_RE), ('preview_markdown', MEDIA_RE), ('heatmap_markdown', IMAGE_RE), ('attachment_markdown', SCRIPT_RE)):
            text = inputs[key]
            if text.strip() and not uploaded(text, pattern):
                reject({'cover_markdown': '封面须粘贴 ES 上传 GIF 或图片的 Markdown',
                        'preview_markdown': '预览须粘贴 ES 上传图片或视频的 Markdown',
                        'heatmap_markdown': '热力图只支持 ES 上传图片 Markdown',
                        'attachment_markdown': '脚本附件须粘贴 ES 上传 funscript 或压缩包的 Markdown'}[key])
        if inputs['cover_markdown'].strip() and len(media_references(inputs['cover_markdown'])) != 1:
            reject('封面区域只需一张 GIF 或图片')

    def save(self, work_id, payload: InputsEdit):
        sources = self.sources(work_id)
        inputs = payload.inputs.model_dump()
        self.validate_inputs(inputs, sources)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._state(db, work_id, sources)
            if current['revision'] != payload.expected_revision:
                reject('贴文资料已被其他操作更新，请刷新后重试', 409)
            db.execute('INSERT INTO es_posts(work_id,revision,inputs,updated_at) VALUES(?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,inputs=excluded.inputs,updated_at=excluded.updated_at',
                       (work_id, payload.expected_revision + 1, json.dumps(inputs, ensure_ascii=False), now()))
            return self._state(db, work_id, sources)

    def duration(self, work, inputs, videos):
        try:
            manifest_path = self.previews.output_directory(work['script_id']) / 'manifest.json'
            manifest = self.previews._manifest(manifest_path, work['script_id'])
        except (PreviewError, OSError):
            # A damaged generated directory must not prevent a local draft.
            manifest_path, manifest = None, None
        selected = next((asset for asset in videos if asset['id'] == inputs['video_asset_id']), None)
        if selected is None and inputs['video_asset_id'] is None and len(videos) == 1:
            selected = videos[0]
        if selected is None and inputs['video_asset_id'] is None and len(videos) > 1:
            source_info = manifest.get('video', {}) if manifest and manifest.get('status') == 'completed' else {}
            source_path = source_info.get('path') if isinstance(source_info, dict) else None
            if source_path:
                matches = [asset for asset in videos if str(Path(asset['path']) / asset['relative_path']) == source_path]
                if len(matches) == 1:
                    selected = matches[0]
            with self.store.connection() as db:
                binding = db.execute('SELECT DISTINCT video_asset_id FROM preview_bindings WHERE work_id=? AND video_asset_id IS NOT NULL', (work['id'],)).fetchall()
            if selected is None and len(binding) == 1:
                selected = next((asset for asset in videos if asset['id'] == binding[0][0]), None)
        if selected is None:
            return None, '请选择用于发布的源视频' if videos else '缺少源视频'
        try:
            source = self.previews.valid_source(selected)
            duration = None
            if manifest and manifest.get('status') == 'completed':
                source_info = manifest.get('video', {})
                if not isinstance(source_info, dict):
                    source_info = {}
                original_path = source_info.get('path') or manifest.get('video_path')
                if original_path == str(source) and source_info.get('size') == source.stat().st_size and source.stat().st_mtime_ns <= manifest_path.stat().st_mtime_ns:
                    duration = source_info.get('duration_seconds')
            if not isinstance(duration, (float, int)) or isinstance(duration, bool) or not math.isfinite(duration) or duration <= 0:
                result = subprocess.run([self.config.ffprobe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(source)],
                                        capture_output=True, text=True, timeout=10, check=True)
                duration = float(json.loads(result.stdout)['format']['duration'])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError
            return duration, None
        except (PreviewError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            return None, '无法读取所选视频时长，请确认素材可用'

    def script_path(self, work_id, asset_id):
        with self.store.connection() as db:
            _, _, _, assets = self.records(db, work_id)
        asset = next((asset for asset in assets if asset['id'] == asset_id and asset['kind'] == 'script'), None)
        if asset is None or Path(asset['relative_path']).suffix.lower() != '.funscript':
            reject('脚本附件不存在', 404)
        try:
            return self.previews.valid_source(asset), asset['name']
        except PreviewError as error:
            reject(str(error), error.status_code)

    def recent(self, db, work_id, config):
        rows = db.execute('SELECT w.id,w.script_id,w.title,w.es_published_date,p.upload_url AS cover_url FROM works w JOIN es_preview_history p ON p.script_id=w.script_id WHERE w.id!=? AND w.es_published=1 ORDER BY (w.es_published_date IS NULL),w.es_published_date DESC,w.script_id DESC', (work_id,)).fetchall()
        candidates = []
        for row in rows:
            item = dict(row)
            url = WorkLinks(self.store).state(db, item['id'])['links']['es']
            if not url or not IMAGE_RE.fullmatch(item['cover_url']):
                continue
            item['es_url'] = url
            candidates.append(item)
        pinned = [item for script_id in config['recentPinnedIds'] for item in candidates if item['script_id'] == script_id]
        selected = pinned + [item for item in candidates if item not in pinned][:4-len(pinned)]
        selected.sort(key=lambda item: (item['es_published_date'] or '', item['script_id']), reverse=True)
        cells = []
        for item in selected:
            url, image = html.escape(item['es_url'], quote=True), html.escape(item['cover_url'], quote=True)
            label = html.escape(item['script_id'] + ' - ' + item['title'])
            cells.append(f'<td align="center" width="50%">\n<a href="{url}"><img src="{image}" width="320"></a><br>\n<b><a href="{url}">{label}</a></b><br>\n<small>{html.escape(item["es_published_date"] or "")}</small>\n</td>')
        if not cells:
            return ''
        rows = ['<tr>\n' + '\n'.join(cells[index:index+2]) + '\n</tr>' for index in range(0, len(cells), 2)]
        return '<h1 align="center">🌿 Recent Releases 🌿</h1>\n\n<table>\n' + '\n'.join(rows) + '\n</table>'

    def build(self, db, work_id, inputs, sources, duration, duration_issue):
        work, tags, links, _ = self.records(db, work_id)
        template = self.template(db)
        config = template['config']
        missing, warnings = [], []
        values = {}
        for category, label in (('author', '作者'), ('video_type', '视频类型'), ('axis_type', '轴类型'), ('release_type', '发布类型'), ('tier', '档位')):
            matches = [tag for tag in tags if tag['category'] == category]
            if len(matches) != 1:
                missing.append(('缺少' if not matches else '需要明确一个') + label)
            values[category] = matches[0]['name'] if len(matches) == 1 else ''
        axis = {'单轴': 'Single-axis', '多轴': 'Multi-axis', 'single-axis': 'Single-axis', 'multi-axis': 'Multi-axis'}.get(values['axis_type'].lower(), values['axis_type'])
        release = values['release_type'].lower()
        free, paid = release in {'free sample', 'free', '免费'}, release in {'paid', '付费'}
        if release and not (free or paid):
            missing.append('发布类型应为 Free Sample 或 Paid')
        release_title = inputs['release_title'].strip() or work['title'].strip()
        if not release_title or release_title.casefold() == work['script_id'].casefold():
            missing.append('请填写发布标题')
            release_title = '[Release title required]'
        # Axis prefixes already present in release titles belong to the separate title field.
        prefix = re.compile(r'^\s*(?:【|\[)(Single-axis|Multi-axis|单轴|多轴)(?:】|\])\s*', re.I)
        match = prefix.match(release_title)
        if match and axis and {'单轴': 'single-axis', '多轴': 'multi-axis'}.get(match[1].lower(), match[1].lower()) == axis.lower():
            release_title = release_title[match.end():]
        title = f'【{values["tier"] or "Tier required"}】【{work["script_id"]}】【{axis or "Axis required"}】 {release_title}'
        author_tags = [tag for tag in tags if tag['category'] == 'author']
        author = author_tags[0] if len(author_tags) == 1 else None
        support = {'status': author['support_status'] if author else 'unknown',
                   'url': author['support_url'] if author else None}
        creator_link = ''
        if support['status'] == 'url':
            creator_link = safe_link(support['url'] or '')
            if not creator_link:
                missing.append('作者支持链接无效')
        elif support['status'] != 'none' and not inputs['no_creator_link']:
            missing.append('请确认作者支持链接或明确没有链接')
        if duration_issue:
            missing.append(duration_issue)
        length = f'{int(duration)//60}:{int(duration)%60:02d}' if duration else '[Length required]'
        preview = inputs['preview_markdown']
        if not uploaded(preview, MEDIA_RE):
            missing.append('请上传预览到 ES 并粘贴 Markdown')
            preview = '[Preview will be inserted here]'
        if not first_image(inputs['cover_markdown']) and not first_image(inputs['preview_markdown']) and not db.execute('SELECT 1 FROM es_preview_history WHERE script_id=?', (work['script_id'],)).fetchone():
            warnings.append('未设置 GIF 或图片封面，暂不能显示在以往作品推荐中')
        attachments = ''
        if free:
            if not inputs['selected_script_ids']:
                missing.append('请选择需要上传的免费脚本')
            for asset_id in inputs['selected_script_ids']:
                try:
                    self.script_path(work_id, asset_id)
                except HTTPException:
                    missing.append('所选免费脚本不可下载，请检查素材')
                    break
            attachment = inputs['attachment_markdown']
            if not uploaded(attachment, SCRIPT_RE):
                missing.append('请上传免费脚本到 ES 并粘贴附件 Markdown')
                attachment = '[Script attachment will be inserted here]'
            attachments = '<details>\n<summary><strong>📎 Script File</strong></summary>\n\n' + attachment + '\n\n</details>'
        if paid and not links['patreon']:
            missing.append('付费作品缺少 Patreon 文章链接')
        if not links['video']:
            missing.append('缺少视频链接')
        if paid and inputs['attachment_markdown'].strip():
            warnings.append('付费作品不会公开脚本附件')
        heatmaps = ''
        if inputs['heatmap_markdown'].strip():
            heatmaps = '<details>\n<summary><strong>📊 Script Heatmaps</strong></summary>\n\n[center]\n' + inputs['heatmap_markdown'] + '\n[/center]\n\n</details>'
        dynamic = {'videoLink': links['video'], 'patreonLink': links['patreon'], 'supportCreatorLink': creator_link}
        buttons = []
        for button in config['promoButtons']:
            if button['group'] == 'action-paid' and not paid:
                continue
            if free and (button['id'] == 'paid-script' or button.get('linkSource') == 'patreonLink' and button['group'] != 'bottom'):
                continue
            url = dynamic.get(button.get('linkSource'), '') if button.get('linkSource') else button.get('linkUrl', '')
            if not url:
                continue
            label = button.get('label') or ('View Patreon Release' if button['id'] == 'paid-script' else button['id'])
            image = button.get('imageUrl')
            content = f'<img src="{html.escape(image, quote=True)}" width="{html.escape(button.get("width", "300"), quote=True)}">' if image else html.escape(label)
            buttons.append(f'<td align="center" width="50%"><a href="{html.escape(url, quote=True)}">{content}</a></td>')
        navigation = ''
        if buttons:
            rows = []
            for index in range(0, len(buttons), 2):
                cells = buttons[index:index+2]
                if len(cells) == 1:
                    cells[0] = cells[0].replace('width="50%"', 'colspan="2"', 1)
                rows.append('<tr>\n' + '\n'.join(cells) + '\n</tr>')
            navigation = '<h2 align="center">🌼 Choose Your Path 🌼</h2>\n\n<table width="100%">\n' + '\n'.join(rows) + '\n</table>'
        metadata = ' · '.join(html.escape(value) for value in (values['video_type'], axis, length, values['tier']) if value)
        motion = '<details>\n<summary><strong>🌿 View Motion &amp; Axis Details</strong></summary>\n\n<p align="center"><code>' + html.escape(axis) + '</code></p>\n\n<p align="center"><small>' + html.escape(values['video_type'] + ' · ' + length) + '</small></p>\n\n</details>'
        parts = {'header': config['brandingHeaderMarkdown'], 'intro': inputs['intro_markdown'],
                 'metadata': '<p align="center"><code>' + metadata + '</code></p>', 'preview': preview,
                 'heatmaps': heatmaps, 'attachments': attachments, 'navigation': navigation, 'motion': motion,
                 'recent': self.recent(db, work_id, config),
                 'footer': '\n\n'.join(item for item in (config['tierNoticeMarkdown'], config['footerText'], config['brandingFooterMarkdown']) if item.strip())}
        for token in ('metadata', 'preview', 'navigation', 'motion'):
            if not re.search(r'{{\s*' + token + r'\s*}}', template['body']):
                warnings.append('模板未包含 ' + token + ' 区域')
        if free and not re.search(r'{{\s*attachments\s*}}', template['body']):
            missing.append('免费模板必须包含附件区域 {{attachments}}')
        if not re.search(r'{{\s*preview\s*}}', template['body']):
            missing.append('模板必须包含预览区域 {{preview}}')
        body = re.sub(r'{{\s*([^{}]+?)\s*}}', lambda match: parts[match[1]], template['body']).strip()
        return {'title': title, 'body': body, 'status': 'draft' if missing else 'ready',
                'missing': list(dict.fromkeys(missing)), 'warnings': warnings, 'generated_at': now(),
                'template_revision': template['revision'], 'stale': False}

    def generate(self, work_id, revision):
        sources = self.sources(work_id)
        with self.store.connection() as db:
            current = self._state(db, work_id, sources)
            work, _, _, _ = self.records(db, work_id)
            initial_fingerprint = self.fingerprint(db, work_id, current['inputs'])
        if current['revision'] != revision:
            reject('贴文资料已被其他操作更新，请刷新后重试', 409)
        self.validate_inputs(current['inputs'], sources)
        duration, issue = self.duration(work, current['inputs'], sources['videos'])
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            latest = self._state(db, work_id, sources)
            if latest['revision'] != revision:
                reject('贴文资料已被其他操作更新，请刷新后重试', 409)
            if self.fingerprint(db, work_id, latest['inputs']) != initial_fingerprint:
                reject('作品资料或模板在生成过程中发生变化，请重新生成', 409)
            output = self.build(db, work_id, latest['inputs'], sources, duration, issue)
            cover = ''
            if output['status'] == 'ready':
                existing_cover = db.execute('SELECT upload_url FROM es_preview_history WHERE script_id=?', (work['script_id'],)).fetchone()
                cover_markdown = latest['inputs']['cover_markdown']
                cover = first_image(cover_markdown)
                if not cover and not existing_cover:
                    cover = first_image(latest['inputs']['preview_markdown'])
                    cover_markdown = f'![Preview cover]({cover})' if cover else ''
                if cover:
                    db.execute('INSERT INTO es_preview_history(script_id,upload_url,markdown,source,updated_at,cover_markdown) VALUES(?,?,?,?,?,?) ON CONFLICT(script_id) DO UPDATE SET upload_url=excluded.upload_url,markdown=CASE WHEN excluded.markdown!=\'\' THEN excluded.markdown ELSE es_preview_history.markdown END,source=excluded.source,updated_at=excluded.updated_at,cover_markdown=excluded.cover_markdown',
                               (work['script_id'], cover, latest['inputs']['preview_markdown'], 'workbench', now(), cover_markdown))
                elif existing_cover and latest['inputs']['preview_markdown'].strip():
                    db.execute('UPDATE es_preview_history SET markdown=?,updated_at=? WHERE script_id=?',
                               (latest['inputs']['preview_markdown'], now(), work['script_id']))
            fingerprint = self.fingerprint(db, work_id, latest['inputs'])
            db.execute('INSERT INTO es_posts(work_id,revision,inputs,output,source_fingerprint,cover_url,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,output=excluded.output,source_fingerprint=excluded.source_fingerprint,cover_url=CASE WHEN excluded.cover_url!=\'\' THEN excluded.cover_url ELSE es_posts.cover_url END,updated_at=excluded.updated_at',
                       (work_id, revision+1, json.dumps(latest['inputs'], ensure_ascii=False), json.dumps(output, ensure_ascii=False), fingerprint, cover, now()))
            return self._state(db, work_id, sources)

    def save_cover(self, work_id, revision):
        sources = self.sources(work_id)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self._state(db, work_id, sources)
            if current['revision'] != revision:
                reject('贴文资料已被其他操作更新，请刷新后重试', 409)
            cover_markdown = current['inputs']['cover_markdown']
            markdown = cover_markdown or current['inputs']['preview_markdown']
            if not uploaded(markdown, MEDIA_RE):
                reject('请先粘贴真实的 ES 上传预览 Markdown')
            image = first_image(markdown)
            if not image:
                reject('预览封面需要 GIF 或图片，视频和热力图不能代替当前预览封面')
            script_id = db.execute('SELECT script_id FROM works WHERE id=?', (work_id,)).fetchone()[0]
            cover_markdown = cover_markdown or f'![Preview cover]({image})'
            db.execute('INSERT INTO es_preview_history(script_id,upload_url,markdown,source,updated_at,cover_markdown) VALUES(?,?,?,?,?,?) ON CONFLICT(script_id) DO UPDATE SET upload_url=excluded.upload_url,markdown=CASE WHEN excluded.markdown!=\'\' THEN excluded.markdown ELSE es_preview_history.markdown END,source=excluded.source,updated_at=excluded.updated_at,cover_markdown=excluded.cover_markdown',
                       (script_id, image, current['inputs']['preview_markdown'], 'workbench-explicit', now(), cover_markdown))
            db.execute('INSERT INTO es_posts(work_id,revision,inputs,updated_at) VALUES(?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,updated_at=excluded.updated_at',
                       (work_id, revision+1, json.dumps(current['inputs'], ensure_ascii=False), now()))
            return self._state(db, work_id, sources)


def import_preview_history(store, history: dict):
    """Explicit one-time migration; caller supplies private data, never a default path."""
    entries = history.get('previews', {}) if isinstance(history, dict) else {}
    if not isinstance(entries, dict):
        raise ValueError('历史封面必须为 previews 映射')
    imported, skipped = [], []
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for script_id, record in entries.items():
            if not isinstance(script_id, str) or not ID_RE.fullmatch(script_id) or not isinstance(record, dict):
                skipped.append(str(script_id))
                continue
            url = record.get('uploadUrl', '')
            markdown = record.get('markdown', '')
            if not isinstance(url, str) or not IMAGE_RE.fullmatch(url) or not isinstance(markdown, str) or len(markdown) > 30000:
                skipped.append(script_id)
                continue
            if markdown.strip() and (not uploaded(markdown, MEDIA_RE) or first_image(markdown) != url):
                skipped.append(script_id)
                continue
            # A historical import must not overwrite a newer workbench-selected cover.
            changed = db.execute('INSERT OR IGNORE INTO es_preview_history(script_id,upload_url,markdown,source,updated_at,cover_markdown) VALUES(?,?,?,?,?,?)',
                                 (script_id, url, markdown, 'skill-history', now(), f'![Preview cover]({url})')).rowcount
            (imported if changed else skipped).append(script_id)
    return {'imported': imported, 'skipped': skipped}


def register_es_post_routes(app, store, config, previews):
    service = ESPosts(store, config, previews)
    app.state.es_posts = service

    @app.get('/api/es-template')
    def get_template():
        return service.template()

    @app.put('/api/es-template')
    def put_template(payload: TemplateEdit):
        return service.save_template(payload)

    @app.get('/api/works/{work_id}/es-post')
    def get_post(work_id: int):
        return service.state(work_id)

    @app.put('/api/works/{work_id}/es-post')
    def put_post(work_id: int, payload: InputsEdit):
        return service.save(work_id, payload)

    @app.post('/api/works/{work_id}/es-post/generate')
    def generate_post(work_id: int, payload: GenerateEdit):
        return service.generate(work_id, payload.expected_revision)

    @app.post('/api/works/{work_id}/es-post/cover')
    def save_cover(work_id: int, payload: GenerateEdit):
        return service.save_cover(work_id, payload.expected_revision)

    @app.get('/api/works/{work_id}/es-post/scripts/{asset_id}')
    def download_script(work_id: int, asset_id: int):
        path, name = service.script_path(work_id, asset_id)
        return FileResponse(path, media_type='application/json', filename=name, content_disposition_type='attachment')
