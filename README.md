# Sandbox Benchmarks

An experiment comparing how code running in a remote sandbox can call functions on the host machine, across
eight sandbox providers: Pydantic Logfire Agent Sandboxes, Modal, E2B, Daytona, Vercel Sandbox, Cloudflare sandboxes,
boat.dev and boxd.

Each script in `play_tune/` runs the same program in a sandbox, a tune that calls `play(note, seconds)` 78
times, and the host plays each note through the speakers. The modules in `benchmarks/` time five
cases on each provider.

<!-- results: written by results.py -->

Median milliseconds from GCP us-east4-a, 2026-11-25 to 2026-11-26, from asking for a new session to having the result:

| Provider                                                                       | `1 + 1`     | `1 + 1` direct | one external call | 5 feeds    | 10 feeds x 5 calls |
| ------------------------------------------------------------------------------ | ----------- | -------------- | ----------------- | ---------- | ------------------ |
| [Pydantic Logfire (turbo)](https://pydantic.dev/docs/logfire/agent-sandboxes/) | 24          | 23             | 36                | 74         | 692 (1.0x)         |
| [Pydantic Logfire](https://pydantic.dev/docs/logfire/agent-sandboxes/)         | 85 (3.5x)   | 85 (3.7x)      | 91 (2.5x)         | 125 (1.7x) | 661                |
| [E2B](https://e2b.dev)                                                         | 459 (19x)   | 514 (22x)      | 548 (15x)         | 773 (10x)  | 5056 (7.6x)        |
| [Modal](https://modal.com/docs/guide/sandboxes)                                | 464 (19x)   | 414 (18x)      | 434 (12x)         | 818 (11x)  | 1480 (2.2x)        |
| [Daytona](https://www.daytona.io)                                              | 506 (21x)   | 131 (5.7x)     | 569 (16x)         | 926 (13x)  | 3306 (5.0x)        |
| [boxd](https://boxd.sh)                                                        | 787 (33x)   | 828 (36x)      | 799 (22x)         | 847 (11x)  | 1282 (1.9x)        |
| [Cloudflare sandbox](https://developers.cloudflare.com/sandbox/)               | 880 (37x)   | 960 (42x)      | 886 (25x)         | 914 (12x)  | 3243 (4.9x)        |
| [Vercel](https://vercel.com/docs/sandbox)                                      | 1070 (45x)  | 772 (34x)      | 944 (26x)         | 1543 (21x) | 5934 (9.0x)        |
| [boat.dev](https://boat.dev)                                                   | 2395 (100x) | 1016 (44x)     | 2390 (66x)        | 3702 (50x) | 7480 (11x)         |

- Pydantic Logfire (turbo): Pydantic Logfire with `pre_warm=5`: the client keeps five sessions open in the background, so asking for one takes a session that already exists instead of waiting for one to start. They are opened before the timer starts, and the second the benchmark waits between sessions is enough for the pool to refill. Every other row starts its sandbox inside the timed part.
- Daytona: The sandboxes are in Daytona's `us` region.
- boxd: The machines are in boxd's `nyc` region.
- boat.dev: Five sessions per case, to stay inside boat.dev's start limits. Its sandboxes are all in the EU.

<!-- /results -->

## Running the cases

You need [uv](https://docs.astral.sh/uv/) and an account with each provider you want to run. Put the keys in a
`.env` file as `export` lines, then install and load them:

```bash
uv sync
. ./.env
```

| Provider                 | Module                          | Needs                                                                                               |
| ------------------------ | ------------------------------- | --------------------------------------------------------------------------------------------------- |
| Pydantic Logfire         | `benchmarks.logfire`            | `LOGFIRE_API_KEY`                                                                                   |
| Pydantic Logfire (turbo) | `benchmarks.logfire_turbo`      | `LOGFIRE_API_KEY`                                                                                   |
| Modal                    | `benchmarks.modal`              | `modal setup` to have been run                                                                      |
| E2B                      | `benchmarks.e2b`                | `E2B_API_KEY`                                                                                       |
| Cloudflare sandbox       | `benchmarks.cloudflare_sandbox` | `CLOUDFLARE_SANDBOX_URL` and `CLOUDFLARE_SANDBOX_KEY`, for your deployment of `cloudflare_sandbox/` |
| Daytona                  | `benchmarks.daytona`            | `DAYTONA_API_KEY`                                                                                   |
| Vercel                   | `benchmarks.vercel`             | `VERCEL_OIDC_TOKEN`, which `vercel env pull` writes and which lasts 12 hours                        |
| boat.dev                 | `benchmarks.boat`               | `BOAT_API_KEY`, and `ssh` on your machine                                                           |
| boxd                     | `benchmarks.boxd`               | `BOXD_API_KEY`                                                                                      |

Run a module from the root of the repo, with the number of sessions per case (5 if you leave it out):

```bash
uv run -m benchmarks.modal 15
```

It runs the five cases in turn and prints a line for each. The median, minimum and maximum are milliseconds
from asking for a session to having the result; `end` is the median time ending the session then took:

```
modal              1 + 1                p50     346  min     252  max    1901 ms  end    19 ms  (n=15)
```

Each session creates a sandbox and deletes it afterwards, and the providers bill for them.

Cloudflare has no hosted API for its sandboxes, so that one needs the Worker in `cloudflare_sandbox/` deployed to
your own account first:

```bash
cd cloudflare_sandbox
npm install && npx wrangler deploy    # Docker must be running
npx wrangler secret put API_KEY       # the value you then use as CLOUDFLARE_SANDBOX_KEY
```

`CLOUDFLARE_SANDBOX_URL` is the Worker's address with `wss://` in front.

To run every provider from US east, `gcp_bench.sh` creates a VM in a GCP project of yours, runs them there and
deletes the VM:

```bash
PROJECT=my-project ./gcp_bench.sh 15
```

## How host functions are called

`1 + 1` is run the way the last three cases are, so it pays for what they need: most real uses call
functions on the host or keep state, which makes it the fairer of the two sums to compare on.

`1 + 1 direct` is the same sum with neither, so it uses each provider's own way of running a program, such as
E2B's `run_code` or `python -c` through Modal's `exec`, and reads what it prints.

The four cases other than `1 + 1 direct` call functions on the host or keep state between feeds. Only Pydantic
Logfire has that built in. For the others we built a request and reply on whatever the provider's docs show for talking to a
running program, and it is started only for those cases. The docstring at the top of each module in
`benchmarks/`, such as [`benchmarks/modal.py`](benchmarks/modal.py), says why it was done that way and what it
costs; the one in [`benchmarks/shared.py`](benchmarks/shared.py) covers what they have in common.

## Results

Each provider's latest run is a JSON file in `results/`, and `results.py` builds the table above from them. The
figure in brackets is how many times slower that provider is than the fastest at that case.

The times in the table stop when the result arrives. Ending the session, which for most providers means
deleting the sandbox, is timed separately and kept in each file under `end`, since nobody waits for it.

```bash
uv run results.py             # print the table; --ranges for minimum to maximum
uv run results.py --write     # replace the table in this file
```

To update a provider, run its benchmark with `--save` and where you ran it from, then `uv run results.py --write`:

```bash
uv run -m benchmarks.modal 15 --save 'GCP us-east4-a'
```

Times from different places cannot be compared, so the results here are all from a GCP VM in `us-east4`, which
`gcp_bench.sh` does for you: it saves each provider's results and copies them back into `results/`.

[`AGENTS.md`](AGENTS.md) describes the files, how to run them and the credentials each provider needs.
