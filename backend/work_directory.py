"""Select one source directory; discovery history is never a second binding."""


def current_directory(db, work_id: int):
    rows = db.execute(
        'SELECT * FROM directories WHERE work_id=? ORDER BY last_seen DESC,id DESC',
        (work_id,),
    ).fetchall()
    active = [row for row in rows if row['available']]
    if len(active) > 1:
        # Never merge files from conflicting copies or choose one arbitrarily.
        return None
    return active[0] if active else rows[0] if rows else None
