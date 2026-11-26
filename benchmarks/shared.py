"""The five benchmark cases, and the pieces the providers without host function calls share.

Each provider module in this directory has a `Pool`, whose `checkout()` gives a new `Session`. `1 + 1 direct`
uses the session's `run`, which is the provider's own way of running a program. The other cases use `feed_run`,
which keeps state and calls host functions: Logfire has that built in, and each of the others starts a small
REPL in the sandbox for it, using the transport its docs recommend (see the scripts in play_tune/).

Design decisions
----------------

Only Logfire has calls from the sandbox to the host built in. For the others we used whatever the provider's
own docs show for talking to a running program, and built the same request and reply on top: the code in the
sandbox writes a line of JSON naming a function and its arguments, blocks, and reads a line of JSON back.
`STDIO_REPL` and `StdioSession` below are the two ends of it. Daytona carries the same JSON over HTTP
(see daytona.py).

Three things follow from that protocol on every provider but Logfire:

- Data: arguments and results have to survive JSON. A tuple arrives as a list, a dict's integer keys arrive as
  strings, and `bytes`, `set`, `datetime` and instances of your own classes cannot be sent at all. An
  exception raised by a host function reaches the sandbox as a `RuntimeError` carrying its `repr`, so `except
  ValueError` in the sandbox does not catch a `ValueError` from the host.
- Reliability: the channel is the program's stdout, so anything else written there breaks the protocol. The
  REPL points `sys.stdout` at stderr to stop `print` doing that, but a subprocess or a C extension writing to
  file descriptor 1 still would. There are no timeouts: if the host stops answering, the sandbox waits for
  ever, and is billed until something deletes it. Calls are answered one at a time.
- Security: the request is written by the code you are sandboxing, so the function name and arguments are
  untrusted input. The host only looks the name up in the dict of functions it was given, but it passes the
  arguments on unchecked, so each host function has to validate its own.

This must not import the play_tune scripts: they import `sounddevice`, which fails on a machine with no audio.
"""

import argparse
import asyncio
import json
import statistics
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

Functions = dict[str, Callable[..., Any]]

RESULTS = Path(__file__).parent.parent / 'results'


class Session(ABC):
    """One sandbox. A session is used for one `run`, or for any number of `feed_run`s."""

    # the language the cases send code in, 'python' or 'js'
    language = 'python'

    @abstractmethod
    async def run(self, code: str) -> str:
        """Run a program the way the provider's API offers and return what it printed.

        This has no state and no host functions, so it needs none of the machinery `feed_run` does.
        """

    @abstractmethod
    async def feed_run(self, code: str, external_lookup: Functions | None = None) -> Any:
        """Run code in the sandbox and return the value of its last expression.

        State is kept between feeds, and the code can call the functions in `external_lookup` on this machine.
        """


