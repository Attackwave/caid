"""Small deterministic intake for ROM adapter drafts; unanswered data stays explicit."""

import re


OPEN_ANSWERS = {"offen", "noch offen", "unbekannt", "weiß ich nicht", "weiss ich nicht",
                "open", "unknown", "not known", "tbd", "?"}

ROM_ITEMS = (
    {"id": "rom_images", "topic": "ROM-Images", "phase": "electrical",
     "de": "Welche Größe und Byte-Reihenfolge haben die beiden ROM-Images? Wenn die Dateien noch fehlen, antworte „offen“.",
     "en": "What are the size and byte order of both ROM images? If the files are unavailable, answer 'open'."},
    {"id": "socket_geometry", "topic": "Sockelgeometrie", "phase": "mechanical",
     "de": "Wie sind Pin 1, Kontaktabstand und Sockelreihen-Abstand am echten Ziel-Sockel vermessen? Wenn noch nicht gemessen, antworte „offen“.",
     "en": "How have pin 1, contact pitch and row spacing been measured on the physical target socket? If unmeasured, answer 'open'."},
    {"id": "clearance", "topic": "Bauteilfreiraum", "phase": "mechanical",
     "de": "Welche maximale Bauhöhe und welcher Freiraum gelten auf TOP und BOTTOM im eingebauten Zustand? Wenn unbekannt, antworte „offen“.",
     "en": "What maximum assembly height and clearance are available on TOP and BOTTOM when installed? If unknown, answer 'open'."},
    {"id": "programmer_connector", "topic": "Programmieranschluss", "phase": "programming",
     "de": "Welcher Programmierstecker soll verwendet werden und wo muss er im eingebauten Zustand zugänglich sein? Wenn noch offen, antworte „offen“.",
     "en": "Which programming connector should be used, and where must it be accessible when installed? If undecided, answer 'open'."},
)


def is_open_answer(answer):
    return answer.strip().casefold().rstrip(".! ") in OPEN_ANSWERS


def rom_variant(text):
    text = str(text).casefold()
    if "cdtv" in text:
        return "cdtv"
    if re.search(r"40[- ]?(?:pin|polig|pol)\b", text):
        return "40"
    if "flashrom42" in text or re.search(r"42[- ]?(?:pin|polig|pol)\b", text):
        return "42"
    return None


def guided_questions(task, brief, language="en"):
    context = " ".join((task, str(brief.get("objective", "")), str(brief.get("project", ""))))
    if not re.search(r"(?:amiga|kickstart|flashrom|rom[- ]?ersatz|\brom\b)", context, re.I):
        return []
    requirements = brief.get("requirements", {})
    result = []
    for item in ROM_ITEMS:
        known = requirements.get(item["topic"])
        if known and known.get("status") in {"provided", "sourced", "verified"}:
            continue
        result.append({"id": item["id"], "topic": item["topic"], "phase": item["phase"],
                       "question": item["de" if language == "de" else "en"]})
    return result
