"""Route untouched nets in a separate project copy with KiCad DRC as gate."""

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

try:
    from .diagnostics import _cli_path
    from .process import run_command
    from .project_brief import load_brief
    from .routing import preflight, validate_contract
    from .project_rules import synchronize_project_rules
except ImportError:
    from diagnostics import _cli_path
    from process import run_command
    from project_brief import load_brief
    from routing import preflight, validate_contract
    from project_rules import synchronize_project_rules


def _interpreter():
    if os.name == "nt":
        bundled = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad/10.0/bin/python.exe"
        if bundled.is_file():
            return str(bundled)
    return sys.executable


def _project_copy(source, destination, board_name):
    destination.mkdir(parents=True, exist_ok=True)
    stem = Path(board_name).stem
    for name in (board_name, stem + ".kicad_pro", stem + ".kicad_sch", stem + ".kicad_dru",
                 "CAID-Projekt.json", "sym-lib-table", "fp-lib-table"):
        path = source / name
        if path.is_file():
            shutil.copy2(path, destination / name)
    for path in source.glob("*.kicad_sym"):
        if path.is_file():
            shutil.copy2(path, destination / path.name)
    for path in source.glob("*.pretty"):
        if path.is_dir():
            shutil.copytree(path, destination / path.name)


def _source_fingerprints(project, board_name):
    """Fingerprint every project input that the routing copy can consume."""
    stem = Path(board_name).stem
    names = (board_name, stem + ".kicad_pro", stem + ".kicad_sch",
             stem + ".kicad_dru", "CAID-Projekt.json", "sym-lib-table",
             "fp-lib-table")
    paths = [project / name for name in names]
    paths.extend(project.glob("*.kicad_sym"))
    for library in project.glob("*.pretty"):
        if library.is_dir():
            paths.extend(path for path in library.rglob("*") if path.is_file())
    return {path.relative_to(project).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def _require_source_unchanged(project, board_name, fingerprints):
    current = _source_fingerprints(project, board_name)
    if current != fingerprints:
        changed = sorted(name for name in fingerprints.keys() | current.keys()
                         if fingerprints.get(name) != current.get(name))
        raise ValueError("Project input changed during routing: " +
                         ", ".join(changed[:5]) + "; restart from the saved project")


def _drc(path, language, token):
    report = path.parent / (path.stem + ".caid-drc.json")
    command = [_cli_path(language), "pcb", "drc", "--format", "json", "--refill-zones",
               "--output", str(report)]
    if (path.parent / (path.stem + ".kicad_sch")).is_file():
        command.append("--schematic-parity")
    command.append(str(path))
    completed = run_command(command, timeout=120, token=token)
    if not report.is_file():
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "KiCad DRC failed")
    data = json.loads(report.read_text(encoding="utf-8-sig"))
    report.unlink()
    return data


def _findings(report):
    def fingerprint(item):
        return json.dumps({"type": item.get("type"), "severity": item.get("severity"),
                           "description": item.get("description"),
                           "items": [(part.get("uuid"), part.get("description"))
                                     for part in item.get("items", [])]}, sort_keys=True)
    return Counter(fingerprint(item) for item in report.get("violations", [])
                   if item.get("severity") in {"error", "warning"})


def _worker(payload, staging, token):
    input_file = staging / "route_input.json"
    input_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    try:
        completed = run_command([_interpreter(), str(Path(__file__).with_name("route_worker.py")),
                                 str(input_file)], timeout=120, token=token)
    finally:
        input_file.unlink(missing_ok=True)
    if completed.returncode:
        output = completed.stderr.strip() or completed.stdout.strip()
        detail = next((line.strip() for line in reversed(output.splitlines()) if line.strip()),
                      "KiCad routing worker failed")
        raise ValueError(detail[:500])
    prefix = {"list": "CAID_CANDIDATES=", "inspect": "CAID_SNAPSHOT="}.get(
        payload.get("action"), "CAID_ROUTE=")
    marker = next((line[len(prefix):] for line in completed.stdout.splitlines()
                   if line.startswith(prefix)), None)
    if marker is None:
        raise RuntimeError("KiCad routing worker returned no result")
    return json.loads(marker)


def read_saved_board_snapshot(project_path, board_name, token=None):
    """Inspect the saved PCB with the same geometry reader used by routing."""
    source = Path(project_path) / board_name
    if not source.is_file() or source.suffix != ".kicad_pcb":
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory(prefix="caid-board-inspect-") as temporary:
        return _worker({"action": "inspect", "source": str(source)}, Path(temporary), token)


def _routing_output_parent(project):
    """Keep follow-up passes beside the previous copy, not nested within it."""
    return project.parent if project.parent.name == "CAID-Routing" else project / "CAID-Routing"


