"""Find interrupted project-local jobs without touching active work."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid


_MARKER = ".caid-stage.json"
_LOCK = ".caid-stage.lock"
_PREFIXES = {"design": ".caid-design-", "routing": ".caid-routing-"}


def _lock(file):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


class StageGuard:
    """Hold a platform file lock for the lifetime of one project-local stage."""

    def __init__(self, directory, kind):
        self.directory = Path(directory)
        if kind not in _PREFIXES or not self.directory.name.startswith(_PREFIXES[kind]):
            raise ValueError("Invalid CAID stage")
        self.file = (self.directory / _LOCK).open("w+b")
        self.file.write(b"0")
        self.file.flush()
        try:
            _lock(self.file)
            (self.directory / _MARKER).write_text(json.dumps({
                "schema_version": 1, "kind": kind,
                "project": str(self.directory.parent.resolve()),
                "created_utc": datetime.now(timezone.utc).isoformat(),
            }) + "\n", encoding="utf-8")
        except Exception:
            self.file.close()
            raise

    def close(self):
        if self.file is None:
            return
        (self.directory / _MARKER).unlink(missing_ok=True)
        _unlock(self.file)
        self.file.close()
        self.file = None
        (self.directory / _LOCK).unlink(missing_ok=True)


def _stage_state(directory, kind, project):
    marker = directory / _MARKER
    lock = directory / _LOCK
    if not marker.is_file() or not lock.is_file():
        return "unknown"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        if (data.get("schema_version") != 1 or data.get("kind") != kind or
                data.get("project") != str(project.resolve())):
            return "unknown"
        with lock.open("r+b") as file:
            _lock(file)
            _unlock(file)
        return "abandoned"
    except (OSError, ValueError, TypeError):
        return "active" if marker.is_file() and lock.is_file() else "unknown"


def scan_stages(project_path):
    """Classify only CAID-named immediate child directories of one project."""
    project = Path(project_path)
    if not project.is_dir():
        raise FileNotFoundError(project)
    result = []
    for directory in project.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        for kind, prefix in _PREFIXES.items():
            if directory.name.startswith(prefix):
                result.append({"path": directory, "kind": kind,
                               "state": _stage_state(directory, kind, project)})
                break
    return sorted(result, key=lambda item: item["path"].name)


def archive_abandoned_stages(project_path):
    """Move abandoned stages aside for inspection; never delete their contents."""
    project = Path(project_path)
    abandoned = [item for item in scan_stages(project) if item["state"] == "abandoned"]
    moved = []
    for item in abandoned:
        directory = item["path"]
        if _stage_state(directory, item["kind"], project) != "abandoned":
            continue
        destination_root = project / "CAID-Recovery"
        destination_root.mkdir(exist_ok=True)
        destination = destination_root / (directory.name + "-" + uuid.uuid4().hex[:8])
        directory.replace(destination)
        moved.append(destination)
    return moved


def describe_stages(project_path, language="en"):
    stages = scan_stages(project_path)
    de = language == "de"
    lines = ["Wiederherstellung" if de else "Recovery"]
    if not stages:
        lines.append("Keine liegen gebliebenen Arbeitskopien gefunden." if de else
                     "No interrupted project copies found.")
    for item in stages:
        state = {"active": "läuft" if de else "running",
                 "abandoned": "verwaist" if de else "abandoned",
                 "unknown": "Herkunft unklar" if de else "unverified"}[item["state"]]
        lines.append(f"{item['path'].name} · {state}")
    if any(item["state"] == "abandoned" for item in stages):
        lines.append("/wiederherstellung sichern verschiebt verwaiste Kopien nach CAID-Recovery."
                     if de else "/recovery save moves abandoned copies to CAID-Recovery.")
    if any(item["state"] == "unknown" for item in stages):
        lines.append("Kopien ohne gültige CAID-Markierung bitte manuell prüfen."
                     if de else "Inspect copies without a valid CAID marker manually.")
    return "\n".join(lines)
