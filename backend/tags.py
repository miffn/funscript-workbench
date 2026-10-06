from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from .store import Store
from .work_directory import current_directory

CATEGORIES = ("author", "video_type", "axis_type", "release_type", "tier", "duration", "custom")
CATEGORY_LABELS = dict(zip(CATEGORIES, ('作者', '视频类型', '轴类型', '发布类型', '档位', '时间', '自定义分类')))
SINGLE_CATEGORIES = set(CATEGORIES) - {"custom"}
RELEASE_NAMES = {"free sample": "Free Sample", "paid": "Paid"}
TIER_NAMES = {"free": "Free", "main tier": "Main Tier", "extra tier": "Extra Tier"}
VIDEO_NAMES = {"real": "Real", "anime": "Anime", "3dcg": "3DCG", "vam": "VAM"}
AXIS_NAMES = {"single-axis": "单轴", "single axis": "单轴", "multi-axis": "多轴", "multi axis": "多轴"}
COLOR_FIELDS = ('color_light', 'color_dark')


def sync_axis_tag(db, work_id: int, initialize=False):
    """Refresh an existing axis classification from the current source only."""
    state = db.execute("SELECT manual_edited FROM work_tag_state WHERE work_id=?", (work_id,)).fetchone()
    current = db.execute("SELECT t.id,t.name,wt.source FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? AND t.category='axis_type' AND t.deleted=0", (work_id,)).fetchone()
    if current and initialize:
        return
    # An explicit removal stays removed. Editing other categories must not freeze
    # an existing axis tag, including tags from the historical import/editor.
    if not initialize and not current and state and state[0]:
        return
    directory = current_directory(db, work_id)
    if directory is None and not initialize:
        return
    if not initialize and (not directory['available'] or db.execute(
        "SELECT 1 FROM issues WHERE work_id=? AND type='directory_unreadable'", (work_id,),
    ).fetchone()):
        return
    axes = {row[0] for row in db.execute("SELECT axis FROM assets WHERE directory_id=? AND kind='script' AND axis IS NOT NULL", (directory['id'],))} if directory is not None and directory['available'] else set()
    if current and not axes:
        return
    metadata = json.loads(db.execute("SELECT metadata FROM works WHERE id=?", (work_id,)).fetchone()[0])
    historical = metadata.get("axis_type") or metadata.get("Axis Type") or metadata.get("轴类型")
    name = ("多轴" if len(axes) > 1 else "单轴") if axes else historical
    if not isinstance(name, str) or not name.strip():
        return
    name = validate_name("axis_type", name)
    if current and current["name"] == name:
        return
    db.execute("INSERT OR IGNORE INTO tags(category,name,name_key) VALUES('axis_type',?,?)", (name, name.casefold()))
    target = db.execute("SELECT id,deleted FROM tags WHERE category='axis_type' AND name_key=?", (name.casefold(),)).fetchone()
    if target['deleted']:
        if current:
            db.execute('DELETE FROM work_tags WHERE work_id=? AND tag_id=?', (work_id, current['id']))
            db.execute('INSERT INTO work_tag_state(work_id,revision) VALUES(?,1) ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1', (work_id,))
        return
    tag_id = target['id']
    if current:
        db.execute("DELETE FROM work_tags WHERE work_id=? AND tag_id=?", (work_id, current["id"]))
    db.execute("INSERT INTO work_tags(work_id,tag_id,source) VALUES(?,?,?)", (work_id, tag_id, "scan" if axes else "import"))
    db.execute("INSERT INTO work_tag_state(work_id,revision) VALUES(?,1) ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1", (work_id,))


class TagError(ValueError):
    def __init__(self, message: str, status_code=422):
        super().__init__(message)
        self.status_code = status_code


