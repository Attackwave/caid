"""Small English/German catalog following KiCad's configured interface language."""

import json
import locale
import os
from pathlib import Path


MESSAGES = {
    "subtitle": ("AI assistant in the KiCad PCB editor", "KI-Assistent im KiCad PCB-Editor"),
    "settings": ("Connection", "Verbindung"),
    "settings_summary": ("Connection · {provider} · {model}", "Verbindung · {provider} · {model}"),
    "actions": ("Actions", "Aktionen"),
    "action_board": ("Show board", "Platine anzeigen"),
    "action_schematic": ("Show schematic", "Schaltplan anzeigen"),
    "action_sync": ("Compare PCB and schematic", "Platine abgleichen"),
    "action_update": ("Update PCB in KiCad…", "PCB in KiCad aktualisieren…"),
    "action_new": ("New conversation", "Neues Gespräch"),
    "action_help": ("Help and commands", "Hilfe und Befehle"),
    "action_reconnect": ("Reconnect to KiCad", "KiCad-Verbindung prüfen"),
    "action_project": ("Project brief", "Projekt-Steckbrief"),
    "action_project_suggest": ("Suggest project details", "Projektangaben vorschlagen"),
    "brief_panel": ("Project brief", "Projekt-Steckbrief"),
    "kicad_connecting": ("Connecting to KiCad …", "Verbinde mit KiCad …"),
    "kicad_unavailable": ("KiCad API unavailable · retrying", "KiCad-API nicht erreichbar · erneuter Versuch folgt"),
    "kicad_connection_error": ("KiCad API did not respond: {error}\nUse /reconnect to try again. If it still times out, save your work and fully restart KiCad.",
                               "KiCad-API hat nicht geantwortet: {error}\nMit /verbinden erneut prüfen. Falls es weiter scheitert, Arbeit sichern und KiCad vollständig neu starten."),
    "review_title": ("Review proposed change", "Änderung prüfen"),
    "message_label": ("Message", "Nachricht"),
    "keyboard_hint": ("Enter to send · Shift+Enter for a new line", "Enter senden · Shift+Enter Zeilenumbruch"),
    "welcome": ("Ask about this board or describe a change.", "Frag mich zu dieser Platine oder beschreibe eine Änderung."),
    "board_changed": ("Board changed: {name}", "Platine gewechselt: {name}"),
    "provider": ("Provider", "Zugang"),
    "codex": ("Codex (WSL sign-in)", "Codex (WSL-Anmeldung)"),
    "claude_cli": ("Claude Code (WSL sign-in)", "Claude Code (WSL-Anmeldung)"),
    "agy_cli": ("Antigravity (WSL sign-in)", "Antigravity (WSL-Anmeldung)"),
    "opencode_cli": ("OpenCode (WSL sign-in)", "OpenCode (WSL-Anmeldung)"),
    "model": ("Model", "Modell"),
    "api_key": ("API key", "API-Schlüssel"),
    "api_key_optional": ("API key (optional)", "API-Schlüssel (optional)"),
    "server_url": ("Server URL", "Server-Adresse"),
    "local": ("local", "lokal"),
    "external_server": ("external server", "externer Server"),
    "test_connection": ("Test model", "Modell testen"),
    "testing_connection": ("Checking local model server …", "Prüfe lokalen Modellserver …"),
    "testing_model": ("Testing structured model output …", "Prüfe strukturierte Modellausgabe …"),
    "connection_unchecked": ("Server not checked", "Server nicht geprüft"),
    "connection_ok": ("Model ready · {count} found", "Modell bereit · {count} gefunden"),
    "cloud_model_ok": ("Model ready", "Modell bereit"),
    "connection_failed": ("Connection failed", "Verbindung fehlgeschlagen"),
    "model_needed": ("Enter a model ID first.", "Trage zuerst eine Modellkennung ein."),
    "key_required": ("Enter an API key for {provider} in Connection.",
                     "Trage unter Verbindung einen API-Schlüssel für {provider} ein."),
    "no_local_models": ("No models installed on this server.", "Auf diesem Server sind keine Modelle installiert."),
    "model_not_found": ("Model {model} is not installed on this server.",
                        "Modell {model} ist auf diesem Server nicht installiert."),
    "connection_error": ("Could not connect to the model server: {error}",
                         "Modellserver nicht erreichbar: {error}"),
    "send": ("Send", "Senden"),
    "cancel": ("Cancel", "Abbrechen"),
    "cancelled": ("Request cancelled.", "Anfrage abgebrochen."),
    "syncing": ("Comparing saved schematic nets with PCB pads …", "Vergleiche gespeicherte Schaltplan-Netze mit PCB-Pads …"),
    "sync_preview": ("Net sync preview: {count} pad net changes, {pads} PCB pads, {nets} schematic nets.",
                     "Netzabgleich-Vorschau: {count} Pad-Netze ändern sich, {pads} PCB-Pads, {nets} Schaltplan-Netze."),
    "sync_changes": ("Changes (excerpt):", "Änderungen (Auszug):"),
    "sync_warnings": ("Warnings:", "Hinweise:"),
    "sync_blockers": ("Cannot apply yet:", "Noch nicht anwendbar:"),
    "sync_none": ("PCB pad nets already match the saved schematic.", "PCB-Pad-Netze entsprechen bereits dem gespeicherten Schaltplan."),
    "sync_apply": ("Apply {count} pad net changes", "{count} Pad-Netzänderungen übernehmen"),
    "native_update": ("Update PCB in KiCad (F8)…", "PCB in KiCad aktualisieren (F8)…"),
    "native_running": ("Review changes in KiCad, then close its update dialog …",
                       "Prüfe die Änderungen in KiCad und schließe danach den Aktualisierungsdialog …"),
    "empty_schematic_for_pcb": ("The saved schematic contains no components. Restore or complete the circuit before updating the PCB.",
                                "Der gespeicherte Schaltplan enthält keine Bauteile. Stelle die Schaltung wieder her oder vervollständige sie, bevor du die Platine aktualisierst."),
    "sync_applied": ("{count} pad nets updated in one undo step. Run DRC and save the PCB.",
                     "{count} Pad-Netze in einem Rückgängig-Schritt aktualisiert. DRC ausführen und PCB speichern."),
    "sync_footprints_remaining": ("Footprint differences shown in the preview still need KiCad F8.",
                                  "Die in der Vorschau gezeigten Footprint-Abweichungen benötigen weiterhin KiCad F8."),
    "apply_board": ("Apply proposed placement", "Vorgeschlagene Platzierung anwenden"),
    "mark_board": ("Mark on board", "Auf Platine markieren"),
    "marked_board": ("{count} footprint(s) selected in KiCad for review.",
                     "{count} Bauteil(e) in KiCad zur Prüfung markiert."),
    "mark_failed": ("Could not mark footprints: {error}", "Bauteile konnten nicht markiert werden: {error}"),
    "apply_schematic": ("Apply schematic change", "Schaltplanänderung übernehmen"),
    "input_hint": ("Ask about the board or describe a change …", "Frage zur Platine oder beschreibe eine Änderung …"),
    "hint": ("/board · /schematic · /design task · /sync · /f8 · /help", "/platine · /schaltplan · /entwurf Aufgabe · /abgleich · /f8 · /hilfe"),
    "ready": ("Ready. /board shows the current PCB. For AI requests, your message, chat history and footprint summary are sent to the selected provider. The API key is kept for this session only.",
              "Bereit. /platine zeigt die aktuelle PCB-Übersicht. Bei einer KI-Anfrage gehen Nachricht, Gesprächsverlauf und Bauteilübersicht an den gewählten Modellzugang. Der API-Schlüssel bleibt nur in dieser Sitzung."),
    "you": ("You", "Du"),
    "context_updated": ("KiCad context updated:\n{summary}", "KiCad-Kontext aktualisiert:\n{summary}"),
    "unsaved_board": ("Unsaved board", "Ungespeicherte Platine"),
    "help": (
        "/board reads the PCB.\n\n"
        "/schematic reads the saved schematic and its nets.\n\n"
        "/sync previews pad net changes.\n\n"
        "/f8 opens KiCad's PCB update dialog.\n\n"
        "/sch task stages a schematic change.\n\n"
        "/design task creates a separate draft project with schematic and unrouted PCB.\n\n"
        "/project shows the project brief, requirements and open questions.\n\n"
        "/project suggest proposes project brief changes from the conversation for review.\n\n"
        "/project history shows recent requirement changes.\n\n"
        "/project set Topic: Value records a user-provided requirement.\n\n"
        "/project assumption Topic: Value records an assumption.\n\n"
        "/project verified Topic: Value | Source records a cited claim for review.\n\n"
        "/project size max 80 x 25 mm (or exact 80 x 25 mm) and /project side U1 TOP add automatic checks.\n\n"
        "/reconnect retries the KiCad API connection.\n\n"
        "/project open Question records an unresolved issue.\n\n"
        "/design cancel abandons the current design dialogue.\n\n"
        "/erc checks the saved schematic.\n\n"
        "/drc checks the saved PCB.\n\n"
        "/route sets routing requirements; /route check runs the preflight; /route start [count] attempts up to 100 eligible nets in a project copy.\n\n"
        "/new clears the conversation.\n\n"
        "In chat, you can ask CAID to inspect selected parts, nets, or footprints, "
        "assign an installed footprint, or propose placement.\n\n"
        "Schematic proposals show their PCB impact; after applying one, use F8 "
        "to review the PCB update.",
        "/platine liest das PCB.\n\n"
        "/schaltplan liest die gespeicherte Schaltung und ihre Netze.\n\n"
        "/abgleich zeigt Pad-Netzänderungen.\n\n"
        "/f8 öffnet KiCads PCB-Aktualisierung.\n\n"
        "/sch Aufgabe bereitet eine Schaltplanänderung vor.\n\n"
        "/entwurf Aufgabe erzeugt ein separates Entwurfsprojekt mit Schaltplan und ungerouteter Platine.\n\n"
        "/projekt zeigt Steckbrief, Anforderungen und offene Fragen.\n\n"
        "/projekt vorschlag schlägt Angaben aus dem Gespräch zur Prüfung vor.\n\n"
        "/projekt historie zeigt die letzten Änderungen.\n\n"
        "/projekt set Thema: Wert hält eine Nutzerangabe fest.\n\n"
        "/projekt annahme Thema: Wert hält eine Annahme fest.\n\n"
        "/projekt belegt Thema: Wert | Quelle hält eine belegte Angabe zur Prüfung fest.\n\n"
        "/projekt größe max 80 x 25 mm (oder exakt 80 x 25 mm) und /projekt seite U1 TOP ergänzen automatische Prüfungen.\n\n"
        "/verbinden prüft die KiCad-API-Verbindung erneut.\n\n"
        "/projekt offen Frage hält eine offene Frage fest.\n\n"
        "/entwurf abbrechen beendet den laufenden Entwurfsdialog.\n\n"
        "/erc prüft den gespeicherten Schaltplan.\n\n"
        "/drc prüft die gespeicherte Platine.\n\n"
        "/routing erfasst Routing-Vorgaben; /routing prüfen startet die Vorprüfung; /routing starten [Anzahl] versucht bis zu 100 geeignete Netze auf einer Projektkopie.\n\n"
        "/neu beginnt ein neues Gespräch.\n\n"
        "Im Chat kannst du CAID nach ausgewählten Bauteilen, Netzen oder Footprints "
        "fragen, einen installierten Footprint zuordnen oder eine Platzierung vorschlagen lassen.\n\n"
        "Schaltplanvorschläge zeigen ihre PCB-Auswirkung; nach Übernahme prüfst du "
        "die PCB-Aktualisierung mit F8."),
    "history_cleared": ("Chat history cleared.", "Gesprächsverlauf gelöscht."),
    "route_pass_size_required": ("Provide one routing pass size.", "Gib genau eine Anzahl für den Routinglauf an."),
    "route_pass_size_range": ("Routing pass size must be between 1 and 100.", "Die Anzahl pro Routinglauf muss zwischen 1 und 100 liegen."),
    "board_unknown": ("Could not identify the board: {error}", "Platine konnte nicht erkannt werden: {error}"),
    "board_unreadable": ("Could not read the board: {error}", "Platine konnte nicht gelesen werden: {error}"),
    "checking": ("{check} is running …", "{check} läuft …"),
    "reading_schematic": ("Reading the saved schematic and its nets …", "Lese den gespeicherten Schaltplan und seine Netze …"),
    "codex_only": ("Direct schematic editing currently uses Codex in WSL.", "Direkte Schaltplanbearbeitung nutzt derzeit den Codex-Zugang in WSL."),
    "staging": ("Codex is editing an isolated copy of the saved schematic …", "Codex bearbeitet eine isolierte Kopie des gespeicherten Schaltplans …"),
    "designing": ("AI is preparing a new circuit design …", "KI bereitet einen neuen Schaltungsentwurf vor …"),
    "checking_design": ("KiCad is checking the schematic, nets and ERC …", "KiCad prüft Schaltplan, Netze und ERC …"),
    "design_questions": ("Information needed before creating the circuit:", "Vor dem Schaltungsentwurf fehlen diese Angaben:"),
    "design_board_size": ("What maximum PCB size should I use (width × height in mm, for example 80 × 25 mm)? If it has not been measured yet, reply 'open' and I will keep the outline provisional. /design cancel ends this draft.",
                          "Welche maximale Platinengröße soll gelten (Breite × Höhe in mm, z. B. 80 × 25 mm)? Wenn sie noch nicht vermessen wurde, antworte 'offen'; der Umriss bleibt dann vorläufig. /entwurf abbrechen beendet diesen Entwurf."),
    "design_size_retry": ("Please give width × height in mm or 'open'.", "Bitte Breite × Höhe in mm oder 'offen' angeben."),
    "design_question": ("{question}\n\n{remaining} open question(s). Reply here; CAID will continue the same draft. /design cancel ends it.",
                        "{question}\n\n{remaining} offene Frage(n). Antworte hier; CAID setzt denselben Entwurf fort. /entwurf abbrechen beendet ihn."),
    "design_guided_question": ("{question}\n\nROM design intake · {phase} · {remaining} question(s) remaining. You can answer 'open' and continue with a provisional draft.",
                               "{question}\n\nROM-Entwurfsdialog · {phase} · noch {remaining} Frage(n). Du kannst mit „offen“ antworten und einen vorläufigen Entwurf fortsetzen."),
    "design_continue": ("The draft can be resumed with /design continue.", "Mit /entwurf weiter kannst du den Entwurf fortsetzen."),
    "design_already_active": ("A design dialogue is already active. Answer its question or cancel it before starting another.",
                              "Ein Entwurfsdialog läuft bereits. Beantworte seine Frage oder beende ihn, bevor du einen neuen beginnst."),
    "design_resumed": ("An unfinished design was restored from the project brief.", "Ein unfertiger Entwurf wurde aus dem Projekt-Steckbrief wiederhergestellt."),
    "design_variant_isolated": ("This is a different ROM variant. Requirements from the open project will not be copied into the new draft.",
                                "Das ist eine andere ROM-Variante. Vorgaben des geöffneten Projekts werden nicht in den neuen Entwurf übernommen."),
    "design_aborted": ("The design dialogue was ended; the project brief remains available.", "Der Entwurfsdialog wurde beendet; der Projekt-Steckbrief bleibt erhalten."),
    "design_board_changed": ("The open board changed. Return to the original project to answer this design question.",
                             "Die geöffnete Platine hat gewechselt. Kehre zum ursprünglichen Projekt zurück, um diese Entwurfsfrage zu beantworten."),
    "brief_unavailable": ("Project brief unavailable: {error}", "Projekt-Steckbrief nicht verfügbar: {error}"),
    "brief_set_usage": ("Use /project set Topic: Value", "Verwende /projekt set Thema: Wert"),
    "brief_verified_usage": ("Use /project verified Topic: Value | Source", "Verwende /projekt belegt Thema: Wert | Quelle"),
    "brief_size_usage": ("Use /project size max 80 x 25 mm or exact 80 x 25 mm",
                         "Verwende /projekt größe max 80 x 25 mm oder exakt 80 x 25 mm"),
    "brief_side_usage": ("Use /project side U1 TOP or BOTTOM", "Verwende /projekt seite U1 TOP oder BOTTOM"),
    "brief_design_mismatch": ("Design conflicts with project requirements: {details}",
                              "Der Entwurf widerspricht den Projektvorgaben: {details}"),
    "brief_extracting": ("Looking for project requirements in the conversation …",
                         "Suche Projektvorgaben im Gespräch …"),
    "brief_no_updates": ("No new project requirements were proposed.",
                         "Es wurden keine neuen Projektvorgaben vorgeschlagen."),
    "brief_preview": ("Proposed project brief changes · review before applying",
                      "Vorgeschlagene Änderungen am Projekt-Steckbrief · vor Übernahme prüfen"),
    "brief_open_preview": ("Open question: {question}", "Offene Frage: {question}"),
    "brief_apply": ("Apply project brief changes", "Projekt-Steckbrief ändern"),
    "brief_applied": ("Project brief updated. The change history is saved in CAID-Projekt.json.",
                      "Projekt-Steckbrief aktualisiert. Die Änderungshistorie steht in CAID-Projekt.json."),
    "brief_review_result": ("Project requirements: {checked} automatically matched; {unchecked} still need review. The full comparison is in the new project's CAID-Projekt.json and CAID-REVIEW.txt.",
                            "Projektvorgaben: {checked} automatisch abgeglichen; {unchecked} benötigen weitere Prüfung. Der vollständige Abgleich steht im neuen Projekt in CAID-Projekt.json und CAID-REVIEW.txt."),
    "part_review_result": ("Part evidence: {parts} components have incomplete documentation; {questions} questions remain open. See CAID-REVIEW.json in the new project. Physical verification is required for every part.",
                           "Bauteilnachweise: Bei {parts} Bauteilen fehlen Angaben; {questions} Fragen bleiben offen. Einzelheiten stehen im neuen Projekt in CAID-REVIEW.json. Jedes Bauteil muss physisch geprüft werden."),
    "brief_commands": ("AI proposal: /project suggest · History: /project history\nUser input: /project set Topic: Value · Assumption: /project assumption Topic: Value\nCited claim: /project verified Topic: Value | Source\nMaximum size: /project size max 80 x 25 mm · Exact size: /project size exact 80 x 25 mm · Side: /project side U1 TOP\nRemove: /project delete Topic · Open: /project open Question · Resolve: /project resolved Question",
                       "KI-Vorschlag: /projekt vorschlag · Historie: /projekt historie\nNutzerangabe: /projekt set Thema: Wert · Annahme: /projekt annahme Thema: Wert\nBelegte Angabe: /projekt belegt Thema: Wert | Quelle\nMaximalgröße: /projekt größe max 80 x 25 mm · Exakte Größe: /projekt größe exakt 80 x 25 mm · Seite: /projekt seite U1 TOP\nEntfernen: /projekt löschen Thema · Offen: /projekt offen Frage · Geklärt: /projekt geklärt Frage"),
    "design_created": ("New draft: {directory}\n\n{components} components, {nets} nets. KiCad ERC: {erc_errors} errors, {erc_warnings} warnings. DRC: {drc_errors} errors, {drc_warnings} warnings; schematic parity: 0 differences. Open the new KiCad project and review schematic and PCB. The footprints and pad nets are placed; traces are not routed.",
                       "Neuer Entwurf: {directory}\n\n{components} Bauteile, {nets} Netze. KiCad ERC: {erc_errors} Fehler, {erc_warnings} Warnungen. DRC: {drc_errors} Fehler, {drc_warnings} Warnungen; Schaltplan/PCB-Abweichungen: 0. Öffne das neue KiCad-Projekt und prüfe Schaltplan und PCB. Footprints und Pad-Netze sind gesetzt; Leiterbahnen sind noch nicht geroutet."),
    "api_key_needed": ("Enter an OpenAI API key above to discuss the board in chat.", "Trage oben einen OpenAI API-Schlüssel ein. Danach kann ich die Platine im Chat besprechen."),
    "thinking": ("Thinking …", "Denke nach …"),
    "inspecting_footprints": ("Checking installed KiCad footprint libraries …", "Prüfe installierte KiCad-Footprint-Bibliotheken …"),
    "inspections": ("Checked: {summary}", "Geprüft: {summary}"),
    "tool_limit": ("CAID could not finish after three inspection rounds. Please narrow the request.",
                   "CAID konnte die Anfrage nach drei Prüfrunden nicht abschließen. Bitte grenze die Aufgabe ein."),
    "checking_copy": ("Creating and checking the schematic working copy …", "Erstelle und prüfe die Schaltplan-Arbeitskopie …"),
    "schematic_overview": ("Saved schematic: {components} components, {nets} nets.", "Gespeicherter Schaltplan: {components} Bauteile, {nets} Netze."),
    "schematic_notes": ("Notes on the saved root sheet:", "Notizen auf dem gespeicherten Hauptblatt:"),
    "more_schematic_notes": ("More notes or full text remain in the saved file.",
                             "Weitere Notizen oder der vollständige Text stehen in der gespeicherten Datei."),
    "components": ("Components: ", "Bauteile: "),
    "nets": ("Nets (excerpt):", "Netze (Auszug):"),
    "more_nets": ("… {count} more nets; model context includes up to 300.", "… {count} weitere Netze; der Modellkontext enthält alle bis zur Grenze von 300."),
    "more_diff": ("\n… Read the full diff in the file above.", "\n… Diff in der genannten Datei vollständig lesbar."),
    "schematic_codex_only": ("Direct schematic editing is currently available through Codex (WSL sign-in).", "Direkte Schaltplanbearbeitung ist derzeit über Codex (WSL-Anmeldung) verfügbar."),
    "schematic_required": ("The saved schematic is needed for a combined change preview: {error}",
                           "Für die gemeinsame Änderungsvorschau muss der gespeicherte Schaltplan lesbar sein: {error}"),
    "proposal_rejected": ("Proposal rejected: {error}", "Änderungsvorschlag verworfen: {error}"),
    "placement_preview": ("Preview · footprint positions and sides\n", "Vorschau · Bauteilpositionen und Seiten\n"),
    "adjusted": ("\n\nCAID locally adjusted spacing for {refs}.", "\n\nCAID hat Abstände für {refs} lokal korrigiert."),
    "geometry": ("\n\nGeometry warnings (check before applying):\n", "\n\nGeometrie-Hinweise (vor Anwendung prüfen):\n"),
    "more_warnings": ("\n… {count} more warnings.", "\n… {count} weitere Hinweise."),
    "request_failed": ("Request failed: {error}", "Anfrage fehlgeschlagen: {error}"),
    "schematic_apply_failed": ("Schematic change not applied: {error}", "Schaltplan nicht übernommen: {error}"),
    "schematic_applied": ("Schematic changed. Backup: {backup}. Review ERC and the PCB update.",
                          "Schaltplan übernommen. Sicherung: {backup}. Prüfe ERC und die PCB-Aktualisierung."),
    "design_next_step": ("Schematic applied. Review Update PCB from Schematic (F8) in KiCad to transfer footprint and net changes to the PCB.",
                         "Schaltplan übernommen. Prüfe jetzt in KiCad „PCB aus Schaltplan aktualisieren“ (F8), um Footprint- und Netzänderungen auf die Platine zu übertragen."),
    "different_board": ("A different board is open.", "Es ist eine andere Platine geöffnet."),
    "apply_failed": ("Change not applied: {error}", "Änderung nicht angewendet: {error}"),
    "placements_applied": ("{count} footprint placement(s) changed. You can undo this in KiCad. Please run DRC and save the board.", "{count} Bauteilplatzierung(en) geändert. In KiCad mit Rückgängig widerrufbar. Bitte DRC prüfen und Platine speichern."),
}


def normalize_language(value):
    """Map KiCad names and locale codes to supported languages."""
    value = str(value or "").strip().replace("-", "_").casefold()
    if value == "default":
        return None
    if value in {"de", "deutsch", "german"} or value.startswith("de_"):
        return "de"
    return "en"


def system_language():
    if os.name == "nt":
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
                return normalize_language(buffer.value)
        except (AttributeError, OSError):
            pass
    return normalize_language(locale.getlocale()[0]) or "en"


def kicad_language(config_path=None):
    if config_path is None:
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        config_path = appdata / "kicad" / "10.0" / "kicad_common.json"
    try:
        configured = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))["system"]["language"]
    except (OSError, ValueError, KeyError, TypeError):
        return system_language()
    return normalize_language(configured) or system_language()


def tr(language, key, **values):
    english, german = MESSAGES[key]
    return (german if language == "de" else english).format(**values)


def localized(language, english, german):
    return german if language == "de" else english