def route_project(project_path, board_name, contract, net_name=None, *, max_nets=20,
                  language="en", token=None, progress=None):
    """Return a reviewed route copy; original files remain untouched."""
    validate_contract(contract)
    if contract["requested_layers"] is None:
        raise ValueError("Set routing layers first")
    needed = ("track_width_mm", "clearance_mm", "edge_clearance_mm")
    if contract["requested_layers"] != 1:
        needed += ("via_diameter_mm", "via_drill_mm")
    missing = [name for name in needed if contract["limits_mm"][name] is None]
    if missing:
        raise ValueError("Set routing limits first: " + ", ".join(missing))
    if not 1 <= max_nets <= 100:
        raise ValueError("max_nets must be 1 to 100")
    project = Path(project_path)
    source = project / board_name
    if not source.is_file() or source.suffix != ".kicad_pcb":
        raise FileNotFoundError(source)
    source_fingerprints = _source_fingerprints(project, board_name)
    source_hash = source_fingerprints[board_name]
    staging = Path(tempfile.mkdtemp(prefix=".caid-routing-", dir=project))
    try:
        _project_copy(project, staging, board_name)
        _require_source_unchanged(project, board_name, source_fingerprints)
        working = staging / board_name
        synchronize_project_rules(staging / (Path(board_name).stem + ".kicad_pro"),
                                  contract, preserve_stricter=True)
        baseline = _drc(working, language, token)
        board_snapshot = _worker({"action": "inspect", "source": str(working)}, staging, token)
        brief = load_brief(project)
        if isinstance(brief.get("pcb_size_mm"), dict):
            board_snapshot["target_size_mm"] = {
                **brief["pcb_size_mm"], "mode": brief.get("pcb_size_mode") or "maximum"}
        blockers = preflight(contract, board_snapshot, baseline)
        if blockers:
            raise ValueError("Source PCB failed routing preflight: " + "; ".join(blockers[:6]))
        all_candidates = ([net_name] if net_name else
                          _worker({"action": "list", "source": str(working)}, staging, token))
        candidates = all_candidates[:max_nets]
        if not candidates:
            raise ValueError("No unrouted nets with 2 to 32 pads found")
        accepted = []
        skipped = []
        report = baseline
        for index, name in enumerate(candidates, 1):
            if token:
                token.check()
            if progress:
                progress(index, len(candidates), name)
            candidate_dir = staging / "candidate"
            if candidate_dir.exists():
                shutil.rmtree(candidate_dir)
            _project_copy(staging, candidate_dir, board_name)
            output = candidate_dir / board_name
            try:
                result = _worker({"source": str(working), "destination": str(output),
                                  "net": name, "contract": contract}, staging, token)
                checked = _drc(output, language, token)
                additional = _findings(checked) - _findings(report)
                if additional or checked.get("schematic_parity") or (
                        len(checked.get("unconnected_items", [])) >=
                        len(report.get("unconnected_items", []))):
                    raise ValueError(f"DRC/new connection check rejected route: "
                                     f"{sum(additional.values())} new findings, "
                                     f"{len(checked.get('unconnected_items', []))} open")
                shutil.copy2(output, working)
                report = checked
                accepted.append({key: value for key, value in result.items()
                                 if key not in {"source", "destination"}})
            except ValueError as exc:
                skipped.append({"net": name, "reason": str(exc)[:500]})
        if not accepted:
            reasons = "; ".join(f"{item['net']}: {item['reason']}" for item in skipped[:3])
            raise ValueError("No DRC-clean route found. " + reasons)
        _require_source_unchanged(project, board_name, source_fingerprints)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        parent = _routing_output_parent(project)
        parent.mkdir(exist_ok=True)
        destination = parent / f"{Path(board_name).stem}-{stamp}"
        if destination.exists():
            raise FileExistsError(destination)
        candidate_dir = staging / "candidate"
        if candidate_dir.exists():
            shutil.rmtree(candidate_dir)
        (staging / "CAID-ROUTING.json").write_text(json.dumps({
            "source": str(source), "source_sha256": source_hash,
            "source_files_sha256": source_fingerprints,
            "contract": contract, "accepted": accepted,
            "skipped": skipped, "unconnected_before": len(baseline.get("unconnected_items", [])),
            "unconnected_after": len(report.get("unconnected_items", [])),
            "eligible_before": len(all_candidates), "attempted": len(candidates),
            "unattempted": max(0, len(all_candidates) - len(candidates)),
            "remaining_scope": "nets with 2 to 32 pads and open copper connections"},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if token:
            token.check()
        _require_source_unchanged(project, board_name, source_fingerprints)
        staging.replace(destination)
        return {"directory": str(destination), "accepted": accepted, "skipped": skipped,
                "eligible_before": len(all_candidates), "attempted": len(candidates),
                "unattempted": max(0, len(all_candidates) - len(candidates)),
                "unconnected_before": len(baseline.get("unconnected_items", [])),
                "unconnected_after": len(report.get("unconnected_items", []))}
    finally:
        if staging.exists():
            shutil.rmtree(staging)
