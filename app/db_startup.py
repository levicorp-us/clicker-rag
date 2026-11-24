"""Connect to this local PostgreSQL cluster before considering a new server."""

import argparse
import errno
import locale
from pathlib import Path
import socket
import subprocess
import sys
import time

import psycopg
from psycopg import pq
from psycopg.conninfo import make_conninfo


def connection_state(cluster, secret, port):
    host = "127.0.0.1"
    password = secret.read_text().strip() if secret.is_file() else ""
    dsn = make_conninfo(host=host, port=port, dbname="postgres", user="clicker",
                        password=password, connect_timeout=2, application_name="clicker-start")
    try:
        with psycopg.connect(dsn) as db:
            directory = db.execute("SHOW data_directory").fetchone()[0]
    except psycopg.OperationalError as exc:
        # Only a refused TCP connection establishes that the port is unused.
        with socket.socket() as probe:
            probe.settimeout(5)
            if probe.connect_ex((host, port)) == errno.ECONNREFUSED:
                return "offline"
        if pq.PGconn.ping(dsn.encode()) == pq.Ping.REJECT:
            return "starting"
        raise RuntimeError(f"Cannot connect to the server listening on {host}:{port}. {exc}") from None
    if Path(directory).resolve() != cluster.resolve():
        raise RuntimeError(f"Port {port} belongs to another PostgreSQL data directory: {directory}")
    return "ready"


def connect_or_start(cluster, secret, port, pg_ctl=None):
    state = connection_state(cluster, secret, port)
    if state == "offline" and pg_ctl is None:
        return False
    if state == "offline":
        command = [str(pg_ctl), "-D", str(cluster)]
        output = dict(capture_output=True, encoding=locale.getpreferredencoding(False), errors="replace")
        status = subprocess.run([*command, "status"], **output)
        if status.returncode == 3:  # No running postmaster for this data directory.
            # A Windows postmaster can inherit pg_ctl's handles and keep a pipe open.
            # Use a file for launch diagnostics so returning does not depend on server exit.
            launch_log = secret.parent / "db-start.log"
            with launch_log.open("wb") as log:
                start = subprocess.run(
                    [*command, "-l", str(cluster / "server.log"), "-o", f"-h 127.0.0.1 -p {port}",
                     "-w", "-t", "120", "start"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            if start.returncode:
                raise RuntimeError(f"Database start failed. See {launch_log} and {cluster / 'server.log'}.")
        elif status.returncode != 0:
            raise RuntimeError("Cannot determine PostgreSQL status: " + status.stderr)
    deadline = time.monotonic() + 120
    while state != "ready":
        if time.monotonic() >= deadline:
            raise RuntimeError("The existing Clicker database did not become ready within 120 seconds.")
        time.sleep(0.5)
        state = connection_state(cluster, secret, port)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cluster", type=Path)
    parser.add_argument("secret", type=Path)
    parser.add_argument("--pg-ctl", type=Path)
    parser.add_argument("--port", type=int, default=55438)
    args = parser.parse_args()
    try:
        print("ready" if connect_or_start(args.cluster, args.secret, args.port, args.pg_ctl) else "offline")
    except (OSError, RuntimeError) as error:
        sys.exit(str(error))
