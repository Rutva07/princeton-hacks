"""Connect iMessage identities to invited research rooms."""
import re
import asyncio
from . import storage
from .photon import send_message, enabled


async def handle(message):
    from .realtime import publish, maybe_start_agent
    text = message['text'].strip()
    space = message['space_id']
    async def reply(content):
        await send_message(space, message['sender_id'], content, f'room-{message["id"]}')

    join = re.fullmatch(r'JOIN\s+([a-f0-9]{10})\s+([a-f0-9]{6})', text, re.I)
    with storage.connection() as db:
        row = db.execute('SELECT * FROM photon_links WHERE space_id=?', (space,)).fetchone()
    link = dict(row) if row else None
    if join:
        room_id, code = join.group(1).lower(), join.group(2).upper()
        room = storage.get_room(room_id)
        if not room or room['join_code'] != code:
            await reply('Invitation not found. Copy the JOIN command from the room’s Invite dialog.')
            return True
        if link:
            participant = storage.get_participant(link['participant_id'])
        else:
            participant = storage.create_session('iMessage researcher')['participant']
        storage.join_room(room_id, participant['id'], code)
        with storage.connection() as db:
            if link and link['room_id'] != room_id:
                db.execute('DELETE FROM room_members WHERE room_id=? AND participant_id=?',
                           (link['room_id'], participant['id']))
            db.execute('INSERT OR REPLACE INTO photon_links VALUES(?,?,?,?)',
                       (space, room_id, participant['id'], message['sender_id']))
        await publish(room_id, 'room.join', {'participant': participant})
        await reply(f'Joined {room["title"]}. Text NAME followed by your name, then ask a question or propose an experiment. Reply APPROVE to run the latest proposal.')
        return True
    if not link:
        return False
    room_id = link['room_id']
    if not storage.is_room_member(room_id, link['participant_id']):
        await reply('This room is unavailable. Join another room with its invitation command.')
        return True
    if text.upper() == 'LEAVE':
        with storage.connection() as db:
            db.execute('DELETE FROM photon_links WHERE space_id=?', (space,))
            db.execute('DELETE FROM room_members WHERE room_id=? AND participant_id=?', (room_id,link['participant_id']))
        await reply('Disconnected from the room.')
        return True
    if text.upper().startswith('NAME '):
        name = ' '.join(text[5:].split())
        if not 2 <= len(name) <= 48:
            await reply('Use NAME followed by a name of 2–48 characters.')
            return True
        with storage.connection() as db:
            db.execute('UPDATE participants SET display_name=? WHERE id=?', (name,link['participant_id']))
        await reply(f'Your room name is now {name}.')
        return True
    participant = storage.get_participant(link['participant_id'])
    command = re.fullmatch(r'(APPROVE|REJECT)(?:\s+([a-f0-9]{10}))?', text, re.I)
    if command:
        from .realtime import _room_locks, _finish_experiment, publish_message
        from .workflow import approve
        async with _room_locks[room_id]:
            job = storage.get_job(command.group(2).lower()) if command.group(2) else storage.latest_room_job(room_id, ('awaiting_approval',))
            if not job or job['room_id'] != room_id:
                await reply('No pending proposal found in this room.')
                return True
            accept = command.group(1).upper() == 'APPROVE'
            ok, _ = await approve(job['id'], accept)
            if not ok:
                await reply('That experiment is no longer awaiting approval.')
                return True
            item, _ = storage.add_room_message(room_id,participant['id'],'human',text,client_message_id=message['id'])
            await publish(room_id,'message.created',{'message':item})
            if accept:
                await publish(room_id,'experiment.started',{'job_id':job['id']})
                await _finish_experiment(room_id,job['id'])
            else:
                await publish_message(room_id,'Experiment rejected.',job_id=job['id'])
                await publish(room_id,'agent.completed',{'job_id':job['id'],'status':'rejected'})
        return True
    if len(text) > 2000:
        await reply('Please keep your research question under 2,000 characters.')
        return True
    content = text if '@chatbot' in text.lower() else '@chatbot ' + text
    item, created = storage.add_room_message(room_id,participant['id'],'human',content,client_message_id=message['id'])
    if created:
        await publish(room_id,'message.created',{'message':item})
        maybe_start_agent(room_id,participant,content)
    return True


async def relay(room_id, message):
    """Send substantive shared agent responses to each linked direct conversation."""
    if not enabled() or message['kind'] in ('agent_status','welcome'):
        return
    with storage.connection() as db:
        links = db.execute('SELECT * FROM photon_links WHERE room_id=?', (room_id,)).fetchall()
    for link in links:
        try:
            await asyncio.wait_for(send_message(link['space_id'],link['agent_id'],message['content'],
                               f'room-{message["id"]}-{link["space_id"]}'), timeout=15)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('Room message delivery failed: %s',type(exc).__name__)
