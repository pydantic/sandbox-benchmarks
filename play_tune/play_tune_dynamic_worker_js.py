"""The same tune as play_tune.py, in JavaScript, run in a Cloudflare Dynamic Worker. Needs DYNAMIC_WORKER_URL and
DYNAMIC_WORKER_KEY.

This talks to the Worker in dynamic_worker/ with `?lang=js`, which loads the code, an ES module, into a new
JavaScript isolate and calls its default export with each function named in `names`. Calling one is an RPC call
back to that Worker (https://developers.cloudflare.com/dynamic-workers/usage/bindings/), which sends it here over
a WebSocket as a line of JSON and returns the reply. JavaScript cannot block on a call, so the code awaits
`play`.
"""

import json
import os

from websockets.sync.client import connect

from play_tune import play, speakers

# the same tune as `code` in play_tune.py
code = """
export default async ({ play }) => {
  // each '-' makes a note one beat longer
  const melody = (
    'B3 C#4 D4 E4 F#4 D4 F#4- F4 C#4 F4- E4 C4 E4- ' +
    'B3 C#4 D4 E4 F#4 D4 F#4 B4 A4 F#4 D4 F#4 A4---'
  )
  let beat = 0.18
  for (const octave of [0, 0, 1]) {
    for (const token of melody.split(' ').filter(Boolean)) {
      const note = token.replace(/-+$/, '')
      const dashes = token.length - note.length
      await play(note.slice(0, -1) + (Number(note.at(-1)) + octave), beat * (1 + dashes))
    }
    beat *= 0.75
  }
}
"""

# the functions the sandbox may call
functions = {'play': play}


def run_call(request: dict) -> str:
    try:
        reply = {'result': functions[request['call']](*request['args'])}
    except Exception as e:
        reply = {'error': repr(e)}
    return json.dumps(reply)


def main() -> None:
    headers = {'Authorization': f'Bearer {os.environ["DYNAMIC_WORKER_KEY"]}'}
    with connect(f'{os.environ["DYNAMIC_WORKER_URL"]}/?lang=js', additional_headers=headers) as socket, speakers:
        socket.send(json.dumps({'code': code, 'names': list(functions)}))
        # every message is a call until the one with the result
        while 'call' in (message := json.loads(socket.recv())):
            socket.send(run_call(message))
        if 'error' in message:
            raise RuntimeError(message['error'])


if __name__ == '__main__':
    main()
