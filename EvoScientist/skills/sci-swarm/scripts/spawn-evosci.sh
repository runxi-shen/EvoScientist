#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: spawn-evosci.sh <name> "<prompt>" [options]

Spawn an EvoSci CLI instance in an isolated tmux session.

Arguments:
  name      Session suffix (session will be named evosci-<name>)
  prompt    Research prompt to pass to EvoSci -p

Options:
  --socket-path <path>   tmux socket path (default: $EVOSCI_TMUX_SOCKET_DIR/evosci.sock)
  --workdir <path>       Workspace directory (default: workspace/runs/<name>)
  --model <model>        Model to use (e.g., claude-sonnet-4-5)
  --thread-id <id>       Thread ID for stateful multi-round conversations
  --no-thinking          Disable thinking output (saves tokens)
  --max-sessions <n>     Max allowed sessions (default: $EVOSCI_MAX_TMUX_SESSIONS or 5)
  --max-depth <n>        Max recursion depth (default: $EVOSCI_MAX_TMUX_DEPTH or 3)
  -h, --help             Show this help
USAGE
}

# --- Defaults ---
name=""
prompt=""
socket_path=""
workdir=""
model=""
thread_id=""
no_thinking=false
max_sessions="${EVOSCI_MAX_TMUX_SESSIONS:-5}"
max_depth="${EVOSCI_MAX_TMUX_DEPTH:-3}"
current_depth="${EVOSCI_TMUX_DEPTH:-0}"
socket_dir="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --socket-path)  socket_path="${2-}"; shift 2 ;;
    --workdir)      workdir="${2-}"; shift 2 ;;
    --model)        model="${2-}"; shift 2 ;;
    --thread-id)    thread_id="${2-}"; shift 2 ;;
    --no-thinking)  no_thinking=true; shift ;;
    --max-sessions) max_sessions="${2-}"; shift 2 ;;
    --max-depth)    max_depth="${2-}"; shift 2 ;;
    -h|--help)      usage; exit 0 ;;
    -*)             echo "Unknown option: $1" >&2; usage; exit 1 ;;
    *)
      if [[ -z "$name" ]]; then
        name="$1"
      elif [[ -z "$prompt" ]]; then
        prompt="$1"
      else
        echo "Unexpected argument: $1" >&2; usage; exit 1
      fi
      shift
      ;;
  esac
done

# --- Validate required args ---
if [[ -z "$name" || -z "$prompt" ]]; then
  echo "Error: name and prompt are required" >&2
  usage
  exit 1
fi

