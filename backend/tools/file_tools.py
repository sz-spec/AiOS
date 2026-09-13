"""
File System Tools for Web-based Claude Code
============================================

Tools that allow AI to interact with the local file system.
"""

import os
import shlex
import subprocess
import glob as glob_module
import re
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


# Security: Define allowed base paths
ALLOWED_PATHS = [
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    ),  # VOS project root
]


def is_path_allowed(file_path: str) -> bool:
    """Check if a path is within allowed directories."""
    abs_path = os.path.abspath(file_path)
    return any(abs_path.startswith(allowed) for allowed in ALLOWED_PATHS)


def add_allowed_path(path: str):
    """Add a path to the allowed paths list."""
    ALLOWED_PATHS.append(os.path.abspath(path))


# Tool definitions for Claude API
TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file. Use this to view source code, configuration files, or any text file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to read (relative to project root or absolute)",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional: Start reading from this line number (1-indexed)",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional: Stop reading at this line number (inclusive)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist, or overwrites if it does.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit a file by replacing a specific string with another. Use this for targeted edits.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact string to find and replace",
                },
                "new_string": {
                    "type": "string",
                    "description": "The string to replace it with",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "If true, replace all occurrences. Default is false (first only).",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_files",
        "description": "List files and directories in a path. Use glob patterns for filtering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list, or a glob pattern like '**/*.py'",
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional glob pattern to filter results",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_files",
        "description": "Search for text/regex pattern in files. Like grep.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Optional glob pattern for files to search (e.g., '*.py')",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 50)",
                },
            },
            "required": ["pattern", "path"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command. Use for git, npm, pip, etc. Be careful with destructive commands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run"},
                "cwd": {
                    "type": "string",
                    "description": "Working directory for the command",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60)",
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "create_directory",
        "description": "Create a new directory (and parent directories if needed).",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to create",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete a file or empty directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The path to delete"}
            },
            "required": ["path"],
        },
    },
]


