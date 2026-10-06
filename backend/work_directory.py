"""Select one source directory; discovery history is never a second binding."""
import os
from pathlib import Path



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


def association_signature(db, work_id):
    work = db.execute('SELECT script_id FROM works WHERE id=?', (work_id,)).fetchone()
    active = [tuple(row) for row in db.execute('SELECT path FROM directories WHERE work_id=? AND available=1 ORDER BY path', (work_id,))]
    return (work['script_id'] if work else None, tuple(active))


def invalidate_association(db, work_id):
    """Keep generated-media identity fixed while invalidating source selections."""
    work = db.execute('SELECT script_id,preview_key FROM works WHERE id=?', (work_id,)).fetchone()
    key = work['preview_key'] or work['script_id'] or f'work-{work_id}'
    if db.execute('SELECT 1 FROM works WHERE preview_key=? AND id!=?', (key, work_id)).fetchone():
        key = f'work-{work_id}'
    db.execute('UPDATE works SET preview_key=?,preview_stale=1 WHERE id=?', (key, work_id))
    db.execute('DELETE FROM preview_bindings WHERE work_id=?', (work_id,))


def directory_status(db, work_id, roots, *, check_filesystem=True, unavailable_roots=()):
    """Lists use scan results; details/actions can validate the current directory live."""
    rows = db.execute('SELECT * FROM directories WHERE work_id=?', (work_id,)).fetchall()
    if len([row for row in rows if row['available']]) > 1:
        return 'conflict'
    directory = current_directory(db, work_id)
    if directory is None:
        return 'unlinked'
    root = next((root for root in roots if str(root.path) == directory['root_path']), None)
    if root is None:
        return 'unavailable'
    if not check_filesystem:
        if directory['root_path'] in unavailable_roots:
            return 'unavailable'
        return 'available' if directory['available'] else 'missing'
    try:
        from .scan_roots import no_link_components, ScanRootsError
        no_link_components(root.path)
        # Verify read permission, rather than confusing an inaccessible mount with a missing work.
        with os.scandir(root.path) as children:
            next(children, None)
        path = Path(directory['path'])
        no_link_components(path)
        path.resolve().relative_to(root.path.resolve())
        return 'available' if path.is_dir() else 'missing'
    except (OSError, ValueError, ScanRootsError):
        return 'unavailable'
