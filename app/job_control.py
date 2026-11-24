from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event, Thread
from time import monotonic

from .core import connect


class JobInterrupted(Exception):
    """The user stopped this run or a newer worker claimed it."""


current_run = ContextVar("current_job_run", default=None)


def check_running():
    run = current_run.get()
    if run and run.interrupted.is_set():
        raise JobInterrupted


class JobRun:
    def __init__(self, job):
        self.identity = (job["id"], job["updated"])
        self.interrupted = Event()
        self.finished = Event()

    def lock_current(self, db):
        # Serialize a checkpoint or the final commit against the user's action.
        if not db.execute("""SELECT 1 FROM jobs WHERE id=%s AND updated=%s
                AND status='실행' FOR NO KEY UPDATE""", self.identity).fetchone():
            raise JobInterrupted

    def watch(self):
        try:
            with connect() as db:
                db.autocommit = True
                renew = monotonic() + 20
                while not self.finished.wait(0.6):
                    if monotonic() >= renew:
                        sql = """UPDATE jobs SET lease=clock_timestamp()+interval '90 seconds'
                                 WHERE id=%s AND updated=%s AND status='실행' RETURNING 1"""
                        renew = monotonic() + 20
                    else:
                        sql = "SELECT 1 FROM jobs WHERE id=%s AND updated=%s AND status='실행'"
                    if not db.execute(sql, self.identity).fetchone():
                        self.interrupted.set()
                        return
        except Exception:
            # Stop work if we cannot verify ownership. An expired lease is recoverable.
            self.interrupted.set()

    @contextmanager
    def monitor(self):
        token = current_run.set(self)
        watcher = Thread(target=self.watch, daemon=True)
        watcher.start()
        try:
            yield
        finally:
            self.finished.set()
            current_run.reset(token)
            watcher.join()