class FileTools:
    """File system tools for AI agents."""

    def __init__(self, base_path: Optional[str] = None):
        self.base_path = base_path or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        add_allowed_path(self.base_path)

    def _resolve_path(self, path: str) -> str:
        """Resolve a path relative to base_path if not absolute."""
        if os.path.isabs(path):
            return path
        return os.path.join(self.base_path, path)

    def _check_access(self, path: str) -> None:
        """Check if path access is allowed."""
        if not is_path_allowed(path):
            raise PermissionError(
                f"Access denied: {path} is outside allowed directories"
            )

    def read_file(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read file contents."""
        full_path = self._resolve_path(path)
        self._check_access(full_path)

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)

            if start_line or end_line:
                start = (start_line or 1) - 1
                end = end_line or total_lines
                lines = lines[start:end]
                content = "".join(lines)
                line_range = f"lines {start + 1}-{min(end, total_lines)}"
            else:
                content = "".join(lines)
                line_range = f"all {total_lines} lines"

            return {
                "success": True,
                "path": full_path,
                "content": content,
                "total_lines": total_lines,
                "range": line_range,
            }
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write content to a file."""
        full_path = self._resolve_path(path)
        self._check_access(full_path)

        try:
            # Create parent directories if needed
            os.makedirs(os.path.dirname(full_path), exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            return {
                "success": True,
                "path": full_path,
                "bytes_written": len(content.encode("utf-8")),
                "message": f"Successfully wrote to {path}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def edit_file(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Dict[str, Any]:
        """Edit a file by replacing text."""
        full_path = self._resolve_path(path)
        self._check_access(full_path)

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_string not in content:
                return {
                    "success": False,
                    "error": f"String not found in file: {old_string[:100]}...",
                }

            count = content.count(old_string)

            if replace_all:
                new_content = content.replace(old_string, new_string)
                replaced = count
            else:
                new_content = content.replace(old_string, new_string, 1)
                replaced = 1

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return {
                "success": True,
                "path": full_path,
                "occurrences_found": count,
                "occurrences_replaced": replaced,
                "message": f"Replaced {replaced} occurrence(s)",
            }
        except FileNotFoundError:
            return {"success": False, "error": f"File not found: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_files(self, path: str, pattern: Optional[str] = None) -> Dict[str, Any]:
        """List files in a directory or matching a glob pattern."""
        full_path = self._resolve_path(path)

        try:
            if "*" in path or "?" in path:
                # Path is a glob pattern
                matches = glob_module.glob(full_path, recursive=True)
            elif pattern:
                # Use pattern to filter
                full_pattern = os.path.join(full_path, pattern)
                matches = glob_module.glob(full_pattern, recursive=True)
            else:
                # List directory contents
                if os.path.isdir(full_path):
                    matches = [
                        os.path.join(full_path, f) for f in os.listdir(full_path)
                    ]
                else:
                    matches = [full_path] if os.path.exists(full_path) else []

            # Format results
            results = []
            for match in sorted(matches)[:100]:  # Limit to 100 results
                rel_path = os.path.relpath(match, self.base_path)
                is_dir = os.path.isdir(match)
                results.append(
                    {
                        "path": rel_path,
                        "type": "directory" if is_dir else "file",
                        "size": os.path.getsize(match) if not is_dir else None,
                    }
                )

            return {
                "success": True,
                "base_path": self.base_path,
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def search_files(
        self,
        pattern: str,
        path: str,
        file_pattern: Optional[str] = None,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        """Search for pattern in files."""
        full_path = self._resolve_path(path)

        try:
            regex = re.compile(pattern, re.IGNORECASE)
            results = []
            files_searched = 0

            # Get files to search
            if os.path.isfile(full_path):
                files = [full_path]
            else:
                glob_pattern = file_pattern or "**/*"
                files = glob_module.glob(
                    os.path.join(full_path, glob_pattern), recursive=True
                )
                files = [f for f in files if os.path.isfile(f)]

            for file_path in files:
                if len(results) >= max_results:
                    break

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line_num, line in enumerate(f, 1):
                            if regex.search(line):
                                results.append(
                                    {
                                        "file": os.path.relpath(
                                            file_path, self.base_path
                                        ),
                                        "line": line_num,
                                        "content": line.strip()[:200],
                                    }
                                )
                                if len(results) >= max_results:
                                    break
                    files_searched += 1
                except:
                    pass

            return {
                "success": True,
                "pattern": pattern,
                "results": results,
                "count": len(results),
                "files_searched": files_searched,
                "truncated": len(results) >= max_results,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Commands allowed to be executed via the run_command tool
    # NOTE: python/python3/node deliberately EXCLUDED — they allow arbitrary
    # code execution via -c / -e flags (interpreter escape vector).
    COMMAND_ALLOWLIST = frozenset(
        [
            "git",
            "npm",
            "npx",
            "pip",
            "ls",
            "cat",
            "grep",
            "find",
            "mkdir",
            "cp",
            "mv",
            "head",
            "tail",
            "wc",
            "sort",
            "uniq",
            "diff",
            "echo",
            "touch",
            "chmod",
            "pwd",
            "tree",
        ]
    )

    # Flags that must NEVER appear as arguments (interpreter code-exec flags)
    _BLOCKED_FLAGS = frozenset(["-c", "-e", "--eval", "-exec", "--exec"])

    # Phase v17: Blocked argument substrings for interpreter-level escape vectors.
    # These patterns are checked with substring match (case-insensitive) in all args.
    _BLOCKED_PATTERNS = (
        "core.pager",
        "alias.",
        "--eval",
        "core.editor",
        "core.sshcommand",
        "credential.helper",
    )

    # Phase v17: Per-executable safe-flag allowlists for commands that accept
    # arbitrary config via dash-args. ALL dash-prefixed args for these executables
    # are blocked UNLESS they appear in the safe-list.
    _SAFE_FLAGS: Dict[str, frozenset] = {
        "git": frozenset(
            [
                "-m",
                "--message",
                "-a",
                "--all",
                "-b",
                "--branch",
                "--oneline",
                "--graph",
                "--stat",
                "--name-only",
                "--name-status",
                "--pretty",
                "--format",
                "--abbrev-commit",
                "--no-pager",
                "-n",
                "--max-count",
                "-1",
                "-2",
                "-3",
                "-5",
                "-10",
                "--cached",
                "--staged",
                "--short",
                "--porcelain",
                "-u",
                "--set-upstream",
                "--tags",
                "--force",
                "--hard",
                "--soft",
                "--mixed",
                "-d",
                "--delete",
                "-D",
                "--no-edit",
                "--amend",
                "--rebase",
                "-v",
                "--verbose",
                "--quiet",
                "-q",
                "--version",
                "--diff-filter",
                "--follow",
                "--no-merges",
                "--author",
                "--since",
                "--until",
                "--after",
                "--before",
                "--color",
                "--no-color",
            ]
        ),
        "npm": frozenset(
            [
                "--save",
                "--save-dev",
                "-D",
                "-g",
                "--global",
                "--production",
                "--legacy-peer-deps",
                "--force",
                "-f",
                "--verbose",
                "--json",
                "--prefix",
                "--registry",
            ]
        ),
        "pip": frozenset(
            [
                "--user",
                "--upgrade",
                "-U",
                "--no-cache-dir",
                "--index-url",
                "--extra-index-url",
                "-r",
                "--requirement",
                "--target",
                "-t",
                "--force-reinstall",
                "--no-deps",
                "-q",
                "--quiet",
                "-v",
                "--verbose",
            ]
        ),
    }

    def run_command(
        self, command: str, cwd: Optional[str] = None, timeout: int = 60
    ) -> Dict[str, Any]:
        """Run a shell command (shell=False, allowlist-gated)."""
        work_dir = self._resolve_path(cwd) if cwd else self.base_path

        # Validate cwd is within allowed paths
        if cwd and not is_path_allowed(work_dir):
            return {"success": False, "error": f"Working directory not allowed: {cwd}"}

        # Parse command into argv (no shell interpretation)
        try:
            argv = shlex.split(command)
        except ValueError as e:
            return {"success": False, "error": f"Invalid command syntax: {e}"}

        if not argv:
            return {"success": False, "error": "Empty command"}

        # Extract the base executable name (strip path prefixes like /usr/bin/)
        executable = os.path.basename(argv[0])

        # Validate against allowlist
        if executable not in self.COMMAND_ALLOWLIST:
            logger.warning(
                "Blocked command not in allowlist: %s (executable=%s)",
                command,
                executable,
            )
            return {
                "success": False,
                "error": f"Command '{executable}' is not in the allowed commands list",
            }

        # Block dangerous flags on any command (interpreter escape prevention)
        for arg in argv[1:]:
            if arg in self._BLOCKED_FLAGS:
                logger.warning(
                    "Blocked dangerous flag: %s in command: %s", arg, command
                )
                return {
                    "success": False,
                    "error": f"Flag '{arg}' is not allowed for security reasons",
                }

        # Phase v17: Block dangerous argument patterns (case-insensitive substring match)
        for arg in argv[1:]:
            arg_lower = arg.lower()
            for pattern in self._BLOCKED_PATTERNS:
                if pattern in arg_lower:
                    logger.warning(
                        "Blocked dangerous pattern '%s' in arg '%s': %s",
                        pattern,
                        arg,
                        command,
                    )
                    return {
                        "success": False,
                        "error": f"Argument containing '{pattern}' is not allowed for security reasons",
                    }

        # Phase v17: Strict dash-arg filtering for interpreter-capable executables
        if executable in self._SAFE_FLAGS:
            safe = self._SAFE_FLAGS[executable]
            for arg in argv[1:]:
                if arg.startswith("-"):
                    # Extract the flag root (e.g., "--message=foo" -> "--message")
                    flag_root = arg.split("=", 1)[0]
                    if flag_root not in safe:
                        logger.warning(
                            "Blocked unsafe flag '%s' for %s: %s",
                            arg,
                            executable,
                            command,
                        )
                        return {
                            "success": False,
                            "error": f"Flag '{flag_root}' is not in the safe-list for '{executable}'",
                        }

        # For file-reading commands, validate all path arguments stay within allowed paths
        _FILE_READERS = {
            "cat",
            "head",
            "tail",
            "grep",
            "find",
            "cp",
            "mv",
            "chmod",
            "ls",
            "tree",
        }
        if executable in _FILE_READERS:
            for arg in argv[1:]:
                if arg.startswith("-"):
                    continue  # skip flags
                candidate = (
                    os.path.abspath(os.path.join(work_dir, arg))
                    if not os.path.isabs(arg)
                    else os.path.abspath(arg)
                )
                if not is_path_allowed(candidate):
                    logger.warning(
                        "Blocked path traversal: %s in command: %s", arg, command
                    )
                    return {
                        "success": False,
                        "error": f"Path '{arg}' is outside allowed directories",
                    }

        logger.info("Executing command: %s (cwd=%s)", argv, work_dir)

        try:
            result = subprocess.run(
                argv,
                shell=False,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            return {
                "success": result.returncode == 0,
                "command": command,
                "exit_code": result.returncode,
                "stdout": result.stdout[:10000] if result.stdout else "",
                "stderr": result.stderr[:10000] if result.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"success": False, "error": f"Command not found: {executable}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def create_directory(self, path: str) -> Dict[str, Any]:
        """Create a directory."""
        full_path = self._resolve_path(path)
        self._check_access(full_path)

        try:
            os.makedirs(full_path, exist_ok=True)
            return {
                "success": True,
                "path": full_path,
                "message": f"Directory created: {path}",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def delete_file(self, path: str) -> Dict[str, Any]:
        """Delete a file or empty directory."""
        full_path = self._resolve_path(path)
        self._check_access(full_path)

        try:
            if os.path.isdir(full_path):
                os.rmdir(full_path)  # Only removes empty directories
            else:
                os.remove(full_path)

            return {"success": True, "path": full_path, "message": f"Deleted: {path}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def execute_tool(
        self, tool_name: str, tool_input: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a tool by name with given input."""
        tool_map = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "edit_file": self.edit_file,
            "list_files": self.list_files,
            "search_files": self.search_files,
            "run_command": self.run_command,
            "create_directory": self.create_directory,
            "delete_file": self.delete_file,
        }

        if tool_name not in tool_map:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

        try:
            return tool_map[tool_name](**tool_input)
        except Exception as e:
            return {"success": False, "error": str(e)}


# Singleton instance
_file_tools: Optional[FileTools] = None


def get_file_tools(base_path: Optional[str] = None) -> FileTools:
    """Get or create the FileTools singleton."""
    global _file_tools
    if _file_tools is None:
        _file_tools = FileTools(base_path)
    return _file_tools
