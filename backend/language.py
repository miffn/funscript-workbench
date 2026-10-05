"""Shared interface language stored independently of inventory and user content."""
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt


class LanguageEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    language: Literal['zh-CN', 'en']
    expected_revision: StrictInt = Field(ge=0)


def read_language(db):
    row = db.execute("SELECT value FROM settings WHERE key='interface_language'").fetchone()
    return json.loads(row[0]) if row else {'language': 'zh-CN', 'revision': 0}


def register_language_routes(app, store):
    @app.get('/api/settings/language')
    def language():
        with store.connection() as db:
            return read_language(db)

    @app.put('/api/settings/language')
    def save_language(options: LanguageEdit):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = read_language(db)
            if current['revision'] != options.expected_revision:
                raise HTTPException(409, '界面语言已被其他页面修改，请重新加载后再试')
            saved = {'language': options.language, 'revision': current['revision'] + 1}
            db.execute("INSERT INTO settings(key,value) VALUES('interface_language',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (json.dumps(saved),))
            return saved
