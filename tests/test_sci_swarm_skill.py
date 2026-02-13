"""Tests for the sci-swarm skill.

Validates SKILL.md structure, script syntax, naming conventions,
and sandbox compatibility. No tmux or API keys required.
"""

import re
import subprocess
from pathlib import Path

import pytest
import yaml

# Skill directory
SKILL_DIR = Path(__file__).parent.parent / "EvoScientist" / "skills" / "sci-swarm"
SCRIPTS_DIR = SKILL_DIR / "scripts"
SKILL_MD = SKILL_DIR / "SKILL.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from a markdown file."""
    match = re.match(r"^---\n(.+?)\n---", text, re.DOTALL)
    if not match:
        return {}
    return yaml.safe_load(match.group(1))


def extract_code_blocks(text: str) -> list[str]:
    """Extract bash/sh fenced code block contents from markdown."""
    return re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.DOTALL)


# ---------------------------------------------------------------------------
# SKILL.md frontmatter
# ---------------------------------------------------------------------------

class TestSkillMdFrontmatter:
    """SKILL.md YAML frontmatter parses correctly."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.text = SKILL_MD.read_text()
        self.meta = parse_frontmatter(self.text)

    def test_name(self):
        assert self.meta.get("name") == "sci-swarm"

    def test_description_present(self):
        desc = self.meta.get("description", "")
        assert len(desc) > 20, "description should be meaningful"

    def test_license_reference(self):
        license_ref = self.meta.get("license", "")
        assert "LICENSE.txt" in license_ref

    def test_allowed_tools_in_metadata(self):
        metadata = self.meta.get("metadata", {})
        tools = metadata.get("allowed-tools", "")
        assert "execute" in tools


# ---------------------------------------------------------------------------
# SKILL.md sections
# ---------------------------------------------------------------------------

class TestSkillMdSections:
    """SKILL.md contains all expected sections."""

    EXPECTED_HEADINGS = [
        "Sandbox Path Warning",
        "Quick Start",
        "First-Time Script Setup",
        "Workspace Mode: Always Use --workdir",
        "Path Perspective Asymmetry",
        "Spawning EvoSci Instances",
        "Monitoring Progress",
        "Collecting Results",
        "Interactive REPL Control",
        "Agent Communication",
        "Finding & Cleaning Sessions",
        "Swarm Conventions",
    ]

    @pytest.fixture(autouse=True)
    def _load(self):
        self.text = SKILL_MD.read_text()

    @pytest.mark.parametrize("heading", EXPECTED_HEADINGS)
    def test_section_present(self, heading):
        assert heading in self.text, f"Missing section: {heading}"


# ---------------------------------------------------------------------------
# Script syntax validation
# ---------------------------------------------------------------------------

class TestScriptSyntax:
    """All shell scripts pass bash -n syntax check."""

    SCRIPTS = list(SCRIPTS_DIR.glob("*.sh"))

    @pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
    def test_bash_syntax(self, script):
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{script.name} has syntax errors:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# No literal absolute paths in SKILL.md code blocks
# ---------------------------------------------------------------------------

class TestNoLiteralAbsolutePaths:
    """Code blocks must not contain hardcoded absolute paths.

    The sandbox converts /path to ./path, so all code examples must
    use shell variables ($SOCKET, $SOCKET_DIR, etc.) for absolute paths.
    """

    # Patterns that indicate a literal absolute path (not in a variable assignment)
    FORBIDDEN_PATTERNS = [
        # Literal /tmp/ not part of a variable expansion or default value
        r'(?<!\$\{TMPDIR:-)/tmp/',
        # Literal /Users/ or /home/
        r'/Users/',
        r'/home/',
    ]

    @pytest.fixture(autouse=True)
    def _load(self):
        self.text = SKILL_MD.read_text()
        self.code_blocks = extract_code_blocks(self.text)

    def test_no_literal_tmp_paths(self):
        """No hardcoded /tmp/ outside of ${TMPDIR:-/tmp} defaults and WRONG examples."""
        for i, block in enumerate(self.code_blocks):
            # Determine if this block is in the "WRONG" example section
            block_pos = self.text.find(block)
            preceding = self.text[:block_pos]
            is_wrong_example = "# WRONG" in preceding[preceding.rfind("```"):] if "```" in preceding else False

            for line in block.strip().splitlines():
                line_stripped = line.strip()
                # Skip comments
                if line_stripped.startswith("#"):
                    continue
                # Skip lines that are variable assignments with defaults
                if "TMPDIR:-/tmp" in line:
                    continue
                # Skip lines in the WRONG example block
                if is_wrong_example:
                    continue
                if "/tmp/" in line:
                    pytest.fail(
                        f"Code block {i} has literal /tmp/ path "
                        f"(use shell variable instead):\n  {line}"
                    )

    def test_no_literal_user_paths(self):
        """No hardcoded /Users/ or /home/ paths in code blocks."""
        for i, block in enumerate(self.code_blocks):
            for line in block.strip().splitlines():
                if line.strip().startswith("#"):
                    continue
                for pattern in ["/Users/", "/home/"]:
                    if pattern in line:
                        pytest.fail(
                            f"Code block {i} has literal {pattern} path:\n  {line}"
                        )


# ---------------------------------------------------------------------------
# Scripts reference correct env vars (not openclaw)
# ---------------------------------------------------------------------------

class TestEnvVarNaming:
    """Scripts must use EVOSCI_ prefixed env vars, not OPENCLAW_ or CLAWDBOT_."""

    SCRIPTS = list(SCRIPTS_DIR.glob("*.sh"))

    @pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
    def test_no_openclaw_vars(self, script):
        content = script.read_text()
        assert "OPENCLAW_" not in content, (
            f"{script.name} references OPENCLAW_ env vars"
        )
        assert "CLAWDBOT_" not in content, (
            f"{script.name} references CLAWDBOT_ env vars"
        )

    # wait-for-text.sh doesn't need EVOSCI_TMUX_SOCKET_DIR — it takes
    # the socket path via -S argument from the caller.
    SCRIPTS_NEEDING_SOCKET_DIR = [
        s for s in SCRIPTS if s.name != "wait-for-text.sh"
    ]

    @pytest.mark.parametrize(
        "script", SCRIPTS_NEEDING_SOCKET_DIR, ids=lambda s: s.name
    )
    def test_uses_evosci_socket_dir(self, script):
        content = script.read_text()
        assert "EVOSCI_TMUX_SOCKET_DIR" in content, (
            f"{script.name} should reference EVOSCI_TMUX_SOCKET_DIR"
        )


# ---------------------------------------------------------------------------
# Inline commands pass validate_command()
# ---------------------------------------------------------------------------

class TestSandboxCompatibility:
    """Inline commands from SKILL.md should pass the sandbox validator."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.text = SKILL_MD.read_text()
        self.code_blocks = extract_code_blocks(self.text)

    def _extract_commands(self) -> list[str]:
        """Extract executable commands from code blocks."""
        commands = []
        for block in self.code_blocks:
            for line in block.strip().splitlines():
                line = line.strip()
                # Skip comments, empty lines, variable assignments, control flow
                if not line or line.startswith("#"):
                    continue
                if line.startswith("if ") or line.startswith("fi"):
                    continue
                if line.startswith("for ") or line.startswith("done"):
                    continue
                if line.startswith("else") or line.startswith("then"):
                    continue
                # Skip pure variable assignments (VAR=value with no command)
                if re.match(r'^[A-Z_]+=', line) and "tmux" not in line:
                    continue
                # Skip non-shell directives
                if line.startswith("read_file") or line.startswith("write_file"):
                    continue
                if line.startswith("ls(") or line.startswith("To monitor"):
                    continue
                commands.append(line)
        return commands

    def test_no_blocked_commands(self):
        """No commands use sudo, chmod, or other blocked operations."""
        # Import validate_command directly to avoid relative import issues
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "backends",
            Path(__file__).parent.parent / "EvoScientist" / "backends.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        validate_command = mod.validate_command

        commands = self._extract_commands()
        assert len(commands) > 0, "Should find some commands to validate"

        for cmd in commands:
            # Only validate simple commands (not multi-line or complex pipes)
            if "&&" in cmd:
                # Validate each part separately
                parts = cmd.split("&&")
                for part in parts:
                    part = part.strip()
                    if part:
                        error = validate_command(part)
                        if error:
                            pytest.fail(f"Command blocked: {part}\n  Error: {error}")
            else:
                error = validate_command(cmd)
                if error:
                    pytest.fail(f"Command blocked: {cmd}\n  Error: {error}")


