"""Cloudflare Dynamic Workers in Python: the REPL runs in a Python isolate, and calls the host over RPC and a WebSocket.

Run with `uv run -m benchmarks.dynamic_worker [n] [--save LOCATION]`; see `main` in shared.py.

Design decisions
----------------

A Dynamic Worker is created by another Worker, so you deploy the one in `dynamic_worker/`, which loads a new
Dynamic Worker for each WebSocket and runs the program it is sent in it. Like the Worker for Cloudflare
sandboxes, it is a public address that runs whatever program it is sent, guarded by one shared key.

Host functions are what Dynamic Workers are built for, as RPC calls from the sandbox to objects the loading
Worker hands it (https://developers.cloudflare.com/dynamic-workers/usage/bindings/). The loading Worker is on
Cloudflare, though, not on this machine, so it has to pass each call on to us: the program gets
`exchange(line)`, which sends a line down the WebSocket and blocks until the next line comes back. The lines are
the same JSON as on the providers that use stdin and stdout (see shared.py), with the same limits on what can
be sent, but nothing the program prints can get mixed up with them.

The docs recommend JavaScript, and warn that Python isolates are much slower to start: about 1 to 2.5
seconds, against about 10ms. dynamic_worker_js.py runs the first three cases in JavaScript.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedOK

from .shared import RUN_FEED, Pool, StdioSession, main

# the REPL: feeds and calls are exchanged with the host, one line of JSON each, as in shared.STDIO_REPL
REPL = (
    """
import json

def call(name, *args):
    reply = json.loads(exchange(json.dumps({'call': name, 'args': args})))
    if 'error' in reply:
        raise RuntimeError(reply['error'])
    return reply['result']
"""
    + RUN_FEED
    + """
# the reply to each result is the next feed, and None once the host has gone
line = exchange(None)
while line is not None:
    feed = json.loads(line)
    try:
        message = {'result': run_feed(feed['feed'], feed['names'])}
    except Exception as e:  # noqa: BLE001
        message = {'error': repr(e)}
    line = exchange(json.dumps(message))
"""
)


class DynamicWorkerSession(StdioSession):
    """The first message on the socket is the program the Worker runs, so a session runs one program."""

    def __init__(self, socket: Any):
        self.socket = socket

    async def run(self, code: str) -> str:
        await self.socket.send(code)
        printed = ''
        # the Worker sends what the program printed, then closes the socket
        try:
            while True:
                printed += await self.socket.recv()
        except ConnectionClosedOK:
            return printed

    async def start(self) -> None:
        await self.socket.send(REPL)

    async def write(self, line: str) -> None:
        await self.socket.send(line.rstrip('\n'))

    async def readline(self) -> str:
        # each message is one line
        return await self.socket.recv()


class DynamicWorkerPool(Pool):
    """Talks to the Worker in dynamic_worker/, which loads a new Dynamic Worker for each WebSocket."""

    @asynccontextmanager
    async def checkout(self) -> AsyncIterator[DynamicWorkerSession]:
        headers = {'Authorization': f'Bearer {os.environ["DYNAMIC_WORKER_KEY"]}'}
        async with connect(os.environ['DYNAMIC_WORKER_URL'], additional_headers=headers, open_timeout=30) as socket:
            yield DynamicWorkerSession(socket)


if __name__ == '__main__':
    main(
        'dynamic_worker',
        'Cloudflare Dynamic Workers (Python)',
        'https://developers.cloudflare.com/dynamic-workers/',
        DynamicWorkerPool(),
    )
