"""A shared time zone for display and newly recorded publication days."""
from functools import lru_cache
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt

DEFAULT_TIMEZONE = 'Asia/Shanghai'


@lru_cache(maxsize=1)
def timezone_choices() -> tuple[str, ...]:
    return tuple(sorted(available_timezones() - {'localtime', 'posixrules', 'Factory'}))


class TimezoneEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timezone: str = Field(strict=True, min_length=1, max_length=120)
    expected_revision: StrictInt = Field(ge=0)


def read_timezone(db) -> dict:
    row = db.execute("SELECT value FROM settings WHERE key='workspace_timezone'").fetchone()
    return json.loads(row[0]) if row else {'timezone': DEFAULT_TIMEZONE, 'revision': 0}


def workspace_timezone(store=None) -> str:
    if store is None:
        return DEFAULT_TIMEZONE
    with store.connection() as db:
        return read_timezone(db)['timezone']


def register_timezone_routes(app, store):
    def response(saved):
        return {**saved, 'choices': timezone_choices()}

    @app.get('/api/settings/timezone')
    def timezone():
        with store.connection() as db:
            return response(read_timezone(db))

    @app.put('/api/settings/timezone')
    def save_timezone(options: TimezoneEdit):
        if options.timezone not in timezone_choices():
            raise HTTPException(422, '请选择有效的 IANA 时区')
        try:
            ZoneInfo(options.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise HTTPException(422, '请选择有效的 IANA 时区') from None
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = read_timezone(db)
            if current['revision'] != options.expected_revision:
                raise HTTPException(409, '工作台时区已被其他页面修改，请重新读取后再保存')
            saved = {'timezone': options.timezone, 'revision': current['revision'] + 1}
            db.execute("INSERT INTO settings(key,value) VALUES('workspace_timezone',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(saved),))
            return response(saved)