# Validate name (alphanumeric, hyphens, underscores only)
if ! [[ "$name" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  echo "Error: name must be alphanumeric with hyphens/underscores (got: $name)" >&2
  exit 1
fi

# --- Check prerequisites ---
if ! command -v tmux >/dev/null 2>&1; then
  echo "Error: tmux not found in PATH" >&2
  exit 1
fi

if ! command -v EvoSci >/dev/null 2>&1; then
  echo "Error: EvoSci not found in PATH (install with: pip install -e '.[dev]')" >&2
  exit 1
fi

# --- Check recursion depth ---
if (( current_depth >= max_depth )); then
  echo "Error: recursion depth limit reached (current=$current_depth, max=$max_depth)" >&2
  echo "Set EVOSCI_MAX_TMUX_DEPTH to increase the limit." >&2
  exit 1
fi

# --- Resolve socket ---
mkdir -p "$socket_dir"
if [[ -z "$socket_path" ]]; then
  socket_path="$socket_dir/evosci.sock"
fi

# --- Check session limit ---
session_count=0
if tmux -S "$socket_path" list-sessions 2>/dev/null | grep -c '^' > /dev/null 2>&1; then
  session_count=$(tmux -S "$socket_path" list-sessions 2>/dev/null | grep -c '^' || echo 0)
fi
if (( session_count >= max_sessions )); then
  echo "Error: session limit reached ($session_count/$max_sessions)" >&2
  echo "Kill completed sessions or increase --max-sessions." >&2
  exit 1
fi

# --- Check duplicate session ---
session_name="evosci-${name}"
if tmux -S "$socket_path" has-session -t "$session_name" 2>/dev/null; then
  if [[ -n "$thread_id" ]]; then
    # With --thread-id, reuse is intentional: kill the stale session
    # (EvoSci has exited but the tmux shell remains)
    tmux -S "$socket_path" kill-session -t "$session_name" 2>/dev/null || true
  else
    echo "Error: session '$session_name' already exists" >&2
    echo "Use a different name or kill the existing session:" >&2
    echo "  tmux -S \"$socket_path\" kill-session -t \"$session_name\"" >&2
    exit 1
  fi
fi

# --- Resolve workspace ---
if [[ -z "$workdir" ]]; then
  workdir="$(pwd)/workspace/runs/${name}"
fi

# Make workdir absolute if relative
if [[ "$workdir" != /* ]]; then
  workdir="$(pwd)/$workdir"
fi
mkdir -p "$workdir"

# --- Build EvoSci command ---
evosci_cmd="EvoSci -p $(printf '%q' "$prompt") --workdir $(printf '%q' "$workdir")"
if [[ -n "$model" ]]; then
  evosci_cmd+=" --model $(printf '%q' "$model")"
fi
if [[ -n "$thread_id" ]]; then
  evosci_cmd+=" --thread-id $(printf '%q' "$thread_id")"
fi
if [[ "$no_thinking" == true ]]; then
  evosci_cmd+=" --no-thinking"
fi

# --- Create tmux session ---
next_depth=$((current_depth + 1))

if ! tmux -S "$socket_path" new-session -d -s "$session_name" -n shell \
  -e "EVOSCI_TMUX_DEPTH=$next_depth" \
  -e "EVOSCI_TMUX_SOCKET_DIR=$socket_dir" 2>/dev/null; then
  echo "Error: failed to create tmux session '$session_name'" >&2
  exit 2
fi

# --- Launch EvoSci ---
tmux -S "$socket_path" send-keys -t "$session_name":0.0 -- "$evosci_cmd" Enter

# --- Update registry ---
registry_file="$socket_dir/registry.json"
EVOSCI_REG_FILE="$registry_file" \
EVOSCI_REG_NAME="$name" \
EVOSCI_REG_SESSION="$session_name" \
EVOSCI_REG_SOCKET="$socket_path" \
EVOSCI_REG_WORKDIR="$workdir" \
EVOSCI_REG_PROMPT="$prompt" \
EVOSCI_REG_DEPTH="$next_depth" \
EVOSCI_REG_THREAD="$thread_id" \
python3 -c "
import json, os, datetime
registry_path = os.environ['EVOSCI_REG_FILE']
try:
    with open(registry_path) as f:
        registry = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    registry = []
entry = {
    'name': os.environ['EVOSCI_REG_NAME'],
    'session': os.environ['EVOSCI_REG_SESSION'],
    'socket': os.environ['EVOSCI_REG_SOCKET'],
    'workdir': os.environ['EVOSCI_REG_WORKDIR'],
    'prompt': os.environ['EVOSCI_REG_PROMPT'],
    'status': 'running',
    'started_at': datetime.datetime.now().isoformat(),
    'depth': int(os.environ['EVOSCI_REG_DEPTH'])
}
thread_id = os.environ.get('EVOSCI_REG_THREAD', '')
if thread_id:
    entry['thread_id'] = thread_id
registry.append(entry)
with open(registry_path, 'w') as f:
    json.dump(registry, f, indent=2)
" 2>/dev/null || true

# --- Print confirmation ---
echo "Spawned EvoSci session: $session_name"
echo "  Workspace: $workdir"
if [[ -n "$thread_id" ]]; then
  echo "  Thread ID: $thread_id (stateful)"
fi
echo "  Depth: $next_depth/$max_depth"
echo ""
echo "Monitor commands:"
echo "  tmux -S \"$socket_path\" attach -t \"$session_name\""
echo "  tmux -S \"$socket_path\" capture-pane -p -J -t \"$session_name\":0.0 -S -200"
