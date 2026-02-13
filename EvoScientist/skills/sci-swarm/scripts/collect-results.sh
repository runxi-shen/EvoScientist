#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: collect-results.sh <name> [options]

Collect results from a spawned EvoSci session.

Arguments:
  name    Session name suffix (matches evosci-<name>)

Options:
  --socket-path <path>   tmux socket path (default: $EVOSCI_TMUX_SOCKET_DIR/evosci.sock)
  --workdir <path>       Workspace directory (default: workspace/runs/<name>)
  --pane-output          Also capture the final pane output
  -h, --help             Show this help
USAGE
}

# --- Defaults ---
name=""
socket_path=""
workdir=""
pane_output=false
socket_dir="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --socket-path)  socket_path="${2-}"; shift 2 ;;
    --workdir)      workdir="${2-}"; shift 2 ;;
    --pane-output)  pane_output=true; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             echo "Unknown option: $1" >&2; usage; exit 1 ;;
    *)
      if [[ -z "$name" ]]; then
        name="$1"
      else
        echo "Unexpected argument: $1" >&2; usage; exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$name" ]]; then
  echo "Error: name is required" >&2
  usage
  exit 1
fi

# --- Resolve paths ---
if [[ -z "$socket_path" ]]; then
  socket_path="$socket_dir/evosci.sock"
fi

if [[ -z "$workdir" ]]; then
  # Try registry first
  registry_file="$socket_dir/registry.json"
  if [[ -f "$registry_file" ]]; then
    found_workdir=$(EVOSCI_REG_FILE="$registry_file" EVOSCI_REG_NAME="$name" python3 -c "
import json, os
with open(os.environ['EVOSCI_REG_FILE']) as f:
    registry = json.load(f)
for entry in registry:
    if entry.get('name') == os.environ['EVOSCI_REG_NAME']:
        print(entry.get('workdir', ''))
        break
" 2>/dev/null || true)
    if [[ -n "$found_workdir" ]]; then
      workdir="$found_workdir"
    fi
  fi
  # Fallback to convention
  if [[ -z "$workdir" ]]; then
    workdir="$(pwd)/workspace/runs/${name}"
  fi
fi

# --- Check session status ---
session_name="evosci-${name}"
session_running=false
if tmux -S "$socket_path" has-session -t "$session_name" 2>/dev/null; then
  session_running=true
fi

if [[ "$session_running" == true ]]; then
  echo "Status: RUNNING"
  echo "Session '$session_name' is still active."
else
  echo "Status: COMPLETED"
  echo "Session '$session_name' has ended."

  # Update registry status
  registry_file="$socket_dir/registry.json"
  if [[ -f "$registry_file" ]]; then
    EVOSCI_REG_FILE="$registry_file" EVOSCI_REG_NAME="$name" python3 -c "
import json, os, datetime
reg_file = os.environ['EVOSCI_REG_FILE']
reg_name = os.environ['EVOSCI_REG_NAME']
with open(reg_file) as f:
    registry = json.load(f)
for entry in registry:
    if entry.get('name') == reg_name and entry.get('status') == 'running':
        entry['status'] = 'completed'
        entry['completed_at'] = datetime.datetime.now().isoformat()
with open(reg_file, 'w') as f:
    json.dump(registry, f, indent=2)
" 2>/dev/null || true
  fi
fi

echo ""

# --- Check workspace ---
if [[ ! -d "$workdir" ]]; then
  echo "Workspace not found: $workdir"
  exit 1
fi

echo "Workspace: $workdir"
echo ""

# --- List output files ---
echo "Output files:"
file_count=0
for ext in md py csv png json ipynb txt pdf; do
  while IFS= read -r -d '' file; do
    rel_path="${file#"$workdir"/}"
    size=$(wc -c < "$file" 2>/dev/null | tr -d ' ')
    printf '  %-50s %s bytes\n' "$rel_path" "$size"
    file_count=$((file_count + 1))
  done < <(find "$workdir" -maxdepth 3 -name "*.${ext}" -print0 2>/dev/null)
done

if [[ "$file_count" -eq 0 ]]; then
  echo "  (no output files found)"
fi

# --- Optionally capture pane output ---
if [[ "$pane_output" == true && "$session_running" == true ]]; then
  echo ""
  echo "--- Pane Output (last 500 lines) ---"
  tmux -S "$socket_path" capture-pane -p -J -t "$session_name":0.0 -S -500 2>/dev/null || echo "(could not capture pane)"
fi

echo ""
echo "To read a specific file:"
echo "  read_file \"/runs/${name}/<filename>\""
