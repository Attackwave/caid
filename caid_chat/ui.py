"""Native KiCad chat window with a review step for PCB edits."""

import json
import os
from pathlib import Path
import threading
import time

import wx
from kipy.errors import ApiError, ConnectionError as KiCadConnectionError
from kipy.proto.common import ApiStatusCode

from board_ops import apply_placements, describe_placements, geometry_warnings, mark_footprints, repair_placements, snapshot, validate_placements
from brief_ai import BriefProposal, ask_brief_proposal
from context import EditorContext, capability_summary
from context_probe import probe_context
from circuit_design import stage_new_design
from design_ai import ask_design
from design_session import DesignSession, board_size_mode, parse_board_size
from design_checklist import is_open_answer
from design_plan import DesignPlan, apply_design_plan, describe_design_plan, prepare_design_plan
from diagnostics import read_drc_report, run_drc, run_erc
from footprints import asks_about_footprint, inspect_footprints
from i18n import kicad_language, tr
from ipc_support import board_hint_path, connection_detail
from net_sync import NetSyncPlan, apply_sync, prepare_sync
from native_sync import describe_outcome, invoke_kicad_update
from process import CancelToken
from project_brief import (add_open_question, apply_brief_proposal, brief_hash, clear_active_design,
                           complete_design, load_brief, model_context, remove_fact,
                           resolve_open_question, save_active_design,
                           set_fact, set_requirement, summary as brief_summary,
                           set_routing,
                           history_summary as brief_history_summary,
                           compact_summary as brief_compact_summary)
from project_recovery import archive_abandoned_stages, describe_stages
from project_status import overview as project_overview
from requirement_review import mismatches, review_spec
from route_project import read_saved_board_snapshot, route_project
from provider_settings import load_settings, save_settings
from routing import default_contract, describe as describe_routing, set_layers, set_limits
from providers import CLI_PROVIDERS, DEFAULT_MODELS, DEFAULT_URLS, LOCAL_PROVIDERS, PROVIDERS, ask_provider, is_loopback_url, list_local_models, probe_model
from project_tools import execute_read_tool, tool_label
from schematic import (read_schematic, stage_field_updates, stage_footprint_updates,
                       stage_net_renames, stage_no_connect_markers,
                       stage_pin_connections, stage_schematic_edit)


