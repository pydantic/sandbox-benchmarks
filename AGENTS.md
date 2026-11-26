# Sandbox Benchmarks

An experiment comparing how code running in a remote sandbox can call functions on the host machine, across
eight sandbox providers. Each script runs the same sandboxed program, a tune that calls `play(note, seconds)`
78 times, and the host plays each note through the speakers.

## Files

`play_tune/` has one script per provider, each playing the tune:

- `play_tune.py`: Logfire Agent Sandboxes (Monty). Also the shared module: the other scripts import
  `code` (the program run in the sandbox), `play` (the host function) and `speakers` from it.
- `play_tune_modal.py`: Modal. Calls go as JSON lines over the stdin and stdout of `sandbox.exec`.
- `play_tune_e2b.py`: E2B. The same JSON-lines protocol, over a background command with `send_stdin`.
- `play_tune_daytona.py`: Daytona. A Flask broker runs inside the sandbox; the host polls it through the
  preview URL and posts replies back.
- `play_tune_vercel.py`: Vercel Sandbox. The same JSON-lines protocol, over the stdin of a running command.
- `play_tune_cloudflare_sandbox.py`: Cloudflare sandboxes. The same JSON-lines protocol, over a WebSocket to the
  Worker in `cloudflare_sandbox/`, which passes it to the stdin and stdout of the program in a container.
- `play_tune_boat.py`: boat.dev. The same JSON-lines protocol, over `ssh`, since its commands have no stdin.
- `play_tune_boxd.py`: boxd. The same JSON-lines protocol, over the stdin and stdout of an interactive `exec`.

`benchmarks/` has one module per provider, each running the same five cases:

- `shared.py`: the `Pool` and `Session` base classes, the five cases, the loop that times each from asking
  for a new session to having the result, and the REPL the providers other than Logfire and Daytona run in
  the sandbox.
- `logfire.py`, `logfire_turbo.py` (Logfire with `pre_warm`), `modal.py`, `e2b.py`, `daytona.py`, `vercel.py`,
  `cloudflare_sandbox.py`, `boat.py`, `boxd.py`: a `Pool` whose `checkout()` gives a `Session`. Its `run(code)` runs a
  program the provider's own way and is what `1 + 1 direct` uses; its `feed_run(code, external_lookup=...)` has
  Monty's interface, and for that each provider but Logfire starts a small REPL in the sandbox over the
  same transport as its `play_tune_*.py` script, so state survives between feeds and each feed returns its
  last expression. To add a provider, add a module with a `Pool` and a `Session` and call `main`.

The rest:

- `cloudflare_sandbox/`: the Worker for Cloudflare sandboxes, which have no hosted API. Deploy it with
  `npm install && npx wrangler deploy` (Docker must be running), then `npx wrangler secret put API_KEY`.
- `gcp_bench.sh`: runs every benchmark from a temporary GCP VM in `us-east4`, copies the results into
  `results/`, then deletes the VM.
- `results/` and `results.py`: each provider's latest run, and the script that prints them as a table.
- `balls_video.py`: renders a video of one ball per provider, each crossing in its median `1 + 1` time. It needs
  each provider's icon in `logos/`, which is git-ignored.

Only Logfire has host function calls built in (`external_lookup={'play': play}`). The others have no
such feature, so each script carries a `stub` that is prepended to the sandbox code, and a host loop that
answers the calls. The protocols follow what each provider's docs recommend; the docstring of each script
links the source.

## Running

```bash
uv sync
. ./.env
uv run play_tune/play_tune.py           # or play_tune/play_tune_modal.py, play_tune/play_tune_e2b.py, ...
```

Each benchmark is a module, run from the repo root, taking the number of sessions per case (default 5):

```bash
uv run -m benchmarks.logfire 5     # or logfire_turbo, modal, e2b, daytona, vercel, cloudflare_sandbox, boat, boxd
```

To run it from US east, which creates a VM in a GCP project of yours and deletes it afterwards:

```bash
PROJECT=my-project ./gcp_bench.sh 15                     # ZONE=us-east1-b for another zone
```

Credentials:

- `.env` holds `DAYTONA_API_KEY`, `E2B_API_KEY`, `LOGFIRE_API_KEY_EU` and `LOGFIRE_API_KEY_US` as `export`
  lines. Logfire reads `LOGFIRE_API_KEY`, so export one of the two under that name; `gcp_bench.sh` uses the US
  one. It is git-ignored and nothing loads it automatically, so source it first.
