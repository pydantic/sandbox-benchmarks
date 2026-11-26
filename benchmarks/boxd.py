"""boxd: the REPL runs over the stdin and stdout of a running `exec`.

Run with `uv run -m benchmarks.boxd [n] [--save LOCATION]`; see `main` in shared.py.

Design decisions
----------------

JSON lines over the stdin and stdout of `stream_exec`, the SDK's interactive exec
(https://docs.boxd.sh/reference/python-sdk), which keeps one stream open in both directions, so each line is a
message on that stream rather than a request of its own. `run` is the SDK's one-shot `exec` of `python3 -c`.

Each session creates a new machine in the `nyc` region, the nearest to GCP us-east4, and deletes it by its id when
the session ends. The machines are isolated, as boxd's docs advise for code you don't trust
(https://docs.boxd.sh/use-cases/sandboxes), and get a delete-after timer as a safety net for a run that dies before
it deletes them. `create` answers once the machine is scheduled, so each session calls `wait_until_ready`, as the
docs say to before the first `exec`.

The protocol is JSON lines over the program's stdin and stdout, and the docstring of shared.py has what that
costs on every provider that uses it. boxd's docs have nothing on calling functions on the host: the SDK reference
only documents writing to the stdin of a running command, which is what we send those JSON lines over.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Self

from boxd import AsyncBoxd

from .shared import STDIO_REPL, Pool, StdioSession, main


class BoxdSession(StdioSession):
    def __init__(self, boxd: AsyncBoxd, machine_id: str):
        self.boxd = boxd
        self.machine_id = machine_id
        self.stream: Any = None
        self.lines: Any = None

    async def run(self, code: str) -> str:
        return str((await self.boxd.machines.exec(self.machine_id, ['python3', '-c', code])).stdout)

    async def start(self) -> None:
        self.stream = self.boxd.machines.stream_exec(self.machine_id, command=['python3', '-u', '-c', STDIO_REPL])
        self.lines = self.read_lines()

    async def read_lines(self) -> AsyncIterator[str]:
        buffer = b''
        # stderr carries what the feeds print, so only stdout is the protocol
        async for chunk in self.stream.iter_chunks():
            if not chunk.is_stderr:
                buffer += chunk.data
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    yield line.decode()

    async def write(self, line: str) -> None:
        await self.stream.write(line)

    async def readline(self) -> str:
        return await anext(self.lines)


class BoxdPool(Pool):
    """Reads `BOXD_API_KEY`; the key decides the organisation the machines are created in."""

    def __init__(self) -> None:
        self.boxd: Any = None

    async def __aenter__(self) -> Self:
        self.boxd = AsyncBoxd(region='nyc')
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.boxd.close()

    @asynccontextmanager
    async def checkout(self) -> AsyncIterator[BoxdSession]:
        boxd = self.boxd
        # the delete-after timer, in seconds, deletes the machine if this process dies before the `finally` does
        machine = await boxd.machines.create(f'sandbox-bench-{uuid.uuid4().hex[:8]}', isolated=True, delete_after=600)
        try:
            await boxd.machines.wait_until_ready(machine.id, poll_interval=0.05)
            session = BoxdSession(boxd, machine.id)
            try:
                yield session
            finally:
                # a reader left suspended on the stream gives 'asynchronous generator is already running' at exit
                if session.lines is not None:
                    await session.lines.aclose()
                if session.stream is not None:
                    await session.stream.close()
        finally:
            await boxd.machines.delete(machine.id)


if __name__ == '__main__':
    main('boxd', 'boxd', 'https://boxd.sh', BoxdPool())
