"""Sub-agent widget — bordered area with nested tool calls."""

from __future__ import annotations

from rich.text import Text

from textual.containers import Vertical
from textual.widgets import Static

from .tool_call_widget import ToolCallWidget

_SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


class SubAgentWidget(Vertical):
    """Displays a sub-agent's activity with a bordered frame.

    Active state::

        ┌ ▶ Cooking with research-agent — Search literature ─┐
        │   ● tavily_search  query="LLM attention"           │
        │     ✓ 3 results                                     │
        └─────────────────────────────────────────────────────┘

    Completed state::

        ✓ Cooking with research-agent  (3 tools)
    """

    DEFAULT_CSS = """
    SubAgentWidget {
        height: auto;
        margin: 0 0;
    }
    SubAgentWidget .sa-header {
        height: auto;
        color: #22d3ee;
    }
    SubAgentWidget .sa-tools {
        height: auto;
        padding: 0 0 0 2;
    }
    SubAgentWidget .sa-footer {
        height: auto;
        color: #22d3ee;
    }
    SubAgentWidget.--completed .sa-header {
        color: #4ade80;
    }
    SubAgentWidget.--completed .sa-footer {
        color: #4ade80;
    }
    """

    def __init__(self, name: str, description: str = "") -> None:
        super().__init__()
        self._sa_name = name
        self._description = description
        self._is_active = True
        self._frame = 0
        self._tool_count = 0
        self._timer_handle = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}

    @property
    def sa_name(self) -> str:
        return self._sa_name

    def update_name(self, name: str, description: str = "") -> None:
        """Update the sub-agent display name after resolution."""
        self._sa_name = name
        if description:
            self._description = description
        try:
            self._render_header()
        except Exception:
            pass  # Widget may not be mounted yet

    def compose(self):
        yield Static("", classes="sa-header")
        yield Vertical(classes="sa-tools")
        yield Static("", classes="sa-footer")

    def on_mount(self) -> None:
        self._timer_handle = self.set_interval(0.1, self._tick)
        self._render_header()
        self._render_footer()

    def _tick(self) -> None:
        if self._is_active:
            self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
            self._render_header()

    def _display_name(self) -> str:
        name = f"Cooking with {self._sa_name}"
        if self._description:
            desc = self._description.split("\n")[0].strip()
            if len(desc) > 50:
                desc = desc[:47] + "\u2026"
            name += f" \u2014 {desc}"
        return name

    def _render_header(self) -> None:
        header = self.query_one(".sa-header", Static)
        line = Text()
        if self._is_active:
            char = _SPINNER_FRAMES[self._frame]
            line.append(f"\u250c \u25b6 {self._display_name()} {char}", style="bold cyan")
        else:
            line.append(f"\u2713 {self._display_name()}", style="bold green")
            line.append(f"  ({self._tool_count} tools)", style="dim")
        header.update(line)

    def _render_footer(self) -> None:
        footer = self.query_one(".sa-footer", Static)
        if self._is_active:
            footer.update(Text("\u2514 running...", style="dim cyan"))
        else:
            footer.update(Text(""))

    async def add_tool_call(
        self,
        tool_name: str,
        tool_args: dict | None = None,
        tool_id: str = "",
    ) -> ToolCallWidget:
        """Mount a new ToolCallWidget inside this sub-agent.

        If a widget with the same *tool_id* already exists (re-emitted with
        updated args during incremental streaming), update it in place instead
        of creating a duplicate.
        """
        if tool_id and tool_id in self._tool_widgets:
            # Re-emitted with updated args — update in place
            existing = self._tool_widgets[tool_id]
            existing._tool_name = tool_name
            existing._tool_args = tool_args or {}
            try:
                existing._render_header()
            except Exception:
                pass  # Widget may not be mounted yet
            return existing

        self._tool_count += 1
        w = ToolCallWidget(tool_name, tool_args, tool_id)
        tools_container = self.query_one(".sa-tools", Vertical)
        await tools_container.mount(w)
        if tool_id:
            self._tool_widgets[tool_id] = w
        return w

    def complete_tool(
        self,
        tool_name: str,
        content: str,
        success: bool = True,
        tool_id: str = "",
    ) -> None:
        """Update the matching ToolCallWidget with its result."""
        widget = None
        if tool_id and tool_id in self._tool_widgets:
            widget = self._tool_widgets[tool_id]
        else:
            # Match by name — find first running tool with this name
            for w in self._tool_widgets.values():
                if w.tool_name == tool_name and w._status == "running":
                    widget = w
                    break
            if widget is None:
                # Fallback: find any running tool
                tools = self.query_one(".sa-tools", Vertical)
                for child in tools.children:
                    if isinstance(child, ToolCallWidget) and child._status == "running":
                        if child.tool_name == tool_name:
                            widget = child
                            break

        if widget is not None:
            if success:
                widget.set_success(content)
            else:
                widget.set_error(content)

    def finalize(self) -> None:
        """Mark sub-agent as completed and stop all nested timers."""
        self._is_active = False
        if self._timer_handle is not None:
            self._timer_handle.stop()
            self._timer_handle = None
        # Mark any nested ToolCallWidgets still running as interrupted
        for tw in self._tool_widgets.values():
            if tw._status == "running":
                try:
                    tw.set_interrupted()
                except Exception:
                    pass
        self.add_class("--completed")
        self._render_header()
        self._render_footer()
