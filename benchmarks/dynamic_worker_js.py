"""Cloudflare Dynamic Workers in JavaScript: one module in a new isolate, which calls the host as the Python one does.

Run with `uv run -m benchmarks.dynamic_worker_js [n] [--save LOCATION]`; see `main` in shared.py.

Design decisions
----------------

This uses the same Worker in `dynamic_worker/` as dynamic_worker.py, with `?lang=js`, and the same JSON lines for
calls to the host. The code is an ES module, which the Worker loads into a new isolate as it is: its default
export is called with the host functions, and what it returns is the result. JavaScript cannot block, so the
module awaits each call.

There is no REPL. Workers only allow code to be compiled from a string while a module loads, not while it
handles a request, so a JavaScript isolate cannot run one feed after another the way Python's `exec` does.
Each session runs one module, and the two cases that keep state between feeds are recorded as failed.
"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.client import connect

from .shared import Functions, Pool, Session, answer, main


class DynamicWorkerJsSession(Session):
    """Runs one module, so one `run` or one `feed_run`."""

    language = 'js'

    def __init__(self, socket: Any):
        self.socket = socket

    async def run(self, code: str) -> str:
        return (await self.execute(code, {}))['printed']

    async def feed_run(self, code: str, external_lookup: Functions | None = None) -> Any:
        return (await self.execute(code, external_lookup or {}))['result']

    async def execute(self, code: str, functions: Functions) -> dict[str, Any]:
        await self.socket.send(json.dumps({'code': code, 'names': list(functions)}))
        # every message is a call until the one with the result
        while 'call' in (message := json.loads(await self.socket.recv())):
            await self.socket.send(json.dumps(answer(message, functions)))
        if 'error' in message:
            raise RuntimeError(message['error'])
        return message


class DynamicWorkerJsPool(Pool):
    """Talks to the Worker in dynamic_worker/, which loads a new Dynamic Worker for each WebSocket."""

    repl = False

    @asynccontextmanager
    async def checkout(self) -> AsyncIterator[DynamicWorkerJsSession]:
        headers = {'Authorization': f'Bearer {os.environ["DYNAMIC_WORKER_KEY"]}'}
        url = f'{os.environ["DYNAMIC_WORKER_URL"]}/?lang=js'
        async with connect(url, additional_headers=headers, open_timeout=30) as socket:
            yield DynamicWorkerJsSession(socket)


if __name__ == '__main__':
    main(
        'dynamic_worker_js',
        'Cloudflare Dynamic Workers (JS)',
        'https://developers.cloudflare.com/dynamic-workers/',
        DynamicWorkerJsPool(),
    )