class ChatFrame(wx.Frame):
    def __init__(self, kicad_client):
        super().__init__(None, title="CAID", size=(690, 760))
        self._kicad = kicad_client
        self._context = EditorContext("unknown", None, "pcb", None)
        self._connection_ok = False
        self._connection_error = ""
        self._context_probe_running = False
        self._next_context_probe = 0.0
        hinted_board = board_hint_path(os.environ.get("CAID_BOARD_HINT"))
        self._project_path = str(hinted_board.parent) if hinted_board else None
        self._hinted_board_name = hinted_board.name if hinted_board else None
        self._resume_announced = False
        self._history = []
        self._pending = None
        self._design_session = None
        self._brief_project = None
        self._busy = False
        self._closed = False
        self._job = None
        self._job_cancelable = True
        self._native_sync_running = False
        self._language = kicad_language()
        saved_connection = load_settings()
        self.SetMinSize((520, 700))
        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_BTNFACE))
        root = wx.BoxSizer(wx.VERTICAL)
        header = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(panel, label="CAID")
        title_font = wx.Font(panel.GetFont())
        title_font.SetPointSize(title_font.GetPointSize() + 5)
        title_font.SetWeight(wx.FONTWEIGHT_BOLD)
        title.SetFont(title_font)
        header.Add(title, 0, wx.ALIGN_CENTER_VERTICAL)
        header.AddStretchSpacer()
        self.actions_button = wx.Button(panel, label=self._t("actions"))
        header.Add(self.actions_button, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(header, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        self.context_label = wx.StaticText(panel, label=self._t("kicad_connecting"))
        root.Add(self.context_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        self.brief_pane = wx.CollapsiblePane(panel, label=self._t("brief_panel"))
        brief_parent = self.brief_pane.GetPane()
        brief_layout = wx.BoxSizer(wx.VERTICAL)
        self.brief_text = wx.TextCtrl(brief_parent, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE,
                                      size=(-1, 130))
        brief_layout.Add(self.brief_text, 1, wx.EXPAND)
        brief_parent.SetSizer(brief_layout)
        root.Add(self.brief_pane, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

        self.settings_pane = wx.CollapsiblePane(panel, label=self._t("settings"))
        settings_parent = self.settings_pane.GetPane()
        settings = wx.FlexGridSizer(0, 2, 8, 12)
        settings.AddGrowableCol(1)
        settings.Add(wx.StaticText(settings_parent, label=self._t("provider")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.provider = wx.Choice(settings_parent, choices=[self._t("codex"), self._t("claude_cli"), self._t("agy_cli"), self._t("opencode_cli"), "OpenAI API", "Anthropic Claude", "Google Gemini", "Ollama", "LM Studio"])
        default_provider = "codex" if os.name == "nt" else "openai"
        self.provider.SetSelection(PROVIDERS.index(saved_connection.get("provider", default_provider)))
        self._previous_provider = None
        self._models = saved_connection.get("models", {})
        self._urls = saved_connection.get("urls", {})
        self._keys = {"openai": os.environ.get("OPENAI_API_KEY", ""),
                      "anthropic": os.environ.get("ANTHROPIC_API_KEY", ""),
                      "gemini": os.environ.get("GEMINI_API_KEY", "")}
        settings.Add(self.provider, 1, wx.EXPAND)
        settings.Add(wx.StaticText(settings_parent, label=self._t("model")), 0, wx.ALIGN_CENTER_VERTICAL)
        initial_provider = PROVIDERS[self.provider.GetSelection()]
        self.model = wx.TextCtrl(settings_parent, value=os.environ.get("CAID_MODEL", self._models.get(initial_provider, DEFAULT_MODELS[initial_provider])))
        settings.Add(self.model, 1, wx.EXPAND)
        self.server_label = wx.StaticText(settings_parent, label=self._t("server_url"))
        settings.Add(self.server_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.server_url = wx.TextCtrl(settings_parent, value="")
        settings.Add(self.server_url, 1, wx.EXPAND)
        self.api_key_label = wx.StaticText(settings_parent, label=self._t("api_key"))
        settings.Add(self.api_key_label, 0, wx.ALIGN_CENTER_VERTICAL)
        self.api_key = wx.TextCtrl(settings_parent, value="", style=wx.TE_PASSWORD)
        settings.Add(self.api_key, 1, wx.EXPAND)
        settings_parent.SetSizer(settings)
        connection_row = wx.BoxSizer(wx.HORIZONTAL)
        self.connection_status = wx.StaticText(settings_parent, label="")
        connection_row.Add(self.connection_status, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.test_connection = wx.Button(settings_parent, label=self._t("test_connection"))
        connection_row.Add(self.test_connection, 0)
        settings_parent.GetSizer().Add(connection_row, 0, wx.EXPAND | wx.TOP, 8)
        root.Add(self.settings_pane, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 16)

        self.transcript = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.BORDER_SIMPLE)
        self.transcript.SetBackgroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOW))
        chat_font = wx.Font(self.transcript.GetFont())
        chat_font.SetPointSize(chat_font.GetPointSize() + 1)
        self.transcript.SetFont(chat_font)
        self.transcript.SetMargins(10, 8)
        root.Add(self.transcript, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)
        self.proposal_label = wx.StaticText(panel, label=self._t("review_title"))
        self.proposal_label.Hide()
        root.Add(self.proposal_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)
        self.proposal = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.BORDER_SIMPLE, size=(-1, 130))
        self.proposal.Hide()
        root.Add(self.proposal, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self.apply_button = wx.Button(panel, label=tr(self._language, "apply_board"))
        self.apply_button.Hide()
        self.mark_button = wx.Button(panel, label=self._t("mark_board"))
        self.mark_button.Hide()
        self.native_update_button = wx.Button(panel, label=tr(self._language, "native_update"))
        self.native_update_button.Hide()
        action_row = wx.BoxSizer(wx.HORIZONTAL)
        action_row.Add(self.apply_button, 0, wx.RIGHT, 8)
        action_row.Add(self.mark_button, 0, wx.RIGHT, 8)
        action_row.Add(self.native_update_button, 0)
        root.Add(action_row, 0, wx.LEFT | wx.RIGHT | wx.TOP, 16)
        self.activity_label = wx.StaticText(panel, label="")
        self.activity_label.Hide()
        root.Add(self.activity_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        self.activity_gauge = wx.Gauge(panel, range=100)
        self.activity_gauge.Hide()
        root.Add(self.activity_gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        composer_heading = wx.BoxSizer(wx.HORIZONTAL)
        composer_heading.Add(wx.StaticText(panel, label=self._t("message_label")), 0, wx.ALIGN_CENTER_VERTICAL)
        composer_heading.AddStretchSpacer()
        keyboard_hint = wx.StaticText(panel, label=self._t("keyboard_hint"))
        keyboard_hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        composer_heading.Add(keyboard_hint, 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(composer_heading, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 16)
        compose = wx.BoxSizer(wx.HORIZONTAL)
        self.input = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_PROCESS_ENTER, size=(-1, 110))
        self.input.SetHint(self._t("input_hint"))
        self.input.SetFont(chat_font)
        self.input.SetMargins(10, 8)
        compose.Add(self.input, 1, wx.EXPAND | wx.RIGHT, 10)
        self.send_button = wx.Button(panel, label=self._t("send"), size=(105, 34))
        compose.Add(self.send_button, 0, wx.ALIGN_BOTTOM)
        self.cancel_button = wx.Button(panel, label=self._t("cancel"), size=(105, 34))
        self.cancel_button.Hide()
        compose.Add(self.cancel_button, 0, wx.ALIGN_BOTTOM)
        root.Add(compose, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP | wx.BOTTOM, 16)
        panel.SetSizer(root)
        self._panel = panel
        self.send_button.Bind(wx.EVT_BUTTON, self._send)
        self.cancel_button.Bind(wx.EVT_BUTTON, self._cancel)
        self.provider.Bind(wx.EVT_CHOICE, self._provider_changed)
        self.model.Bind(wx.EVT_TEXT, self._model_changed)
        self.server_url.Bind(wx.EVT_TEXT, self._connection_changed)
        self.test_connection.Bind(wx.EVT_BUTTON, self._test_connection)
        self.settings_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self._settings_changed)
        self.brief_pane.Bind(wx.EVT_COLLAPSIBLEPANE_CHANGED, self._settings_changed)
        self.actions_button.Bind(wx.EVT_BUTTON, self._show_actions)
        self.apply_button.Bind(wx.EVT_BUTTON, self._apply)
        self.mark_button.Bind(wx.EVT_BUTTON, self._mark_proposal)
        self.native_update_button.Bind(wx.EVT_BUTTON, self._open_native_sync)
        self.input.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.input.Bind(wx.EVT_TEXT, self._resize_composer)
        self.Bind(wx.EVT_CLOSE, self._close)
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._refresh_context, self._timer)
        self._timer.Start(3000)
        self._activity_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._pulse_activity, self._activity_timer)
        self._refresh_context()
        if self._project_path:
            self.context_label.SetLabel(f"{self._hinted_board_name}  ·  {self._t('kicad_connecting')}")
            self._refresh_project_brief()
        self._provider_changed(None)
        self._append("CAID", self._t("welcome"))
        self._restore_window_position()
        self.input.SetFocus()

    def _t(self, key, **values):
        return tr(self._language, key, **values)

    def _window_state_path(self):
        appdata = Path(os.environ.get("APPDATA", Path.home() / ".config"))
        return appdata / "CAID" / "window.json"

    def _restore_window_position(self):
        try:
            state = json.loads(self._window_state_path().read_text(encoding="utf-8"))
            x, y, width, height = (int(state[key]) for key in ("x", "y", "width", "height"))
            if not (520 <= width <= 2400 and 700 <= height <= 1800):
                return
            display = wx.Display.GetFromPoint(wx.Point(x + 40, y + 40))
            if display == wx.NOT_FOUND:
                return
            area = wx.Display(display).GetClientArea()
            if not area.Intersects(wx.Rect(x, y, width, height)):
                return
            self.SetSize((width, height))
            self.SetPosition((x, y))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_window_position(self):
        if self.IsIconized() or self.IsMaximized():
            return
        position, size = self.GetPosition(), self.GetSize()
        path = self._window_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"x": position.x, "y": position.y,
                                             "width": size.width, "height": size.height}), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            pass

    def _start_work(self, label, cancelable=True):
        token = CancelToken()
        self._job = token
        self._job_cancelable = cancelable
        self._busy = True
        self.send_button.Hide()
        self.actions_button.Disable()
        self.apply_button.Disable()
        self.mark_button.Disable()
        self.native_update_button.Disable()
        self.test_connection.Disable()
        self.provider.Disable()
        self.model.Disable()
        self.server_url.Disable()
        self.api_key.Disable()
        self.cancel_button.Show(cancelable)
        self.activity_label.SetLabel(label)
        self.activity_label.Show()
        self.activity_gauge.Show()
        self._activity_timer.Start(150)
        self._panel.Layout()
        return token

    def _set_activity(self, token, label):
        if token is self._job and not self._closed:
            self.activity_label.SetLabel(label)

    def _pulse_activity(self, _event):
        self.activity_gauge.Pulse()

    def _finish_work(self, token):
        if token is not self._job or self._closed:
            return False
        self._job = None
        self._busy = False
        self._activity_timer.Stop()
        self.activity_label.Hide()
        self.activity_gauge.Hide()
        self.cancel_button.Hide()
        self.send_button.Show()
        self.actions_button.Enable()
        self.apply_button.Enable()
        self.mark_button.Enable()
        self.native_update_button.Enable()
        self.test_connection.Enable()
        self.provider.Enable()
        self.model.Enable()
        self.server_url.Enable()
        self.api_key.Enable()
        if self._native_sync_running:
            self._native_sync_running = False
            self._timer.Start(3000)
            self.Iconize(False)
            self.Raise()
        self._panel.Layout()
        return True

    def _cancel(self, _event=None):
        token = self._job
        if token is None or not self._job_cancelable:
            return
        token.cancel()
        self._finish_work(token)
        self._append("CAID", self._t("cancelled"))
        self.input.SetFocus()

    def _append(self, speaker, message, *, highlight_commands=False):
        if self.transcript.GetValue():
            self.transcript.AppendText("\n\n")
        start = self.transcript.GetLastPosition()
        self.transcript.AppendText(f"{speaker}\n{message}")
        label_style = wx.TextAttr()
        label_font = wx.Font(self.transcript.GetFont())
        label_font.SetWeight(wx.FONTWEIGHT_BOLD)
        label_style.SetFont(label_font)
        label_style.SetTextColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_WINDOWTEXT))
        self.transcript.SetStyle(start, start + len(speaker), label_style)
        if highlight_commands:
            offset = start + len(speaker) + 1
            for line in message.splitlines(keepends=True):
                if line.startswith("/"):
                    command = line.split(maxsplit=1)[0]
                    self.transcript.SetStyle(offset, offset + len(command), label_style)
                offset += len(line)
        self.transcript.ShowPosition(self.transcript.GetLastPosition())

    def _refresh_context(self, _event=None):
        if (self._closed or self._context_probe_running or self._busy or
                time.monotonic() < self._next_context_probe):
            return
        self._context_probe_running = True
        threading.Thread(target=self._probe_context, daemon=True).start()

    def _probe_context(self):
        try:
            result = probe_context()
            context = EditorContext(result["version"], result["major"], "pcb",
                                    result["document"], result.get("saved_file_exists"))
            wx.CallAfter(self._context_result, context, result["project_path"], "")
        except Exception as exc:
            wx.CallAfter(self._context_result, None, None, str(exc))

    def _context_result(self, updated, project_path, error):
        if self._closed:
            return
        self._context_probe_running = False
        client = None
        if not error:
            try:
                from kipy import KiCad
                client = KiCad()
            except Exception as exc:
                error = str(exc)
        if error:
            if self._connection_ok and self._pending is not None:
                self._clear_proposal()
                self._append("CAID", self._t("proposal_discarded_disconnect"))
            self._connection_ok = False
            self._connection_error = connection_detail(error, self._language)
            self._next_context_probe = time.monotonic() + 10
            label = self._t("kicad_unavailable")
            previous = self._hinted_board_name or self._context.document
            if previous:
                label = f"{os.path.basename(previous)}  ·  {label}"
            self.context_label.SetLabel(label)
            if not self._project_path:
                self.brief_pane.SetLabel(self._t("brief_unavailable", error=error[:100]))
            self.context_label.Wrap(max(300, self.GetClientSize().width - 32))
            return
        self._kicad = client
        self._connection_ok = True
        self._connection_error = ""
        self._next_context_probe = time.monotonic() + 8
        project_changed = self._project_path is not None and self._project_path != project_path
        if updated != self._context or project_changed:
            old_document = self._context.document
            self._context = updated
            if old_document is not None and (old_document != updated.document or project_changed):
                self._clear_proposal()
                self._append("CAID", self._t("board_changed", name=os.path.basename(
                    updated.document) if updated.document else self._t("unsaved_board")))
        if project_changed:
            self._resume_announced = False
        self._project_path = project_path
        self._hinted_board_name = None
        name = os.path.basename(updated.document) if updated.document else self._t("unsaved_board")
        disk_state = (self._t("pcb_on_disk") if updated.saved_file_exists is True else
                      self._t("pcb_not_on_disk") if updated.saved_file_exists is False else "")
        self.context_label.SetLabel(f"{name}  ·  KiCad {updated.version}" +
                                    (f"  ·  {disk_state}" if disk_state else ""))
        self.context_label.Wrap(max(300, self.GetClientSize().width - 32))
        self._refresh_project_brief()
        if self._design_session and not self._resume_announced:
            self._resume_announced = True
            self._append("CAID", self._t("design_resumed") + "\n\n" + self._design_prompt())

    def _refresh_project_brief(self):
        try:
            path = self._project_path
            if not path:
                raise ValueError(self._t("kicad_unavailable"))
            brief = load_brief(path)
        except Exception as exc:
            self.brief_pane.SetLabel(self._t("brief_unavailable", error=str(exc)[:100]))
            return
        if path != self._brief_project:
            self._brief_project = path
            active = brief.get("active_design")
            try:
                self._design_session = DesignSession.from_record(active) if active else None
            except (ValueError, KeyError, TypeError):
                self._design_session = None
        self.brief_pane.SetLabel(brief_compact_summary(brief, self._language))
        self.brief_text.ChangeValue(project_overview(path, brief, self._language) +
                                    "\n\n" + brief_summary(brief, self._language))

    def _design_prompt(self):
        session = self._design_session
        if session is None:
            return ""
        if session.needs_board_size():
            return self._t("design_board_size")
        if session.guided:
            item = session.guided[0]
            phase = {"electrical": ("Elektrik" if self._language == "de" else "electrical"),
                     "mechanical": ("Mechanik" if self._language == "de" else "mechanical"),
                     "programming": ("Programmierung" if self._language == "de" else "programming")}.get(
                         item["phase"], item["phase"])
            return self._t("design_guided_question", phase=phase,
                           question=item["question"], remaining=len(session.guided))
        if session.questions:
            return self._t("design_question", question=session.questions[0],
                           remaining=len(session.questions))
        return self._t("design_continue")

    def _on_key_down(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE and self._busy and self._job_cancelable:
            self._cancel()
        elif event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not event.ShiftDown():
            self._send()
        else:
            event.Skip()

    def _resize_composer(self, event):
        lines = self.input.GetNumberOfLines()
        height = min(220, max(110, 24 + lines * self.input.GetCharHeight()))
        if self.input.GetMinSize().height != height:
            self.input.SetMinSize((-1, height))
            self._panel.Layout()
        event.Skip()

    def _provider_changed(self, _event):
        provider = PROVIDERS[self.provider.GetSelection()]
        previous = self._previous_provider
        if previous is not None and previous != provider:
            self._models[previous] = self.model.GetValue().strip()
            self._keys[previous] = self.api_key.GetValue().strip()
            if previous in LOCAL_PROVIDERS:
                self._urls[previous] = self.server_url.GetValue().strip()
            self.model.SetValue(self._models.get(provider, DEFAULT_MODELS[provider]))
        if previous != provider:
            self.api_key.SetValue(self._keys.get(provider, ""))
        using_api = provider not in CLI_PROVIDERS
        local = provider in LOCAL_PROVIDERS
        self.api_key_label.Show(using_api)
        self.api_key.Show(using_api)
        self.server_label.Show(local)
        self.server_url.Show(local)
        self.test_connection.Show(using_api)
        self.connection_status.Show(using_api)
        if local:
            self.server_url.SetValue(self._urls.get(provider, DEFAULT_URLS[provider]))
        self._previous_provider = provider
        self.connection_status.SetLabel(self._t("connection_unchecked") if using_api else "")
        self.api_key_label.SetLabel(self._t("api_key_optional") if local else self._t("api_key"))
        self._update_settings_label()
        self.settings_pane.GetPane().Layout()
        self._panel.Layout()

    def _update_settings_label(self, _event=None):
        location = self._t("local") if is_loopback_url(self.server_url.GetValue()) else self._t("external_server")
        provider = ("Codex", "Claude Code", "Antigravity", "OpenCode", "OpenAI API", "Anthropic Claude", "Google Gemini",
                    "Ollama · " + location, "LM Studio · " + location)[self.provider.GetSelection()]
        self.settings_pane.SetLabel(self._t("settings_summary", provider=provider,
                                            model=self.model.GetValue().strip() or "—"))

    def _model_changed(self, _event=None):
        self._update_settings_label()
        if PROVIDERS[self.provider.GetSelection()] not in CLI_PROVIDERS:
            self.connection_status.SetLabel(self._t("connection_unchecked"))

    def _connection_changed(self, _event=None):
        self.connection_status.SetLabel(self._t("connection_unchecked"))
        self._update_settings_label()

    def _test_connection(self, _event):
        provider = PROVIDERS[self.provider.GetSelection()]
        if provider in CLI_PROVIDERS or self._busy:
            return
        url = self.server_url.GetValue().strip()
        model = self.model.GetValue().strip()
        api_key = self.api_key.GetValue().strip()
        token = self._start_work(self._t("testing_connection"))
        threading.Thread(target=self._probe_connection, args=(provider, url, model, api_key, token), daemon=True).start()

    def _probe_connection(self, provider, url, model, api_key, token):
        try:
            models = list_local_models(provider, url, token, api_key) if provider in LOCAL_PROVIDERS else []
            selected = model or (models[0] if models else "")
            if not selected:
                raise RuntimeError(self._t("no_local_models") if provider in LOCAL_PROVIDERS else self._t("model_needed"))
            if provider in LOCAL_PROVIDERS and selected not in models:
                raise RuntimeError(self._t("model_not_found", model=selected))
            wx.CallAfter(self._set_activity, token, self._t("testing_model"))
            probe_model(provider, url, selected, api_key, token)
            wx.CallAfter(self._connection_result, provider, url, selected, models, token)
        except Exception as exc:
            wx.CallAfter(self._connection_error, str(exc), token)

    def _connection_result(self, provider, url, selected, models, token):
        if not self._finish_work(token):
            return
        if provider != PROVIDERS[self.provider.GetSelection()] or (provider in LOCAL_PROVIDERS and url != self.server_url.GetValue().strip()):
            return
        if not self.model.GetValue().strip():
            self.model.SetValue(selected)
        self.connection_status.SetLabel(self._t("connection_ok", count=len(models)) if provider in LOCAL_PROVIDERS else self._t("cloud_model_ok"))
        self.connection_status.SetToolTip(", ".join(models[:20]))

    def _connection_error(self, error, token):
        if not self._finish_work(token):
            return
        self.connection_status.SetLabel(self._t("connection_failed"))
        self._append("CAID", self._t("connection_error", error=error))

    def _settings_changed(self, event):
        self._panel.Layout()
        event.Skip()

    def _show_actions(self, _event):
        menu = wx.Menu()
        actions = [
            (self._t("action_board"), "/platine"),
            (self._t("action_schematic"), "/schaltplan"),
            (self._t("action_project"), "/projekt"),
            (self._t("action_recovery"), "/wiederherstellung"),
            (self._t("action_project_suggest"), "/projekt vorschlag"),
            (("Routing-Vorprüfung" if self._language == "de" else "Routing preflight"), "/routing prüfen"),
            (("Routing-Kopie erzeugen" if self._language == "de" else "Create routed copy"), "/routing starten"),
            (self._t("action_sync"), "/abgleich"),
            (self._t("action_update"), "/f8"),
            None,
            ("ERC", "/erc"), ("DRC", "/drc"),
            None,
            (self._t("action_reconnect"), "/verbinden"),
            (self._t("action_new"), "/neu"),
            (self._t("action_help"), "/hilfe"),
        ]
        for action in actions:
            if action is None:
                menu.AppendSeparator()
                continue
            label, command = action
            item = menu.Append(wx.ID_ANY, label)
            menu.Bind(wx.EVT_MENU, lambda event, value=command: self._send(command_text=value), item)
        self._panel.PopupMenu(menu, self.actions_button.GetPosition() + wx.Point(0, self.actions_button.GetSize().height))
        menu.Destroy()

    def _send(self, _event=None, command_text=None):
        if self._busy:
            return
        message = command_text if command_text is not None else self.input.GetValue().strip()
        if not message:
            return
        if command_text is None:
            self.input.Clear()
        self._append(self._t("you"), message)
        command = message.casefold()
        if command in ("/status", "status"):
            live_context = self._context if self._connection_ok else EditorContext(
                "unknown", None, "pcb", None)
            status = capability_summary(live_context, self._language)
            if self._project_path:
                try:
                    status += "\n\n" + project_overview(
                        self._project_path, load_brief(self._project_path), self._language)
                except (OSError, ValueError, KeyError) as exc:
                    status += "\n\n" + self._t("brief_unavailable", error=exc)
            if not self._connection_ok:
                status += "\n\n" + (self._t("kicad_connection_error", error=self._connection_error)
                                     if self._connection_error else self._t("kicad_connecting"))
            self._append("CAID", status)
            return
        if command in ("/hilfe", "/help", "hilfe", "help"):
            self._append("CAID", self._t("help"), highlight_commands=True)
            return
        if command in ("/neu", "/new"):
            self._history.clear()
            self._clear_proposal()
            if self._design_session:
                try:
                    clear_active_design(self._design_session.project_path)
                except (OSError, ValueError) as exc:
                    self._append("CAID", str(exc))
                self._design_session = None
                self._refresh_project_brief()
            self._append("CAID", self._t("history_cleared"))
            return
        if command in ("/entwurf abbrechen", "/design cancel"):
            if self._design_session:
                clear_active_design(self._design_session.project_path)
                self._design_session = None
                self._refresh_project_brief()
            self._append("CAID", self._t("design_aborted"))
            return
        if command in ("/verbinden", "/reconnect"):
            self._next_context_probe = 0.0
            self._refresh_context()
            self._append("CAID", self._t("kicad_connecting"))
            return
        if command in ("/wiederherstellung", "/recovery",
                       "/wiederherstellung sichern", "/recovery save"):
            if not self._project_path:
                self._append("CAID", self._t("recovery_no_project"))
                return
            try:
                if command.endswith(("sichern", "save")):
                    moved = archive_abandoned_stages(self._project_path)
                    self._append("CAID", self._t("recovery_archived", count=len(moved),
                                                 directory=Path(self._project_path) / "CAID-Recovery"))
                self._append("CAID", describe_stages(self._project_path, self._language))
            except (OSError, ValueError) as exc:
                self._append("CAID", self._t("recovery_failed", error=exc))
            return
        if not self._connection_ok and command in ("/projekt", "/project",
                                                   "/projekt historie", "/project history"):
            if not self._project_path:
                self._append("CAID", self._t("kicad_connection_error", error=self._connection_error))
                return
            try:
                brief = load_brief(self._project_path)
                content = (brief_history_summary(brief, self._language)
                           if command.endswith(("historie", "history")) else
                           project_overview(self._project_path, brief, self._language) +
                           "\n\n" + brief_summary(brief, self._language))
                self._append("CAID", content)
            except (OSError, ValueError, KeyError) as exc:
                self._append("CAID", self._t("brief_unavailable", error=exc))
            return
        if not self._connection_ok:
            self._append("CAID", self._t("kicad_connection_error", error=self._connection_error)
                         if self._connection_error else self._t("kicad_connecting"))
            return
        if command in ("/routing", "/route", "/routing prüfen", "/route check",
                       "/routing starten", "/route start") or command.startswith((
                           "/routing lagen ", "/route layers ", "/routing regeln ",
                           "/route rules ", "/routing netz ", "/route net ",
                           "/routing starten ", "/route start ")):
            try:
                board = self._kicad.get_board()
                project_path = str(board.document.project.path)
                brief = load_brief(project_path)
                contract = brief.get("routing") or default_contract()
                if command.startswith(("/routing lagen ", "/route layers ")):
                    contract = set_layers(contract, int(message.split()[-1]))
                    set_routing(project_path, contract)
                    self._refresh_project_brief()
                elif command.startswith(("/routing regeln ", "/route rules ")):
                    contract = set_limits(contract, message.split()[2:])
                    set_routing(project_path, contract)
                    self._refresh_project_brief()
                if command in ("/routing starten", "/route start") or command.startswith((
                        "/routing netz ", "/route net ", "/routing starten ", "/route start ")):
                    net_name = (message.split(" ", 2)[2].strip() if command.startswith((
                        "/routing netz ", "/route net ")) else None)
                    if net_name == "":
                        raise ValueError("Net name is empty")
                    max_nets = 20
                    if command.startswith(("/routing starten ", "/route start ")):
                        parts = message.split()
                        if len(parts) != 3:
                            raise ValueError(self._t("route_pass_size_required"))
                        try:
                            max_nets = int(parts[2])
                        except ValueError as exc:
                            raise ValueError(self._t("route_pass_size_range")) from exc
                    if not 1 <= max_nets <= 100:
                        raise ValueError(self._t("route_pass_size_range"))
                    token = self._start_work("Routing …")
                    threading.Thread(target=self._routing_run,
                                     args=(project_path, board.name, contract, net_name, max_nets, token),
                                     daemon=True).start()
                    return
                if command in ("/routing prüfen", "/route check"):
                    token = self._start_work(self._t("checking", check="Routing"))
                    target_size = brief.get("pcb_size_mm")
                    if isinstance(target_size, dict):
                        target_size = {**target_size,
                                       "mode": brief.get("pcb_size_mode") or "maximum"}
                    threading.Thread(target=self._routing_check,
                                     args=(board, contract, target_size, token), daemon=True).start()
                else:
                    route_snapshot = snapshot(board)
                    target_size = brief.get("pcb_size_mm")
                    route_snapshot["target_size_mm"] = ({**target_size,
                                                         "mode": brief.get("pcb_size_mode") or "maximum"}
                                                        if isinstance(target_size, dict) else None)
                    self._append("CAID", describe_routing(contract, route_snapshot, language=self._language) +
                                 "\n\n" + ("/routing lagen 1|2|4|…|32\n/routing regeln Breite Abstand Randabstand (1 Lage)\n/routing regeln Breite Abstand ViaDurchmesser ViaBohrung Randabstand (2+ Lagen)\n/routing prüfen\n/routing starten [Anzahl] (Standard 20, maximal 100 Netze pro Kopie)\n/routing netz NETZNAME (ein Netz)" if self._language == "de" else
                                              "/route layers 1|2|4|…|32\n/route rules width clearance edge_clearance (1 layer)\n/route rules width clearance via_diameter via_drill edge_clearance (2+ layers)\n/route check\n/route start [count] (default 20, up to 100 nets per copy)\n/route net NET_NAME (one net)"))
            except (ValueError, OSError, RuntimeError) as exc:
                self._append("CAID", str(exc))
            return
        if command in ("/projekt", "/project", "/projekt historie", "/project history") or command.startswith(("/projekt set ", "/project set ",
                                                                        "/projekt annahme ", "/project assumption ",
                                                                        "/projekt belegt ", "/project verified ",
                                                                        "/projekt größe ", "/project size ",
                                                                        "/projekt seite ", "/project side ",
                                                                        "/projekt löschen ", "/project delete ",
                                                                        "/projekt offen ", "/project open ",
                                                                        "/projekt geklärt ", "/project resolved ")):
            try:
                project_path = str(self._kicad.get_board().document.project.path)
                if command.startswith(("/projekt set ", "/project set ")):
                    detail = message.split(" ", 2)[2]
                    if ":" not in detail:
                        raise ValueError(self._t("brief_set_usage"))
                    topic, value = detail.split(":", 1)
                    brief = set_fact(project_path, topic, value)
                elif command.startswith(("/projekt annahme ", "/project assumption ")):
                    detail = message.split(" ", 2)[2]
                    if ":" not in detail:
                        raise ValueError(self._t("brief_set_usage"))
                    topic, value = detail.split(":", 1)
                    brief = set_requirement(project_path, topic, value, status="assumed")
                elif command.startswith(("/projekt belegt ", "/project verified ")):
                    detail = message.split(" ", 2)[2]
                    if ":" not in detail or "|" not in detail:
                        raise ValueError(self._t("brief_verified_usage"))
                    topic, remainder = detail.split(":", 1)
                    value, evidence = remainder.rsplit("|", 1)
                    brief = set_requirement(project_path, topic, value, status="sourced", evidence=evidence)
                elif command.startswith(("/projekt größe ", "/project size ")):
                    detail = message.split(" ", 2)[2]
                    size = parse_board_size(detail)
                    if size is None:
                        raise ValueError(self._t("brief_size_usage"))
                    brief = set_requirement(project_path, "PCB size / Platinengröße",
                                            f"{'Maximal ' if board_size_mode(detail) == 'maximum' else ''}"
                                            f"{size['width_mm']:g} × {size['height_mm']:g} mm",
                                            check={"kind": "board_size", "mode": board_size_mode(detail),
                                                   **size})
                elif command.startswith(("/projekt seite ", "/project side ")):
                    detail = message.split(" ", 2)[2].split()
                    if len(detail) != 2 or detail[1].upper() not in {"TOP", "BOTTOM"}:
                        raise ValueError(self._t("brief_side_usage"))
                    ref, side = detail[0].upper(), detail[1].upper()
                    brief = set_requirement(project_path, f"PCB side / Seite {ref}", side,
                                            check={"kind": "component_side", "ref": ref, "side": side})
                elif command.startswith(("/projekt löschen ", "/project delete ")):
                    topic = message.split(" ", 2)[2].strip()
                    brief = remove_fact(project_path, topic)
                elif command.startswith(("/projekt offen ", "/project open ")):
                    brief = add_open_question(project_path, message.split(" ", 2)[2])
                elif command.startswith(("/projekt geklärt ", "/project resolved ")):
                    brief = resolve_open_question(project_path, message.split(" ", 2)[2].strip())
                elif command in ("/projekt historie", "/project history"):
                    self._append("CAID", brief_history_summary(load_brief(project_path), self._language))
                    return
                else:
                    brief = load_brief(project_path)
                self._append("CAID", brief_summary(brief, self._language) + "\n\n" +
                             self._t("brief_commands"))
                self._refresh_project_brief()
            except Exception as exc:
                self._append("CAID", str(exc))
            return
        if command in ("/f8", "/update"):
            self._open_native_sync()
            return
        if command in ("/erc", "/drc"):
            try:
                board = self._kicad.get_board()
            except Exception as exc:
                self._append("CAID", self._t("board_unknown", error=exc))
                return
            token = self._start_work(self._t("checking", check=command[1:].upper()))
            threading.Thread(target=self._run_check, args=(command, board, token), daemon=True).start()
            return
        try:
            board_snapshot = snapshot(self._kicad.get_board())
            board_snapshot["project_brief"] = model_context(load_brief(board_snapshot["project_path"]))
        except Exception as exc:
            self._append("CAID", self._t("board_unreadable", error=exc))
            return
        if command in ("/platine", "/board"):
            self._append("CAID", json.dumps(board_snapshot, ensure_ascii=False, indent=2))
            return
        provider = PROVIDERS[self.provider.GetSelection()]
        if command in ("/projekt vorschlag", "/project suggest"):
            api_key = self.api_key.GetValue().strip() if provider not in CLI_PROVIDERS else ""
            if provider in ("openai", "anthropic", "gemini") and not api_key:
                self._append("CAID", self._t("key_required", provider=self.provider.GetStringSelection()))
                return
            self._clear_proposal()
            token = self._start_work(self._t("brief_extracting"))
            threading.Thread(target=self._suggest_brief,
                             args=(provider, api_key, self.server_url.GetValue(), self.model.GetValue(),
                                   list(self._history), board_snapshot, token), daemon=True).start()
            return
        if command in ("/abgleich", "/sync"):
            token = self._start_work(self._t("syncing"))
            threading.Thread(target=self._prepare_sync, args=(token,), daemon=True).start()
            return
        if command in ("/entwurf weiter", "/design continue") and self._design_session:
            self._launch_design(self._design_session, provider, board_snapshot)
            return
        if self._design_session and not command.startswith("/"):
            session = self._design_session
            if (str(board_snapshot["project_path"]) != session.project_path or
                    str(board_snapshot["document"]) != session.document):
                self._append("CAID", self._t("design_board_changed"))
                return
            if session.needs_board_size():
                try:
                    accepted = session.record_board_size(message)
                except ValueError:
                    accepted = False
                if not accepted:
                    self._append("CAID", self._t("design_size_retry") + "\n\n" + self._design_prompt())
                    return
            elif session.guided:
                item = session.guided[0]
                try:
                    if session.inherit_requirements:
                        if is_open_answer(message):
                            add_open_question(session.project_path, item["question"])
                        else:
                            set_requirement(session.project_path, item["topic"], message)
                            if item["question"] in load_brief(session.project_path)["open_questions"]:
                                resolve_open_question(session.project_path, item["question"])
                    session.answer_guided(message)
                except (OSError, ValueError) as exc:
                    self._append("CAID", str(exc))
                    return
            elif session.questions:
                session.answer_question(message)
            save_active_design(session.project_path, session)
            board_snapshot["project_brief"] = model_context(load_brief(session.project_path))
            self._refresh_project_brief()
            if session.guided or session.questions:
                self._append("CAID", self._design_prompt())
            else:
                self._launch_design(session, provider, board_snapshot)
            return
        if command in ("/schaltplan", "/schematic"):
            token = self._start_work(self._t("reading_schematic"))
            threading.Thread(target=self._read_schematic, args=(board_snapshot, token), daemon=True).start()
            return
        if command.startswith("/sch "):
            if provider != "codex":
                self._append("CAID", self._t("codex_only"))
                return
            self._clear_proposal()
            token = self._start_work(self._t("staging"))
            instruction = message[5:].strip()
            messages = self._history + [{"role": "user", "content": instruction}]
            threading.Thread(target=self._stage_schematic,
                             args=(board_snapshot, instruction, self.model.GetValue(), messages, token),
                             daemon=True).start()
            return
        if command.startswith(("/entwurf ", "/design ")):
            if self._design_session:
                self._append("CAID", self._t("design_already_active") + "\n\n" + self._design_prompt())
                return
            task = message.split(" ", 1)[1].strip()
            if not task:
                return
            if task.lower().endswith(".json") and Path(task).is_file():
                self._launch_design(None, provider, board_snapshot, json_path=task)
                return
            try:
                session = DesignSession.start(task, board_snapshot, self._language)
                save_active_design(session.project_path, session)
                board_snapshot["project_brief"] = model_context(load_brief(session.project_path))
            except Exception as exc:
                self._append("CAID", str(exc))
                return
            self._design_session = session
            self._refresh_project_brief()
            if not session.inherit_requirements:
                self._append("CAID", self._t("design_variant_isolated"))
            if session.needs_board_size():
                self._append("CAID", self._design_prompt())
            else:
                self._launch_design(session, provider, board_snapshot)
            return
        api_key = self.api_key.GetValue().strip() if provider not in CLI_PROVIDERS else ""
        if provider in ("openai", "anthropic", "gemini") and not api_key:
            self._append("CAID", self._t("key_required", provider=self.provider.GetStringSelection()))
            return
        self._clear_proposal()
        token = self._start_work(self._t("thinking"))
        messages = self._history + [{"role": "user", "content": message}]
        threading.Thread(target=self._request, args=(provider, api_key, self.server_url.GetValue(), self.model.GetValue(), messages, board_snapshot, token), daemon=True).start()

    def _request(self, provider, api_key, base_url, model, messages, board_snapshot, token):
        try:
            try:
                schematic_snapshot = read_schematic(board_snapshot, self._language, token)
            except Exception as exc:
                token.check()
                schematic_snapshot = {"unavailable": str(exc)}
            footprint_evidence = None
            if asks_about_footprint(messages[-1]["content"]) and "components" in schematic_snapshot:
                wx.CallAfter(self._set_activity, token, self._t("inspecting_footprints"))
                footprint_evidence = inspect_footprints(board_snapshot, schematic_snapshot, messages[-1]["content"])
                token.check()
            tool_context = []
            inspected = []
            cache = {}
            for round_index in range(4):
                token.check()
                wx.CallAfter(self._set_activity, token, self._t("thinking"))
                options = {"footprint_evidence": footprint_evidence, "tool_context": tool_context,
                           "tools_remaining": 3 - round_index}
                result = ask_provider(provider, api_key, base_url, model, messages, board_snapshot,
                                      schematic_snapshot, self._language, token, **options)
                token.check()
                requests = result["tool_requests"]
                if not requests:
                    break
                if round_index == 3:
                    raise RuntimeError(self._t("tool_limit"))
                for request in requests:
                    token.check()
                    label = tool_label(request, self._language)
                    wx.CallAfter(self._set_activity, token, label + " …")
                    key = (request["tool"], request["argument"].strip().casefold())
                    if key not in cache:
                        try:
                            board = self._kicad.get_board()
                            if board.name != board_snapshot["document"] or board.document.project.path != board_snapshot["project_path"]:
                                raise RuntimeError(self._t("different_board"))
                            cache[key] = {"result": execute_read_tool(request, board, board_snapshot,
                                                                        schematic_snapshot, self._language, token)}
                        except Exception as exc:
                            token.check()
                            cache[key] = {"error": str(exc)[:350]}
                    tool_context.append({"tool": request["tool"], "argument": request["argument"], **cache[key]})
                    if "result" in cache[key] and label not in inspected:
                        inspected.append(label)
            token.check()
            if (result["footprint_updates"] or result["field_updates"] or result["net_renames"] or
                    result["pin_connections"] or result["no_connect_markers"] or
                    result["edit_schematic"]) and "components" not in schematic_snapshot:
                raise RuntimeError(self._t("schematic_required", error=schematic_snapshot.get("unavailable", "")))
            if result["no_connect_markers"]:
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_no_connect_markers(board_snapshot, result["no_connect_markers"], self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["pin_connections"]:
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_pin_connections(board_snapshot, result["pin_connections"], self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["net_renames"]:
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_net_renames(board_snapshot, result["net_renames"], self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["field_updates"]:
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_field_updates(board_snapshot, result["field_updates"], self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["footprint_updates"]:
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_footprint_updates(board_snapshot, result["footprint_updates"], self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["edit_schematic"] and provider == "codex":
                wx.CallAfter(self._set_activity, token, self._t("checking_copy"))
                staged = stage_schematic_edit(board_snapshot, messages[-1]["content"], model, self._language, token)
                try:
                    plan = prepare_design_plan(self._kicad.get_board(), staged, schematic_snapshot, self._language)
                except Exception:
                    staged.cleanup()
                    raise
                wx.CallAfter(self._schematic_result, plan, messages, result["answer"], token, inspected)
            elif result["edit_schematic"]:
                raise RuntimeError(self._t("codex_only"))
            else:
                wx.CallAfter(self._result, result, messages, board_snapshot, token, inspected)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _launch_design(self, session, provider, board_snapshot, json_path=None):
        if session is not None and (session.needs_board_size() or session.guided or session.questions):
            self._append("CAID", self._design_prompt())
            return
        if session is not None and not session.inherit_requirements:
            board_snapshot = dict(board_snapshot)
            board_snapshot["project_brief"] = {
                "project": board_snapshot["project_brief"].get("project"),
                "objective": session.task, "requirements": {}, "open_questions": [],
                "active_design": session.to_record(),
                "variant_note": "Separate ROM variant; do not inherit source project requirements"}
        api_key = self.api_key.GetValue().strip() if provider not in CLI_PROVIDERS else ""
        if json_path is None and provider in ("openai", "anthropic", "gemini") and not api_key:
            self._append("CAID", self._t("key_required", provider=self.provider.GetStringSelection()))
            return
        self._clear_proposal()
        token = self._start_work(self._t("designing"))
        threading.Thread(target=self._new_design,
                         args=(session, json_path, provider, api_key, self.server_url.GetValue(),
                               self.model.GetValue(), board_snapshot, token), daemon=True).start()

    def _new_design(self, session, json_path, provider, api_key, base_url, model, board_snapshot, token):
        try:
            if json_path is not None:
                spec = json.loads(Path(json_path).read_text(encoding="utf-8-sig"))
                questions = []
            else:
                spec, questions = ask_design(provider, api_key, base_url, model, session.model_task(),
                                             board_snapshot, self._language, token)
            token.check()
            if questions:
                session.set_questions(questions)
                save_active_design(session.project_path, session)
                wx.CallAfter(self._new_design_questions, questions, token)
                return
            if session is not None:
                spec = session.finalize_spec(spec)
            source_brief = load_brief(board_snapshot["project_path"])
            if session is not None and not session.inherit_requirements:
                source_brief = {"requirements": {}}
            if json_path is None:
                spec.pop("routing", None)
                if session is not None and session.inherit_requirements and source_brief.get("routing"):
                    spec["routing"] = source_brief["routing"]
            requirement_rows = review_spec(source_brief, spec,
                                           session.board_size if session is not None else None,
                                           session.board_size_mode if session is not None else "exact")
            failures = mismatches(requirement_rows)
            if failures:
                raise ValueError(self._t("brief_design_mismatch", details="; ".join(
                    f"{row['topic']}: {row['detail']}" for row in failures[:5])))
            spec["requirement_review"] = requirement_rows
            wx.CallAfter(self._set_activity, token, self._t("checking_design"))
            staged = stage_new_design(spec, board_snapshot["project_path"],
                                      language=self._language, token=token)
            if session is None:
                session = DesignSession.start(
                    spec.get("objective", "Imported design model: " + Path(json_path).name),
                    board_snapshot)
                session.board_size = spec.get("board")
                session.board_size_decided = True
            complete_design(session.project_path, staged["directory"], session, staged,
                            requirement_rows)
            wx.CallAfter(self._new_design_result, staged, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _new_design_questions(self, questions, token):
        if self._finish_work(token):
            self._refresh_project_brief()
            self._append("CAID", self._t("design_questions") + "\n\n" + self._design_prompt())

    def _new_design_result(self, staged, token):
        if self._finish_work(token):
            self._design_session = None
            self._refresh_project_brief()
            self._append("CAID", self._t("design_created", **staged))
            self._append("CAID", self._t("part_review_result",
                                         parts=staged.get("parts_needing_review", 0),
                                         questions=staged.get("open_questions", 0)))
            try:
                rows = load_brief(staged["directory"]).get("requirement_review", [])
                checked = sum(row["status"] == "pass" for row in rows)
                unchecked = sum(row["status"] == "unchecked" for row in rows)
                self._append("CAID", self._t("brief_review_result", checked=checked,
                                             unchecked=unchecked))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._append("CAID", self._t("brief_unavailable", error=str(exc)))

    def _suggest_brief(self, provider, api_key, base_url, model, messages, board_snapshot, token):
        try:
            path = str(board_snapshot["project_path"])
            baseline = load_brief(path)
            updates, questions = ask_brief_proposal(provider, api_key, base_url, model,
                                                     messages, board_snapshot, self._language, token)
            token.check()
            proposal = BriefProposal(path, brief_hash(baseline), tuple(updates), tuple(questions))
            wx.CallAfter(self._brief_proposal_result, proposal, baseline, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _brief_proposal_result(self, proposal, baseline, token):
        if not self._finish_work(token):
            return
        if not proposal.updates and not proposal.open_questions:
            self._append("CAID", self._t("brief_no_updates"))
            return
        self._clear_proposal()
        self._pending = proposal
        lines = [self._t("brief_preview")]
        for item in proposal.updates:
            old = baseline["requirements"].get(item["topic"], {}).get("value", "∅")
            lines.append(f"{item['topic']}: {old} → {item['value']} [{item['status']}]" +
                         (f" · {item['evidence']}" if item["evidence"] else ""))
        for question in proposal.open_questions:
            lines.append(self._t("brief_open_preview", question=question))
        self.proposal.SetValue("\n\n".join(lines))
        self.proposal.SetMinSize((-1, 180))
        self.proposal_label.Show()
        self.proposal.Show()
        self.apply_button.SetLabel(self._t("brief_apply"))
        self.apply_button.Show()
        self._panel.Layout()

    def _read_schematic(self, board_snapshot, token):
        try:
            data = read_schematic(board_snapshot, self._language, token)
            wx.CallAfter(self._schematic_overview, data, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _schematic_overview(self, data, token):
        if not self._finish_work(token):
            return
        lines = [self._t("schematic_overview", components=data['component_count'], nets=data['net_count'])]
        notes = data.get("notes", [])
        if notes or data.get("note_count"):
            lines.append(self._t("schematic_notes"))
            lines.extend("• " + note for note in notes[:12])
            if len(notes) > 12 or data.get("notes_truncated"):
                lines.append(self._t("more_schematic_notes"))
        lines.extend((self._t("components") + ", ".join(
            item["ref"] + " " + item["value"] for item in data["components"][:30]),
            self._t("nets")))
        for net in data["nets"][:20]:
            lines.append(net["name"] + ": " + ", ".join(node["ref"] + "." + node["pin"] for node in net["nodes"][:12]))
        if data["net_count"] > 20:
            lines.append(self._t("more_nets", count=data['net_count'] - 20))
        self._append("CAID", "\n".join(lines))

    def _stage_schematic(self, board_snapshot, instruction, model, messages, token):
        try:
            current = read_schematic(board_snapshot, self._language, token)
            staged = stage_schematic_edit(board_snapshot, instruction, model, self._language, token)
            try:
                plan = prepare_design_plan(self._kicad.get_board(), staged, current, self._language)
            except Exception:
                staged.cleanup()
                raise
            wx.CallAfter(self._schematic_result, plan, messages, "", token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _schematic_result(self, plan, messages, prior_answer, token, inspected=()):
        if not self._finish_work(token):
            plan.cleanup()
            return
        staged = plan.staged
        if prior_answer:
            self._append("CAID", prior_answer)
        if inspected:
            self._append("CAID", self._t("inspections", summary=" · ".join(inspected)))
        if staged.agent_answer:
            self._append("CAID", staged.agent_answer[:1600])
        if messages:
            self._history = (messages + [{"role": "assistant", "content": prior_answer or staged.agent_answer}])[-16:]
        self._clear_proposal()
        self._pending = plan
        preview = describe_design_plan(plan, self._language) + "\n\n" + staged.diff[:10000]
        if len(staged.diff) > 10000:
            preview += self._t("more_diff")
        self.proposal.SetValue(preview)
        self.proposal.SetMinSize((-1, 180))
        self.proposal_label.Show()
        self.proposal.Show()
        self.apply_button.SetLabel(self._t("apply_schematic"))
        self.apply_button.Show()
        self._panel.Layout()

    def _run_check(self, command, board, token):
        try:
            result = run_erc(board, self._language, token) if command == "/erc" else run_drc(board, self._language, token)
            wx.CallAfter(self._check_result, result, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _routing_check(self, board, contract, target_size, token):
        try:
            board_snapshot = read_saved_board_snapshot(board.document.project.path, board.name, token)
            board_snapshot["target_size_mm"] = target_size
            report = read_drc_report(board, self._language, token)
            result = describe_routing(contract, board_snapshot, report, self._language)
            wx.CallAfter(self._check_result, result, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _routing_run(self, project_path, board_name, contract, net_name, max_nets, token):
        try:
            def progress(index, total, name):
                label = (f"Route {index}/{total}: {name}" if self._language != "de" else
                         f"Route {index}/{total}: {name}")
                wx.CallAfter(self._set_activity, token, label)
            result = route_project(project_path, board_name, contract, net_name,
                                   max_nets=max_nets, language=self._language,
                                   token=token, progress=progress)
            wx.CallAfter(self._routing_result, result, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _routing_result(self, result, token):
        if not self._finish_work(token):
            return
        if self._language == "de":
            message = (f"Routing-Kopie: {result['directory']}\n\n"
                       f"Verbesserte Netze: {len(result['accepted'])}; übersprungen: {len(result['skipped'])}. "
                       f"Offene Verbindungen: {result['unconnected_before']} → {result['unconnected_after']}.\n\n"
                       f"Geeignete Netze vor dem Lauf: {result['eligible_before']}; "
                       f"nicht versucht: {result['unattempted']}.\n\n"
                       "Öffne die neue Projektkopie in KiCad; dort kannst du den nächsten Routinglauf starten. "
                       "CAID-ROUTING.json enthält jedes Ergebnis. "
                       "Der Routinglauf nutzt den zuletzt gespeicherten Stand der Platine.")
        else:
            message = (f"Routed copy: {result['directory']}\n\n"
                       f"Improved nets: {len(result['accepted'])}; skipped: {len(result['skipped'])}. "
                       f"Open connections: {result['unconnected_before']} → {result['unconnected_after']}.\n\n"
                       f"Eligible nets before the pass: {result['eligible_before']}; "
                       f"not attempted: {result['unattempted']}.\n\n"
                       "Open the new project copy in KiCad to start another routing pass. "
                       "CAID-ROUTING.json records each result. "
                       "Routing used the last saved board.")
        if result["skipped"]:
            heading = "Übersprungene Netze:" if self._language == "de" else "Skipped nets:"
            message += "\n\n" + heading + "\n" + "\n".join(
                f"• {item['net']}: {item['reason']}" for item in result["skipped"][:3])
        self._append("CAID", message)

    def _check_result(self, result, token):
        if not self._finish_work(token):
            return
        self._append("CAID", result)

    def _prepare_sync(self, token):
        try:
            plan = prepare_sync(self._kicad.get_board(), self._language, token)
            wx.CallAfter(self._sync_result, plan, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _sync_result(self, plan, token):
        if not self._finish_work(token):
            return
        self._display_sync_plan(plan)

    def _display_sync_plan(self, plan):
        self._clear_proposal()
        lines = [self._t("sync_preview", count=len(plan.changes), pads=plan.pad_count,
                         nets=plan.net_count)]
        if plan.changes:
            lines.append(self._t("sync_changes"))
            lines.extend(f"{change.ref}.{change.pin}: {change.old_net or '∅'} → {change.new_net or '∅'}"
                         for change in plan.changes[:30])
            if len(plan.changes) > 30:
                lines.append(f"… +{len(plan.changes) - 30}")
        else:
            lines.append(self._t("sync_none"))
        if plan.warnings:
            lines.append("\n" + self._t("sync_warnings"))
            lines.extend("• " + warning for warning in plan.warnings)
        if plan.blockers:
            lines.append("\n" + self._t("sync_blockers"))
            lines.extend("• " + blocker for blocker in plan.blockers)
        self.proposal.SetValue("\n".join(lines))
        self.proposal_label.Show()
        self.proposal.Show()
        if plan.changes and not plan.blockers:
            self._pending = plan
            self.apply_button.SetLabel(self._t("sync_apply", count=len(plan.changes)))
            self.apply_button.Show()
        if plan.changes or plan.warnings or plan.blockers:
            self.native_update_button.Show()
        self._panel.Layout()

    def _open_native_sync(self, _event=None):
        if self._busy:
            return
        self._timer.Stop()
        self._native_sync_running = True
        token = self._start_work(self._t("native_running"), cancelable=False)
        self.Iconize(True)
        threading.Thread(target=self._run_native_sync, args=(token,), daemon=True).start()

    def _run_native_sync(self, token):
        try:
            before = prepare_sync(self._kicad.get_board(), self._language, token)
            token.check()
            if before.board_footprints and before.component_count == 0:
                raise ValueError(self._t("empty_schematic_for_pcb"))
            invoke_kicad_update(self._kicad, self._language)
            # The native action normally replies after its modal dialog closes.
            # Retry if KiCad is briefly busy finishing the update.
            for attempt in range(600):
                token.check()
                try:
                    after = prepare_sync(self._kicad.get_board(), self._language, token)
                    break
                except (KiCadConnectionError, ApiError) as exc:
                    if isinstance(exc, ApiError) and exc.code != ApiStatusCode.AS_BUSY:
                        raise
                    if attempt == 599:
                        raise
                    time.sleep(1)
            wx.CallAfter(self._native_sync_result, before, after, token)
        except Exception as exc:
            wx.CallAfter(self._error, str(exc), token)

    def _native_sync_result(self, before, after, token):
        if not self._finish_work(token):
            return
        self._append("CAID", describe_outcome(before, after, self._language))
        self._display_sync_plan(after)

    def _result(self, result, messages, board_snapshot, token, inspected=()):
        if not self._finish_work(token):
            return
        self._append("CAID", result["answer"])
        if inspected:
            self._append("CAID", self._t("inspections", summary=" · ".join(inspected)))
        if result.get("edit_schematic"):
            self._append("CAID", self._t("schematic_codex_only"))
        self._history = (messages + [{"role": "assistant", "content": result["answer"]}])[-16:]
        try:
            model_placements = validate_placements(result["placements"], board_snapshot, self._language)
            placements = repair_placements(model_placements, board_snapshot)
        except ValueError as exc:
            self._append("CAID", self._t("proposal_rejected", error=exc))
            return
        if placements:
            warnings = geometry_warnings(placements, board_snapshot, self._language)
            self._pending = (placements, board_snapshot)
            preview = self._t("placement_preview") + describe_placements(placements, board_snapshot, self._language)
            adjusted = [item.ref for before, item in zip(model_placements, placements) if before != item]
            if adjusted:
                preview += self._t("adjusted", refs=", ".join(adjusted))
            if warnings:
                preview += self._t("geometry") + "\n".join(warnings[:20])
                if len(warnings) > 20:
                    preview += self._t("more_warnings", count=len(warnings) - 20)
            self.proposal.SetValue(preview)
            self.proposal_label.Show()
            self.proposal.Show()
            self.apply_button.SetLabel(self._t("apply_board"))
            self.apply_button.Show()
            self.mark_button.Show()
            self._panel.Layout()

    def _error(self, message, token):
        if not self._finish_work(token):
            return
        self._append("CAID", self._t("request_failed", error=message))

    def _mark_proposal(self, _event=None):
        if self._busy or not isinstance(self._pending, tuple):
            return
        placements, original = self._pending
        try:
            board = self._kicad.get_board()
            if board.name != original["document"]:
                raise ValueError(self._t("different_board"))
            mark_footprints(board, [item.ref for item in placements], self._language)
        except Exception as exc:
            self._append("CAID", self._t("mark_failed", error=exc))
        else:
            self._append("CAID", self._t("marked_board", count=len(placements)))

    def _apply(self, _event=None):
        if self._busy or not self._pending:
            return
        if not self._connection_ok:
            self._append("CAID", self._t("kicad_connection_error", error=self._connection_error))
            return
        if isinstance(self._pending, BriefProposal):
            try:
                path = str(self._kicad.get_board().document.project.path)
                if path != self._pending.project_path:
                    raise ValueError(self._t("different_board"))
                apply_brief_proposal(path, self._pending.base_hash,
                                     self._pending.updates, self._pending.open_questions)
            except Exception as exc:
                self._append("CAID", self._t("apply_failed", error=exc))
                return
            self._clear_proposal()
            self._refresh_project_brief()
            self._append("CAID", self._t("brief_applied"))
            return
        if isinstance(self._pending, NetSyncPlan):
            footprint_warnings = any("Footprint" in warning for warning in self._pending.warnings)
            try:
                count = apply_sync(self._kicad.get_board(), self._pending, self._language)
            except Exception as exc:
                self._append("CAID", self._t("apply_failed", error=exc))
                return
            self._append("CAID", self._t("sync_applied", count=count))
            if footprint_warnings:
                self._append("CAID", self._t("sync_footprints_remaining"))
            self._clear_proposal()
            return
        if isinstance(self._pending, DesignPlan):
            try:
                backup = apply_design_plan(self._kicad.get_board(), self._pending, self._language)
            except Exception as exc:
                self._append("CAID", self._t("schematic_apply_failed", error=exc))
                return
            self._append("CAID", self._t("schematic_applied", backup=backup))
            self._clear_proposal()
            self.proposal.SetValue(self._t("design_next_step"))
            self.proposal_label.Show()
            self.proposal.Show()
            self.native_update_button.Show()
            self._panel.Layout()
            return
        placements, original = self._pending
        try:
            board = self._kicad.get_board()
            if board.name != original["document"]:
                raise ValueError(self._t("different_board"))
            apply_placements(board, placements, original, self._language)
        except Exception as exc:
            self._append("CAID", self._t("apply_failed", error=exc))
        else:
            self._append("CAID", self._t("placements_applied", count=len(placements)))
        self._clear_proposal()

    def _clear_proposal(self):
        if isinstance(self._pending, DesignPlan):
            self._pending.cleanup()
        self._pending = None
        self.proposal.SetMinSize((-1, 130))
        self.proposal_label.Hide()
        self.proposal.Hide()
        self.apply_button.Hide()
        self.mark_button.Hide()
        self.native_update_button.Hide()
        if hasattr(self, "_panel"):
            self._panel.Layout()

    def _close(self, event):
        self._save_window_position()
        current_provider = PROVIDERS[self.provider.GetSelection()]
        self._models[current_provider] = self.model.GetValue().strip()
        if current_provider in LOCAL_PROVIDERS:
            self._urls[current_provider] = self.server_url.GetValue().strip()
        try:
            save_settings(current_provider, self._models, self._urls)
        except OSError:
            pass
        self._closed = True
        self._timer.Stop()
        self._activity_timer.Stop()
        if self._job:
            self._job.cancel()
        self._clear_proposal()
        event.Skip()
