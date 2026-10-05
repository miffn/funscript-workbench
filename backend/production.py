"""A work which lacked scripts stays in production until explicitly confirmed."""
from .work_directory import current_directory


def require_production_confirmation(db, work_id):
    directory = current_directory(db, work_id)
    if directory is not None and not db.execute("SELECT 1 FROM assets WHERE directory_id=? AND kind='script' LIMIT 1", (directory['id'],)).fetchone():
        db.execute('UPDATE works SET production_required=1,production_confirmed_at=NULL,production_revision=production_revision+1 WHERE id=? AND production_required=0', (work_id,))
