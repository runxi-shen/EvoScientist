"""Tests for EvoScientist/stream/events.py helpers."""

from types import SimpleNamespace

from EvoScientist.stream.events import _extract_tool_content


class TestExtractToolContent:
    """Verify _extract_tool_content handles image and text ToolMessages."""

    def test_image_via_additional_kwargs(self):
        """Image ToolMessages with read_file_media_type return summary."""
        msg = SimpleNamespace(
            content=[{"type": "image", "base64": "abc123..."}],
            additional_kwargs={
                "read_file_media_type": "image/png",
                "read_file_path": "/chart.png",
            },
            name="read_file",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is True
        assert "chart.png" in content
        assert "image/png" in content
        # Must NOT contain base64 data
        assert "abc123" not in content

    def test_image_via_list_content_blocks(self):
        """Image content blocks without metadata are still detected."""
        msg = SimpleNamespace(
            content=[
                {"type": "text", "text": "Image: chart.png"},
                {"type": "image", "base64": "iVBORw0KGgo..."},
            ],
            additional_kwargs={},
            name="read_file",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is True
        assert "iVBORw0KGgo" not in content

    def test_normal_text_passthrough(self):
        """Normal text content passes through unchanged."""
        msg = SimpleNamespace(
            content="File written successfully to /output.txt",
            additional_kwargs={},
            name="write_file",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is False
        assert content == "File written successfully to /output.txt"

    def test_empty_content(self):
        """Empty content returns empty string."""
        msg = SimpleNamespace(
            content="",
            additional_kwargs={},
            name="read_file",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is False
        assert content == ""

    def test_list_text_blocks(self):
        """List of text blocks are joined."""
        msg = SimpleNamespace(
            content=[
                {"type": "text", "text": "Line 1"},
                {"type": "text", "text": "Line 2"},
            ],
            additional_kwargs={},
            name="read_file",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is False
        assert "Line 1" in content
        assert "Line 2" in content

    def test_no_additional_kwargs_attr(self):
        """Messages without additional_kwargs attribute are handled."""
        msg = SimpleNamespace(
            content="some result",
            name="execute",
        )
        content, is_image = _extract_tool_content(msg)
        assert is_image is False
        assert content == "some result"
