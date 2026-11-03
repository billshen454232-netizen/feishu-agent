"""On-demand stack dumps for a running service (no extra tools needed on the host).

``kill -USR1 <pid>``: Python stacks of every thread (works even if the event loop is blocked).
``kill -USR2 <pid>``: where every asyncio task is currently awaiting.
Output goes to stderr, i.e. ``journalctl -u <service>``.
"""

from __future__ import annotations

import asyncio
import faulthandler
import io
import logging
import signal
import sys

logger = logging.getLogger(__name__)


def install_stack_dump_signals(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Register the dump signals; returns False on platforms without SIGUSR1/SIGUSR2 (Windows)."""
    usr1 = getattr(signal, "SIGUSR1", None)
    usr2 = getattr(signal, "SIGUSR2", None)
    if usr1 is None or usr2 is None:
        return False
    faulthandler.register(usr1, file=sys.stderr, all_threads=True, chain=False)
    target_loop = loop or asyncio.get_running_loop()
    try:
        target_loop.add_signal_handler(usr2, dump_asyncio_tasks)
    except (NotImplementedError, RuntimeError):
        return False
    return True


def dump_asyncio_tasks() -> None:
    buffer = io.StringIO()
    tasks = asyncio.all_tasks()
    buffer.write("asyncio task dump: {} task(s)\n".format(len(tasks)))
    for task in tasks:
        buffer.write("--- {!r}\n".format(task))
        task.print_stack(limit=20, file=buffer)
    print(buffer.getvalue(), file=sys.stderr, flush=True)
