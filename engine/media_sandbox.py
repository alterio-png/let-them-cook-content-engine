"""Internal exec launcher: set resource limits before starting a native parser.

This avoids Python preexec_fn in a threaded worker. It is a local rlimit boundary,
not a production container, syscall filter or hostile-code isolation claim.
"""
import ctypes
import os
from pathlib import Path
import resource
import signal
import sys


def main():
    parent_pid, cpu, memory, file_size, executable, *args = sys.argv[1:]
    if not Path(executable).is_absolute() or Path(executable).name not in {"ffmpeg", "ffprobe"}:
        raise SystemExit("unsupported internal media executable")
    values = tuple(int(value) for value in (cpu, memory, file_size))
    if any(value <= 0 for value in values):
        raise SystemExit("invalid resource limit")
    # Linux parent-death protection survives exec into the same-UID FFmpeg
    # binary. The PID check closes the race where the worker dies before this
    # launcher installs the signal; no Python finally block runs on SIGKILL.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "cannot install media parent-death signal")
    if os.getppid() != int(parent_pid):
        os.kill(os.getpid(), signal.SIGKILL)
        raise SystemExit("media supervisor already exited")
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (values[0], values[0]))
    resource.setrlimit(resource.RLIMIT_AS, (values[1], values[1]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (values[2], values[2]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.execv(executable, [executable, *args])


if __name__ == "__main__":
    main()
