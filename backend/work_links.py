from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
from urllib.parse import urlsplit

from .scanner import HISTORY_FIELDS, pick
from .store import Store, now


LINK_FIELDS = {
    'patreon': ('patreon_url', *HISTORY_FIELDS['patreon_url']),
    'video': ('video_url', *HISTORY_FIELDS['video_url']),
    'script': ('script_url', 'script_link', 'download_url', 'Script Link', 'Script URL', 'Script Download URL', '脚本链接'),
    'es': ('es_url', *HISTORY_FIELDS['es_url']),
}


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
        work = db.execute('SELECT metadata FROM works WHERE id=?', (work_id,)).fetchone()
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
        return {'work_id': work_id, 'links': links, 'links_revision': row['revision'] if row else 0}

    def update(self, work_id: int, changes: dict[str, str], expected_revision: int) -> dict:
        if not changes or set(changes) - LINK_FIELDS.keys():
            raise WorkLinksError('请选择至少一种有效的链接类型')
        changes = {kind: validate_link(value) for kind, value in changes.items()}
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.state(db, work_id)
            if current['links_revision'] != expected_revision:
                raise WorkLinksError('链接已被其他操作更新，请刷新后重试', 409)
            row = db.execute('SELECT overrides FROM work_links WHERE work_id=?', (work_id,)).fetchone()
            overrides = json.loads(row['overrides']) if row else {}
            overrides.update(changes)
            db.execute('INSERT INTO work_links(work_id,overrides,revision) VALUES(?,?,?) ON CONFLICT(work_id) DO UPDATE SET overrides=excluded.overrides,revision=excluded.revision',
                       (work_id, json.dumps(overrides, ensure_ascii=False), expected_revision + 1))
            db.execute('UPDATE works SET updated_at=? WHERE id=?', (now(), work_id))
            return self.state(db, work_id)
