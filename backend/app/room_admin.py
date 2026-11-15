"""Room lifecycle and authenticated account controls."""
import shutil
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from . import storage
from .config import settings


def install(app, authenticate):
    router = APIRouter()

    class Profile(BaseModel):
        display_name: str = Field(min_length=2, max_length=48)

    @router.patch('/api/me')
    async def profile(data: Profile, person=Depends(authenticate)):
        name = ' '.join(data.display_name.split())
        if len(name) < 2:
            raise HTTPException(400, 'Name must contain at least two characters')
        with storage.connection() as db:
            db.execute('UPDATE participants SET display_name=? WHERE id=?', (name, person['id']))
        return storage.get_participant(person['id'])

    @router.post('/api/logout')
    async def logout(person=Depends(authenticate)):
        with storage.connection() as db:
            db.execute('DELETE FROM api_sessions WHERE participant_id=?', (person['id'],))
        return {'ok': True}

    @router.post('/api/rooms/{room_id}/archive')
    async def archive(room_id: str, person=Depends(authenticate)):
        room = storage.get_room(room_id, person['id'])
        if not room or room['created_by'] != person['id']:
            raise HTTPException(403, 'Room owner required')
        from .realtime import _room_locks, manager
        async with _room_locks[room_id]:
            with storage.connection() as db:
                if db.execute("SELECT 1 FROM jobs WHERE room_id=? AND status IN ('researching','answering','running')", (room_id,)).fetchone():
                    raise HTTPException(409, 'Wait for the active run to finish')
                db.execute('UPDATE rooms SET archived=1 WHERE id=?', (room_id,))
            for socket in list(manager.rooms.get(room_id, {})):
                await socket.close(code=4403, reason='Room archived')
        return {'ok': True}

    @router.post('/api/rooms/{room_id}/restore')
    async def restore(room_id: str, person=Depends(authenticate)):
        with storage.connection() as db:
            room = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
            if not room or room['created_by'] != person['id']:
                raise HTTPException(403, 'Room owner required')
            db.execute('UPDATE rooms SET archived=0 WHERE id=?', (room_id,))
        return {'ok': True}

    @router.delete('/api/rooms/{room_id}')
    async def delete(room_id: str, person=Depends(authenticate)):
        with storage.connection() as db:
            room = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
        if not room or room['created_by'] != person['id']:
            raise HTTPException(403, 'Room owner required')
        from .realtime import _room_locks, manager
        async with _room_locks[room_id]:
            with storage._lock, storage.connection() as db:
                jobs = db.execute('SELECT id,status FROM jobs WHERE room_id=?', (room_id,)).fetchall()
                if any(job['status'] in ('researching','answering','running') for job in jobs):
                    raise HTTPException(409, 'Wait for the active run to finish')
                for table in ('events','artifacts'):
                    db.execute(f'DELETE FROM {table} WHERE job_id IN (SELECT id FROM jobs WHERE room_id=?)', (room_id,))
                for table in ('room_messages','room_events','room_members','room_research','research_source_uses','source_graph_papers','photon_links','jobs'):
                    db.execute(f'DELETE FROM {table} WHERE room_id=?', (room_id,))
                db.execute('DELETE FROM rooms WHERE id=?', (room_id,))
                db.execute('DELETE FROM meta WHERE key=?', (f'research_revision:{room_id}',))
            for job in jobs:
                shutil.rmtree(settings.data_dir/'jobs'/job['id'], ignore_errors=True)
            for socket in list(manager.rooms.get(room_id, {})):
                await socket.close(code=4403, reason='Room deleted')
        return {'ok': True}

    @router.get('/api/rooms/{room_id}/reports/{job_id}')
    async def report(room_id: str, job_id: str, person=Depends(authenticate)):
        job = storage.get_job(job_id)
        if not storage.is_room_member(room_id, person['id']) or not job or job['room_id'] != room_id:
            raise HTTPException(404, 'Report not found')
        report = storage.get_artifacts(job_id).get('experiment_report.md')
        if not report:
            raise HTTPException(404, 'Report is available after the experiment completes')
        return PlainTextResponse(report, headers={'Content-Disposition': f'attachment; filename="experiment-{job_id}.md"'})

    app.include_router(router)
