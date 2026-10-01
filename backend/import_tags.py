"""Explicit, reviewable tag migration; never synchronize metadata or release state."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from .config import normalize_id
from .scanner import rows_of, pick
from .store import Store, now
from .tags import TagError, validate_name, validate_url, validate_selection

FIELDS = {
    "author": ("Creator", "Author", "作者"),
    "video_type": ("Video Type", "video_type", "视频类型"),
    "axis_type": ("Axis Type", "axis_type", "轴类型"),
    "release_type": ("Release Type", "release_type", "发布类型"),
    "tier": ("Slot Type", "Tier", "tier", "档位"),
}
URL_FIELDS = ("Support Creator URL", "Creator URL", "Support URL", "支持作者 URL")
PLACEHOLDERS = {"tbd", "n/a", "na", "none", "null", "unknown", "-", "--", "待定", "待填写", "未填写"}


def clean(value: str) -> str | None:
    value = value.strip()
    return value if value and value.casefold() not in PLACEHOLDERS else None


def tag_key(category: str, name: str) -> str:
    return category + ":" + name.casefold()


def database_state(db) -> tuple[dict, dict, dict, dict]:
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    works = {row["script_id"]: dict(row) for row in db.execute("SELECT id,script_id FROM works")}
    tags = {tag_key(row["category"], row["name"]): dict(row) for row in db.execute("SELECT * FROM tags")} if "tags" in tables else {}
    by_id = {tag["id"]: tag for tag in tags.values()}
    bindings = {}
    if "work_tags" in tables:
        for row in db.execute("SELECT * FROM work_tags"):
            if row["tag_id"] in by_id:
                bindings.setdefault(row["work_id"], []).append({**by_id[row["tag_id"]], "binding_source": row["source"]})
    states = {row["work_id"]: dict(row) for row in db.execute("SELECT * FROM work_tag_state")} if "work_tag_state" in tables else {}
    return works, tags, bindings, states


def plan_import(db, monthly: object, master: object) -> dict:
    works, existing, bindings, states = database_state(db)
    records = {}
    authors = {}
    conflicts, warnings = [], []
    source_ids = {"monthly": set(), "master": set()}
    invalid_rows = 0
    for source, document in (("master", master), ("monthly", monthly)):
        for row_number, row in enumerate(rows_of(document), 1):
            raw_id = pick(row, "Script ID", "script_id", "编号", "ID")
            script_id = normalize_id(raw_id)
            if not script_id:
                if raw_id:
                    invalid_rows += 1
                    warnings.append({"type": "invalid_identifier", "message": f"跳过无法识别的完整编号：{raw_id}", "source": source, "row": row_number})
                continue
            source_ids[source].add(script_id)
            record = records.setdefault(script_id, {})
            values = {}
            for category, names in FIELDS.items():
                value = clean(pick(row, *names))
                if not value:
                    continue
                try:
                    value = validate_name(category, value)
                except TagError as error:
                    conflicts.append({"type": "invalid_field", "script_id": script_id, "category": category,
                                      "values": [value], "sources": [{"source": source, "row": row_number}], "message": str(error)})
                    continue
                evidence = {"source": source, "row": row_number, "script_id": script_id, "value": value}
                record.setdefault(category, []).append(evidence)
                values[category] = value
            author = values.get("author")
            if author:
                creator = authors.setdefault(author.casefold(), {"name": author, "urls": set(), "provenance": []})
                creator["provenance"].append({"source": source, "row": row_number, "script_id": script_id, "name": author})
                raw_url = clean(pick(row, *URL_FIELDS))
                if raw_url:
                    try:
                        url = validate_url(raw_url)
                        creator["urls"].add(url)
                        creator["provenance"][-1]["support_url"] = url
                        if "discuss.eroscripts.com/t/" in url.lower():
                            warnings.append({"type": "support_url_review", "script_id": script_id, "category": "author", "name": author,
                                             "values": [url], "message": "支持作者地址指向 ES 帖子，保留原地址，请人工核对用途"})
                    except TagError as error:
                        warnings.append({"type": "invalid_support_url", "script_id": script_id, "category": "author", "name": author,
                                         "values": [raw_url], "message": str(error)})
    selected = {}
    for script_id, fields in records.items():
        selected[script_id] = {}
        for category, entries in fields.items():
            preferred = "master" if category == "author" else "monthly"
            preferred_entries = [entry for entry in entries if entry["source"] == preferred] or entries
            unique = {entry["value"].casefold(): entry["value"] for entry in preferred_entries}
            all_unique = {entry["value"].casefold(): entry["value"] for entry in entries}
            if len(all_unique) > 1:
                conflicts.append({"type": "field_conflict", "script_id": script_id, "category": category,
                                  "values": sorted(all_unique.values()), "sources": entries,
                                  "message": "优先来源内存在不同值，暂不绑定该分类" if len(unique) > 1 else f"两表字段不同，按 {preferred} 优先采用 {next(iter(unique.values()))}"})
            if len(unique) == 1:
                name = next(iter(unique.values()))
                selected[script_id][category] = {"category": category, "name": name, "key": tag_key(category, name), "provenance": entries}
    tag_plans = {}
    for creator in authors.values():
        key = tag_key("author", creator["name"])
        current = existing.get(key)
        candidates = set(creator["urls"])
        if current and not current["support_manual"]:
            candidates.update(json.loads(current["support_candidates"]))
            if current["support_url"]:
                candidates.add(current["support_url"])
        if len(candidates) > 1:
            conflicts.append({"type": "support_url_conflict", "category": "author", "name": creator["name"],
                              "values": sorted(candidates), "sources": creator["provenance"], "message": "同名作者有多个支持地址，保持未确认，等待人工选择"})
        if current and current["support_manual"]:
            status, url = current["support_status"], current["support_url"]
            visible_candidates = json.loads(current["support_candidates"])
            if candidates and (status != "url" or url not in candidates):
                warnings.append({"type": "manual_support_preserved", "category": "author", "name": current["name"],
                                 "values": sorted(candidates), "message": "保留人工作者支持地址及状态，不按文档覆盖"})
        else:
            status = "url" if len(candidates) == 1 else "unknown"
            url = next(iter(candidates)) if len(candidates) == 1 else None
            visible_candidates = sorted(candidates) if len(candidates) > 1 else []
        changed = current and (status != current["support_status"] or url != current["support_url"] or visible_candidates != json.loads(current["support_candidates"]))
        tag_plans[key] = {"key": key, "category": "author", "name": current["name"] if current else creator["name"],
                          "support_status": status, "support_url": url, "support_candidates": visible_candidates,
                          "provenance": creator["provenance"], "action": "create" if not current else "update" if changed else "unchanged"}
    for script_id, fields in selected.items():
        if script_id not in works:
            continue
        for category, value in fields.items():
            if category == "author":
                continue
            key = value["key"]
            current = existing.get(key)
            tag_plans.setdefault(key, {**value, "support_status": "unknown", "support_url": None, "support_candidates": [],
                                       "action": "unchanged" if current else "create"})
    plans = []
    for script_id in sorted(set(records) | set(works)):
        work = works.get(script_id)
        before = bindings.get(work["id"], []) if work else []
        before_keys = sorted(tag_key(tag["category"], tag["name"]) for tag in before)
        state = states.get(work["id"], {}) if work else {}
        selected_fields = selected.get(script_id, {})
        proposed = sorted(value["key"] for value in selected_fields.values())
        entry = {"script_id": script_id, "work_id": work["id"] if work else None, "before_tags": before_keys,
                 "proposed_tags": proposed, "after_tags": before_keys, "tags_revision": state.get("revision", 0),
                 "fields": selected_fields}
        if work is None:
            entry.update(action="unmatched", reason="没有对应的完整编号库存，保留作者候选，不新建作品")
        elif state.get("manual_edited") or any(tag["binding_source"] == "manual" for tag in before):
            entry.update(action="skip_manual", reason="保留用户维护的标签选择，包括手工清空")
        elif script_id not in records:
            entry.update(action="no_data", reason="两表没有这个完整编号的资料")
        else:
            desired = {tag["category"]: tag_key(tag["category"], tag["name"]) for tag in before if tag["category"] != "custom"}
            desired.update({category: value["key"] for category, value in selected_fields.items()})
            desired_custom = [tag_key(tag["category"], tag["name"]) for tag in before if tag["category"] == "custom"]
            desired_keys = list(desired.values()) + desired_custom
            try:
                validate_selection([tag_plans.get(key, existing.get(key)) for key in desired_keys])
            except TagError as error:
                conflicts.append({"type": "invalid_combination", "script_id": script_id, "category": "release_type/tier", "values": desired_keys, "message": str(error)})
                for category in ("release_type", "tier"):
                    previous = next((tag for tag in before if tag["category"] == category), None)
                    if previous:
                        desired[category] = tag_key(category, previous["name"])
                    else:
                        desired.pop(category, None)
                desired_keys = list(desired.values()) + desired_custom
            after = sorted(desired_keys)
            entry.update(action="bind" if after != before_keys else "unchanged", reason=None, after_tags=after)
        plans.append(entry)
    matched = len(set(records) & set(works))
    skipped = sum(plan["action"] in {"unmatched", "skip_manual", "no_data"} for plan in plans)
    created = sum(tag["action"] == "create" for tag in tag_plans.values())
    added = sum(len(set(plan["after_tags"]) - set(plan["before_tags"])) for plan in plans if plan["action"] == "bind")
    return {"version": 1, "matched": matched, "skipped": skipped, "created": created, "bindings": added,
            "matched_by_source": {source: len(ids & set(works)) for source, ids in source_ids.items()},
            "counts": {"matched": matched, "skipped": skipped, "created": created, "bindings": added, "conflicts": len(conflicts)},
            "conflicts": conflicts, "warnings": warnings, "invalid_rows": invalid_rows, "tag_plans": sorted(tag_plans.values(), key=lambda tag: tag["key"]), "plans": plans}


def import_tags(data_dir: Path, monthly: object, master: object, dry_run=False) -> dict:
    database = data_dir / "workbench.sqlite3"
    if not database.is_file():
        raise ValueError("库存数据库不存在，请先扫描库存并检查 --data-dir")
    if dry_run:
        db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN")
            report = plan_import(db, monthly, master)
            return {**report, "dry_run": True}
        finally:
            db.close()
    store = Store(data_dir)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # Re-read protection state inside the transaction, not from an earlier dry-run.
        report = plan_import(db, monthly, master)
        for plan in report["tag_plans"]:
            if plan["action"] == "create":
                db.execute("INSERT INTO tags(category,name,name_key,support_url,support_status,support_candidates,provenance) VALUES(?,?,?,?,?,?,?)",
                           (plan["category"], plan["name"], plan["name"].casefold(), plan["support_url"], plan["support_status"], json.dumps(plan["support_candidates"]), json.dumps(plan["provenance"], ensure_ascii=False)))
            elif plan["action"] == "update":
                db.execute("UPDATE tags SET support_status=?,support_url=?,support_candidates=?,provenance=?,revision=revision+1 WHERE category=? AND name_key=? AND support_manual=0",
                           (plan["support_status"], plan["support_url"], json.dumps(plan["support_candidates"]), json.dumps(plan["provenance"], ensure_ascii=False), plan["category"], plan["name"].casefold()))
        keys = {tag_key(row["category"], row["name"]): row["id"] for row in db.execute("SELECT id,category,name FROM tags")}
        for plan in report["plans"]:
            if plan["action"] != "bind":
                continue
            db.execute("DELETE FROM work_tags WHERE work_id=? AND tag_id IN (SELECT id FROM tags WHERE category!='duration')", (plan["work_id"],))
            for key in plan["after_tags"]:
                if db.execute("SELECT id FROM tags WHERE id=? AND category='duration'", (keys[key],)).fetchone():
                    continue
                provenance = next((value["provenance"] for value in plan["fields"].values() if value["key"] == key), [])
                db.execute("INSERT INTO work_tags(work_id,tag_id,source,provenance) VALUES(?,?,'import',?)", (plan["work_id"], keys[key], json.dumps(provenance, ensure_ascii=False)))
            db.execute("INSERT INTO work_tag_state(work_id,revision,manual_edited) VALUES(?,?,0) ON CONFLICT(work_id) DO UPDATE SET revision=excluded.revision", (plan["work_id"], plan["tags_revision"] + 1))
        report.update(dry_run=False, imported_at=now())
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('tag_import_report',?)", (json.dumps(report, ensure_ascii=False),))
        return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导入历史分类标签与共享作者地址；不修改作品标题、链接或发布状态")
    parser.add_argument("--monthly", type=Path, required=True)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        monthly = json.loads(args.monthly.read_text(encoding="utf-8-sig"))
        master = json.loads(args.master.read_text(encoding="utf-8-sig"))
        report = import_tags(args.data_dir, monthly, master, args.dry_run)
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
            print(json.dumps({**report["counts"], "dry_run": report["dry_run"], "matched_by_source": report["matched_by_source"],
                              "warnings": len(report["warnings"]), "report": str(args.report)}, ensure_ascii=False))
        else:
            print(output)
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"标签导入失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
