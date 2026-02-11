"""Background iMessage channel — state management, thread lifecycle, handlers."""

import asyncio
import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from rich.panel import Panel
from rich.text import Text

from ..stream.display import console

_channel_logger = logging.getLogger(__name__)


@dataclass
class ChannelMessage:
    """Message from a channel (iMessage, Email, etc.)."""
    msg_id: str
    content: str
    sender: str
    channel_type: str  # "iMessage", "Email", "Slack"
    metadata: Any = None


class _ChannelState:
    """Singleton tracking background iMessage channel and message queue."""

    server = None       # IMessageServer | None
    thread = None       # threading.Thread | None
    loop = None         # asyncio.AbstractEventLoop | None
    agent = None        # shared agent reference (same as CLI)
    thread_id = None    # shared thread_id (same conversation as CLI)

    # Queue-based communication between channel thread and main CLI thread
    message_queue: queue.Queue = queue.Queue()
    pending_responses: dict = {}  # msg_id -> {"event": Event, "response": str | None}
    _response_lock = threading.Lock()

    @classmethod
    def is_running(cls) -> bool:
        return cls.thread is not None and cls.thread.is_alive()

    @classmethod
    def stop(cls):
        if cls.loop and cls.server:
            cls.loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(cls.server.stop())
            )
        if cls.thread:
            cls.thread.join(timeout=5)
        cls.server = None
        cls.thread = None
        cls.loop = None
        cls.agent = None
        cls.thread_id = None
        # Clear pending responses
        with cls._response_lock:
            for slot in cls.pending_responses.values():
                slot["event"].set()  # Unblock any waiting handlers
            cls.pending_responses.clear()

    @classmethod
    def enqueue(
        cls,
        content: str,
        sender: str,
        channel_type: str,
        metadata: Any = None,
    ) -> tuple[str, threading.Event]:
        """Enqueue a message from any channel for main thread processing.

        Returns:
            Tuple of (msg_id, event) - caller can wait on event for response.
        """
        msg_id = str(uuid.uuid4())
        event = threading.Event()
        with cls._response_lock:
            cls.pending_responses[msg_id] = {"event": event, "response": None}
        cls.message_queue.put(ChannelMessage(msg_id, content, sender, channel_type, metadata))
        return msg_id, event

    @classmethod
    def set_response(cls, msg_id: str, response: str) -> None:
        """Set response and signal completion."""
        with cls._response_lock:
            if msg_id in cls.pending_responses:
                cls.pending_responses[msg_id]["response"] = response
                cls.pending_responses[msg_id]["event"].set()

    @classmethod
    def get_response(cls, msg_id: str, timeout: float = 300) -> str | None:
        """Wait for and retrieve response.

        Args:
            msg_id: The message ID to get response for.
            timeout: Maximum seconds to wait (default 300 = 5 minutes).

        Returns:
            The response text, or None if timed out or not found.
        """
        with cls._response_lock:
            slot = cls.pending_responses.get(msg_id)
        if not slot:
            return None
        if slot["event"].wait(timeout=timeout):
            with cls._response_lock:
                return cls.pending_responses.pop(msg_id, {}).get("response")
        return None


