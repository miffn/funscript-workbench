"""A work which lacked scripts stays in production until explicitly confirmed."""
from .work_directory import current_directory
from fastapi import HTTPException


def require_production_confirmation(db, work_id):
    directory = current_directory(db, work_id)
    if directory is not None and not db.execute("SELECT 1 FROM assets WHERE directory_id=? AND kind='script' LIMIT 1", (directory['id'],)).fetchone():
        db.execute('UPDATE works SET production_required=1,production_confirmed_at=NULL,production_revision=production_revision+1 WHERE id=? AND production_required=0', (work_id,))


def reset_production(db, work_id, expected_revision):
    work = db.execute('SELECT * FROM works WHERE id=?', (work_id,)).fetchone()
    if work is None:
        raise HTTPException(404, '作品不存在')
    if work['production_revision'] != expected_revision:
        raise HTTPException(409, '作品制作状态已变化，请刷新后重试')
    if db.execute("SELECT 1 FROM jobs WHERE type IN ('scan','rematch','preview') AND status IN ('queued','running') LIMIT 1").fetchone():
        raise HTTPException(409, '后台任务正在运行，请完成后再调整制作状态')
    db.execute('UPDATE works SET production_required=1,production_confirmed_at=NULL,production_revision=production_revision+1 WHERE id=?', (work_id,))
