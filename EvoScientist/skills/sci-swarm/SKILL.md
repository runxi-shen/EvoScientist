---
name: sci-swarm
description: Spawn parallel EvoSci experiments, control interactive REPLs, and orchestrate multi-agent workflows via tmux sessions. Use when the user wants to run multiple experiments simultaneously, needs an interactive Python/shell REPL, or wants to coordinate independent research tasks.
license: Complete terms in LICENSE.txt
metadata:
  version: "0.1.0"
  author: Xi Zhang
  tags: [parallel, experiments, tmux, orchestration, multi-agent]
  allowed-tools: "execute read_file write_file ls"
---

# sci-swarm — Multi-Agent Experiment Orchestration

Spawn independent EvoSci CLI instances in isolated tmux sessions for parallel experiments, interactive REPL control, and collaborative research workflows.

## Sandbox Path Warning

The EvoScientist sandbox converts literal `/path` to `./path` in shell commands. **Always use shell variables for absolute paths**, never hardcoded literals like `/tmp/...`.

```bash
# CORRECT - uses shell variable
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
tmux -S "$SOCKET" new -d -s my-session
```

WRONG — literal absolute path will be converted to `./tmp/...` by the sandbox:

```
tmux -S /tmp/evosci-tmux-sockets/evosci.sock new -d -s my-session
```

## Quick Start

Create a session, send a command, and capture output:

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/evosci.sock"
SESSION=evosci-demo

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'echo hello from tmux' Enter
sleep 1
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

After starting a session, always print monitor commands:

```
To monitor:
  tmux -S "$SOCKET" attach -t "$SESSION"
  tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -200
```

## First-Time Script Setup

The skill includes helper scripts under `/skills/sci-swarm/scripts/`. Because the skills directory is read-only and shell CWD differs from the virtual path, copy them to the workspace first:

```bash
mkdir -p tmux-scripts
```

Then use `read_file` and `write_file` to copy each script:

1. `read_file("/skills/sci-swarm/scripts/spawn-evosci.sh")` then `write_file("/tmux-scripts/spawn-evosci.sh", content)`
2. `read_file("/skills/sci-swarm/scripts/find-sessions.sh")` then `write_file("/tmux-scripts/find-sessions.sh", content)`
3. `read_file("/skills/sci-swarm/scripts/wait-for-text.sh")` then `write_file("/tmux-scripts/wait-for-text.sh", content)`
4. `read_file("/skills/sci-swarm/scripts/collect-results.sh")` then `write_file("/tmux-scripts/collect-results.sh", content)`

Run scripts with `bash` (no `chmod` needed):

```bash
bash tmux-scripts/spawn-evosci.sh experiment-1 "Run baseline comparison"
```

## Workspace Mode: Always Use --workdir

**Critical**: Always use `--workdir` to spawn sub-agents. Never use `-m run` (it nests `workspace/runs/` inside the main workspace, breaking read_file paths).

| Mode | Effect | Verdict |
|------|--------|---------|
| `-m run -n "xxx"` | Nests `workspace/runs/xxx/` with copied memory/skills | Avoid — path nesting breaks |
| `--use-cwd` | Agent works in main agent's root directory | Pollutes root directory |
| `--workdir <path>` | Agent works in specified subdirectory | Best practice — isolated yet accessible |

## Path Perspective Asymmetry

Each spawned EvoSci instance sees its `--workdir` as `/` (virtual root). When the main agent needs to read a sub-agent's output, it must use the **main agent's path perspective**:

- Sub-agent writes: `write_file("/results.md", ...)` → file lands in its workdir
- Main agent reads: `read_file("/agents/experiment-1/results.md")` → using main agent's path

This applies to all file operations (read_file, ls, grep, glob).

## Spawning EvoSci Instances

### Using the spawn script (recommended)

```bash
bash tmux-scripts/spawn-evosci.sh experiment-1 "Investigate the effect of learning rate on convergence"
bash tmux-scripts/spawn-evosci.sh experiment-2 "Compare Adam vs SGD optimizers" --model claude-sonnet-4-5
```

Add `--no-thinking` to save tokens in automated workflows:

```bash
bash tmux-scripts/spawn-evosci.sh experiment-1 "Run baseline comparison" --no-thinking
```

Each instance gets its own workspace at `workspace/runs/<name>` and shares `/memory/` with all other instances.

### Stateful multi-round sessions with --thread-id

Use `--thread-id` to maintain conversation context across multiple invocations to the same session. The sub-agent remembers previous instructions and can build on prior work:

```bash
bash tmux-scripts/spawn-evosci.sh builder "Step 1: Set up the data pipeline" --thread-id pipeline-001
```

After the first call completes, send follow-up prompts to the same session with the same thread ID:

```bash
bash tmux-scripts/spawn-evosci.sh builder "Step 2: Train the model using the pipeline from step 1" --thread-id pipeline-001
```

| Without --thread-id | With --thread-id |
|---------------------|------------------|
| Each call is a fresh instance, no memory | Preserves full conversation history |
| Best for one-shot tasks | Best for multi-step iterative experiments |

### Inline (without helper script)

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
mkdir -p "$SOCKET_DIR"
SOCKET="$SOCKET_DIR/evosci.sock"
NAME=experiment-1
WORKDIR="$(pwd)/workspace/runs/$NAME"
mkdir -p "$WORKDIR"

