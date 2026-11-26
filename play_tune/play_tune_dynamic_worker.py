"""The same tune as play_tune.py, run in a Cloudflare Dynamic Worker. Needs DYNAMIC_WORKER_URL and DYNAMIC_WORKER_KEY.

Dynamic Workers are loaded by another Worker, so this talks to the one in dynamic_worker/, which runs the code
in a new Python isolate. The code calls `exchange(line)`, an RPC call back to that Worker
(https://developers.cloudflare.com/dynamic-workers/usage/bindings/), which sends the line here over a WebSocket
and returns the reply.
"""

import json
import os

from websockets.sync.client import connect

from play_tune import code, play, speakers

# the Worker gives the code `exchange(line)`, which sends a line here and blocks until the reply comes back;
# this goes at the top of the code to make each call a line of JSON
stub = """
import json

def call(name, *args):
    reply = json.loads(exchange(json.dumps({"call": name, "args": args})))
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
    return json.dumps(reply)


def main() -> None:
    headers = {'Authorization': f'Bearer {os.environ["DYNAMIC_WORKER_KEY"]}'}
    # the Worker loads a new Dynamic Worker for each socket, and closes the socket when the code ends
    with connect(os.environ['DYNAMIC_WORKER_URL'], additional_headers=headers) as socket, speakers:
        socket.send(stub + code)
        # each message is a call; the code prints nothing, so there is no output message at the end
        for message in socket:
            socket.send(run_call(message))


if __name__ == '__main__':
    main()