def _run_channel_thread(server):
    """Entry point for background channel thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _ChannelState.loop = loop
    try:
        loop.run_until_complete(server.run())
    except Exception as e:
        _channel_logger.error(f"Channel error: {e}")
    finally:
        loop.close()


def _create_channel_handler():
    """Create iMessage handler that enqueues messages for main thread processing.

    The handler enqueues messages to the shared queue and waits for the main
    CLI thread to process them with full Rich Live streaming. This ensures
    channel messages get the same display quality as direct CLI input.

    Returns:
        Async handler function: (msg) -> str
    """

    async def handler(msg) -> str:
        # Enqueue for main thread to process with full Live streaming
        msg_id, event = _ChannelState.enqueue(
            content=msg.content,
            sender=msg.sender,
            channel_type="iMessage",
            metadata=msg.metadata,
        )

        # Wait indefinitely for main thread to process and set response
        # (no timeout - let the agent work as long as needed)
        await asyncio.to_thread(event.wait)

        # Get the response
        with _ChannelState._response_lock:
            response = _ChannelState.pending_responses.pop(msg_id, {}).get("response", "")

        return response if response else "(empty response)"

    return handler


def _cmd_channel(args: str, agent: Any, thread_id: str) -> None:
    """Start iMessage channel in background thread using the shared agent.

    CLI and iMessage share the same agent + thread_id (same conversation).
    When an iMessage arrives, the main CLI thread processes it with full
    Rich Live streaming — same experience as direct CLI input.

    Usage: /channel [--allow SENDER]
    """
    from ..channels.imessage import IMessageConfig
    from ..channels.imessage.serve import IMessageServer

    if _ChannelState.is_running():
        console.print("[dim]iMessage channel already running[/dim]")
        console.print("[dim]Use[/dim] /channel stop [dim]to disconnect[/dim]\n")
        return

    parts = args.split() if args else []
    allowed = set()

    for i, p in enumerate(parts):
        if p == "--allow" and i + 1 < len(parts):
            allowed.add(parts[i + 1])

    config = IMessageConfig(
        allowed_senders=list(allowed) if allowed else [],
    )

    # Store shared agent reference — no separate agent creation
    _ChannelState.agent = agent
    _ChannelState.thread_id = thread_id

    # Read send_thinking preference from config
    from ..config import load_config as _load_config
    send_thinking = _load_config().imessage_send_thinking

    server = IMessageServer(
        config,
        handler=_create_channel_handler(),
        send_thinking=send_thinking,
    )

    _ChannelState.server = server
    _ChannelState.thread = threading.Thread(
        target=_run_channel_thread,
        args=(server,),
        daemon=True,
    )
    _ChannelState.thread.start()

    console.print("[green]iMessage channel running in background[/green]")
    if allowed:
        console.print(f"[dim]Allowed:[/dim] {allowed}")
    else:
        console.print("[dim]Allowed: all senders[/dim]")
    console.print("[dim]Use[/dim] /channel stop [dim]to disconnect[/dim]\n")


def _cmd_channel_stop() -> None:
    """Stop background iMessage channel."""
    if not _ChannelState.is_running():
        console.print("[dim]No channel running[/dim]\n")
        return
    _ChannelState.stop()
    console.print("[dim]iMessage channel stopped[/dim]\n")


def _print_channel_panel(channels: list[tuple[str, bool, str]]) -> None:
    """Print a summary panel for active channels.

    Args:
        channels: List of (name, ok, detail) tuples.
    """
    lines: list[Text] = []
    all_ok = True
    for name, ok, detail in channels:
        line = Text()
        if ok:
            line.append("\u25cf ", style="green")
            line.append(name, style="bold")
        else:
            line.append("\u2717 ", style="yellow")
            line.append(name, style="bold yellow")
            all_ok = False
        if detail:
            line.append(f"  {detail}", style="dim")
        lines.append(line)

    body = Text("\n").join(lines)
    border = "green" if all_ok else "yellow"
    console.print(Panel(body, title="[bold]Channels[/bold]", border_style=border, expand=False))
    console.print()


def _auto_start_channel(agent: Any, thread_id: str, allowed_senders_csv: str, send_thinking: bool = True) -> None:
    """Start iMessage channel automatically from config.

    Args:
        agent: Compiled agent graph.
        thread_id: Current thread ID.
        allowed_senders_csv: Comma-separated allowed senders (empty = all).
        send_thinking: Whether to forward thinking content to channel.
    """
    try:
        from ..channels.imessage import IMessageConfig
        from ..channels.imessage.serve import IMessageServer

        allowed: set[str] | None = None
        if allowed_senders_csv.strip():
            allowed = {s.strip() for s in allowed_senders_csv.split(",") if s.strip()}

        config = IMessageConfig(allowed_senders=list(allowed) if allowed else [])

        _ChannelState.agent = agent
        _ChannelState.thread_id = thread_id

        server = IMessageServer(config, handler=_create_channel_handler(), send_thinking=send_thinking)
        _ChannelState.server = server
        _ChannelState.thread = threading.Thread(
            target=_run_channel_thread,
            args=(server,),
            daemon=True,
        )
        _ChannelState.thread.start()

        detail = ", ".join(sorted(allowed)) if allowed else "all senders"
        _print_channel_panel([("iMessage", True, detail)])
    except Exception as e:
        _print_channel_panel([("iMessage", False, str(e))])
