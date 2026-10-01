from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
 id INTEGER PRIMARY KEY, script_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','published')),
 es_published INTEGER NOT NULL DEFAULT 0 CHECK(es_published IN (0,1)),
 patreon_published INTEGER NOT NULL DEFAULT 0 CHECK(patreon_published IN (0,1)),
 es_published_date TEXT, patreon_published_date TEXT,
 notes TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}', manual_fields TEXT NOT NULL DEFAULT '[]',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS directories (
 id INTEGER PRIMARY KEY, work_id INTEGER NOT NULL REFERENCES works(id),
 path TEXT NOT NULL, windows_path TEXT NOT NULL, root_path TEXT NOT NULL,
 name TEXT NOT NULL, available INTEGER NOT NULL DEFAULT 1, last_seen TEXT,
 UNIQUE(work_id,path)
);
CREATE TABLE IF NOT EXISTS assets (
 id INTEGER PRIMARY KEY, directory_id INTEGER NOT NULL REFERENCES directories(id),
 name TEXT NOT NULL, relative_path TEXT NOT NULL, kind TEXT NOT NULL,
 axis TEXT, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
 UNIQUE(directory_id,relative_path)
);
CREATE TABLE IF NOT EXISTS covers (
 work_id INTEGER PRIMARY KEY REFERENCES works(id), fingerprint TEXT NOT NULL,
 path TEXT, error TEXT, source_path TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS issues (
 id INTEGER PRIMARY KEY, type TEXT NOT NULL, message TEXT NOT NULL,
 script_id TEXT, work_id INTEGER REFERENCES works(id), paths TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, type TEXT NOT NULL DEFAULT 'scan', status TEXT NOT NULL,
 trigger TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
 progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 result TEXT, error TEXT, inputs TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS history (
 script_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, status TEXT NOT NULL,
 applied INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags (
 id INTEGER PRIMARY KEY,
 category TEXT NOT NULL CHECK(category IN ('author','video_type','axis_type','release_type','tier','custom')),
 name TEXT NOT NULL, name_key TEXT NOT NULL,
 support_url TEXT,
 support_status TEXT NOT NULL DEFAULT 'unknown' CHECK(support_status IN ('unknown','none','url')),
 support_candidates TEXT NOT NULL DEFAULT '[]',
 support_manual INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1,
 provenance TEXT NOT NULL DEFAULT '[]',
 UNIQUE(category,name_key),
 CHECK((support_status='url' AND support_url IS NOT NULL) OR (support_status IN ('unknown','none') AND support_url IS NULL))
);
CREATE TABLE IF NOT EXISTS work_tags (
 work_id INTEGER NOT NULL REFERENCES works(id),
 tag_id INTEGER NOT NULL REFERENCES tags(id),
 source TEXT NOT NULL DEFAULT 'manual',
 provenance TEXT NOT NULL DEFAULT '[]',
 PRIMARY KEY(work_id,tag_id)
);
CREATE TABLE IF NOT EXISTS work_tag_state (
 work_id INTEGER PRIMARY KEY REFERENCES works(id),
 revision INTEGER NOT NULL DEFAULT 0,
 manual_edited INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS preview_bindings (
 work_id INTEGER NOT NULL REFERENCES works(id),
 video_path TEXT NOT NULL,
 video_asset_id INTEGER,
 scripts TEXT NOT NULL DEFAULT '{}',
 revision INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(work_id,video_path)
);
CREATE TABLE IF NOT EXISTS work_links (
 work_id INTEGER PRIMARY KEY REFERENCES works(id),
 overrides TEXT NOT NULL DEFAULT '{}',
 revision INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_assets_directory ON assets(directory_id);
CREATE INDEX IF NOT EXISTS idx_directories_work ON directories(work_id);
CREATE INDEX IF NOT EXISTS idx_issues_work ON issues(work_id);
CREATE INDEX IF NOT EXISTS idx_work_tags_tag ON work_tags(tag_id);
"""


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "workbench.sqlite3"
        with self.connection() as db:
            db.executescript(SCHEMA)
            tag_schema = db.execute("SELECT sql FROM sqlite_master WHERE name='tags'").fetchone()[0]
            if "'axis_type'" not in tag_schema:
                # Rebuild only the category constraint; preserve IDs, bindings and revisions.
                db.execute("PRAGMA foreign_keys=OFF")
                db.execute("BEGIN IMMEDIATE")
                try:
                    db.execute(tag_schema.replace("CREATE TABLE tags", "CREATE TABLE tags_expanded", 1).replace("'video_type'", "'video_type','axis_type'", 1))
                    db.execute("INSERT INTO tags_expanded SELECT * FROM tags")
                    db.execute("DROP TABLE tags")
                    db.execute("ALTER TABLE tags_expanded RENAME TO tags")
                    if db.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError("标签迁移发现无效关联")
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
                finally:
                    db.execute("PRAGMA foreign_keys=ON")
            if "inputs" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN inputs TEXT NOT NULL DEFAULT '{}'")
            if 'video_asset_id' not in {row[1] for row in db.execute('PRAGMA table_info(preview_bindings)')}:
                db.execute('ALTER TABLE preview_bindings ADD COLUMN video_asset_id INTEGER')
            # Migrate each platform once; subsequent restarts preserve manual choices.
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            work_columns = {row[1] for row in db.execute('PRAGMA table_info(works)')}
            # Existing links cannot establish the actual release date; leave it unknown.
            for field in ('es_published_date', 'patreon_published_date'):
                if field not in work_columns:
                    db.execute(f'ALTER TABLE works ADD COLUMN {field} TEXT')
            for field in ('es_published', 'patreon_published'):
                if field not in work_columns:
                    db.execute(f'ALTER TABLE works ADD COLUMN {field} INTEGER NOT NULL DEFAULT 0 CHECK({field} IN (0,1))')
                    db.execute(f"UPDATE works SET {field}=(status='published')")
            if 'es_published' not in work_columns:
                from .work_links import WorkLinks, WorkLinksError, validate_link
                links = WorkLinks(self)
                for work in db.execute('SELECT id FROM works').fetchall():
                    try:
                        es_url = validate_link(links.state(db, work['id'])['links']['es'])
                    except WorkLinksError:
                        continue
                    if es_url:
                        db.execute('UPDATE works SET es_published=1 WHERE id=?', (work['id'],))
            if not {'es_published', 'patreon_published'} <= work_columns:
                db.execute("UPDATE works SET status=CASE WHEN es_published=1 AND patreon_published=1 THEN 'published' ELSE 'pending' END")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def job(self, job_id: int) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["result"]:
            result["result"] = json.loads(result["result"])
        result["inputs"] = json.loads(result["inputs"])
        return result

    def issue(self, db, kind: str, message: str, script_id=None, work_id=None, paths=()):
        db.execute("INSERT INTO issues(type,message,script_id,work_id,paths) VALUES(?,?,?,?,?)",
                   (kind, message, script_id, work_id, json.dumps(list(paths), ensure_ascii=False)))
