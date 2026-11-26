"""The same tune as play_tune.py, run on a boxd machine. Needs BOXD_API_KEY.

Follows the interactive exec (`stream_exec`) of https://docs.boxd.sh/reference/python-sdk
"""

import json
import sys
import uuid

from boxd import Boxd

from play_tune import code, play, speakers

# there is no built-in way for code in the sandbox to call a function on this machine, so this goes at the
# top of the code: each call is written to stdout as a line of JSON, and the result is read from stdin
stub = """
import json, sys

def call(name, *args):
    print(json.dumps({"call": name, "args": args}), flush=True)
    reply = json.loads(sys.stdin.readline())
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply["result"]

def play(note, seconds):
    return call("play", note, seconds)
"""

# the functions the sandbox may call
functions = {'play': play}


def run_call(line: str) -> str:
    request = json.loads(line)
    try:
        reply = {'result': functions[request['call']](*request['args'])}
    except Exception as e:
        reply = {'error': repr(e)}
    return json.dumps(reply) + '\n'


def main() -> None:
    with Boxd(region='nyc') as boxd:
        machine = boxd.machines.create(f'sandbox-bench-{uuid.uuid4().hex[:8]}', isolated=True, delete_after=600)
        try:
            boxd.machines.wait_until_ready(machine.id, poll_interval=0.05)
            boxd.machines.files.upload(machine.id, 'tune.py', stub + code)
            with speakers, boxd.machines.stream_exec(machine.id, command=['python3', '-u', 'tune.py']) as process:
                buffer = b''
                # every line the code prints has to be a call, anything else breaks the protocol
                for chunk in process.iter_chunks():
                    if chunk.is_stderr:
                        sys.stderr.buffer.write(chunk.data)
                        continue
                    buffer += chunk.data
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        process.write(run_call(line.decode()))
        finally:
            boxd.machines.delete(machine.id)


if __name__ == '__main__':
    main()
