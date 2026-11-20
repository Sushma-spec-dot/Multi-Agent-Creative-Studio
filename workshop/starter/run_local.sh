#!/bin/bash
# Run the whole studio on your computer: the 5 specialists as local A2A servers (ports 8101-8105), then the
# Creative Director against them (run_local.py). Needs `gcloud auth application-default login` and .env filled in.
#   ./run_local.sh                  # quality bar from .env (default 8)
#   QUALITY_BAR=9 ./run_local.sh    # stricter bar: watch the Critic send work back for revision
set -u
STARTER="$(cd "$(dirname "$0")" && pwd)"
LOGS="$STARTER/.local_logs"; mkdir -p "$LOGS"
PIDS=()
start() { # agent folder, port
  (cd "$STARTER/agents/$1" && PORT=$2 PUBLIC_PORT=$2 .venv/bin/python agent.py >"$LOGS/$1.log" 2>&1) & PIDS+=($!)
}
start brand_strategist 8101; start copywriter 8102; start designer 8103; start critic 8104; start project_manager 8105
trap 'kill "${PIDS[@]}" 2>/dev/null; pkill -f "agents/.*/agent.py" 2>/dev/null' EXIT
for p in 8101 8102 8103 8104 8105; do
  for i in $(seq 1 60); do curl -s -o /dev/null "http://localhost:$p/.well-known/agent-card.json" && break; sleep 1; done
done
export STRATEGIST_AGENT_URL=http://localhost:8101 COPYWRITER_AGENT_URL=http://localhost:8102 \
  DESIGNER_AGENT_URL=http://localhost:8103 CRITIC_AGENT_URL=http://localhost:8104 PM_AGENT_URL=http://localhost:8105
cd "$STARTER" && .venv/bin/python run_local.py