def validate_color(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise TagError('标签文字颜色必须为 #RRGGBB 或 null')
    return value.upper()


def validate_bold(value: bool | None) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise TagError('标签加粗必须为 true、false 或 null')
    return value


def custom_category_id(category):
    match = re.fullmatch(r'custom_([1-9][0-9]{0,18})', category) if isinstance(category, str) else None
    return int(match[1]) if match and int(match[1]) <= 9223372036854775807 else None


def validate_name(category: str, name: str) -> str:
    if category not in CATEGORIES and custom_category_id(category) is None:
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
    if category == "axis_type":
        return AXIS_NAMES.get(name.casefold(), name)
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
            raise TagError("作者、视频类型、轴类型、发布类型和档位每类只能选择一个标签")
        categories[category] = tag["name"]
    release, tier = categories.get("release_type"), categories.get("tier")
    if release and tier and ((release == "Free Sample" and tier != "Free") or (release == "Paid" and tier == "Free")):
        raise TagError("Free Sample 只可搭配 Free；Paid 可搭配 Main Tier / Extra Tier")


class TagService:
    def __init__(self, store: Store):
        self.store = store
        with store.connection() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='axis_tags_initialized'").fetchone():
                return
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM settings WHERE key='axis_tags_initialized'").fetchone():
                for work in db.execute("SELECT id FROM works").fetchall():
                    sync_axis_tag(db, work[0], initialize=True)
                db.execute("INSERT INTO settings(key,value) VALUES('axis_tags_initialized','true')")

    def category_definitions(self, db) -> list[dict]:
        return [{'category': category, 'name': CATEGORY_LABELS[category], 'is_custom': False, 'revision': 0} for category in CATEGORIES] + [
            {'category': f'custom_{row["id"]}', 'name': row['name'], 'is_custom': True, 'revision': row['revision']}
            for row in db.execute('SELECT * FROM tag_categories ORDER BY id')]

    def require_category(self, db, category):
        if category in CATEGORIES:
            return
        category_id = custom_category_id(category)
        if category_id is None or not db.execute('SELECT 1 FROM tag_categories WHERE id=?', (category_id,)).fetchone():
            raise TagError('标签类别不存在')

    def create_category(self, name: str) -> dict:
        name = validate_name('custom', name)
        if name.casefold() in {label.casefold() for label in CATEGORY_LABELS.values()}:
            raise TagError('该类别名称已由系统使用')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM tag_categories WHERE name_key=?', (name.casefold(),)).fetchone():
                raise TagError('已存在同名标签类别', 409)
            category_id = db.execute('INSERT INTO tag_categories(name,name_key) VALUES(?,?)', (name, name.casefold())).lastrowid
            category = f'custom_{category_id}'
            db.execute('INSERT INTO tag_category_styles(category) VALUES(?)', (category,))
            return {'category': category, 'name': name, 'is_custom': True, 'revision': 1}

    def rename_category(self, category: str, name: str, expected_revision: int) -> dict:
        name = validate_name('custom', name)
        if name.casefold() in {label.casefold() for label in CATEGORY_LABELS.values()}:
            raise TagError('该类别名称已由系统使用')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.require_category(db, category)
            category_id = custom_category_id(category)
            if category_id is None:
                raise TagError('系统标签类别不能重命名')
            row = db.execute('SELECT * FROM tag_categories WHERE id=?', (category_id,)).fetchone()
            if row['revision'] != expected_revision:
                raise TagError('标签类别已被其他客户端修改，请刷新后重试', 409)
            if db.execute('SELECT 1 FROM tag_categories WHERE name_key=? AND id!=?', (name.casefold(), category_id)).fetchone():
                raise TagError('已存在同名标签类别', 409)
            if name != row['name']:
                db.execute('UPDATE tag_categories SET name=?,name_key=?,revision=revision+1 WHERE id=?', (name, name.casefold(), category_id))
            current = db.execute('SELECT * FROM tag_categories WHERE id=?', (category_id,)).fetchone()
            return {'category': category, 'name': current['name'], 'is_custom': True, 'revision': current['revision']}

    def category_styles(self, db, definitions=None) -> dict[str, dict]:
        rows = {row['category']: dict(row) for row in db.execute('SELECT * FROM tag_category_styles')}
        return {category: {**rows.get(category, {'category': category, 'color_light': None,
                                                'color_dark': None, 'bold': None, 'revision': 0}),
                           'bold': bool(rows[category]['bold']) if category in rows and rows[category]['bold'] is not None else None}
                for category in (item['category'] for item in (definitions or self.category_definitions(db)))}

    def category_style(self, db, category: str) -> dict:
        self.require_category(db, category)
        return self.category_styles(db)[category]

    def update_category_style(self, category: str, changes: dict) -> dict:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.category_style(db, category)
            if changes['expected_revision'] != current['revision']:
                raise TagError('类型样式已被其他客户端修改，请刷新后重试', 409)
            colors = [validate_color(changes.get(field, current[field])) for field in COLOR_FIELDS]
            bold = validate_bold(changes.get('bold', current['bold']))
            if colors != [current[field] for field in COLOR_FIELDS] or bold != current['bold']:
                db.execute('INSERT INTO tag_category_styles(category,color_light,color_dark,bold,revision) VALUES(?,?,?,?,?) '
                           'ON CONFLICT(category) DO UPDATE SET color_light=excluded.color_light,color_dark=excluded.color_dark,'
                           'bold=excluded.bold,revision=excluded.revision',
                           (category, *colors, bold, current['revision'] + 1))
            return self.category_style(db, category)

    def tag(self, db, tag_id: int, category_styles: dict | None = None, category_labels=None, include_deleted=False) -> dict:
        row = db.execute("SELECT t.*,count(wt.work_id) AS usage_count FROM tags t LEFT JOIN work_tags wt ON wt.tag_id=t.id WHERE t.id=? GROUP BY t.id", (tag_id,)).fetchone()
        if row is None or row['deleted'] and not include_deleted:
            raise TagError("标签不存在", 404)
        result = {key: row[key] for key in ("id", "category", "name", "support_url", "support_status", "revision", "usage_count", *COLOR_FIELDS)}
        result['bold'] = bool(row['bold']) if row['bold'] is not None else None
        result['category_style'] = (category_styles or self.category_styles(db))[row['category']]
        labels = category_labels or {item['category']: item['name'] for item in self.category_definitions(db)}
        result['category_label'] = labels[row['category']]
        result['deleted'] = bool(row['deleted'])
        result["support_candidates"] = json.loads(row["support_candidates"])
        return result

    def catalog(self, include_deleted=False) -> dict:
        with self.store.connection() as db:
            definitions = self.category_definitions(db)
            styles = self.category_styles(db, definitions)
            labels = {item['category']: item['name'] for item in definitions}
            ids = [row[0] for row in db.execute('SELECT id FROM tags' + ('' if include_deleted else ' WHERE deleted=0') + ' ORDER BY category,name_key')]
            report = db.execute("SELECT value FROM settings WHERE key='tag_import_report'").fetchone()
            return {"items": [self.tag(db, tag_id, styles, labels, include_deleted) for tag_id in ids], "categories": list(labels),
                    'category_definitions': definitions,
                    'category_styles': list(styles.values()),
                    "import_report": json.loads(report[0]) if report else None}

    def work_state(self, db, work_id: int) -> dict:
        if not db.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
            raise TagError("库存编号不存在", 404)
        row = db.execute("SELECT * FROM work_tag_state WHERE work_id=?", (work_id,)).fetchone()
        ids = [row[0] for row in db.execute("SELECT wt.tag_id FROM work_tags wt JOIN tags t ON t.id=wt.tag_id WHERE wt.work_id=? AND t.deleted=0 ORDER BY t.category,t.name_key", (work_id,))]
        definitions = self.category_definitions(db)
        styles = self.category_styles(db, definitions)
        labels = {item['category']: item['name'] for item in definitions}
        return {"work_id": work_id, "tags": [self.tag(db, tag_id, styles, labels) for tag_id in ids], "tags_revision": row["revision"] if row else 0}

    def create(self, changes: dict) -> dict:
        category = changes["category"]
        if category == 'duration':
            raise TagError('时间标签由源视频自动计算，不能手动创建')
        name = validate_name(category, changes["name"])
        status, url = support_fields(category, changes)
        colors = [validate_color(changes.get(field)) for field in COLOR_FIELDS]
        bold = validate_bold(changes.get('bold'))
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.require_category(db, category)
            if db.execute("SELECT id FROM tags WHERE category=? AND name_key=?", (category, name.casefold())).fetchone():
                raise TagError("该类别中已存在同名标签，请使用已有标签", 409)
            manual = category == "author" and any(key in changes for key in ("support_url", "support_status"))
            tag_id = db.execute("INSERT INTO tags(category,name,name_key,support_url,support_status,support_manual,color_light,color_dark,bold) VALUES(?,?,?,?,?,?,?,?,?)",
                                (category, name, name.casefold(), url, status, int(manual), *colors, bold)).lastrowid
            return self.tag(db, tag_id)

    def update(self, tag_id: int, changes: dict) -> dict:
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
            if row is None:
                raise TagError("标签不存在", 404)
            current = dict(row)
            if current['deleted']:
                raise TagError('标签已删除，请先恢复', 409)
            if current['category'] == 'duration':
                raise TagError('时间标签由源视频自动计算，不能手动修改')
            if changes["expected_revision"] != current["revision"]:
                raise TagError("标签已被其他客户端修改，请刷新后重试", 409)
            name = validate_name(current["category"], changes.get("name", current["name"]))
            if db.execute("SELECT id FROM tags WHERE category=? AND name_key=? AND id!=?", (current["category"], name.casefold(), tag_id)).fetchone():
                raise TagError("该类别中已存在同名标签", 409)
            status, url = support_fields(current["category"], changes, current)
            colors = [validate_color(changes.get(field, current[field])) for field in COLOR_FIELDS]
            colors_changed = any(value != current[field] for field, value in zip(COLOR_FIELDS, colors))
            current_bold = bool(current['bold']) if current['bold'] is not None else None
            bold = validate_bold(changes.get('bold', current_bold))
            appearance_changed = colors_changed or bold != current_bold
            # Renaming an enum label may change validity of every linked work.
            if current["category"] in {"release_type", "tier"} and name != current["name"]:
                for work in db.execute("SELECT work_id FROM work_tags WHERE tag_id=?", (tag_id,)).fetchall():
                    selection = [dict(value) for value in db.execute("SELECT t.* FROM tags t JOIN work_tags wt ON wt.tag_id=t.id WHERE wt.work_id=? AND t.deleted=0", (work[0],))]
                    for selected in selection:
                        if selected["id"] == tag_id:
                            selected["name"] = name
                    validate_selection(selection)
            manual = current["support_manual"] or (current["category"] == "author" and any(key in changes for key in ("support_url", "support_status")))
            changed = name != current["name"] or status != current["support_status"] or url != current["support_url"] or manual != current["support_manual"] or appearance_changed
            if changed:
                db.execute("UPDATE tags SET name=?,name_key=?,support_status=?,support_url=?,support_manual=?,color_light=?,color_dark=?,bold=?,revision=revision+1 WHERE id=?", (name, name.casefold(), status, url, int(manual), *colors, bold, tag_id))
                if name != current["name"]:
                    # Shared renaming is a manual classification decision for linked works.
                    # Protect their association and invalidate stale binding editors atomically.
                    db.execute("INSERT INTO work_tag_state(work_id,revision,manual_edited) SELECT work_id,1,1 FROM work_tags WHERE tag_id=? ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1,manual_edited=1", (tag_id,))
                elif appearance_changed:
                    # Cosmetic edits invalidate readers without freezing scanned classifications.
                    db.execute("INSERT INTO work_tag_state(work_id,revision) SELECT work_id,1 FROM work_tags WHERE tag_id=? ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1", (tag_id,))
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
            automatic = {tag['id']: tag for tag in state['tags'] if tag['category'] == 'duration'}
            if any(tag['category'] == 'duration' and tag['id'] not in automatic for tag in selected):
                raise TagError('不能手动绑定时间标签，请扫描或重新匹配文件')
            # Editors may omit read-only tags; preserve the calculated association.
            selected = [tag for tag in selected if tag['category'] != 'duration']
            validate_selection(selected)
            tag_ids = [tag['id'] for tag in selected]
            db.execute("DELETE FROM work_tags WHERE work_id=? AND tag_id IN (SELECT id FROM tags WHERE category!='duration' AND deleted=0)", (work_id,))
            db.executemany("INSERT INTO work_tags(work_id,tag_id,source) VALUES(?,?,'manual')", [(work_id, tag_id) for tag_id in tag_ids])
            db.execute("INSERT INTO work_tag_state(work_id,revision,manual_edited) VALUES(?,?,1) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision,manual_edited=1", (work_id, state["tags_revision"] + 1))
            return self.work_state(db, work_id)

    def set_deleted(self, tag_id: int, deleted: bool, expected_revision: int) -> dict:
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            tag = self.tag(db, tag_id, include_deleted=True)
            if tag['revision'] != expected_revision:
                raise TagError('标签已被其他客户端修改，请刷新后重试', 409)
            works = [row[0] for row in db.execute('SELECT work_id FROM work_tags WHERE tag_id=?', (tag_id,))]
            if tag['deleted'] != deleted:
                if not deleted:
                    for work_id in works:
                        selected = [dict(row) for row in db.execute('SELECT t.* FROM tags t JOIN work_tags wt ON wt.tag_id=t.id '
                                                                    'WHERE wt.work_id=? AND t.deleted=0', (work_id,))]
                        try:
                            validate_selection([*selected, tag])
                        except TagError as error:
                            raise TagError(f'恢复标签会与现有分类冲突，请先调整关联作品标签：{error}', 409) from error
                db.execute('UPDATE tags SET deleted=?,revision=revision+1 WHERE id=?', (int(deleted), tag_id))
                db.execute('INSERT INTO work_tag_state(work_id,revision) SELECT work_id,1 FROM work_tags WHERE tag_id=? '
                           'ON CONFLICT(work_id) DO UPDATE SET revision=work_tag_state.revision+1', (tag_id,))
            return {'tag': self.tag(db, tag_id, include_deleted=True), 'affected_work_count': len(works)}
