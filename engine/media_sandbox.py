"""Internal exec launcher: set resource limits before starting a native parser.

This avoids Python preexec_fn in a threaded worker. It is a local rlimit boundary,
not a production container, syscall filter or hostile-code isolation claim.
"""
import os
from pathlib import Path
import resource
import sys


def main():
    cpu, memory, file_size, executable, *args = sys.argv[1:]
    if not Path(executable).is_absolute() or Path(executable).name not in {"ffmpeg", "ffprobe"}:
        raise SystemExit("unsupported internal media executable")
    values = tuple(int(value) for value in (cpu, memory, file_size))
    if any(value <= 0 for value in values):
        raise SystemExit("invalid resource limit")
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (values[0], values[0]))
    resource.setrlimit(resource.RLIMIT_AS, (values[1], values[1]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (values[2], values[2]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.execv(executable, [executable, *args])


if __name__ == "__main__":
    main()
