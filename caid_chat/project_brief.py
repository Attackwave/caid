"""Project-local requirements; KiCad files remain authoritative for nets and geometry."""

import json
import math
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path

try:
    from .design_checklist import is_open_answer, rom_variant
except ImportError:
    from design_checklist import is_open_answer, rom_variant


FILENAME = "CAID-Projekt.json"
STATUSES = {"provided", "assumed", "sourced", "verified", "open", "conflict"}


def brief_path(project_path):
    return Path(project_path) / FILENAME


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _empty(project_path):
    return {"schema_version": 2, "project": Path(project_path).name,
            "requirements": {}, "open_questions": [], "decisions": [], "history": [],
            "active_design": None, "latest_design": None, "routing": None}


def load_brief(project_path):
    path = brief_path(project_path)
    if not path.is_file():
        return _empty(project_path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid CAID project brief: {path}")
    if data.get("schema_version") == 1 and isinstance(data.get("facts"), dict):
        data["requirements"] = {topic: {"value": value, "status": "provided",
                                        "source": "legacy project brief", "evidence": "",
                                        "updated_at": ""}
                                for topic, value in data.pop("facts").items()}
        data["schema_version"] = 2
        data["decisions"] = [{"question": item["question"], "answer": item["answer"],
                              "status": "provided", "source": "legacy project brief"}
                             for item in data.pop("clarifications", [])]
    if data.get("schema_version") != 2 or not isinstance(data.get("requirements"), dict):
        raise ValueError(f"Invalid CAID project brief: {path}")
    for topic, record in data["requirements"].items():
        if (not isinstance(topic, str) or not isinstance(record, dict) or
                not isinstance(record.get("value"), str) or
                record.get("status") not in STATUSES or
                not isinstance(record.get("source", ""), str) or
                not isinstance(record.get("evidence", ""), str)):
            raise ValueError(f"Invalid CAID requirement in {path}: {topic}")
    for key in ("open_questions", "decisions", "history"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"Invalid CAID project brief: {path}")
        data.setdefault(key, [])
    data.setdefault("active_design", None)
    data.setdefault("latest_design", None)
    data.setdefault("routing", None)
    if data["routing"] is not None:
        try:
            from .routing import validate_contract
        except ImportError:
            from routing import validate_contract
        validate_contract(data["routing"])
    return data


def save_brief(project_path, data):
    path = brief_path(project_path)
    if not path.parent.is_dir():
        raise FileNotFoundError(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def brief_hash(brief):
    return sha256(json.dumps(brief, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _history(data, topic, before, after):
    data["history"].append({"at": _now(), "topic": topic, "before": before, "after": after})


def set_requirement(project_path, topic, value, *, status="provided", source="user",
                    evidence="", check=None):
    topic, value, source, evidence = (str(item).strip() for item in (topic, value, source, evidence))
    if not 1 <= len(topic) <= 120 or not 1 <= len(value) <= 1000:
        raise ValueError("Requirement needs a topic (1-120 characters) and value (1-1000 characters)")
    if status not in STATUSES or not 1 <= len(source) <= 200 or len(evidence) > 1000:
        raise ValueError("Invalid requirement status or source")
    if status in {"sourced", "verified"} and not evidence:
        raise ValueError("A sourced requirement needs a specific evidence reference")
    if check is not None and not isinstance(check, dict):
        raise ValueError("Requirement check must be an object")
    if check and check.get("kind") == "board_size":
        if (check.get("mode", "exact") not in {"exact", "maximum"} or
                any(type(check.get(key)) not in (int, float) or
                    not math.isfinite(check[key]) or check[key] <= 0
                    for key in ("width_mm", "height_mm"))):
            raise ValueError("Invalid board size check")
    data = load_brief(project_path)
    previous = data["requirements"].get(topic)
    if len(data["requirements"]) >= 100 and previous is None:
        raise ValueError("The project brief is full")
    record = {"value": value, "status": status, "source": source,
              "evidence": evidence, "updated_at": _now()}
    if check is not None:
        record["check"] = check
    elif previous and previous.get("check") and previous.get("value") == value:
        record["check"] = previous["check"]
    data["requirements"][topic] = record
    if check and check.get("kind") == "board_size":
        data["pcb_size_mm"] = {key: float(check[key]) for key in ("width_mm", "height_mm")}
        data["pcb_size_mode"] = check.get("mode", "exact")
        data["pcb_size_status"] = "specified"
    _history(data, topic, previous, record)
    save_brief(project_path, data)
    return data


def set_fact(project_path, topic, value):
    return set_requirement(project_path, topic, value)


def remove_fact(project_path, topic):
    data = load_brief(project_path)
    if topic not in data["requirements"]:
        raise KeyError(topic)
    previous = data["requirements"].pop(topic)
    if previous.get("check", {}).get("kind") == "board_size":
        data["pcb_size_mm"] = None
        data["pcb_size_mode"] = None
        data["pcb_size_status"] = "open"
    _history(data, topic, previous, None)
    save_brief(project_path, data)
    return data


def add_open_question(project_path, question):
    question = question.strip()
    if not 1 <= len(question) <= 300:
        raise ValueError("Open question must have 1-300 characters")
    data = load_brief(project_path)
    questions = data["open_questions"]
    if len(questions) >= 60 and question not in questions:
        raise ValueError("Too many open questions")
    if question not in questions:
        questions.append(question)
        _history(data, question, None, {"status": "open question"})
    save_brief(project_path, data)
    return data


def resolve_open_question(project_path, question):
    data = load_brief(project_path)
    questions = data["open_questions"]
    if question not in questions:
        raise KeyError(question)
    questions.remove(question)
    _history(data, question, {"status": "open question"}, None)
    save_brief(project_path, data)
    return data


def apply_brief_proposal(project_path, base_hash, updates, questions):
    """Apply a reviewed model proposal in one write if the brief has not changed."""
    data = load_brief(project_path)
    if brief_hash(data) != base_hash:
        raise ValueError("The project brief changed since the proposal; request a new review")
    if len(updates) > 12 or len(questions) > 8:
        raise ValueError("Project brief proposal is too large")
    for item in updates:
        topic, value = item["topic"].strip(), item["value"].strip()
        status, evidence = item["status"], item["evidence"].strip()
        if (not 1 <= len(topic) <= 120 or not 1 <= len(value) <= 1000 or
                status not in {"provided", "assumed", "sourced"} or
                (status == "sourced" and not evidence)):
            raise ValueError("Invalid project brief update")
        if len(data["requirements"]) >= 100 and topic not in data["requirements"]:
            raise ValueError("The project brief is full")
        previous = data["requirements"].get(topic)
        record = {"value": value, "status": status, "source": "conversation, reviewed by user",
                  "evidence": evidence, "updated_at": _now()}
        if previous and previous.get("check") and previous.get("value") == value:
            record["check"] = previous["check"]
        data["requirements"][topic] = record
        _history(data, topic, previous, record)
    for question in questions:
        question = question.strip()
        if not 1 <= len(question) <= 300:
            raise ValueError("Invalid open question")
        if question not in data["open_questions"]:
            if len(data["open_questions"]) >= 60:
                raise ValueError("Too many open questions")
            data["open_questions"].append(question)
            _history(data, question, None, {"status": "open question"})
    save_brief(project_path, data)
    return data


def save_active_design(project_path, session):
    data = load_brief(project_path)
    data["active_design"] = session.to_record()
    save_brief(project_path, data)
    return data


def clear_active_design(project_path):
    data = load_brief(project_path)
    data["active_design"] = None
    save_brief(project_path, data)
    return data


def complete_design(project_path, destination, session, result, review=None):
    """Copy requirements into the new project, preserving provenance and open work."""
    source = load_brief(project_path)
    target = load_brief(destination)
    inherited = bool(getattr(session, "inherit_requirements", True))
    target["requirements"] = (json.loads(json.dumps(source["requirements"], ensure_ascii=False))
                              if inherited else {})
    selected_variant = rom_variant(session.task)
    if selected_variant:
        target["requirements"]["ROM-Variante"] = {
            "value": {"40": "40-pin", "42": "42-pin", "cdtv": "CDTV"}[selected_variant],
            "status": "provided", "source": "user task",
            "evidence": "", "updated_at": _now()}
    for item in getattr(session, "guided_records", []):
        if not item["deferred"]:
            target["requirements"][item["topic"]] = {
                "value": item["answer"], "status": "provided", "source": "user",
                "evidence": "", "updated_at": _now()}
    answered = {question.casefold().strip() for question, answer in session.answers
                if not is_open_answer(answer)}
    target["open_questions"] = ([question for question in source["open_questions"]
                                 if question.casefold().strip() not in answered] if inherited else [])
    for question, answer in session.answers:
        if is_open_answer(answer) and question != "PCB size / Platinengröße" and question not in target["open_questions"]:
            target["open_questions"].append(question)
    target["objective"] = session.task
    target["decisions"] = (list(source["decisions"]) if inherited else []) + [
        {"question": q, "answer": a, "source": "user",
         "status": "open" if is_open_answer(a) else "provided", "at": _now()}
        for q, a in session.answers]
    target["pcb_size_mm"] = session.board_size
    target["pcb_size_mode"] = getattr(session, "board_size_mode", "exact")
    target["pcb_size_status"] = "specified" if session.board_size else "open"
    target["generated_design"] = {key: result[key] for key in
                                  ("name", "components", "nets", "erc_errors", "erc_warnings",
                                   "drc_errors", "drc_warnings")}
    target["requirement_review"] = review or []
    target["history"] = list(source["history"]) if inherited else []
    target["routing"] = (json.loads(json.dumps(source.get("routing"), ensure_ascii=False))
                         if inherited else None)
    target["origin_project"] = str(project_path)
    save_brief(destination, target)
    source["active_design"] = None
    source["latest_design"] = {"directory": str(destination), "name": result["name"]}
    save_brief(project_path, source)
    return target


def model_context(brief):
    return {"project": brief.get("project"), "objective": brief.get("objective"),
            "requirements": dict(list(brief.get("requirements", {}).items())[:100]),
            "decisions": brief.get("decisions", [])[:40],
            "active_design": brief.get("active_design"),
            "open_questions": brief.get("open_questions", [])[:60],
            "pcb_size_mm": brief.get("pcb_size_mm"),
            "pcb_size_mode": brief.get("pcb_size_mode"),
            "pcb_size_status": brief.get("pcb_size_status"),
            "requirement_review": brief.get("requirement_review", [])[:100],
            "latest_design": brief.get("latest_design"),
            "routing": brief.get("routing")}


def set_routing(project_path, contract):
    try:
        from .routing import validate_contract
    except ImportError:
        from routing import validate_contract
    validate_contract(contract)
    data = load_brief(project_path)
    previous = data.get("routing")
    data["routing"] = contract
    _history(data, "routing", previous, contract)
    save_brief(project_path, data)
    return data


def summary(brief, language="en"):
    de = language == "de"
    labels = ({"provided": "Nutzerangabe", "assumed": "Annahme", "sourced": "Quelle genannt",
               "verified": "verifiziert", "open": "offen", "conflict": "Widerspruch"}
              if de else {"provided": "user input", "assumed": "assumption", "sourced": "source named",
                          "verified": "verified", "open": "open", "conflict": "conflict"})
    lines = [("Projekt-Steckbrief" if de else "Project brief") + ": " + str(brief.get("project", "?"))]
    if brief.get("objective"):
        lines.append(("Ziel: " if de else "Objective: ") + brief["objective"])
    for topic, record in brief.get("requirements", {}).items():
        source, evidence = record.get("source", ""), record.get("evidence", "")
        detail = f" [{labels.get(record['status'], record['status'])}; {source}" + (f"; {evidence}" if evidence else "") + "]"
        lines.append(f"{topic}: {record['value']}{detail}")
    for item in brief.get("decisions", [])[:40]:
        status = item.get("status", "provided")
        lines.append(f"{item['question']}: {item['answer']} [{labels.get(status, status)}; {item.get('source', 'user')}]")
    active = brief.get("active_design")
    if active:
        lines.append(("Aktiver Entwurf: " if de else "Active design: ") + active["task"])
        size = active.get("board_size")
        if size:
            lines.append(f"PCB: {size['width_mm']:g} × {size['height_mm']:g} mm")
        elif active.get("board_size_decided"):
            lines.append("PCB: Maße offen" if de else "PCB: dimensions open")
        for item in active.get("answers", []):
            lines.append(f"{item['question']}: {item['answer']}")
        for question in active.get("questions", []):
            lines.append(("Offen: " if de else "Open: ") + question)
        for item in active.get("guided", []):
            lines.append(("Nächste Frage: " if de else "Next question: ") + item["question"])
    elif brief.get("pcb_size_status"):
        size = brief.get("pcb_size_mm")
        lines.append(f"PCB: {size['width_mm']:g} × {size['height_mm']:g} mm" if size else
                     ("PCB: Maße offen" if de else "PCB: dimensions open"))
    for question in brief.get("open_questions", [])[:60]:
        lines.append(("Offen: " if de else "Open: ") + question)
    review = brief.get("requirement_review", [])
    if review:
        counts = {state: sum(row.get("status") == state for row in review)
                  for state in ("pass", "fail", "unchecked")}
        lines.append(("Entwurfsabgleich: " if de else "Design review: ") +
                     f"{counts['pass']} OK, {counts['fail']} " + ("Abweichungen" if de else "mismatches") +
                     f", {counts['unchecked']} " + ("ungeprüft" if de else "unchecked"))
        for row in review[:30]:
            state = {"pass": "OK", "fail": "Abweichung", "unchecked": "ungeprüft"}.get(
                row["status"], row["status"]) if de else row["status"]
            lines.append(f"  {row['topic']}: {state} — {row['detail']}")
    if brief.get("latest_design"):
        lines.append(("Letzter Entwurf: " if de else "Latest draft: ") +
                     brief["latest_design"]["directory"])
    return "\n".join(lines)


def history_summary(brief, language="en"):
    lines = ["Änderungshistorie:" if language == "de" else "Change history:"]
    for item in brief.get("history", [])[-20:]:
        before, after = item.get("before"), item.get("after")
        old = before.get("value", "∅") if isinstance(before, dict) else "∅"
        new = after.get("value", "∅") if isinstance(after, dict) else "∅"
        lines.append(f"{item.get('at', '?')} · {item.get('topic', '?')}: {old} → {new}")
    if len(lines) == 1:
        lines.append("Noch keine Änderungen." if language == "de" else "No changes yet.")
    return "\n".join(lines)


def compact_summary(brief, language="en"):
    de = language == "de"
    title = str(brief.get("project", "?"))
    if len(title) > 34:
        title = title[:31] + "…"
    parts = [("Projekt: " if de else "Project: ") + title]
    active = brief.get("active_design")
    size = active.get("board_size") if active else brief.get("pcb_size_mm")
    size_decided = active.get("board_size_decided") if active else brief.get("pcb_size_status")
    if size:
        mode = active.get("board_size_mode") if active else brief.get("pcb_size_mode")
        parts.append(f"PCB {'≤ ' if mode == 'maximum' else ''}{size['width_mm']:g} × {size['height_mm']:g} mm")
    elif size_decided:
        parts.append("PCB offen" if de else "PCB open")
    count = len(brief.get("open_questions", [])) + len(active.get("questions", []) if active else []) + len(active.get("guided", []) if active else [])
    if count:
        parts.append(f"{count} offen" if de else f"{count} open")
    if active:
        parts.append("Entwurf aktiv" if de else "design active")
    assumed = sum(record.get("status") == "assumed" for record in brief.get("requirements", {}).values())
    if assumed:
        parts.append(f"{assumed} Annahmen" if de else f"{assumed} assumptions")
    conflicts = sum(record.get("status") == "conflict" for record in brief.get("requirements", {}).values())
    if conflicts:
        parts.append(f"{conflicts} Widersprüche" if de else f"{conflicts} conflicts")
    return " · ".join(parts)