# ---------------------------------------------------------------------------
# Script file completeness
# ---------------------------------------------------------------------------

class TestScriptCompleteness:
    """All expected scripts exist."""

    EXPECTED_SCRIPTS = [
        "find-sessions.sh",
        "wait-for-text.sh",
        "spawn-evosci.sh",
        "collect-results.sh",
    ]

    @pytest.mark.parametrize("script_name", EXPECTED_SCRIPTS)
    def test_script_exists(self, script_name):
        script = SCRIPTS_DIR / script_name
        assert script.exists(), f"Missing script: {script_name}"
        assert script.stat().st_size > 100, f"Script too small: {script_name}"

    def test_all_scripts_have_shebang(self):
        for script in SCRIPTS_DIR.glob("*.sh"):
            first_line = script.read_text().splitlines()[0]
            assert first_line.startswith("#!/"), (
                f"{script.name} missing shebang line"
            )

    def test_all_scripts_have_set_euo(self):
        for script in SCRIPTS_DIR.glob("*.sh"):
            content = script.read_text()
            assert "set -euo pipefail" in content, (
                f"{script.name} missing 'set -euo pipefail'"
            )

    def test_all_scripts_have_usage(self):
        for script in SCRIPTS_DIR.glob("*.sh"):
            content = script.read_text()
            assert "usage()" in content, (
                f"{script.name} missing usage() function"
            )


# ---------------------------------------------------------------------------
# spawn-evosci.sh specifics
# ---------------------------------------------------------------------------

class TestSpawnScript:
    """spawn-evosci.sh has required safety features."""

    @pytest.fixture(autouse=True)
    def _load(self):
        self.content = (SCRIPTS_DIR / "spawn-evosci.sh").read_text()

    def test_checks_recursion_depth(self):
        assert "EVOSCI_TMUX_DEPTH" in self.content

    def test_checks_max_depth(self):
        assert "EVOSCI_MAX_TMUX_DEPTH" in self.content

    def test_checks_session_limit(self):
        assert "EVOSCI_MAX_TMUX_SESSIONS" in self.content

    def test_checks_duplicate_session(self):
        assert "has-session" in self.content

    def test_creates_workspace(self):
        assert "mkdir -p" in self.content

    def test_updates_registry(self):
        assert "registry.json" in self.content

    def test_uses_workdir_flag(self):
        assert "--workdir" in self.content

    def test_validates_name(self):
        # Should validate name format
        assert "alphanumeric" in self.content.lower() or re.search(
            r'\[a-zA-Z0-9\]', self.content
        )

    def test_supports_thread_id(self):
        assert "--thread-id" in self.content

    def test_supports_no_thinking(self):
        assert "--no-thinking" in self.content
