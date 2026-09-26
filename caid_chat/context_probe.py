"""Bounded, isolated read of the running KiCad editor context."""

import json
from pathlib import Path
import subprocess
import sys

try:
    from .process import run_command
except ImportError:
    from process import run_command


_MARKER = "CAID_CONTEXT="


def probe_context(timeout=8):
    """Kill a stalled IPC probe without blocking CAID's event loop indefinitely."""
    command = [sys.executable, str(Path(__file__).resolve()), "--worker"]
    try:
        result = run_command(command, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"KiCad did not answer the context probe within {timeout:g} seconds") from exc
    if result.returncode:
        detail = next((item.removeprefix("CAID_PROBE_ERROR=")
                       for item in result.stderr.splitlines()
                       if item.startswith("CAID_PROBE_ERROR=")), None)
        raise RuntimeError((detail or "KiCad context probe failed")[:500])
    line = next((item[len(_MARKER):] for item in result.stdout.splitlines()
                 if item.startswith(_MARKER)), None)
    if line is None:
        raise RuntimeError("KiCad context probe returned no result")
    try:
        data = json.loads(line)
    except ValueError as exc:
        raise RuntimeError("KiCad context probe returned invalid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("document"), (str, type(None))) or not isinstance(
            data.get("project_path"), (str, type(None))) or not isinstance(data.get("version"), str) or not isinstance(
            data.get("major"), (int, type(None))) or isinstance(data.get("major"), bool):
        raise RuntimeError("KiCad context probe returned invalid data")
    return data


def _worker():
    from kipy import KiCad

    client = KiCad()
    board = client.get_board()
    document = str(board.name) if board and board.name else None
    project_path = str(board.document.project.path) if board else None
    try:
        version = client.get_version()
        version_name, major = str(version.full_version), int(version.major)
    except Exception:
        version_name, major = "unknown", None
    print(_MARKER + json.dumps({"document": document, "project_path": project_path,
                                "version": version_name, "major": major}))


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    try:
        _worker()
    except Exception as exc:
        print("CAID_PROBE_ERROR=" + str(exc)[:500], file=sys.stderr)
        sys.exit(2)