class Pool(ABC):
    """What each provider module gives `main`: somewhere to get new sessions from."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    # False for a provider that cannot keep state between feeds: the cases in NEEDS_REPL are then recorded as failed
    repl = True

    @abstractmethod
    def checkout(self) -> AbstractAsyncContextManager[Session]:
        """A new session, ended on leaving the block. The benchmark times the whole block."""


# seconds between sessions, long enough for a pre-warmed pool to have a warm session ready again
GAP = 1.0
# seconds to wait after a provider refuses a session before trying again
REFUSED_WAIT = 5.0


def get_name() -> str:
    return 'world'


def double(x: int) -> int:
    return x * 2


# the code the first three cases send, by language: in JavaScript it is a module whose default export is given
# the host functions and returns the result
ONE_PLUS_ONE = {'python': '1 + 1', 'js': 'export default async () => 1 + 1'}
PRINT_1_PLUS_1 = {'python': 'print(1 + 1)', 'js': 'export default async () => console.log(1 + 1)'}
ONE_CALL = {'python': 'get_name()', 'js': 'export default async ({ get_name }) => await get_name()'}
FIVE_CALLS = 'a = double(1)\nb = double(a)\nc = double(b)\nd = double(c)\ne = double(d)\ne'
EXT = {'external_lookup': {'get_name': get_name, 'double': double}}


async def case_1_plus_1(s: Session) -> None:
    # through `feed_run`, so it pays for what state and host functions need, as most real uses do
    assert await s.feed_run(ONE_PLUS_ONE[s.language], **EXT) == 2


async def case_1_plus_1_direct(s: Session) -> None:
    # the same sum run the provider's own way, with no state or host functions
    assert (await s.run(PRINT_1_PLUS_1[s.language])).strip() == '2'


async def case_one_call(s: Session) -> None:
    assert await s.feed_run(ONE_CALL[s.language], **EXT) == 'world'


async def case_5_feeds(s: Session) -> None:
    for i in range(5):
        assert await s.feed_run(f'x{i} = {i} * 2\nx{i}') == i * 2


async def case_10_feeds_5_calls(s: Session) -> None:
    for _ in range(10):
        assert await s.feed_run(FIVE_CALLS, **EXT) == 32


CASES = [
    ('1 + 1', case_1_plus_1),
    ('1 + 1 direct', case_1_plus_1_direct),
    ('one external call', case_one_call),
    ('5 feeds', case_5_feeds),
    ('10 feeds x 5 calls', case_10_feeds_5_calls),
]
# the cases that keep state between feeds
NEEDS_REPL = {'5 feeds', '10 feeds x 5 calls'}


def summary(times: list[float]) -> dict[str, Any]:
    return {
        'median': round(statistics.median(times)),
        'min': round(min(times)),
        'max': round(max(times)),
        'times': [round(t) for t in times],
    }


async def run(key: str, pool: Pool, n: int) -> dict[str, Any]:
    """Time n sessions of each case, print a line for each and return what `results/<key>.json` holds for them.

    The time that counts runs from asking for a session to having the result. Ending the session, which for
    most providers means deleting the sandbox, is timed separately: nobody waits for it.
    """
    cases: dict[str, Any] = {}
    for name, case in CASES:
        if name in NEEDS_REPL and not pool.repl:
            print(f'{key:<18} {name:<20} failed: no REPL', flush=True)
            cases[name] = {'failed': 'no REPL'}
            continue
        times: list[float] = []
        ends: list[float] = []
        refused = 0
        while len(times) < n:
            await asyncio.sleep(GAP)
            start = time.perf_counter()
            try:
                async with pool.checkout() as s:
                    await case(s)
                    done = time.perf_counter()
            except Exception as e:
                # Logfire refuses sessions for about 20 seconds after 32 in a row, and boat allows a number of
                # starts a minute; those are not timed
                if 'worker_unavailable' not in str(e) and 'rate_limited' not in str(e):
                    raise
                refused += 1
                await asyncio.sleep(REFUSED_WAIT)
                continue
            times.append((done - start) * 1000)
            ends.append((time.perf_counter() - done) * 1000)
        print(
            f'{key:<18} {name:<20} p50 {statistics.median(times):7.0f}  min {min(times):7.0f}  '
            f'max {max(times):7.0f} ms  end {statistics.median(ends):5.0f} ms  (n={n})'
            + (f'  refused {refused}' if refused else ''),
            flush=True,
        )
        cases[name] = {**summary(times), 'end': summary(ends)}
        if refused:
            cases[name]['refused'] = refused
    return cases


async def run_pool(key: str, pool: Pool, n: int) -> dict[str, Any]:
    async with pool:
        return await run(key, pool, n)


def main(key: str, provider: str, url: str, pool: Pool) -> None:
    """Run the five cases against a pool, each timed from asking for a new session to having the result.

    `key` names the results file, and `provider` and `url` are the name shown in the table and what it links to. With `--save LOCATION` the
    run replaces `results/<key>.json`, keeping the note of the file it replaces.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('n', nargs='?', type=int, default=5, help='sessions per case')
    parser.add_argument('--save', metavar='LOCATION', help='write results/<key>.json; where this was run from')
    args = parser.parse_args()
    cases = asyncio.run(run_pool(key, pool, args.n))
    if args.save:
        path = RESULTS / f'{key}.json'
        result = {
            'provider': provider,
            'url': url,
            'date': str(datetime.now(UTC).date()),
            'location': args.save,
            'sessions': args.n,
        }
        if path.exists() and (note := json.loads(path.read_text()).get('note')):
            result['note'] = note
        result['cases'] = cases
        path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n')
        print(f'wrote {path.relative_to(RESULTS.parent)}')


# runs one feed in the sandbox; `call(name, *args)` is defined by the transport around it
RUN_FEED = """
import ast

state = {}

def run_feed(code, names):
    for name in names:
        state[name] = lambda *args, name=name: call(name, *args)
    tree = ast.parse(code)
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, '<feed>', 'exec'), state)
    if last is not None:
        return eval(compile(ast.Expression(last.value), '<feed>', 'eval'), state)
"""

# the REPL for all but Daytona: feeds arrive on stdin and calls and results leave on stdout, one line of JSON each
STDIO_REPL = (
    """
import json, sys

out = sys.stdout
sys.stdout = sys.stderr  # so that a print() in a feed cannot break the protocol

def send(message):
    out.write(json.dumps(message) + '\\n')
    out.flush()

def call(name, *args):
    send({'call': name, 'args': args})
    reply = json.loads(sys.stdin.readline())
    if 'error' in reply:
        raise RuntimeError(reply['error'])
    return reply['result']
"""
    + RUN_FEED
    + """
for line in sys.stdin:
    feed = json.loads(line)
    try:
        send({'result': run_feed(feed['feed'], feed['names'])})
    except Exception as e:  # noqa: BLE001
        send({'error': repr(e)})
"""
)


def answer(message: dict[str, Any], functions: Functions) -> dict[str, Any]:
    """Run the host function a sandbox asked for."""
    try:
        return {'result': functions[message['call']](*message['args'])}
    except Exception as e:  # noqa: BLE001
        return {'error': repr(e)}


class StdioSession(Session):
    """A session whose `feed_run` talks to STDIO_REPL.

    Subclasses provide `run`, `start`, which starts the REPL in the sandbox, `write(line)` and `readline()`.
    """

    started = False

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def write(self, line: str) -> None: ...

    @abstractmethod
    async def readline(self) -> str: ...

    async def feed_run(self, code: str, external_lookup: Functions | None = None) -> Any:
        if not self.started:
            await self.start()
            self.started = True
        functions = external_lookup or {}
        await self.write(json.dumps({'feed': code, 'names': list(functions)}) + '\n')
        while True:
            message = json.loads(await self.readline())
            if 'call' in message:
                await self.write(json.dumps(answer(message, functions)) + '\n')
            elif 'error' in message:
                raise RuntimeError(message['error'])
            else:
                return message['result']
