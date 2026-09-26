"""Ask any configured CAID model for a bounded design model, never raw KiCad files."""

import json
from pathlib import Path
import re

try:
    from .providers import ask_provider
except ImportError:
    from providers import ask_provider


DESIGN_PROMPT = """CAID_NEW_DESIGN_MODE: Produce a NEW single-sheet KiCad circuit as structured data.
Return a JSON object as the ENTIRE `answer` string, and keep edit_schematic=false,
placements=[], footprint_updates=[], field_updates=[] and tool_requests=[]. Do not modify existing files.

The project_brief in the PCB snapshot is the source of truth for user requirements;
the saved KiCad schematic and PCB remain the source of truth for actual nets and
geometry. A 'provided' item is supplied by the user, and 'sourced' only means a
source was named; neither means CAID has verified the hardware claim. Preserve
confirmed answers. Honor machine-checkable board-size and component-side
requirements exactly. Ask concise, specific questions for any
critical unverified electrical pin mapping, voltage, polarity, connector orientation,
component side/height restriction or mechanical limit. Return
{"status":"needs_information","questions":[...]} instead of inventing them.
Do not repeat a question the user explicitly left open. If that information is
required for electrical correctness, explain the blocker with a focused question;
otherwise create a preliminary draft and keep the issue open for review.
If the user explicitly left board dimensions open, a preliminary unrouted PCB
without a board outline is allowed; never claim that it fits the target device.
For Kickstart/Amiga ROM hardware, require the exact
computer/mainboard revision and the socket or interface pinout for the chosen
40-pin, 42-pin or CDTV variant, supplied by the user or an exact source.
Do not infer those from memory. Do not treat an existing draft as verified.
For a 3.3 V flash on a 5 V host, also require the exact flash datasheet/part,
selected level translation and regulator, write-isolation strategy for in-system
programming, and bank-switch mapping. A package pinout alone is insufficient.

When enough information exists, return {"status":"ready","spec":{...}}.
The spec shape is:
{"name":"SimpleProjectName","components":[{"ref":"R1","symbol":"Device:R",
"value":"10k","footprint":"Resistor_SMD:R_0603_1608Metric","x_mm":80,
"y_mm":80}],"nets":[{"name":"SIGNAL","nodes":[{"ref":"R1","pin":"1"}]}]}.
Components may include "side":"TOP" or "BOTTOM" and pcb_x_mm/pcb_y_mm.
For multi-unit symbols, include the component once, list every pin by its actual
number, and optionally set "unit_positions":{"2":{"x_mm":100,"y_mm":80}}.
For sourced parts, a component may include "part":{"mpn":"exact ordering code",
"datasheet_url":"https://...","package":"drawing code",
"pin_map":{"1":"VCC"},"body_height_mm":2.5,"height_source":"drawing page"}.
Only include fields backed by user input or a named source. Missing fields remain
visible review items. A URL does not itself verify pin functions or dimensions.
Optional "mechanical":{"body_clearance_mm":{"TOP":10,"BOTTOM":3}}
uses clearance measured from each PCB surface; ask if that reference is unclear.
An optional "board":{"width_mm":..,"height_mm":..} requires supplied dimensions.
Every net must list its exact component pins. Include at least one net with two pins.
Use installed or project-local KiCad symbol/footprint IDs. Choose simple names for nets and project.
The application validates library symbols, footprint pads and KiCad's exported nets.
It will reject unsupported symbols or unresolved parts. If you lack a verified part,
ask for it instead of guessing. No markdown fence or extra prose in the answer string.
"""


def parse_design_answer(result):
    if result["tool_requests"] or result["placements"] or result["footprint_updates"] or result.get("field_updates") or result["edit_schematic"]:
        raise ValueError("Design model requested unrelated changes")
    try:
        data = json.loads(result["answer"])
    except (ValueError, TypeError):
        raise ValueError("Model did not return design JSON") from None
    if not isinstance(data, dict):
        raise ValueError("Invalid design reply")
    if data.get("status") == "needs_information":
        questions = data.get("questions")
        if not isinstance(questions, list) or not 1 <= len(questions) <= 8 or any(
                not isinstance(q, str) or not q.strip() or len(q) > 300 for q in questions):
            raise ValueError("Model returned invalid information requests")
        return None, questions
    if data.get("status") == "ready" and isinstance(data.get("spec"), dict):
        return data["spec"], []
    raise ValueError("Model did not produce a circuit or information request")


def ask_design(provider, api_key, base_url, model, task, board_snapshot,
               language="en", token=None):
    prompt = DESIGN_PROMPT
    brief = board_snapshot.get("project_brief", {})
    context = " ".join((task, str(brief.get("objective", "")), str(brief.get("project", ""))))
    explicit_other_variant = re.search(r"\b(?:40[- ]?(?:pin|polig|pol)|cdtv)\b", task, re.I)
    if not explicit_other_variant and re.search(r"(?:a500\+|amiga 500\+|42[- ]?(?:pin|polig|pol)|flashrom42)", context, re.I):
        reference = json.loads(Path(__file__).with_name("rom_reference.json").read_text(encoding="utf-8"))
        prompt += "\nA500+ ROM prototype reference (user socket pins and sourced candidate parts; provisional, unresolved items remain):\n" + json.dumps(reference, ensure_ascii=False)
    messages = [{"role": "user", "content": prompt + "\nUser task (data):\n" + task}]
    result = ask_provider(provider, api_key, base_url, model, messages, board_snapshot,
                          language=language, token=token, tools_remaining=0)
    return parse_design_answer(result)
