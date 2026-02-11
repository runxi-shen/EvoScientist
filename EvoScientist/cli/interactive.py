"""Interactive CLI mode and single-shot execution."""

import asyncio
import os
import queue
import sys
import uuid
from typing import Any

import typer  # type: ignore[import-untyped]
from prompt_toolkit import PromptSession  # type: ignore[import-untyped]
from prompt_toolkit.completion import Completer, Completion  # type: ignore[import-untyped]
from prompt_toolkit.history import FileHistory  # type: ignore[import-untyped]
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory  # type: ignore[import-untyped]
from prompt_toolkit.formatted_text import HTML  # type: ignore[import-untyped]
from prompt_toolkit.shortcuts import CompleteStyle  # type: ignore[import-untyped]
from prompt_toolkit.styles import Style as PtStyle  # type: ignore[import-untyped]
from rich.text import Text

from ..stream.display import console, _run_streaming
from .agent import _shorten_path, _create_session_workspace, _load_agent
from .channel import (
    ChannelMessage,
    _ChannelState,
    _cmd_channel,
    _cmd_channel_stop,
    _auto_start_channel,
)
from .mcp_ui import _cmd_mcp
from .skills_cmd import _cmd_list_skills, _cmd_install_skill, _cmd_uninstall_skill


# =============================================================================
# Banner
# =============================================================================

EVOSCIENTIST_ASCII_LINES = [
    r" \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557   \u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2557   \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557",
]

# Blue gradient: deep navy -> royal blue -> sky blue -> cyan
_GRADIENT_COLORS = ["#1a237e", "#1565c0", "#1e88e5", "#42a5f5", "#64b5f6", "#90caf9"]

# Keep the real ASCII art lines (raw strings) rather than the escaped version above
_REAL_ASCII_LINES = [
    r" ███████╗ ██╗   ██╗  ██████╗  ███████╗  ██████╗ ██╗ ███████╗ ███╗   ██╗ ████████╗ ██╗ ███████╗ ████████╗",
    r" ██╔════╝ ██║   ██║ ██╔═══██╗ ██╔════╝ ██╔════╝ ██║ ██╔════╝ ████╗  ██║ ╚══██╔══╝ ██║ ██╔════╝ ╚══██╔══╝",
    r" █████╗   ██║   ██║ ██║   ██║ ███████╗ ██║      ██║ █████╗   ██╔██╗ ██║    ██║    ██║ ███████╗    ██║   ",
    r" ██╔══╝   ╚██╗ ██╔╝ ██║   ██║ ╚════██║ ██║      ██║ ██╔══╝   ██║╚██╗██║    ██║    ██║ ╚════██║    ██║   ",
    r" ███████╗  ╚████╔╝  ╚██████╔╝ ███████║ ╚██████╗ ██║ ███████╗ ██║ ╚████║    ██║    ██║ ███████║    ██║   ",
    r" ╚══════╝   ╚═══╝    ╚═════╝  ╚══════╝  ╚═════╝ ╚═╝ ╚══════╝ ╚═╝  ╚═══╝    ╚═╝    ╚═╝ ╚══════╝    ╚═╝   ",
]


def print_banner(
    thread_id: str,
    workspace_dir: str | None = None,
    memory_dir: str | None = None,
    mode: str | None = None,
    model: str | None = None,
    provider: str | None = None,
):
    """Print welcome banner with ASCII art logo, thread ID, workspace path, and mode."""
    for line, color in zip(_REAL_ASCII_LINES, _GRADIENT_COLORS):
        console.print(Text(line, style=f"{color} bold"))
    info = Text()
    if model or provider or mode:
        info.append("  ", style="dim")
        parts = []
        if model:
            parts.append(("Model: ", model))
        if provider:
            parts.append(("Provider: ", provider))
        if mode:
            parts.append(("Mode: ", mode))
        for i, (label, value) in enumerate(parts):
            if i > 0:
                info.append("  ", style="dim")
            info.append(label, style="dim")
            info.append(value, style="magenta")
    info.append("\n  Type ", style="#ffe082")
    info.append("/", style="#ffe082 bold")
    info.append(" for commands", style="#ffe082")
    console.print(info)
    console.print()


