"""Start a Windows service without inheriting the installer's output pipes."""

import os
import subprocess
import sys


if __name__ == "__main__":
    log_base, *command = sys.argv[1:]
    stdout_path = log_base + ".log" if log_base != "-" else os.devnull
    stderr_path = log_base + ".err" if log_base != "-" else os.devnull
    with open(stdout_path, "wb") as output, open(stderr_path, "wb") as errors:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    print(process.pid)
