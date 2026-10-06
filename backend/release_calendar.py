from __future__ import annotations

import calendar
from datetime import date
import hashlib
import json
import re
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .release_dates import release_today, validate_release_date
from .store import Store, now
from .work_links import WorkLinks


SCHEMA = """
CREATE TABLE IF NOT EXISTS release_calendar_plans (
 work_id INTEGER PRIMARY KEY REFERENCES works(id),
 es_planned_date TEXT, patreon_planned_date TEXT,
 revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS release_calendar_operations (
 id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
 before_state TEXT NOT NULL, after_state TEXT NOT NULL,
 after_revision TEXT NOT NULL, fields TEXT NOT NULL,
 created_at TEXT NOT NULL, undone_at TEXT
);
"""
CALENDAR_FIELDS = ('es_published', 'patreon_published', 'es_published_date',
                   'patreon_published_date', 'es_planned_date', 'patreon_planned_date')


def calendar_date(value):
    if value == '':
        raise ValueError('日历日期必须为 YYYY-MM-DD 或 null')
    return validate_release_date(value)


class CalendarEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    work_id: int = Field(strict=True, gt=0)
    platforms: list[Literal['es', 'patreon']] = Field(min_length=1, max_length=2)
    mode: Literal['actual', 'planned']
    date: Annotated[str | None, BeforeValidator(calendar_date)]
    expected_revision: str = Field(strict=True, min_length=1, max_length=128)

    @model_validator(mode='after')
    def unique_platforms(self):
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError('平台不能重复')
        return self


class UndoEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')


def month_bounds(value: str) -> tuple[str, str]:
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise HTTPException(422, '月份必须为 YYYY-MM')
    try:
        year, month = map(int, value.split('-'))
        first = date(year, month, 1)
        last = date(year, month, calendar.monthrange(year, month)[1])
    except ValueError:
        raise HTTPException(422, '月份不是有效的日历月份') from None
    # Monday through Sunday, including the neighboring month's visible dates.
    start = date.fromordinal(max(1, first.toordinal() - first.weekday()))
    end = date.fromordinal(min(date.max.toordinal(), last.toordinal() + 6 - last.weekday()))
    return start.isoformat(), end.isoformat()


