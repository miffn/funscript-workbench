from __future__ import annotations

import ipaddress
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit

from .scanner import HISTORY_FIELDS, pick
from .store import Store, now
from .release_dates import RELEASE_DATE_FIELDS, release_today, validate_release_date


LINK_FIELDS = {
    'patreon': ('patreon_url', *HISTORY_FIELDS['patreon_url']),
    'video': ('video_url', *HISTORY_FIELDS['video_url']),
    'script': ('script_url', 'script_link', 'download_url', 'Script Link', 'Script URL', 'Script Download URL', '脚本链接'),
    'es': ('es_url', *HISTORY_FIELDS['es_url']),
}
PUBLICATION_FIELDS = ('es_published', 'patreon_published', 'es_planned_date', 'patreon_planned_date')


class WorkLinksError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def validate_link(value: str) -> str:
    if not isinstance(value, str) or len(value) > 4000:
        raise WorkLinksError('链接必须是长度不超过 4000 的字符串')
    if any(unicodedata.category(char) in {'Cc', 'Cf'} for char in value):
        raise WorkLinksError('链接不能包含控制字符')
    value = value.strip()
    if not value:
        return ''
    if any(char.isspace() for char in value) or '\\' in value:
        raise WorkLinksError('链接不能包含空白字符或反斜杠')
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
        if parsed.scheme not in {'http', 'https'} or not hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if ':' in hostname:
            ipaddress.IPv6Address(hostname)
        else:
            domain = hostname.rstrip('.').encode('idna').decode('ascii')
            if len(domain) > 253 or not all(re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', part) for part in domain.split('.')):
                raise ValueError
        if parsed.netloc.endswith(':'):
            raise ValueError
    except (ValueError, UnicodeError):
        raise WorkLinksError('请填写有效的 HTTP 或 HTTPS 链接，不能包含用户名或密码')
    return value


class WorkLinks:
    def __init__(self, store: Store):
        self.store = store

    def state(self, db, work_id: int) -> dict:
        work = db.execute('SELECT metadata,es_published,patreon_published,es_published_date,patreon_published_date FROM works WHERE id=?', (work_id,)).fetchone()
        if work is None:
            raise WorkLinksError('库存编号不存在', 404)
        metadata = json.loads(work['metadata'])
        row = db.execute('SELECT overrides,revision FROM work_links WHERE work_id=?', (work_id,)).fetchone()
        overrides = json.loads(row['overrides']) if row else {}
        links = {}
        for kind, aliases in LINK_FIELDS.items():
            if kind in overrides:
                links[kind] = overrides[kind]
            else:
                try:
                    links[kind] = validate_link(pick(metadata, *aliases))
                except WorkLinksError:
                    # Historical notes or malformed cells are not executable links.
                    links[kind] = ''
        # WorkLinks also serves non-HTTP tools; the calendar table is initialized by
        # the application, but may not exist in a standalone service's store yet.
        plan_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='release_calendar_plans'").fetchone()
        plan = db.execute('SELECT * FROM release_calendar_plans WHERE work_id=?', (work_id,)).fetchone() if plan_table else None
        result = {'work_id': work_id, 'links': links, 'links_revision': row['revision'] if row else 0,
                  **{field: work[field] for field in RELEASE_DATE_FIELDS},
                  **{f'{kind}_published': bool(work[f'{kind}_published']) for kind in ('es', 'patreon')},
                  **{f'{kind}_planned_date': plan[f'{kind}_planned_date'] if plan else None for kind in ('es', 'patreon')}}
        fingerprint = {field: result[field] for field in (*RELEASE_DATE_FIELDS, *PUBLICATION_FIELDS)}
        fingerprint.update(links=links, links_revision=result['links_revision'], calendar_revision=plan['revision'] if plan else 0)
        result['publication_revision'] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return result

    def update(self, work_id: int, changes: dict[str, str], expected_revision: int,
               dates: dict[str, str | None] | None = None,
               publication: dict | None = None, expected_publication_revision: str | None = None) -> dict:
        dates = dates or {}
        publication = publication or {}
        if (not changes and not dates and not publication) or set(changes) - LINK_FIELDS.keys() or set(dates) - set(RELEASE_DATE_FIELDS) or set(publication) - set(PUBLICATION_FIELDS):
            raise WorkLinksError('请选择至少一种有效的链接类型')
        changes = {kind: validate_link(value) for kind, value in changes.items()}
        try:
            dates = {field: validate_release_date(value) for field, value in dates.items()}
            publication = {field: validate_release_date(value) if field.endswith('_date') else value for field, value in publication.items()}
        except ValueError as error:
            raise WorkLinksError(str(error)) from error
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.state(db, work_id)
            if current['links_revision'] != expected_revision:
                raise WorkLinksError('链接已被其他操作更新，请刷新后重试', 409)
            if publication and not expected_publication_revision:
                raise WorkLinksError('编辑平台状态或计划日期需要当前发布版本')
            if expected_publication_revision is not None and current['publication_revision'] != expected_publication_revision:
                raise WorkLinksError('发布信息已被其他操作更新，请刷新后重试', 409)
            for kind in ('es', 'patreon'):
                field = f'{kind}_published_date'
                # New platform links publish that platform. Explicit date edits
                # win; replacing an already published URL preserves its date.
                changed_link = changes.get(kind) and changes[kind] != current['links'][kind]
                if changed_link:
                    publication[f'{kind}_published'] = True
                if (field not in dates and changes.get(kind) and changes[kind] != current['links'][kind]
                        and not current[f'{kind}_published']):
                    dates[field] = release_today()
                if publication.get(f'{kind}_published') and not current[f'{kind}_published'] and field not in dates:
                    dates[field] = current[field] or release_today()
            row = db.execute('SELECT overrides FROM work_links WHERE work_id=?', (work_id,)).fetchone()
            overrides = json.loads(row['overrides']) if row else {}
            overrides.update(changes)
            db.execute('INSERT INTO work_links(work_id,overrides,revision) VALUES(?,?,?) ON CONFLICT(work_id) DO UPDATE SET overrides=excluded.overrides,revision=excluded.revision',
                       (work_id, json.dumps(overrides, ensure_ascii=False), expected_revision + 1))
            db.execute('UPDATE works SET updated_at=? WHERE id=?', (now(), work_id))
            if dates:
                work = db.execute('SELECT manual_fields FROM works WHERE id=?', (work_id,)).fetchone()
                manual = set(json.loads(work['manual_fields'])) | dates.keys()
                assignments = ','.join(f'{field}=?' for field in dates)
                db.execute(f'UPDATE works SET {assignments},manual_fields=? WHERE id=?',
                           [*dates.values(), json.dumps(sorted(manual)), work_id])
            states = {field: value for field, value in publication.items() if field.endswith('_published')}
            if states:
                work = db.execute('SELECT manual_fields FROM works WHERE id=?', (work_id,)).fetchone()
                manual = set(json.loads(work['manual_fields'])) | states.keys()
                assignments = ','.join(f'{field}=?' for field in states)
                db.execute(f'UPDATE works SET {assignments},manual_fields=? WHERE id=?', [*states.values(), json.dumps(sorted(manual)), work_id])
                db.execute("UPDATE works SET status=CASE WHEN es_published=1 AND patreon_published=1 THEN 'published' ELSE 'pending' END WHERE id=?", (work_id,))
            plans = {field: value for field, value in publication.items() if field.endswith('_planned_date')}
            if plans:
                db.execute('INSERT INTO release_calendar_plans(work_id,revision) VALUES(?,1) ON CONFLICT(work_id) DO UPDATE SET revision=revision+1', (work_id,))
                assignments = ','.join(f'{field}=?' for field in plans)
                db.execute(f'UPDATE release_calendar_plans SET {assignments} WHERE work_id=?', [*plans.values(), work_id])
            return self.state(db, work_id)
