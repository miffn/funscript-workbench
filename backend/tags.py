from __future__ import annotations

import json
from urllib.parse import urlsplit

from .store import Store

CATEGORIES = ("author", "video_type", "release_type", "tier", "custom")
SINGLE_CATEGORIES = set(CATEGORIES) - {"custom"}
RELEASE_NAMES = {"free sample": "Free Sample", "paid": "Paid"}
TIER_NAMES = {"free": "Free", "main tier": "Main Tier", "extra tier": "Extra Tier"}
VIDEO_NAMES = {"real": "Real", "anime": "Anime", "3dcg": "3DCG", "vam": "VAM"}


class TagError(ValueError):
    def __init__(self, message: str, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def validate_name(category: str, name: str) -> str:
    if category not in CATEGORIES:
        raise TagError("标签类别无效")
    if not isinstance(name, str):
        raise TagError("标签名称必须是文字")
    name = name.strip()
    if not name or len(name) > 120 or any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise TagError("标签名称应为 1 至 120 个字符，不能包含控制字符")
    enum = RELEASE_NAMES if category == "release_type" else TIER_NAMES if category == "tier" else None
    if enum is not None:
        if name.casefold() not in enum:
            raise TagError("发布类型只支持 Free Sample / Paid" if category == "release_type" else "档位只支持 Free / Main Tier / Extra Tier")
        return enum[name.casefold()]
    return VIDEO_NAMES.get(name.casefold(), name) if category == "video_type" else name


def validate_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TagError("支持作者地址必须是 http(s) URL")
    value = value.strip()
    if not value:
        return None
    if len(value) > 2000 or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise TagError("支持作者地址不能包含空白或控制字符")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise TagError("支持作者地址只允许 http(s)，不能包含用户名或密码")
        parsed.port  # Validate malformed/out-of-range ports and IPv6 syntax.
        if "\\" in parsed.netloc:
            raise TagError("支持作者地址的域名无效")
    except ValueError as error:
        if isinstance(error, TagError):
            raise
        raise TagError("支持作者地址无效") from error
    return value


def support_fields(category: str, changes: dict, current: dict | None = None) -> tuple[str, str | None]:
    current = current or {"support_status": "unknown", "support_url": None}
    if category != "author":
        if changes.get("support_url") not in (None, "") or changes.get("support_status", "unknown") != "unknown":
            raise TagError("只有作者标签可以维护支持地址")
        return "unknown", None
    has_url = "support_url" in changes
    has_status = "support_status" in changes
    url = validate_url(changes["support_url"]) if has_url else current["support_url"]
    status = changes.get("support_status", "url" if url else "unknown") if has_url else changes.get("support_status", current["support_status"])
    if status not in {"unknown", "none", "url"}:
        raise TagError("支持地址状态应为 unknown、none 或 url")
    if has_status and not has_url and status in {"unknown", "none"}:
        url = None
    if status == "url" and not url:
        raise TagError("支持地址状态为 url 时必须填写有效地址")
    if status != "url" and url:
        raise TagError("unknown / none 状态不能同时填写支持地址")
    return status, url


def validate_selection(tags: list[dict]):
    categories = {}
    for tag in tags:
        category = tag["category"]
        if category in SINGLE_CATEGORIES and category in categories:
            raise TagError("作者、视频类型、发布类型和档位每类只能选择一个标签")
        categories[category] = tag["name"]
    release, tier = categories.get("release_type"), categories.get("tier")
    if release and tier and ((release == "Free Sample" and tier != "Free") or (release == "Paid" and tier == "Free")):
        raise TagError("Free Sample 只可搭配 Free；Paid 可搭配 Main Tier / Extra Tier")


class TagService:
    def __init__(self, store: Store):
        self.store = store

    def tag(self, db, tag_id: int) -> dict:
        row = db.execute("SELECT t.*,count(wt.work_id) AS usage_count FROM tags t LEFT JOIN work_tags wt ON wt.tag_id=t.id WHERE t.id=? GROUP BY t.id", (tag_id,)).fetchone()
        if row is None:
            raise TagError("标签不存在", 404)
        result = {key: row[key] for key in ("id", "category", "name", "support_url", "support_status", "revision", "usage_count")}
        result["support_candidates"] = json.loads(row["support_candidates"])
        return result

    def catalog(self) -> dict:
        with self.store.connection() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM tags ORDER BY category,name_key")]
            report = db.execute("SELECT value FROM settings WHERE key='tag_import_report'").fetchone()
            return {"items": [self.tag(db, tag_id) for tag_id in ids], "categories": list(CATEGORIES),
                    "import_report": json.loads(report[0]) if report else None}

    def work_state(self, db, work_id: int) -> dict:
        if not db.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
            raise TagError("库存编号不存在", 404)
        row = db.execute("SELECT * FROM work_tag_state WHERE work_id=?", (work_id,)).fetchone()
        ids = [row[0] for row in db.execute("SELECT wt.tag_id FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=? ORDER BY t.category,t.name_key", (work_id,))]
        return {"work_id": work_id, "tags": [self.tag(db, tag_id) for tag_id in ids], "tags_revision": row["revision"] if row else 0}

    def create(self, changes: dict) -> dict:
        category = changes["category"]
        name = validate_name(category, changes["name"])
        status, url = support_fields(category, changes)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM tags WHERE category=? AND name_key=?", (category, name.casefold())).fetchone():
                raise TagError("该类别中已存在同名标签，请使用已有标签", 409)
            manual = category == "author" and any(key in changes for key in ("support_url", "support_status"))
            tag_id = db.execute("INSERT INTO tags(category,name,name_key,support_url,support_status,support_manual) VALUES(?,?,?,?,?,?)",
                                (category, name, name.casefold(), url, status, int(manual))).lastrowid
            return self.tag(db, tag_id)

    def update(self, tag_id: int, changes: dict) -> dict:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
            if row is None:
                raise TagError("标签不存在", 404)
            current = dict(row)
            if changes["expected_revision"] != current["revision"]:
                raise TagError("标签已被其他客户端修改，请刷新后重试", 409)
            name = validate_name(current["category"], changes.get("name", current["name"]))
            if db.execute("SELECT id FROM tags WHERE category=? AND name_key=? AND id!=?", (current["category"], name.casefold(), tag_id)).fetchone():
                raise TagError("该类别中已存在同名标签", 409)
            status, url = support_fields(current["category"], changes, current)
            # Renaming an enum label may change validity of every linked work.
            if current["category"] in {"release_type", "tier"}:
                for work in db.execute("SELECT work_id FROM work_tags WHERE tag_id=?", (tag_id,)).fetchall():
                    selection = [dict(value) for value in db.execute("SELECT t.* FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=?", (work[0],))]
                    for selected in selection:
                        if selected["id"] == tag_id:
                            selected["name"] = name
                    validate_selection(selection)
            manual = current["support_manual"] or (current["category"] == "author" and any(key in changes for key in ("support_url", "support_status")))
            changed = name != current["name"] or status != current["support_status"] or url != current["support_url"] or manual != current["support_manual"]
            if changed:
                db.execute("UPDATE tags SET name=?,name_key=?,support_status=?,support_url=?,support_manual=?,revision=revision+1 WHERE id=?", (name, name.casefold(), status, url, int(manual), tag_id))
                if name != current["name"]:
                    # Shared renaming is a manual classification decision for linked works.
                    # Protect their association and invalidate stale binding editors atomically.
                    db.execute("INSERT INTO work_tag_state(work_id,revision,manual_edited) SELECT work_id,1,1 FROM work_tags WHERE tag_id=? ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1,manual_edited=1", (tag_id,))
            return self.tag(db, tag_id)

    def replace_work(self, work_id: int, tag_ids: list[int], expected_revision: int) -> dict:
        if len(tag_ids) > 100 or len(set(tag_ids)) != len(tag_ids):
            raise TagError("标签列表不能重复，且最多 100 个")
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self.work_state(db, work_id)
            if expected_revision != state["tags_revision"]:
                raise TagError("库存标签已被其他客户端修改，请刷新后重试", 409)
            selected = [self.tag(db, tag_id) for tag_id in tag_ids]
            validate_selection(selected)
            db.execute("DELETE FROM work_tags WHERE work_id=?", (work_id,))
            db.executemany("INSERT INTO work_tags(work_id,tag_id,source) VALUES(?,?,'manual')", [(work_id, tag_id) for tag_id in tag_ids])
            db.execute("INSERT INTO work_tag_state(work_id,revision,manual_edited) VALUES(?,?,1) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,manual_edited=1", (work_id, state["tags_revision"] + 1))
            return self.work_state(db, work_id)
