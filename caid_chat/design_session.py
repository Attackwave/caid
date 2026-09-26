"""A bounded, explicit question/answer state for new circuit and PCB drafts."""

from dataclasses import dataclass, field
import re

try:
    from .design_checklist import guided_questions, is_open_answer, rom_variant
except ImportError:
    from design_checklist import guided_questions, is_open_answer, rom_variant


_DIMENSIONS = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*(?:mm\s*)?[x×]\s*(\d{1,3}(?:[.,]\d+)?)\s*mm\b", re.I)
_OPEN = re.compile(r"^(?:offen|unbekannt|noch offen|noch unbekannt|open|unknown|tbd|später|later|ohne umriss|no outline)[.!\s]*$", re.I)
_MAXIMUM = re.compile(r"\b(?:max(?:imal)?|maximum|höchstens|hoechstens|at\s+most)\b", re.I)


def board_size_mode(message, default="exact"):
    if default not in {"exact", "maximum"}:
        raise ValueError("Invalid board size mode")
    if _MAXIMUM.search(message):
        return "maximum"
    if re.search(r"\b(?:exakt|genau|exact)\b", message, re.I):
        return "exact"
    return default


def parse_board_size(message):
    """Return millimetres only for an explicit width × height statement."""
    match = _DIMENSIONS.search(message)
    if not match:
        return None
    width, height = (float(value.replace(",", ".")) for value in match.groups())
    if not 10 <= width <= 400 or not 10 <= height <= 400:
        raise ValueError("Board width and height must be between 10 and 400 mm")
    return {"width_mm": width, "height_mm": height}


@dataclass
class DesignSession:
    task: str
    document: str
    project_path: str
    board_size: dict | None = None
    board_size_decided: bool = False
    answers: list = field(default_factory=list)
    guided: list = field(default_factory=list)
    guided_records: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    model_rounds: int = 0
    inherit_requirements: bool = True
    board_size_mode: str = "exact"

    @classmethod
    def start(cls, task, board_snapshot, language="en"):
        size = parse_board_size(task)
        brief = board_snapshot.get("project_brief", {})
        requested_variant = rom_variant(task)
        source_variant = rom_variant(" ".join((str(brief.get("objective", "")),
                                               str(brief.get("project", "")))))
        inherit = not (requested_variant and source_variant and requested_variant != source_variant)
        return cls(task, str(board_snapshot["document"]), str(board_snapshot["project_path"]),
                   board_size=size, board_size_decided=size is not None,
                   guided=guided_questions(task, brief if inherit else {}, language),
                   inherit_requirements=inherit, board_size_mode=board_size_mode(task))

    def needs_board_size(self):
        return not self.board_size_decided

    def record_board_size(self, answer):
        size = parse_board_size(answer)
        if size is None and not _OPEN.fullmatch(answer.strip()):
            return False
        self.board_size = size
        self.board_size_decided = True
        if size:
            self.board_size_mode = board_size_mode(answer, "maximum")
        self.answers.append(("PCB size / Platinengröße", answer.strip()))
        return True

    def set_questions(self, questions):
        if self.model_rounds >= 8:
            raise ValueError("Too many design clarification rounds")
        pending = list(dict.fromkeys(question.strip() for question in questions))
        if not pending:
            raise ValueError("The model did not provide a usable design question")
        self.questions = pending
        self.model_rounds += 1

    def answer_question(self, answer):
        if not self.questions or not answer.strip():
            return False
        self.answers.append((self.questions.pop(0), answer.strip()))
        return True

    def answer_guided(self, answer):
        if not self.guided or not answer.strip():
            return None
        if len(answer.strip()) > 1000:
            raise ValueError("Design answer must have at most 1000 characters")
        item = self.guided.pop(0)
        self.answers.append((item["question"], answer.strip()))
        record = {"topic": item["topic"], "question": item["question"],
                  "answer": answer.strip(), "deferred": is_open_answer(answer)}
        self.guided_records.append(record)
        return record

    def model_task(self):
        lines = [self.task]
        if self.board_size:
            meaning = ("maximum; choose an outline no larger than these dimensions" if
                       self.board_size_mode == "maximum" else "exact; use these dimensions")
            lines.append(f"PCB outline: {self.board_size['width_mm']} × {self.board_size['height_mm']} mm {meaning}. Keep the full footprint extent within the chosen outline beginning at (20, 20) mm.")
        elif self.board_size_decided:
            lines.append("The user explicitly left PCB dimensions open. Produce an unrouted PCB without a board outline; do not invent dimensions.")
        for question, answer in self.answers:
            lines.append(f"Clarification — {question}\nAnswer — {answer}")
        if any(is_open_answer(answer) for _, answer in self.answers):
            lines.append("Some user answers explicitly leave details open. Keep these as review items. Do not infer them or claim manufacturing readiness.")
        return "\n\n".join(lines)

    def finalize_spec(self, spec):
        result = dict(spec)
        if self.board_size:
            if self.board_size_mode == "exact":
                result["board"] = dict(self.board_size)
            else:
                result.setdefault("board", dict(self.board_size))
        else:
            result.pop("board", None)
        result["design_brief"] = {"request": self.task,
                                  "answers": [{"question": q, "answer": a} for q, a in self.answers],
                                  "deferred_questions": [q for q, a in self.answers if is_open_answer(a)],
                                  "board_size_status": "specified" if self.board_size else "open",
                                  "board_size_mode": self.board_size_mode}
        return result

    def to_record(self):
        return {"task": self.task, "document": self.document,
                "project_path": self.project_path, "board_size": self.board_size,
                "board_size_decided": self.board_size_decided,
                "answers": [{"question": q, "answer": a} for q, a in self.answers],
                "guided": list(self.guided),
                "guided_records": list(self.guided_records),
                "questions": list(self.questions), "model_rounds": self.model_rounds,
                "inherit_requirements": self.inherit_requirements,
                "board_size_mode": self.board_size_mode}

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, dict) or not isinstance(record.get("task"), str):
            raise ValueError("Invalid active design record")
        answers = record.get("answers", [])
        guided = record.get("guided", [])
        guided_records = record.get("guided_records", [])
        questions = record.get("questions", [])
        if (not isinstance(answers, list) or not isinstance(guided, list) or
                not isinstance(guided_records, list) or not isinstance(questions, list)):
            raise ValueError("Invalid active design answers")
        return cls(record["task"], record["document"], record["project_path"],
                   record.get("board_size"), bool(record.get("board_size_decided")),
                   [(item["question"], item["answer"]) for item in answers],
                   list(guided), list(guided_records),
                   [str(question) for question in questions], int(record.get("model_rounds", 0)),
                   bool(record.get("inherit_requirements", True)),
                   record.get("board_size_mode", "exact"))
