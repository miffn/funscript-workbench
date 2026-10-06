from __future__ import annotations

from datetime import date, datetime
import re
from zoneinfo import ZoneInfo
from .timezone import workspace_timezone


RELEASE_DATE_FIELDS = ('es_published_date', 'patreon_published_date')


def validate_release_date(value: object) -> str | None:
    if value is None or value == '':
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('发布日期必须为 YYYY-MM-DD，清空可使用空字符串或 null')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ValueError('发布日期不是有效的日历日期') from None
    return value


def release_today(store=None) -> str:
    return datetime.now(ZoneInfo(workspace_timezone(store))).date().isoformat()
