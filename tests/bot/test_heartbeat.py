import asyncio
import logging
import os
import time

from aiogram.exceptions import TelegramNetworkError

from app.bot.utils.heartbeat import run_heartbeat


class _Bot:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0

    async def get_me(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise TelegramNetworkError(method=None, message="network is down")


def beat_until(bot: _Bot, path, calls: int) -> None:
    async def scenario():
        task = asyncio.create_task(run_heartbeat(bot, str(path), interval=0))
        while bot.calls < calls:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    asyncio.run(scenario())


def test_heartbeat_writes_the_time(tmp_path):
    path = tmp_path / "heartbeat"
    before = int(time.time())

    beat_until(_Bot(), path, calls=2)

    assert before <= int(path.read_text()) <= int(time.time())


def test_heartbeat_keeps_the_file_stale_while_the_api_fails(tmp_path, caplog):
    path = tmp_path / "heartbeat"
    path.write_text("0\n")
    os.utime(path, (1, 1))

    with caplog.at_level(logging.WARNING):
        beat_until(_Bot(failures=10), path, calls=3)

    assert path.read_text() == "0\n"
    assert os.path.getmtime(path) == 1
    assert "Heartbeat failed" in caplog.text


def test_heartbeat_recovers_after_a_failure(tmp_path):
    path = tmp_path / "heartbeat"
    beat_until(_Bot(failures=1), path, calls=3)
    assert path.exists()


def test_startup_runs_the_heartbeat_only_when_configured(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import __main__ as bot_main

    started = []

    async def fake_heartbeat(bot, path):
        started.append(path)

    async def noop(*args):
        return None

    monkeypatch.setattr(bot_main, "run_heartbeat", fake_heartbeat)
    monkeypatch.setattr(bot_main, "drop_reply_waits", noop)
    monkeypatch.setattr(bot_main.commands, "setup", noop)

    async def start(path):
        dispatcher = {}
        config = SimpleNamespace(bot=SimpleNamespace(HEARTBEAT_FILE=path))
        await bot_main.on_startup(SimpleNamespace(start=lambda: None), dispatcher, config, None, object())
        task = dispatcher.get("heartbeat_task")
        if task is not None:
            await task

    asyncio.run(start(""))
    asyncio.run(start(str(tmp_path / "heartbeat")))
    assert started == [str(tmp_path / "heartbeat")]
