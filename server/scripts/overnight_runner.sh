#!/bin/bash
# Overnight Terminal-Bench runner.
# Monitors the current collaborate run, then kicks off codex solo.
# Writes all progress to /tmp/overnight-runner.log
#
# Usage: nohup bash scripts/overnight_runner.sh > /tmp/overnight-runner.log 2>&1 &

set -euo pipefail
cd /root/orpheus/server

SERVER_URL="https://orpheus-server-morphvm-p5wwmknl.http.cloud.morph.so"
AUTH_TOKEN="test-eval-token"
RESULTS="/tmp/terminal-bench-v2-run/results.json"
RESULTS_DIR="/root/orpheus/server/results"

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $*"
}

count_agent_results() {
    local agent="$1"
    python3 -c "
import json
r = json.loads(open('$RESULTS').read())
print(sum(1 for k in r if '/$agent' in k))
" 2>/dev/null || echo 0
}

save_results() {
    local agent="$1"
    local label="$2"
    local date=$(date -u '+%Y-%m-%d')
    mkdir -p "$RESULTS_DIR"
    python3 -c "
import json
r = json.loads(open('$RESULTS').read())
filtered = {k:v for k,v in r.items() if '/$agent' in k}
open('$RESULTS_DIR/terminal-bench-v2-${label}-${date}.json','w').write(json.dumps(filtered, indent=2))
print(f'Saved {len(filtered)} results')
"
}

print_summary() {
    local agent="$1"
    python3 -c "
import json
r = json.loads(open('$RESULTS').read())
filtered = {k:v for k,v in r.items() if '/$agent' in k}
passed = sum(1 for v in filtered.values() if v.get('passed'))
total = len(filtered)
print(f'  {passed}/{total} passed')
for k in sorted(filtered):
    v = filtered[k]
    icon = 'PASS' if v.get('passed') else 'FAIL'
    elapsed = v.get('elapsed', 0)
    print(f'    {icon} {k:<55s} {elapsed:>6.0f}s')
"
}

check_server() {
    local status=$(curl -s -o /dev/null -w "%{http_code}" "$SERVER_URL/health" -H "Authorization: Bearer $AUTH_TOKEN" 2>/dev/null)
    if [ "$status" != "200" ]; then
        log "WARNING: Server not healthy (status=$status), restarting..."
        # Check if server process exists
        if ! pgrep -f "orpheus.app" > /dev/null 2>&1; then
            log "Server not running, starting..."
            cd /root/orpheus/server
            nohup .venv/bin/python3 -m orpheus.app > /tmp/orpheus-server.log 2>&1 &
            sleep 10
        fi
    fi
}

# =========================================================
# Phase 1: Wait for collaborate run to finish (9 tasks)
# =========================================================
log "=== Phase 1: Monitoring collaborate run (9 tasks) ==="

while true; do
    n=$(count_agent_results "collaborate")
    log "Collaborate: $n/9 tasks complete"
    if [ "$n" -ge 9 ]; then
        log "Collaborate run finished!"
        break
    fi
    # Show active executions
    .venv/bin/python3 -u scripts/monitor_runs.py 2>/dev/null || true
    log "Sleeping 5 minutes..."
    sleep 300
done

# Save collaborate results
log "Saving collaborate results..."
save_results "collaborate" "collaborate-opus46-codex52"
log "Collaborate summary:"
print_summary "collaborate"

# =========================================================
# Phase 2: Run mteb if snapshot ready
# =========================================================
log "=== Phase 2: Check mteb-leaderboard snapshot ==="
mteb_snap=$(.venv/bin/python3 -c "
from orpheus.evals.terminal_bench import V2Loader
loader = V2Loader()
m = loader.load_metadata('mteb-leaderboard')
print(m.snapshot_id if m else 'none')
" 2>/dev/null || echo "none")

if [ "$mteb_snap" != "none" ]; then
    log "mteb snapshot found ($mteb_snap), running collaborate on it..."
    check_server
    .venv/bin/python3 -u scripts/run_terminal_bench.py \
        --tasks mteb-leaderboard \
        --agent collaborate --resume \
        --server-url "$SERVER_URL" --auth-token "$AUTH_TOKEN" \
        2>&1 | while read line; do log "  [collab-mteb] $line"; done

    save_results "collaborate" "collaborate-opus46-codex52"
    log "Updated collaborate results with mteb"
else
    log "mteb snapshot not ready, skipping"
fi

# =========================================================
# Phase 3: Run codex 5.2 solo (high reasoning) on all tasks
# =========================================================
log "=== Phase 3: Starting codex 5.2 solo run (high reasoning) ==="
check_server

# Clear old codex results so we get a clean run
python3 -c "
import json
f = '$RESULTS'
r = json.loads(open(f).read())
kept = {k:v for k,v in r.items() if '/codex' not in k}
open(f, 'w').write(json.dumps(kept, indent=2))
print(f'Cleared codex results, kept {len(kept)}')
"

CODEX_TASKS="break-filter-js-from-html circuit-fibsqrt feal-linear-cryptanalysis financial-document-processor llm-inference-batching-scheduler overfull-hbox qemu-alpine-ssh sanitize-git-repo winning-avg-corewars"

# Add mteb if snapshot is available
if [ "$mteb_snap" != "none" ]; then
    CODEX_TASKS="$CODEX_TASKS mteb-leaderboard"
fi

log "Running codex on tasks: $CODEX_TASKS"
.venv/bin/python3 -u scripts/run_terminal_bench.py \
    --tasks $CODEX_TASKS \
    --agent codex --model gpt-5.2-codex \
    --concurrency 5 --resume \
    --server-url "$SERVER_URL" --auth-token "$AUTH_TOKEN" \
    2>&1 | while read line; do log "  [codex] $line"; done

# Save codex results
log "Saving codex results..."
save_results "codex" "codex-52-high-reasoning"
log "Codex summary:"
print_summary "codex"

# =========================================================
# Phase 4: Final summary
# =========================================================
log "=== All runs complete ==="
log ""
log "Claude solo (opus-4.6):"
print_summary "claude"
log ""
log "Collaborate (opus-4.6 + codex-5.2):"
print_summary "collaborate"
log ""
log "Codex solo (5.2 high reasoning):"
print_summary "codex"
log ""
log "Results saved to: $RESULTS_DIR/"
ls -la "$RESULTS_DIR/"
log "=== Done ==="
