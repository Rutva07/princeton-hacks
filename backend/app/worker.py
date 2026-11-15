"""Single-consumer durable experiment worker."""
from __future__ import annotations

import asyncio
import fcntl
import os
import socket

from . import storage
from .config import settings
from .experiment_graph import run_experiment_graph


async def worker_loop(stop_event: asyncio.Event | None = None):
    storage.init_db()
    lock_path = settings.data_dir / 'experiment-worker.lock'
    worker_lock = lock_path.open('a+')
    try:
        fcntl.flock(worker_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        worker_lock.close()
        raise RuntimeError('Another Labthread experiment worker already owns the queue.') from exc
    storage.recover_interrupted_experiments()
    worker_id = f'{socket.gethostname()}-{os.getpid()}'
    try:
        while not (stop_event and stop_event.is_set()):
            job = storage.claim_next_experiment(worker_id)
            if job:
                await run_experiment_graph(job)
                continue
            try:
                await asyncio.wait_for(
                    stop_event.wait() if stop_event else asyncio.sleep(settings.experiment_poll_seconds),
                    timeout=settings.experiment_poll_seconds if stop_event else None,
                )
            except asyncio.TimeoutError:
                pass
    finally:
        fcntl.flock(worker_lock.fileno(), fcntl.LOCK_UN)
        worker_lock.close()


def main():
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
