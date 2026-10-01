"""Workspace identity kept in the existing SQLite settings table."""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
import json
import warnings

from fastapi import FastAPI, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

PROFILE_KEY = "workspace_profile"
DEFAULT_PROFILE = {"name": "Funscript", "bio": "脚本工作台", "avatar": None, "revision": 0}
MAX_AVATAR_DATA_URL = 400 * 1024
MAX_AVATAR_PIXELS = 4_000_000
AVATAR_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(max_length=80)
    bio: StrictStr = Field(max_length=160)
    avatar: StrictStr | None = Field(max_length=MAX_AVATAR_DATA_URL)
    expected_revision: StrictInt = Field(ge=0)

    @field_validator("name", "bio")
    @classmethod
    def trim_text(cls, value: str, info):
        value = value.strip()
        if info.field_name == "name" and not value:
            raise ValueError("姓名不能为空")
        return value

    @field_validator("avatar")
    @classmethod
    def validate_avatar(cls, value: str | None):
        if value is None:
            return None
        prefix, separator, encoded = value.partition(",")
        mime = prefix.removeprefix("data:").removesuffix(";base64")
        if not separator or prefix != f"data:{mime};base64" or mime not in AVATAR_TYPES:
            raise ValueError("头像仅支持 PNG、JPEG 或 WebP 图片")
        try:
            content = base64.b64decode(encoded, validate=True)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(content)) as image:
                    width, height = image.size
                    if image.format != AVATAR_TYPES[mime] or not (1 <= width <= 4096 and 1 <= height <= 4096) or width * height > MAX_AVATAR_PIXELS:
                        raise ValueError("头像格式不匹配或尺寸过大，请使用不超过 400 万像素的图片")
                    image.verify()
                with Image.open(BytesIO(content)) as image:
                    image.load()
        except (binascii.Error, OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("头像图片无法读取，请重新选择图片") from exc
        return value


def read_profile(db) -> dict:
    row = db.execute("SELECT value FROM settings WHERE key=?", (PROFILE_KEY,)).fetchone()
    return json.loads(row["value"]) if row else dict(DEFAULT_PROFILE)


def register_profile_routes(app: FastAPI, store) -> None:
    @app.get("/api/profile")
    def get_profile():
        with store.connection() as db:
            return read_profile(db)

    @app.put("/api/profile")
    def save_profile(update: ProfileUpdate):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = read_profile(db)
            if current["revision"] != update.expected_revision:
                raise HTTPException(409, "工作台资料已被其他页面修改，请重新加载资料后再保存；当前输入已保留")
            saved = {"name": update.name, "bio": update.bio, "avatar": update.avatar, "revision": current["revision"] + 1}
            db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (PROFILE_KEY, json.dumps(saved, ensure_ascii=False, separators=(",", ":"))))
            return saved
