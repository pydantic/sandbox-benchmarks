// Runs code from a WebSocket in a Dynamic Worker, in Python or, with `?lang=js`, JavaScript. The code calls the
// host through `exchange(line)`, an RPC call back to this Worker, which sends the line down the socket and
// returns the next message the client sends. https://developers.cloudflare.com/dynamic-workers/usage/bindings/
//
// Python: the first message is a program, run in a new isolate, which blocks on each call until the client
// answers. What it prints is sent as the last message, then the socket is closed.
//
// JavaScript: the first message is `{"code": module, "names": [host functions]}`. The module's default export
// is called with the host functions, which send each call as a line of JSON and await the reply, and the last
// message is `{"result": ..., "printed": ...}` or `{"error": ...}`. There is no REPL: Workers only compile code
// from a string while a module loads, so a JavaScript isolate cannot run one feed after another.
import { RpcTarget } from 'cloudflare:workers'

const COMPATIBILITY_DATE = '2026-11-24'

// the Python Dynamic Worker's only module: run() is called over RPC with the program and the client, and returns
// what the program printed
const MAIN_PY = `
import io, sys
from pyodide.ffi import run_sync
from workers import WorkerEntrypoint

class Default(WorkerEntrypoint):
    async def run(self, program, client):
        def exchange(line):
            # run_sync blocks the Python code until the RPC call returns
            return run_sync(client.exchange(line))

        out = sys.stdout = io.StringIO()
        try:
            exec(program, {'__name__': '__main__', 'exchange': exchange})
        finally:
            sys.stdout = sys.__stdout__
        return out.getvalue()
`

// the JS Dynamic Worker's main module, loaded with the client's code as program.js
const MAIN_JS = `
import { WorkerEntrypoint } from 'cloudflare:workers'
import program from './program.js'

export default class extends WorkerEntrypoint {
  async run(client, names) {
    let printed = ''
    console.log = (...args) => {
      printed += args.join(' ') + '\\n'
    }
    const call = async (name, ...args) => {
      const reply = JSON.parse(await client.exchange(JSON.stringify({ call: name, args })))
      if ('error' in reply) throw new Error(reply.error)
      return reply.result
    }
    const functions = Object.fromEntries(names.map((name) => [name, (...args) => call(name, ...args)]))
    const result = await program(functions)
    return { result: result ?? null, printed }
  }
}
`

// the messages from the client, in order
class Inbox {
  messages = []
  waiting = null
  closed = false

  push(message) {
    if (this.waiting) {
      this.waiting(message)
      this.waiting = null
    } else {
      this.messages.push(message)
    }
  }

  close() {
    this.closed = true
    this.push(null)
  }

  next() {
    if (this.messages.length) return Promise.resolve(this.messages.shift())
    if (this.closed) return Promise.resolve(null)
    return new Promise((resolve) => (this.waiting = resolve))
  }
}

// passed to the Dynamic Worker as an argument of run(), by reference, so the calls it makes on it run here
class Client extends RpcTarget {
  constructor(socket, inbox) {
    super()
    this.socket = socket
    this.inbox = inbox
  }

  // send a line to the client, unless it is null, and return the next line it sends, or null once it has gone
  exchange(line) {
    if (line != null) this.socket.send(line)
    return this.inbox.next()
  }
}

async function pythonSession(socket, inbox, env) {
  const program = await inbox.next()
  if (program == null) return
  const worker = env.LOADER.load({
    compatibilityDate: COMPATIBILITY_DATE,
    compatibilityFlags: ['python_workers'],
    mainModule: 'main.py',
    modules: { 'main.py': MAIN_PY },
    // no network at all: the program can only reach the client, through the Client it is given
    globalOutbound: null,
  })
  // an RpcTarget cannot go in the Dynamic Worker's env, but it can be an argument of an RPC call
  const printed = await worker.getEntrypoint().run(program, new Client(socket, inbox))
  if (printed) socket.send(printed)
  socket.close(1000, 'exit')
}

async function jsSession(socket, inbox, env) {
  const message = await inbox.next()
  if (message == null) return
  const { code, names } = JSON.parse(message)
  const worker = env.LOADER.load({
    compatibilityDate: COMPATIBILITY_DATE,
    mainModule: 'main.js',
    modules: { 'main.js': MAIN_JS, 'program.js': code },
    globalOutbound: null,
  })
  let reply
  try {
    reply = await worker.getEntrypoint().run(new Client(socket, inbox), names)
  } catch (e) {
    reply = { error: String(e) }
  }
  socket.send(JSON.stringify(reply))
  socket.close(1000, 'exit')
}

export default {
  async fetch(request, env, ctx) {
    // without the secret set, nothing gets in
    if (!env.API_KEY || request.headers.get('Authorization') !== `Bearer ${env.API_KEY}`) {
      return new Response('unauthorised', { status: 401 })
    }
    if (request.headers.get('Upgrade') !== 'websocket') {
      return new Response('expected a WebSocket', { status: 426 })
    }
    const [client, server] = Object.values(new WebSocketPair())
    server.accept()
    const inbox = new Inbox()
    server.addEventListener('message', (event) => inbox.push(event.data))
    server.addEventListener('close', () => inbox.close())
    const session = new URL(request.url).searchParams.get('lang') === 'js' ? jsSession : pythonSession
    ctx.waitUntil(session(server, inbox, env).catch((e) => server.close(1011, String(e).slice(0, 120))))
    return new Response(null, { status: 101, webSocket: client })
  },
}