- Modal uses the credentials written by `modal setup`.
- Vercel needs `VERCEL_OIDC_TOKEN`; Cloudflare sandboxes need
  `CLOUDFLARE_SANDBOX_URL` (`wss://...workers.dev`) and `CLOUDFLARE_SANDBOX_KEY` (the Worker's `API_KEY`); boat
  needs `BOAT_API_KEY`; boxd needs `BOXD_API_KEY`. All go in `.env`.

Never print, log or commit key values.

## Rules for changes

- The scripts play audio out loud. To test without sound, replace `speakers` and `play` with stubs that
  return instantly rather than running the scripts as they are.
- Stdout alone is not an acceptable transport. The point is a request and reply mechanism that works for many
  different host functions with return values, so the sandbox must block on each call until the host answers.
- To add a host function, add it to the `functions` dict and add a wrapper to `stub` in each of the
  non-Logfire scripts, and to `external_lookup` in `play_tune/play_tune.py`.
- Keep the scripts short and comparable: the same `code`, the same `play`, and only the transport differing.
- Always terminate or delete the sandbox in a `finally` block, since they are billed while they run.
- Python 3.15, managed with uv. Single quotes, type hints on host functions.

## Benchmark results

`results/` has one JSON file per provider, holding its latest run: the date, where it was run from, the number
of sessions and, for each case, the median, minimum and maximum in milliseconds, the times themselves, and
the same under `end` for ending the session. A
benchmark run with `--save LOCATION` replaces its file, keeping the `note` of the one it replaces.
`uv run results.py` prints the table and `uv run results.py --write` replaces the one in `README.md`; never edit
that table or the numbers in the JSON files by hand.

Only commit results run from a GCP VM in `us-east4` (`gcp_bench.sh` saves them and copies them back), since
times from different places cannot be compared.

Each time runs from entering `checkout()` to having the result, so for every provider but Logfire it includes
creating the sandbox, and for the four cases that use `feed_run`, starting the REPL. `1 + 1 direct` is `1 + 1`
through `run` instead, to show what the REPL costs. Ending the session, which
for most means deleting the sandbox, is timed separately and saved under `end`; the table does not show it.

## Known behaviour

- Logfire's times vary between VMs for a reason not yet found. On 2026-11-25 one full run from `us-east4-a` gave
  45ms for Logfire (turbo) at `1 + 1 direct`, 218ms for Logfire and 4715ms for its `10 feeds x 5 calls`; five other VMs
  in the same zone that day gave 21 to 26ms, 46 to 99ms and 661 to 847ms. The Logfire results in `results/` are
  from a second VM half an hour after that slow run, and the other providers' are from the slow run's VM.
  `gcp_bench.sh` prints the Cloudflare location serving Logfire's endpoint and the time of a request to it.
- GCP `us-east4` VMs reach Cloudflare at different locations (IAD at 3ms, ATL at 10 to 18ms, EWR at 35ms have
  all been seen), and Logfire's US endpoint is that much slower per round trip from a VM with a slow one.
  Check the `# cloudflare colo=` line `gcp_bench.sh` prints before comparing Logfire runs.
- `benchmarks/` must not import the `play_tune/` scripts: they import `sounddevice`, which fails on the VM.
- boat refuses starts over its limit with `429 rate_limited`: 5 a minute, 25 an hour and 75 a day on the trial,
  12, 60 and 200 on the $20 plan. The benchmark waits and tries again without timing the refused attempt.
- boat's `ssh` needs `IdentitiesOnly=yes`, or it offers the agent's keys first and is disconnected.
- Vercel's `VERCEL_OIDC_TOKEN` comes from `vercel env pull` in a linked project, and it lasts
  12 hours. Each reply to a call is its own HTTPS request, about 200ms from London, and stopping and deleting
  a sandbox takes about 1.4 seconds.
- The Cloudflare sandbox Worker deploys under the name `sandbox-host-function`.
- The boxd machines are in its `nyc` region, the nearest to `us-east4`. boxd also has `fra`.
- Closing the WebSocket ends a Cloudflare sandbox session, and the Worker destroys the container after that,
  so its `end` times are near zero and do not include the deletion. The first message on the socket is the
  program to run, so one of its sessions can do a `run` or a series of `feed_run`s, not both.
- Logfire refuses sessions with `worker_unavailable` for about 20 seconds after 32 in a row. The benchmark waits
  and tries again without timing the refused attempt, and prints how many were refused.
- The Daytona sandboxes are in its `us` region, the default of the organisation the results were run with.
  Daytona also has `eu`; an organisation can only use the regions made available to it, and creating a sandbox
  in another fails with `Region ... is not available to the organization`. Its SDK takes the region from
  `DAYTONA_TARGET` when that is set, which `gcp_bench.sh` passes on. From `us-east4`, `eu` sandboxes took about
  three times as long for `1 + 1` (1635ms against 582ms).
- Round trip per call with an instant `play`, measured from one machine: Logfire about 29ms, Daytona about
  56ms, Modal 100 to 150ms, E2B about 200ms. Logfire is three runs, the others one or two.
- Logfire's hosted sandbox limits each `feed_run` to 50ms of execution, not counting time spent waiting on
  host functions. Synthesising audio inside the sandbox exceeds it.
- The `play_tune_modal.py` runs stalled for minutes a few times, once with a hostname resolution error. The
  cause is unknown. `benchmarks/modal.py` uses Modal's async interface (`.aio`), because its blocking one, called
  inside an event loop, gave an 11 second single call and a `RuntimeError` at shutdown.
- The Daytona broker must wait for the reply before removing its queue (`replies[call_id].get()` and then
  `del`), or the sandbox hangs after the first call.