# =============================================================================
# Slash-command completer
# =============================================================================

_SLASH_COMMANDS = [
    ("/thread", "Show thread ID, workspace & memory dir"),
    ("/new", "Start a new session"),
    ("/skills", "List installed skills"),
    ("/install-skill", "Add a skill from path or GitHub"),
    ("/uninstall-skill", "Remove an installed skill"),
    ("/mcp", "Manage MCP servers"),
    ("/channel", "Configure messaging channels"),
    ("/exit", "Quit EvoScientist"),
]

_COMPLETION_STYLE = PtStyle.from_dict({
    "completion-menu": "bg:default noreverse nounderline noitalic",
    "completion-menu.completion": "bg:default #888888 noreverse",
    "completion-menu.completion.current": "bg:default default bold noreverse",
    "completion-menu.meta.completion": "bg:default #888888 noreverse",
    "completion-menu.meta.completion.current": "bg:default default bold noreverse",
    "scrollbar.background": "bg:default",
    "scrollbar.button": "bg:default",
})


class SlashCommandCompleter(Completer):
    """Autocomplete for slash commands — triggers when input starts with '/'."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        for cmd, desc in _SLASH_COMMANDS:
            if cmd.startswith(text):
                yield Completion(
                    cmd,
                    start_position=-len(text),
                    display=f"{cmd:<40}",
                    display_meta=desc,
                )


# =============================================================================
# Interactive & single-shot modes
# =============================================================================


def cmd_interactive(
    agent: Any,
    show_thinking: bool = True,
    workspace_dir: str | None = None,
    workspace_fixed: bool = False,
    mode: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    imessage_enabled: bool = False,
    imessage_allowed_senders: str = "",
    imessage_send_thinking: bool = True,
    run_name: str | None = None,
) -> None:
    """Interactive conversation mode with streaming output.

    Args:
        agent: Compiled agent graph
        show_thinking: Whether to display thinking panels
        workspace_dir: Per-session workspace directory path
        workspace_fixed: If True, /new keeps the same workspace directory
        mode: Workspace mode ('daemon' or 'run'), displayed in banner
        model: Model name to display in banner
        provider: LLM provider name to display in banner
        imessage_enabled: Whether to auto-start iMessage channel
        imessage_allowed_senders: Comma-separated allowed senders
        imessage_send_thinking: Whether to forward thinking to channel
        run_name: Optional run name for /new session deduplication
    """
    import nest_asyncio
    nest_asyncio.apply()

    thread_id = str(uuid.uuid4())
    from ..EvoScientist import MEMORY_DIR
    memory_dir = MEMORY_DIR
    print_banner(thread_id, workspace_dir, memory_dir, mode, model, provider)

    history_file = str(os.path.expanduser("~/.EvoScientist_history"))
    session = PromptSession(
        history=FileHistory(history_file),
        auto_suggest=AutoSuggestFromHistory(),
        completer=SlashCommandCompleter(),
        complete_style=CompleteStyle.COLUMN,
        complete_while_typing=True,
        style=_COMPLETION_STYLE,
    )

    def _print_separator():
        """Print a horizontal separator line spanning the terminal width."""
        width = console.size.width
        console.print(Text("\u2500" * width, style="dim"))

    # Mutable state for async loop
    state = {
        "agent": agent,
        "thread_id": thread_id,
        "workspace_dir": workspace_dir,
        "running": True,
    }

    def _process_channel_message(msg: ChannelMessage) -> None:
        """Process a message from a channel with full Live streaming."""
        # Move past the current prompt line to avoid interference with prompt_toolkit
        # Then move back up and clear that line
        sys.stdout.write("\n\033[A\033[2K\r")
        sys.stdout.flush()
        # Display prompt with channel source on second line
        console.print(f"[bold blue]>[/bold blue] {msg.content}")
        console.print(Text.assemble(
            ("[", "dim"),
            (f"{msg.channel_type}: Received from ", "dim"),
            (msg.sender, "cyan"),
            ("]", "dim"),
        ))
        _print_separator()
        console.print()

        # Build channel callbacks for intermediate messages (thinking + todo + files)
        on_thinking = None
        on_todo = None
        on_file_write = None
        if _ChannelState.is_running() and _ChannelState.server and _ChannelState.loop:
            def _send_thinking(thinking_text: str) -> None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        _ChannelState.server.send_thinking_message(
                            msg.sender, thinking_text, msg.metadata,
                        ),
                        _ChannelState.loop,
                    )
                except Exception:
                    pass  # Non-critical — don't break main flow

            def _send_todo(todo_items: list) -> None:
                try:
                    lines = [f"\U0001f4cb {len(todo_items)} tasks ongoing"]  # 📋
                    for i, item in enumerate(todo_items, 1):
                        content = item.get("content", "")
                        lines.append(f"{i}. {content}")
                    lines.append("\U0001f680")  # 🚀
                    formatted = "\n".join(lines)
                    asyncio.run_coroutine_threadsafe(
                        _ChannelState.server.send_todo_message(
                            msg.sender, formatted, msg.metadata,
                        ),
                        _ChannelState.loop,
                    )
                except Exception:
                    pass  # Non-critical — don't break main flow

            def _send_file(real_path: str) -> None:
                try:
                    asyncio.run_coroutine_threadsafe(
                        _ChannelState.server.channel.send_media(
                            recipient=msg.sender, file_path=real_path,
                            metadata=msg.metadata,
                        ),
                        _ChannelState.loop,
                    )
                except Exception:
                    pass  # Non-critical — don't break main flow

            on_thinking = _send_thinking
            on_todo = _send_todo
            on_file_write = _send_file

        try:
            # Use SAME _run_streaming as CLI input — full Live experience
            response_text = _run_streaming(
                state["agent"], msg.content, state["thread_id"], show_thinking,
                interactive=True, on_thinking=on_thinking, on_todo=on_todo,
                on_file_write=on_file_write,
            )

            # Set response for channel handler to retrieve
            _ChannelState.set_response(msg.msg_id, response_text or "")
            # Show replied indicator
            console.print(Text.assemble(
                ("[", "dim"),
                (f"{msg.channel_type}: Replied to ", "dim"),
                (msg.sender, "cyan"),
                ("]", "dim"),
            ))
        except Exception as e:
            console.print(f"[red]Channel processing error: {e}[/red]")
            _ChannelState.set_response(msg.msg_id, f"Error: {e}")

        _print_separator()

    async def _check_channel_queue():
        """Background task to check channel queue periodically."""
        while state["running"]:
            try:
                msg = _ChannelState.message_queue.get_nowait()
                _process_channel_message(msg)
            except queue.Empty:
                pass
            await asyncio.sleep(0.1)  # Check every 100ms

    async def _async_main_loop():
        """Async main loop with prompt_async and channel queue checking."""
        # Start background queue checker
        queue_task = asyncio.create_task(_check_channel_queue())

        # Auto-start iMessage channel if enabled in config
        if imessage_enabled and not _ChannelState.is_running():
            _auto_start_channel(state["agent"], state["thread_id"], imessage_allowed_senders, imessage_send_thinking)

        try:
            _print_separator()
            while state["running"]:
                try:
                    user_input = await session.prompt_async(
                        HTML('<ansiblue><b>\u276f</b></ansiblue> ')
                    )
                    user_input = user_input.strip()

                    if not user_input:
                        # Erase the empty prompt line so it looks like nothing happened
                        sys.stdout.write("\033[A\033[2K\r")
                        sys.stdout.flush()
                        continue

                    _print_separator()

                    # Special commands
                    if user_input.lower() in ("/exit", "/quit", "/q"):
                        console.print("[dim]Goodbye![/dim]")
                        state["running"] = False
                        break

                    if user_input.lower() == "/new":
                        # New session: new thread; workspace only changes if not fixed
                        if not workspace_fixed:
                            state["workspace_dir"] = _create_session_workspace(run_name)
                        console.print("[dim]Loading new session...[/dim]")
                        state["agent"] = _load_agent(workspace_dir=state["workspace_dir"])
                        state["thread_id"] = str(uuid.uuid4())
                        # Sync shared refs if channel is running
                        if _ChannelState.is_running():
                            _ChannelState.agent = state["agent"]
                            _ChannelState.thread_id = state["thread_id"]
                        console.print(f"[green]New session:[/green] [yellow]{state['thread_id']}[/yellow]")
                        if state["workspace_dir"]:
                            console.print(f"[dim]Workspace:[/dim] [cyan]{_shorten_path(state['workspace_dir'])}[/cyan]\n")
                        continue

                    if user_input.lower() == "/thread":
                        console.print(f"[dim]Thread:[/dim] [yellow]{state['thread_id']}[/yellow]")
                        if state["workspace_dir"]:
                            console.print(f"[dim]Workspace:[/dim] [cyan]{_shorten_path(state['workspace_dir'])}[/cyan]")
                        if memory_dir:
                            console.print(f"[dim]Memory dir:[/dim] [cyan]{_shorten_path(memory_dir)}[/cyan]")
                        console.print()
                        continue

                    if user_input.lower() == "/skills":
                        _cmd_list_skills()
                        continue

                    if user_input.lower().startswith("/install-skill"):
                        source = user_input[len("/install-skill"):].strip()
                        _cmd_install_skill(source)
                        continue

                    if user_input.lower().startswith("/uninstall-skill"):
                        name = user_input[len("/uninstall-skill"):].strip()
                        _cmd_uninstall_skill(name)
                        continue

                    if user_input.lower().startswith("/mcp"):
                        _cmd_mcp(user_input[4:])
                        continue

                    if user_input.lower().startswith("/channel"):
                        args = user_input[len("/channel"):].strip()
                        if args.lower() == "stop":
                            _cmd_channel_stop()
                        else:
                            _cmd_channel(args, state["agent"], state["thread_id"])
                        continue

                    # Stream agent response
                    console.print()
                    _run_streaming(state["agent"], user_input, state["thread_id"], show_thinking, interactive=True)
                    _print_separator()

                except KeyboardInterrupt:
                    console.print("\n[dim]Goodbye![/dim]")
                    state["running"] = False
                    break
                except EOFError:
                    # Handle Ctrl+D
                    console.print("\n[dim]Goodbye![/dim]")
                    state["running"] = False
                    break
                except Exception as e:
                    error_msg = str(e)
                    if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
                        console.print("[red]Error: API key not configured.[/red]")
                        console.print("[dim]Run [bold]EvoSci onboard[/bold] to set up your API key.[/dim]")
                        state["running"] = False
                        break
                    else:
                        console.print(f"[red]Error: {e}[/red]")
        finally:
            queue_task.cancel()
            try:
                await queue_task
            except asyncio.CancelledError:
                pass

    # Run the async main loop
    try:
        asyncio.run(_async_main_loop())
    except KeyboardInterrupt:
        console.print("\n[dim]Goodbye![/dim]")


def cmd_run(agent: Any, prompt: str, thread_id: str | None = None, show_thinking: bool = True, workspace_dir: str | None = None) -> None:
    """Single-shot execution with streaming display.

    Args:
        agent: Compiled agent graph
        prompt: User prompt
        thread_id: Optional thread ID (generates new one if None)
        show_thinking: Whether to display thinking panels
        workspace_dir: Per-session workspace directory path
    """
    thread_id = thread_id or str(uuid.uuid4())

    width = console.size.width
    sep = Text("\u2500" * width, style="dim")
    console.print(sep)
    console.print(Text(f"> {prompt}"))
    console.print(sep)
    console.print(f"[dim]Thread: {thread_id}[/dim]")
    if workspace_dir:
        console.print(f"[dim]Workspace: {_shorten_path(workspace_dir)}[/dim]")
    console.print()

    try:
        _run_streaming(agent, prompt, thread_id, show_thinking, interactive=False)
    except Exception as e:
        error_msg = str(e)
        if "authentication" in error_msg.lower() or "api_key" in error_msg.lower():
            console.print("[red]Error: API key not configured.[/red]")
            console.print("[dim]Run [bold]EvoSci onboard[/bold] to set up your API key.[/dim]")
            raise typer.Exit(1)
        else:
            console.print(f"[red]Error: {e}[/red]")
            raise
