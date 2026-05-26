"""
Tool definitions for Elwin's agent mode.
Python port of the core tool suite from TOPSECRET (Claude Code).

Includes: bash, read_file, write_file, edit_file, glob, grep
"""

import glob as glob_module
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict  # JSON Schema object
    execute: Callable[[dict], str]


# ── Tool implementations ──────────────────────────────────────


def _bash(params: dict) -> str:
    cmd = params.get("command", "").strip()
    timeout = min(int(params.get("timeout", 30)), 120)
    if not cmd:
        return "Error: no command provided"
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout
        stderr = result.stderr.strip()
        out = stdout + ("\n[stderr]\n" + stderr if stderr else "")
        if not out.strip():
            return f"(exit code {result.returncode})"
        if len(out) > 8000:
            out = out[:7900] + "\n…[output truncated]"
        return out
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


def _read_file(params: dict) -> str:
    path = Path(params.get("path", "")).expanduser()
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 200))
    if not path.exists():
        return f"Error: file not found: {path}"
    try:
        lines = path.read_text(errors="replace").splitlines()
        snippet = lines[offset : offset + limit]
        numbered = "\n".join(f"{offset + i + 1}\t{line}" for i, line in enumerate(snippet))
        if offset + limit < len(lines):
            numbered += f"\n…[{len(lines) - offset - limit} more lines]"
        return numbered
    except Exception as e:
        return f"Error: {e}"


def _write_file(params: dict) -> str:
    path = Path(params.get("path", "")).expanduser()
    content = params.get("content", "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def _edit_file(params: dict) -> str:
    path = Path(params.get("path", "")).expanduser()
    old = params.get("old_string", "")
    new = params.get("new_string", "")
    if not path.exists():
        return f"Error: file not found: {path}"
    if not old:
        return "Error: old_string is required"
    try:
        text = path.read_text(errors="replace")
        count = text.count(old)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1:
            return f"Error: old_string found {count} times — add more surrounding context to make it unique"
        path.write_text(text.replace(old, new, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def _glob(params: dict) -> str:
    pattern = params.get("pattern", "")
    cwd = str(Path(params.get("cwd", ".")).expanduser())
    if not pattern:
        return "Error: pattern is required"
    try:
        matches = sorted(glob_module.glob(pattern, root_dir=cwd, recursive=True))[:200]
        if not matches:
            return "No matches found"
        if len(matches) == 200:
            matches.append("…[results truncated at 200]")
        return "\n".join(matches)
    except Exception as e:
        return f"Error: {e}"


def _grep(params: dict) -> str:
    pattern = params.get("pattern", "")
    path = str(Path(params.get("path", ".")).expanduser())
    case_insensitive = bool(params.get("case_insensitive", False))
    if not pattern:
        return "Error: pattern is required"
    flags = ["-rn", "--include=*"]
    if case_insensitive:
        flags.append("-i")
    try:
        result = subprocess.run(
            ["grep", *flags, "--", pattern, path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        out = result.stdout.strip()
        if not out:
            return "No matches found"
        lines = out.splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"…[{len(lines) - 200} more matches]"]
        return "\n".join(lines)
    except subprocess.TimeoutExpired:
        return "Error: grep timed out"
    except FileNotFoundError:
        return "Error: grep not available"
    except Exception as e:
        return f"Error: {e}"


# ── Tool registry ─────────────────────────────────────────────

TOOLS: list[ToolDef] = [
    ToolDef(
        name="bash",
        description=(
            "Execute a shell command on the local machine. "
            "Use for running scripts, checking system state, file operations, "
            "git, package management, etc. Output is capped at 8 KB."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (max 120, default 30)",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
        execute=_bash,
    ),
    ToolDef(
        name="read_file",
        description=(
            "Read a file from disk. Returns lines with line numbers. "
            "Use offset and limit to read large files in chunks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or ~/relative path"},
                "offset": {"type": "integer", "description": "Line to start from (0-indexed)", "default": 0},
                "limit": {"type": "integer", "description": "Max lines to return", "default": 200},
            },
            "required": ["path"],
        },
        execute=_read_file,
    ),
    ToolDef(
        name="write_file",
        description="Write full content to a file, creating it (and parent dirs) if needed.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to write"},
                "content": {"type": "string", "description": "Full file content"},
            },
            "required": ["path", "content"],
        },
        execute=_write_file,
    ),
    ToolDef(
        name="edit_file",
        description=(
            "Replace a unique string in a file with new content. "
            "old_string must appear exactly once — add surrounding context if ambiguous."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the file"},
                "old_string": {"type": "string", "description": "Exact string to replace (must be unique)"},
                "new_string": {"type": "string", "description": "Replacement string"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        execute=_edit_file,
    ),
    ToolDef(
        name="glob",
        description="Find files matching a glob pattern (supports ** for recursive).",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. 'src/**/*.py'"},
                "cwd": {"type": "string", "description": "Directory to search from (default: current dir)", "default": "."},
            },
            "required": ["pattern"],
        },
        execute=_glob,
    ),
    ToolDef(
        name="grep",
        description="Search for a regex pattern across files.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "File or directory to search in"},
                "case_insensitive": {"type": "boolean", "description": "Ignore case", "default": False},
            },
            "required": ["pattern", "path"],
        },
        execute=_grep,
    ),
]

_TOOL_MAP: dict[str, ToolDef] = {t.name: t for t in TOOLS}


def to_api_schema(tool: ToolDef) -> dict:
    """OpenAI-compatible function-calling schema for a tool."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def all_api_schemas() -> list[dict]:
    return [to_api_schema(t) for t in TOOLS]


def execute_tool(name: str, params: dict) -> str:
    tool = _TOOL_MAP.get(name)
    if not tool:
        return f"Error: unknown tool '{name}'"
    try:
        return tool.execute(params)
    except Exception as e:
        return f"Error executing {name}: {e}"
