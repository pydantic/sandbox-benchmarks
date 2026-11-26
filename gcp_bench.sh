#!/usr/bin/env bash
# Run the benchmarks against every provider from a temporary GCP VM, then delete the VM.
#
#     ./gcp_bench.sh [n] [output-file]
#
# n is the number of sessions per case (default 15). PROJECT, ZONE and PROVIDERS can be set in the environment;
# a provider written `name:n` gets its own number of sessions, as boat does by default to stay inside its limits.
# The VM gets this repo's tracked files, the keys from .env and ~/.modal.toml. Logfire uses LOGFIRE_API_KEY_US.
# The VM is deleted when this script exits, however it exits, and deletes itself after two hours if this
# machine never gets that far.
set -euo pipefail
cd "$(dirname "$0")"

N=${1:-15}
OUT=${2:-gcp_bench_output.txt}
PROJECT=${PROJECT:?set PROJECT to a GCP project}
ZONE=${ZONE:-us-east4-a}
PROVIDERS=${PROVIDERS:-logfire logfire_turbo modal daytona e2b vercel cloudflare_sandbox boat:5 boxd}
VM=sandbox-bench-$(date +%s)
GC=(gcloud compute --project "$PROJECT")

work=$(mktemp -d)
cleanup() {
    rm -rf "$work"
    echo "deleting $VM"
    "${GC[@]}" instances delete "$VM" --zone "$ZONE" --quiet || echo "FAILED TO DELETE $VM, delete it by hand"
}
trap cleanup EXIT

# the machine type the runs in results/ used
"${GC[@]}" instances create "$VM" --zone "$ZONE" --machine-type e2-standard-8 \
    --image-family debian-12 --image-project debian-cloud --boot-disk-size 30GB \
    --max-run-duration 2h --instance-termination-action DELETE

mkdir -p "$work/code/sandbox_benchmarks"
git ls-files -z | xargs -0 tar -cf - | tar -xf - -C "$work/code/sandbox_benchmarks"
(
    . ./.env
    umask 077
    cat >"$work/code/sandbox_benchmarks/.env" <<EOF
export DAYTONA_API_KEY='$DAYTONA_API_KEY'
${DAYTONA_TARGET:+export DAYTONA_TARGET='$DAYTONA_TARGET'}
export E2B_API_KEY='$E2B_API_KEY'
export LOGFIRE_API_KEY='$LOGFIRE_API_KEY_US'
export VERCEL_OIDC_TOKEN='${VERCEL_OIDC_TOKEN:-}'
export CLOUDFLARE_SANDBOX_URL='${CLOUDFLARE_SANDBOX_URL:-}'
export CLOUDFLARE_SANDBOX_KEY='${CLOUDFLARE_SANDBOX_KEY:-}'
export BOAT_API_KEY='${BOAT_API_KEY:-}'
export BOXD_API_KEY='${BOXD_API_KEY:-}'
EOF
    cp ~/.modal.toml "$work/.modal.toml"
)
tar -czf "$work/upload.tgz" -C "$work" code .modal.toml

for _ in $(seq 30); do
    "${GC[@]}" ssh "$VM" --zone "$ZONE" --command true 2>/dev/null && break
    sleep 5
done
"${GC[@]}" scp --zone "$ZONE" "$work/upload.tgz" "$VM:upload.tgz"

"${GC[@]}" ssh "$VM" --zone "$ZONE" --command "N=$N ZONE=$ZONE PROVIDERS='$PROVIDERS' bash -s" <<'REMOTE' | tee "$OUT"
set -uo pipefail
{
    tar -xzf upload.tgz && rm upload.tgz
    sudo apt-get update -q && sudo apt-get install -yq build-essential curl
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH=~/.local/bin:$PATH UV_PYTHON=3.15
    cd ~/code/sandbox_benchmarks
    . ./.env
    uv sync
} </dev/null >~/setup.log 2>&1 || { tail -50 ~/setup.log; exit 1; }

echo "# $(date -u +%FT%TZ) $(curl -s -H Metadata-Flavor:Google \
    http://metadata.google.internal/computeMetadata/v1/instance/zone | sed 's|.*/||') n=$N"
# Logfire's endpoint is behind Cloudflare, and which of its locations a VM is sent to changes Logfire's times:
# print the location, the time to connect to it, and the time of a request to Logfire itself on an open connection
logfire=https://logfire-us.pydantic.dev
echo "# logfire $(curl -s $logfire/cdn-cgi/trace | grep colo=) $(curl -s -o /dev/null -o /dev/null \
    -w 'tcp connect %{time_connect}s request %{time_total}s ' $logfire/ $logfire/)"
for entry in $PROVIDERS; do
    # `name:n` gives that provider its own number of sessions
    provider=${entry%%:*}
    n=$N
    [[ $entry == *:* ]] && n=${entry##*:}
    uv run -m "benchmarks.$provider" "$n" --save "GCP $ZONE" </dev/null 2>&1 || echo "$provider FAILED"
done
REMOTE

# a provider that failed part way wrote nothing, so its file here is left as it was
"${GC[@]}" scp --zone "$ZONE" "$VM:code/sandbox_benchmarks/results/*.json" results/
uv run results.py --write
