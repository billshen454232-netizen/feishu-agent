import asyncio
import signal

import pytest

from src.utils.diagnostics import dump_asyncio_tasks, install_stack_dump_signals


@pytest.mark.asyncio
async def test_asyncio_task_dump_lists_waiting_tasks(capsys):
    async def waiting():
        await asyncio.sleep(10)

    task = asyncio.create_task(waiting(), name="waiting-task")
    await asyncio.sleep(0)
    dump_asyncio_tasks()
    task.cancel()

    err = capsys.readouterr().err
    assert "asyncio task dump" in err and "waiting-task" in err and "in waiting" in err


@pytest.mark.asyncio
async def test_install_is_a_no_op_without_posix_signals(monkeypatch):
    monkeypatch.delattr(signal, "SIGUSR1", raising=False)

    assert install_stack_dump_signals() is False