class ReleaseCalendar:
    def __init__(self, store: Store):
        self.store = store
        self.links = WorkLinks(store)
        with store.connection() as db:
            db.executescript(SCHEMA)

    def state(self, db, work_id: int) -> dict:
        row = db.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '库存编号不存在')
        plan = db.execute('SELECT * FROM release_calendar_plans WHERE work_id=?', (work_id,)).fetchone()
        result = {'id': work_id, 'script_id': row['script_id'], 'title': row['title'],
                  **{field: bool(row[field]) if field.endswith('_published') else row[field]
                     for field in CALENDAR_FIELDS if not field.endswith('_planned_date')},
                  **{field: plan[field] if plan else None for field in CALENDAR_FIELDS if field.endswith('_planned_date')}}
        links = self.links.state(db, work_id)
        fingerprint = {field: result[field] for field in CALENDAR_FIELDS}
        fingerprint.update(links=links['links'], links_revision=links['links_revision'],
                           calendar_revision=plan['revision'] if plan else 0)
        result['revision'] = hashlib.sha256(json.dumps(fingerprint, sort_keys=True,
                                                       ensure_ascii=False).encode()).hexdigest()
        return result

    def snapshot(self, db, work_id: int) -> dict:
        state = self.state(db, work_id)
        row = db.execute('SELECT manual_fields FROM works WHERE id=?', (work_id,)).fetchone()
        return {**{field: state[field] for field in CALENDAR_FIELDS},
                'manual_fields': json.loads(row['manual_fields'])}

    def write(self, db, work_id: int, values: dict, manual_fields: set[str]):
        actual = {key: value for key, value in values.items() if not key.endswith('_planned_date')}
        plans = {key: value for key, value in values.items() if key.endswith('_planned_date')}
        db.execute('INSERT INTO release_calendar_plans(work_id,revision) VALUES(?,1) '
                   'ON CONFLICT(work_id) DO UPDATE SET revision=revision+1', (work_id,))
        if plans:
            assignments = ','.join(f'{field}=?' for field in plans)
            db.execute(f'UPDATE release_calendar_plans SET {assignments} WHERE work_id=?',
                       [*plans.values(), work_id])
        if actual:
            assignments = ','.join(f'{field}=?' for field in actual)
            db.execute(f'UPDATE works SET {assignments},manual_fields=?,updated_at=? WHERE id=?',
                       [*actual.values(), json.dumps(sorted(manual_fields)), now(), work_id])
            db.execute("UPDATE works SET status=CASE WHEN es_published=1 AND patreon_published=1 THEN 'published' ELSE 'pending' END WHERE id=?", (work_id,))
            # Existing date/link editors must see calendar changes as a new revision.
            db.execute("INSERT INTO work_links(work_id,overrides,revision) VALUES(?,'{}',1) "
                       'ON CONFLICT(work_id) DO UPDATE SET revision=revision+1', (work_id,))

    def month(self, month: str) -> dict:
        start, end = month_bounds(month)
        with self.store.connection() as db:
            works = [self.state(db, row['id']) for row in
                     db.execute('SELECT id FROM works ORDER BY script_id DESC').fetchall()]
        events = []
        for work in works:
            for platform in ('es', 'patreon'):
                for mode, suffix in (('actual', 'published_date'), ('planned', 'planned_date')):
                    day = work[f'{platform}_{suffix}']
                    if day and start <= day <= end and (mode != 'actual' or work[f'{platform}_published']):
                        events.append({'key': f'{work["id"]}:{platform}:{mode}', 'work_id': work['id'],
                                       'script_id': work['script_id'], 'title': work['title'],
                                       'platform': platform, 'mode': mode, 'date': day,
                                       'published': work[f'{platform}_published'], 'revision': work['revision']})
        events.sort(key=lambda item: (item['date'], item['script_id'] or '', item['work_id'], item['platform'], item['mode']))
        return {'month': month, 'today': release_today(self.store), 'events': events, 'works': works}

    def update(self, edit: CalendarEdit) -> dict:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.state(db, edit.work_id)
            if current['revision'] != edit.expected_revision:
                raise HTTPException(409, '作品的发布信息已更新，请刷新日历后重试')
            before = self.snapshot(db, edit.work_id)
            changes = {}
            for platform in edit.platforms:
                field = f'{platform}_{"published_date" if edit.mode == "actual" else "planned_date"}'
                changes[field] = edit.date
                if edit.mode == 'actual' and edit.date is not None:
                    changes[f'{platform}_published'] = True
            manual = set(before['manual_fields']) | {field for field in changes if not field.endswith('_planned_date')}
            self.write(db, edit.work_id, changes, manual)
            after = self.snapshot(db, edit.work_id)
            work = self.state(db, edit.work_id)
            operation_id = db.execute('INSERT INTO release_calendar_operations(work_id,before_state,after_state,after_revision,fields,created_at) VALUES(?,?,?,?,?,?)',
                                     (edit.work_id, json.dumps(before), json.dumps(after), work['revision'],
                                      json.dumps(list(changes)), now())).lastrowid
            return {'work': work, 'operation': {'id': operation_id, 'undone': False}, 'message': '发布日历已保存'}

    def undo(self, operation_id: int) -> dict:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            operation = db.execute('SELECT * FROM release_calendar_operations WHERE id=?', (operation_id,)).fetchone()
            if operation is None:
                raise HTTPException(404, '日历操作不存在')
            if operation['undone_at']:
                raise HTTPException(409, '这次操作已经撤销')
            work_id = operation['work_id']
            current = self.state(db, work_id)
            if current['revision'] != operation['after_revision']:
                raise HTTPException(409, '发布信息在操作后已改变，不能撤销，请手动编辑当前日期')
            before = json.loads(operation['before_state'])
            fields = json.loads(operation['fields'])
            current_snapshot = self.snapshot(db, work_id)
            touched_manual = {field for field in fields if not field.endswith('_planned_date')}
            manual = ((set(current_snapshot['manual_fields']) - touched_manual)
                      | (set(before['manual_fields']) & touched_manual))
            self.write(db, work_id, {field: before[field] for field in fields}, manual)
            db.execute('UPDATE release_calendar_operations SET undone_at=? WHERE id=?', (now(), operation_id))
            return {'work': self.state(db, work_id), 'operation': {'id': operation_id, 'undone': True},
                    'message': '已撤销此次日历操作'}


def register_release_calendar_routes(app: FastAPI, store: Store):
    service = ReleaseCalendar(store)
    app.state.release_calendar = service

    @app.get('/api/release-calendar')
    def get_calendar(month: str = Query(..., max_length=7)):
        return service.month(month)

    @app.post('/api/release-calendar')
    def update_calendar(edit: CalendarEdit):
        return service.update(edit)

    @app.post('/api/release-calendar/operations/{operation_id}/undo')
    def undo_calendar(operation_id: Annotated[int, Field(gt=0)], edit: UndoEdit):
        return service.undo(operation_id)