tmux -S "$SOCKET" new-session -d -s "evosci-$NAME" -n shell
tmux -S "$SOCKET" send-keys -t "evosci-$NAME":0.0 -- "EvoSci -p 'Your research prompt here' --workdir $WORKDIR --no-thinking" Enter
```

### Spawning multiple experiments

```bash
for exp in baseline-lr0.001 baseline-lr0.01 baseline-lr0.1; do
  bash tmux-scripts/spawn-evosci.sh "$exp" "Train model with learning rate ${exp##*-}" --no-thinking
done
```

## Monitoring Progress

### Capture pane output

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
tmux -S "$SOCKET" capture-pane -p -J -t "evosci-experiment-1":0.0 -S -200
```

### Detect completion

EvoSci prints "Goodbye!" when it exits. Check for it:

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
if tmux -S "$SOCKET" capture-pane -p -t "evosci-experiment-1":0.0 -S -5 | grep -q "Goodbye!"; then
  echo "experiment-1: DONE"
else
  echo "experiment-1: still running"
fi
```

### Wait for text (blocking)

```bash
bash tmux-scripts/wait-for-text.sh -S "$SOCKET" -t "evosci-experiment-1":0.0 -p 'Goodbye!' -T 300
```

Options:
- `-t` target pane (required)
- `-p` regex pattern to match (required)
- `-S` tmux socket path
- `-F` treat pattern as fixed string
- `-T` timeout in seconds (default: 15)
- `-i` poll interval (default: 0.5)
- `-l` history lines to search (default: 1000)

## Collecting Results

### Using the collect script

```bash
bash tmux-scripts/collect-results.sh experiment-1
bash tmux-scripts/collect-results.sh experiment-1 --pane-output
```

This shows session status, lists output files, and optionally captures the pane.

### Manual collection

Read output files from completed workspaces:

```bash
read_file("/runs/experiment-1/results.md")
read_file("/runs/experiment-1/analysis.py")
```

List workspace contents:

```bash
ls("/runs/experiment-1/")
```

## Interactive REPL Control

### Python REPL

Use `PYTHON_BASIC_REPL=1` to prevent the enhanced REPL from breaking send-keys:

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
SESSION=evosci-python

tmux -S "$SOCKET" new -d -s "$SESSION" -n shell
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -- 'PYTHON_BASIC_REPL=1 python3 -q' Enter
sleep 1
tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 -l -- 'print("hello")' && sleep 0.1 && tmux -S "$SOCKET" send-keys -t "$SESSION":0.0 Enter
sleep 1
tmux -S "$SOCKET" capture-pane -p -J -t "$SESSION":0.0 -S -50
```

### Sending input safely

- Prefer literal sends: `tmux -S "$SOCKET" send-keys -t target -l -- "$cmd"`
- Control keys: `tmux -S "$SOCKET" send-keys -t target C-c`
- For TUI apps (interactive CLIs), separate text and Enter with a delay:

```bash
tmux -S "$SOCKET" send-keys -t target -l -- "$cmd" && sleep 0.1 && tmux -S "$SOCKET" send-keys -t target Enter
```

## Agent Communication

For collaborative research across spawned instances, use file-based message passing via shared memory:

```bash
# Agent A writes a message
write_file("/memory/messages/from-experiment-1.md", "## Finding\nLearning rate 0.01 converges fastest.")

# Agent B reads it
read_file("/memory/messages/from-experiment-1.md")
```

All EvoSci instances share `/memory/` regardless of their workspace directory.

## Finding & Cleaning Sessions

### List sessions

```bash
bash tmux-scripts/find-sessions.sh -S "$SOCKET"
bash tmux-scripts/find-sessions.sh --all
```

Options:
- `-L` socket name (tmux -L)
- `-S` socket path (tmux -S)
- `-A`/`--all` scan all sockets under `EVOSCI_TMUX_SOCKET_DIR`
- `-q` filter session names

### Kill a session

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
tmux -S "$SOCKET" kill-session -t "evosci-experiment-1"
```

### Kill all sessions

```bash
SOCKET_DIR="${EVOSCI_TMUX_SOCKET_DIR:-${TMPDIR:-/tmp}/evosci-tmux-sockets}"
SOCKET="$SOCKET_DIR/evosci.sock"
tmux -S "$SOCKET" kill-server
```

## Swarm Conventions

### Session registry

The spawn script maintains a registry at `$EVOSCI_TMUX_SOCKET_DIR/registry.json` tracking all spawned sessions, their workspaces, prompts, and status.

### Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `EVOSCI_MAX_TMUX_SESSIONS` | 5 | Maximum concurrent sessions |
| `EVOSCI_TMUX_DEPTH` | 0 | Current recursion depth (auto-incremented) |
| `EVOSCI_MAX_TMUX_DEPTH` | 3 | Maximum recursion depth |
| `EVOSCI_TMUX_SOCKET_DIR` | `$TMPDIR/evosci-tmux-sockets` | Socket directory |

### Naming

- Session names: `evosci-<name>` (e.g., `evosci-experiment-1`)
- Workspaces: `workspace/runs/<name>/`
- Keep names short, alphanumeric with hyphens

### Targeting panes

- Target format: `session:window.pane` (defaults to `:0.0`)
- Inspect: `tmux -S "$SOCKET" list-sessions`, `tmux -S "$SOCKET" list-panes -a`
