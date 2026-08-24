"""Tkinter Desktop GUI for UndoMesh — polished chat client."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox
from typing import Any

from undomesh.core.config import get_config
from undomesh.llm.client import ModelClient
from undomesh.llm.provider_settings import ProviderSettingsStore
from undomesh.llm.providers import PROVIDERS, get_provider
from undomesh.ui.viewmodels import (
    checkpoint_detail,
    checkpoint_row,
    restore_history as format_restore_history,
)
from undomesh.ui.review import build_review

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
#  Theme
# ══════════════════════════════════════════════════════════════════

class T:
    BG          = "#111210"
    SURFACE     = "#181916"
    ELEVATED    = "#22231f"
    INPUT_BG    = "#1d1e1b"
    HOVER       = "#2b2d28"

    FG          = "#e8e9e4"
    FG_DIM      = "#979991"
    FG_BRIGHT   = "#f8f9f5"

    ACCENT      = "#d6ff75"
    ACCENT_HOV  = "#e0ff99"
    ACCENT_DIM  = "#9bbd4f"

    USER_BG     = "#242722"
    AI_BG       = "#181916"
    TOOL_BG_RUN = "#252316"
    TOOL_BG_OK  = "#17221b"
    TOOL_BG_ERR = "#28191d"

    GREEN       = "#9fd67a"
    RED         = "#ef7f8f"
    YELLOW      = "#dfc875"
    ORANGE      = "#dca66b"

    BORDER      = "#30312d"
    BORDER_ACC  = "#66763f"

    # Dimmer border when input not focused
    INPUT_BORDER_NORMAL = "#3b3c37"
    INPUT_BORDER_FOCUS  = "#9bbd4f"


FONT      = ("Segoe UI", 11)
FONT_B    = ("Segoe UI", 11, "bold")
FONT_SM   = ("Segoe UI", 9)
FONT_SM_B = ("Segoe UI", 9, "bold")
FONT_TTL  = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 10)
FONT_HERO = ("Segoe UI", 26, "bold")


# ══════════════════════════════════════════════════════════════════
#  DesktopApp
# ══════════════════════════════════════════════════════════════════

class DesktopApp:

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("UndoMesh")
        self.root.geometry("1180x780")
        self.root.minsize(900, 600)
        self.root.configure(bg=T.BG)

        # State
        self._app: Any = None          # UndoMeshApp (lazy)
        self._ready = False
        self._busy = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: Any = None         # running asyncio task (for cancel)
        self._placeholder = True
        self._thinking_id: str | None = None   # after-id for animation
        self._dots = 0
        self._pending_config: Any = None

        # Build sections top-to-bottom
        self._build_toolbar()
        self._build_workspace()

        # Shortcuts
        self.root.bind("<Control-n>", lambda e: self._new_session())
        self.root.bind("<Control-r>", lambda e: self._open_recovery_center())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Async background thread
        self._start_bg()
        self.root.after(150, self._focus_input)

    # ────────────────────────────── Toolbar ───────────────────────

    def _build_toolbar(self) -> None:
        bar = tk.Frame(self.root, bg=T.SURFACE, height=54)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        left = tk.Frame(bar, bg=T.SURFACE)
        left.pack(side="left", padx=16, pady=8)

        tk.Label(left, text="UndoMesh", font=FONT_TTL,
                 fg=T.FG_BRIGHT, bg=T.SURFACE).pack(side="left")
        tk.Label(left, text="  workspace agent", font=FONT_SM,
                 fg=T.FG_DIM, bg=T.SURFACE).pack(side="left")

        right = tk.Frame(bar, bg=T.SURFACE)
        right.pack(side="right", padx=12, pady=8)

        self._btn(right, "  Review  ", self._open_review_center)
        self._btn(right, "  Recovery  ", self._open_recovery_center)
        self._btn(right, "  Model  ", self._open_model_settings)

        tk.Label(right, text="  |  ", font=FONT_SM,
                 fg=T.BORDER, bg=T.SURFACE).pack(side="left")

        self._model_lbl = tk.Label(right, text="mimo-v2.5-pro",
                                   font=FONT_SM, fg=T.FG_DIM, bg=T.SURFACE)
        self._model_lbl.pack(side="left")

    def _open_review_center(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Review changes")
        dialog.geometry("960x680")
        dialog.configure(bg=T.SURFACE)
        root = self._app.config.effective_project_dir() if self._app else get_config().effective_project_dir()
        result = subprocess.run(
            ["git", "-C", str(root), "diff", "--binary"], text=True,
            capture_output=True, check=False,
        )
        diff = result.stdout if result.returncode == 0 else f"Git diff unavailable: {result.stderr.strip()}"
        receipts = self._app.change_engine.list_recent(8) if self._app else ()
        receipt = {
            "recent": [
                {"transaction_id": item.transaction_id, "state": item.state.value,
                 "paths": list(item.touched_paths)} for item in receipts
            ]
        }
        review = build_review(diff, receipt=receipt)
        header = tk.Frame(dialog, bg=T.SURFACE)
        header.pack(fill="x", padx=18, pady=14)
        tk.Label(header, text="Diff & Review", font=("Segoe UI", 18, "bold"),
                 fg=T.FG_BRIGHT, bg=T.SURFACE).pack(side="left")
        tk.Label(header, text=f"{len(review.files)} files   +{review.additions}  -{review.deletions}",
                 font=FONT_SM, fg=T.FG_DIM, bg=T.SURFACE).pack(side="right")
        body = tk.PanedWindow(dialog, orient="horizontal", bg=T.BORDER, sashwidth=1)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 18))
        files = tk.Text(body, width=28, bg=T.ELEVATED, fg=T.FG, font=FONT_SM,
                        relief="flat", padx=10, pady=10)
        files.insert("1.0", "FILES\n\n" + ("\n".join(review.files) or "No working-tree diff") +
                     "\n\nTRANSACTION RECEIPTS\n\n" + "\n".join(
                         f"{item['transaction_id'][:10]}  {item['state']}  {len(item['paths'])} paths"
                         for item in receipt["recent"]
                     ))
        files.configure(state="disabled")
        diff_view = tk.Text(body, bg=T.INPUT_BG, fg=T.FG, font=("Consolas", 10),
                            relief="flat", padx=12, pady=10, wrap="none")
        diff_view.insert("1.0", review.diff or "No uncommitted changes")
        diff_view.configure(state="disabled")
        body.add(files)
        body.add(diff_view)

    def _build_workspace(self) -> None:
        workspace = tk.Frame(self.root, bg=T.BG)
        workspace.pack(fill="both", expand=True)
        self._build_sidebar(workspace)
        content = tk.Frame(workspace, bg=T.BG)
        content.pack(side="left", fill="both", expand=True)
        self._build_status_bar(content)
        self._build_input(content)
        self._build_chat(content)

    def _build_sidebar(self, parent) -> None:
        side = tk.Frame(parent, bg=T.SURFACE, width=226)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(side, bg=T.BORDER, width=1).pack(side="right", fill="y")

        top = tk.Frame(side, bg=T.SURFACE)
        top.pack(fill="x", padx=14, pady=(16, 10))
        new_btn = tk.Label(
            top, text="  New task", font=FONT_SM_B, anchor="w",
            fg="#111210", bg=T.ACCENT, padx=10, pady=9, cursor="hand2",
        )
        new_btn.pack(fill="x")
        new_btn.bind("<Button-1>", lambda _e: self._new_session())
        new_btn.bind("<Enter>", lambda _e: new_btn.configure(bg=T.ACCENT_HOV))
        new_btn.bind("<Leave>", lambda _e: new_btn.configure(bg=T.ACCENT))

        tk.Label(side, text="TASKS", font=FONT_SM_B, fg=T.FG_DIM, bg=T.SURFACE,
                 anchor="w").pack(fill="x", padx=16, pady=(12, 6))
        self._sessions = tk.Listbox(
            side, bg=T.SURFACE, fg=T.FG, selectbackground=T.ELEVATED,
            selectforeground=T.FG_BRIGHT, font=FONT_SM, borderwidth=0,
            highlightthickness=0, activestyle="none", exportselection=False,
        )
        self._sessions.pack(fill="both", expand=True, padx=10)
        self._sessions.bind("<<ListboxSelect>>", self._switch_selected_session)
        self._session_ids: list[str] = []

        safety = tk.Frame(side, bg=T.SURFACE)
        safety.pack(fill="x", padx=14, pady=14)
        tk.Label(safety, text="RECOVERY", font=FONT_SM_B, fg=T.FG_DIM,
                 bg=T.SURFACE, anchor="w").pack(fill="x", pady=(0, 6))
        self._recovery_summary = tk.Label(
            safety, text="Not initialized", font=FONT_SM, fg=T.FG_DIM,
            bg=T.ELEVATED, justify="left", anchor="w", padx=10, pady=9,
            cursor="hand2",
        )
        self._recovery_summary.pack(fill="x")
        self._recovery_summary.bind("<Button-1>", lambda _e: self._open_recovery_center())

    def _open_model_settings(self) -> None:
        config = self._app.config if self._app else self._pending_config
        if config is None:
            raw = get_config()
            config = ProviderSettingsStore(raw.provider_settings_path).apply(raw)

        dialog = tk.Toplevel(self.root)
        dialog.title("Model provider")
        dialog.geometry("560x430")
        dialog.resizable(False, False)
        dialog.configure(bg=T.SURFACE)
        dialog.transient(self.root)
        dialog.grab_set()

        provider = tk.StringVar(value=config.provider)
        model = tk.StringVar(value=config.model)
        base_url = tk.StringVar(value=config.api_base_url)
        api_key = tk.StringVar(value="")
        status = tk.StringVar(value="API Key is kept in memory only and is never written to provider.json.")

        def row(label: str, variable: tk.StringVar, *, secret: bool = False):
            tk.Label(dialog, text=label, font=FONT_SM_B, fg=T.FG, bg=T.SURFACE).pack(anchor="w", padx=24, pady=(12, 3))
            entry = tk.Entry(dialog, textvariable=variable, show="*" if secret else "", font=FONT, fg=T.FG, bg=T.INPUT_BG, insertbackground=T.FG, relief="flat")
            entry.pack(fill="x", padx=24, ipady=7)
            return entry

        tk.Label(dialog, text="Provider", font=FONT_SM_B, fg=T.FG, bg=T.SURFACE).pack(anchor="w", padx=24, pady=(20, 3))
        menu = tk.OptionMenu(dialog, provider, *PROVIDERS.keys())
        menu.configure(font=FONT, fg=T.FG, bg=T.INPUT_BG, activebackground=T.HOVER, relief="flat", highlightthickness=0)
        menu["menu"].configure(bg=T.INPUT_BG, fg=T.FG)
        menu.pack(fill="x", padx=24)
        model_entry = row("Model", model)
        base_entry = row("Base URL", base_url)
        row("API Key", api_key, secret=True)

        def provider_changed(*_args):
            preset = get_provider(provider.get())
            base_url.set(preset.base_url)
            if preset.models:
                model.set(preset.models[0])
            base_entry.configure(state="normal" if provider.get() == "custom" else "readonly")
            status.set(preset.compatibility_note or "Official OpenAI-compatible endpoint preset.")

        provider.trace_add("write", provider_changed)

        def candidate_config():
            return config.model_copy(update={
                "provider": provider.get(), "model": model.get().strip(),
                "api_base_url": base_url.get().strip(), "api_key_override": api_key.get().strip(),
            })

        def test_connection():
            try:
                candidate = candidate_config()
                candidate.api_key
                candidate.effective_api_base_url
            except Exception as exc:
                status.set(f"Configuration error: {exc}")
                return
            status.set("Testing connection with one minimal model request...")

            async def go():
                client = ModelClient(candidate)
                try:
                    report = await client.diagnose_connection()
                    prefix = "Connected" if report.success else "Connection failed"
                    self.root.after(0, status.set, f"{prefix}: {report.status.value}: {report.message}")
                except Exception as exc:
                    self.root.after(0, status.set, f"Configuration error: {type(exc).__name__}")
                finally:
                    await client.close()

            if self._loop:
                asyncio.run_coroutine_threadsafe(go(), self._loop)

        def save():
            try:
                candidate = candidate_config()
                candidate.api_key
                store = ProviderSettingsStore(candidate.provider_settings_path)
                store.save(provider=candidate.provider, model=candidate.model, api_base_url=candidate.api_base_url)
                self._pending_config = candidate
                self._model_lbl.configure(text=f"{candidate.provider}: {candidate.model}")
                if self._app and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._app.configure_provider(
                            provider=candidate.provider, model=candidate.model,
                            api_base_url=candidate.api_base_url,
                            api_key=candidate.api_key_override,
                        ), self._loop,
                    )
                dialog.destroy()
            except Exception as exc:
                status.set(f"Cannot save: {exc}")

        buttons = tk.Frame(dialog, bg=T.SURFACE)
        buttons.pack(fill="x", padx=21, pady=18)
        self._btn(buttons, " Test connection ", test_connection)
        self._btn(buttons, " Save ", save)
        tk.Label(dialog, textvariable=status, wraplength=510, justify="left", font=FONT_SM, fg=T.FG_DIM, bg=T.SURFACE).pack(anchor="w", padx=24)

    def _btn(self, parent, text, cmd):
        b = tk.Label(parent, text=text, font=FONT_SM_B,
                     fg=T.FG_DIM, bg=T.ELEVATED, padx=8, pady=3, cursor="hand2")
        b.pack(side="left", padx=3)
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(bg=T.HOVER, fg=T.FG))
        b.bind("<Leave>", lambda e: b.configure(bg=T.ELEVATED, fg=T.FG_DIM))

    async def _ensure_app(self):
        if self._ready:
            return self._app
        from undomesh.app import UndoMeshApp
        self._app = UndoMeshApp(self._pending_config)
        await self._app.initialize()
        self._app.query_loop.set_callbacks(
            on_tool_start=self._cb_tool,
            on_tool_end=self._cb_tool_done,
            on_thinking=self._cb_think,
        )
        if self._app.permission_manager:
            self._app.permission_manager.set_confirm_callback(
                lambda name, params: bool(self._confirm_on_ui_thread(name, params))
            )
        self._ready = True
        self.root.after(0, self._on_init)
        return self._app

    def _confirm_on_ui_thread(self, name: str, params: dict[str, Any]) -> bool:
        completed = threading.Event()
        result = {"approved": False}

        def ask():
            result["approved"] = messagebox.askyesno(
                "Approve operation",
                f"Allow {name}?\n\n{json.dumps(params, ensure_ascii=False, indent=2)[:900]}",
                parent=self.root,
            )
            completed.set()

        self.root.after(0, ask)
        completed.wait(timeout=300)
        return bool(result["approved"])

    def _refresh_sessions(self) -> None:
        if not self._app or not self._app.event_store:
            return
        active = self._app.context.session_id
        self._session_ids = self._app.event_store.list_sessions()
        self._sessions.delete(0, "end")
        for index, session_id in enumerate(self._session_ids):
            marker = "Active" if session_id == active else "Task"
            self._sessions.insert("end", f"  {marker}  {session_id}")
            if session_id == active:
                self._sessions.selection_set(index)
        checkpoints = self._app.checkpoint_manager.list_checkpoints()
        restores = self._app.change_engine.restore_coordinator.list_recent()
        self._recovery_summary.configure(
            text=f"{len(checkpoints)} checkpoints\n{len(restores)} restore receipts\nOpen recovery center"
        )

    def _switch_selected_session(self, _event=None) -> None:
        if not self._app or self._busy:
            return
        selection = self._sessions.curselection()
        if not selection:
            return
        session_id = self._session_ids[selection[0]]
        if session_id == self._app.context.session_id:
            return
        try:
            self._app.switch_session(session_id)
            self._clear()
            self._put(f"Task {session_id} loaded. Continue from the current projection.", "sys")
            self._set_status(f"Task {session_id} active")
            self._refresh_sessions()
        except Exception as exc:
            self._show_error(str(exc))

    def _open_recovery_center(self) -> None:
        if self._busy:
            self._set_status("Finish or stop the current task before rewinding")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Recovery center")
        dialog.geometry("840x590")
        dialog.minsize(760, 520)
        dialog.configure(bg=T.BG)
        dialog.transient(self.root)

        header = tk.Frame(dialog, bg=T.BG)
        header.pack(fill="x", padx=24, pady=(22, 14))
        tk.Label(header, text="Recovery center", font=("Segoe UI", 18, "bold"),
                 fg=T.FG_BRIGHT, bg=T.BG).pack(anchor="w")
        tk.Label(
            header,
            text="Every prompt is a checkpoint. Code restore is preflighted and conversation history stays append-only.",
            font=FONT_SM, fg=T.FG_DIM, bg=T.BG,
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(dialog, bg=T.BG)
        body.pack(fill="both", expand=True, padx=24)
        left = tk.Frame(body, bg=T.SURFACE, width=330)
        left.pack(side="left", fill="both", expand=False, padx=(0, 12))
        left.pack_propagate(False)
        right = tk.Frame(body, bg=T.SURFACE)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="PROMPT CHECKPOINTS", font=FONT_SM_B, fg=T.FG_DIM,
                 bg=T.SURFACE, anchor="w").pack(fill="x", padx=14, pady=(14, 8))
        listing = tk.Listbox(
            left, bg=T.SURFACE, fg=T.FG, selectbackground=T.ELEVATED,
            selectforeground=T.FG_BRIGHT, font=FONT_SM, borderwidth=0,
            highlightthickness=0, activestyle="none", exportselection=False,
        )
        listing.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        detail_title = tk.StringVar(value="Loading recovery state...")
        detail_body = tk.StringVar(value="")
        tk.Label(right, textvariable=detail_title, font=FONT_TTL, fg=T.FG_BRIGHT,
                 bg=T.SURFACE, anchor="w").pack(fill="x", padx=18, pady=(18, 8))
        tk.Label(right, textvariable=detail_body, font=FONT_SM, fg=T.FG_DIM,
                 bg=T.SURFACE, justify="left", anchor="nw", wraplength=420).pack(
                     fill="both", expand=True, padx=18
                 )
        restore_history_var = tk.StringVar(value="RESTORE RECEIPTS\nLoading...")
        tk.Label(
            right, textvariable=restore_history_var, font=FONT_MONO, fg=T.FG_DIM,
            bg=T.ELEVATED, justify="left", anchor="nw", padx=12, pady=10,
        ).pack(fill="x", padx=18, pady=(8, 0))
        actions = tk.Frame(right, bg=T.SURFACE)
        actions.pack(fill="x", padx=14, pady=14)
        status = tk.StringVar(value="")
        tk.Label(dialog, textvariable=status, font=FONT_SM, fg=T.FG_DIM,
                 bg=T.BG, anchor="w").pack(fill="x", padx=24, pady=(10, 16))
        state: dict[str, Any] = {"checkpoints": []}

        def selected():
            choice = listing.curselection()
            return state["checkpoints"][choice[0]] if choice else None

        def show_selected(_event=None):
            item = selected()
            if not item:
                return
            detail_title.set(item.prompt.replace("\n", " ")[:90] or "Empty prompt")
            detail_body.set(checkpoint_detail(item))

        listing.bind("<<ListboxSelect>>", show_selected)

        def render(checkpoints, restores):
            state["checkpoints"] = list(reversed(checkpoints))
            listing.delete(0, "end")
            for item in state["checkpoints"]:
                listing.insert("end", checkpoint_row(item))
            if state["checkpoints"]:
                listing.selection_set(0)
                show_selected()
                status.set("Select an action. Code modes require confirmation.")
            else:
                detail_title.set("No checkpoints yet")
                detail_body.set("Send a prompt to create the first recovery point.")
                status.set("")
            restore_history_var.set(format_restore_history(restores))

        def load():
            async def go():
                try:
                    app = await self._ensure_app()
                    checkpoints = app.checkpoint_manager.list_checkpoints()
                    restores = app.change_engine.restore_coordinator.list_recent()
                    self.root.after(0, render, checkpoints, restores)
                    self.root.after(0, self._refresh_sessions)
                except Exception as exc:
                    self.root.after(0, status.set, f"Cannot load recovery state: {exc}")
            asyncio.run_coroutine_threadsafe(go(), self._loop)

        def rewind(mode: str):
            item = selected()
            if not item:
                return
            if mode in {"code", "both"} and not messagebox.askyesno(
                "Confirm code restore",
                f"Restore {len(item.transaction_ids)} transaction(s) from this checkpoint?\n\n"
                "All dependencies and conflicts will be checked again before the first write.",
                parent=dialog,
            ):
                return
            status.set(f"Running {mode} rewind...")

            async def go():
                try:
                    receipt = self._app.checkpoint_manager.rewind(item.checkpoint_id, mode)
                    self.root.after(0, status.set, f"Completed. Active task: {receipt.active_session_id}")
                    self.root.after(0, self._put, f"Checkpoint rewind completed: {mode}", "sys")
                    self.root.after(0, self._refresh_sessions)
                    self.root.after(0, load)
                except Exception as exc:
                    self.root.after(0, status.set, f"Rewind failed: {exc}")
            asyncio.run_coroutine_threadsafe(go(), self._loop)

        for label, mode in (
            ("Code", "code"), ("Conversation", "conversation"),
            ("Both", "both"), ("Summarize", "summary"),
        ):
            self._btn(actions, label, lambda value=mode: rewind(value))
        load()

    # ────────────────────────────── Chat ──────────────────────────

    def _build_chat(self, parent) -> None:
        wrap = tk.Frame(parent, bg=T.BG)
        wrap.pack(fill="both", expand=True)

        self._chat = tk.Text(
            wrap, wrap="word", font=FONT, fg=T.FG, bg=T.BG,
            bd=0, padx=28, pady=20, spacing1=0, spacing3=0,
            state="disabled", cursor="arrow",
            selectbackground=T.ACCENT, selectforeground=T.FG_BRIGHT,
            highlightthickness=0, undo=False,
        )
        sb = tk.Scrollbar(wrap, command=self._chat.yview, bg=T.SURFACE,
                          troughcolor=T.BG, width=6,
                          activebackground=T.ELEVATED, highlightthickness=0, bd=0)
        self._chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._chat.pack(fill="both", expand=True)

        # ── Tag styles ────────────────────────────────────────────
        c = self._chat
        c.tag_configure("hero", font=FONT_HERO, foreground=T.ACCENT,
                        justify="center", spacing1=24, spacing3=6)
        c.tag_configure("sub", font=FONT, foreground=T.FG_DIM,
                        justify="center", spacing3=6)
        c.tag_configure("tips", font=FONT_SM, foreground=T.FG_DIM,
                        justify="center", spacing1=2, spacing3=2)
        c.tag_configure("sep", font=("Segoe UI", 1), foreground=T.BORDER,
                        lmargin1=40, rmargin=40, spacing1=10, spacing3=10)

        # User message
        c.tag_configure("u_name", font=FONT_SM_B, foreground=T.ACCENT,
                        lmargin1=0, spacing1=14)
        c.tag_configure("u_msg", font=FONT, foreground=T.FG_BRIGHT,
                        background=T.USER_BG, lmargin1=8, lmargin2=8,
                        rmargin=140, spacing1=8, spacing3=10)
        c.tag_configure("u_time", font=FONT_SM, foreground=T.FG_DIM,
                        lmargin1=8, spacing3=10)

        # AI message
        c.tag_configure("a_name", font=FONT_SM_B, foreground=T.GREEN,
                        lmargin1=0, spacing1=14)
        c.tag_configure("a_msg", font=FONT, foreground=T.FG,
                        background=T.AI_BG, lmargin1=8, lmargin2=8,
                        rmargin=8, spacing1=8, spacing3=10)
        c.tag_configure("a_time", font=FONT_SM, foreground=T.FG_DIM,
                        lmargin1=8, spacing3=10)

        # Tool calls
        c.tag_configure("t_run", font=FONT_MONO, foreground=T.ORANGE,
                        background=T.TOOL_BG_RUN,
                        lmargin1=32, lmargin2=32, spacing1=3, spacing3=3)
        c.tag_configure("t_ok", font=FONT_MONO, foreground=T.GREEN,
                        background=T.TOOL_BG_OK,
                        lmargin1=32, lmargin2=32, spacing1=3, spacing3=3)
        c.tag_configure("t_err", font=FONT_MONO, foreground=T.RED,
                        background=T.TOOL_BG_ERR,
                        lmargin1=32, lmargin2=32, spacing1=3, spacing3=3)
        c.tag_configure("t_pre", font=FONT_SM, foreground=T.FG_DIM,
                        lmargin1=48, lmargin2=48, spacing1=1, spacing3=1)

        # Thinking / status
        c.tag_configure("think", font=FONT_SM, foreground=T.FG_DIM,
                        lmargin1=8, spacing1=6, spacing3=6)
        c.tag_configure("sys", font=FONT_SM, foreground=T.FG_DIM,
                        justify="center", spacing1=8, spacing3=8)

        # Make chat area clickable to focus input
        self._chat.bind("<Button-1>", lambda e: self._focus_input())

    # ────────────────────────────── Input ─────────────────────────

    def _build_input(self, parent) -> None:
        outer = tk.Frame(parent, bg=T.BG)
        outer.pack(fill="x", side="bottom")

        # Divider
        tk.Frame(outer, bg=T.BORDER, height=1).pack(fill="x")

        row = tk.Frame(outer, bg=T.BG)
        row.pack(fill="x", padx=20, pady=14)

        # Wrapper — highlights on focus
        self._wrapper = tk.Frame(row, bg=T.INPUT_BORDER_NORMAL, bd=0)
        self._wrapper.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self._wrapper.bind("<Button-1>", lambda e: self._focus_input())

        inner = tk.Frame(self._wrapper, bg=T.INPUT_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._input = tk.Text(
            inner, font=FONT, fg=T.FG, bg=T.INPUT_BG,
            bd=0, padx=12, pady=10, height=2, wrap="word",
            insertbackground=T.ACCENT, insertwidth=2,
            selectbackground=T.ACCENT, selectforeground=T.FG_BRIGHT,
            highlightthickness=0, undo=True,
        )
        self._input.pack(fill="both", expand=True)
        self._input.bind("<FocusIn>", self._on_focus_in)
        self._input.bind("<FocusOut>", self._on_focus_out)
        self._input.bind("<Return>", self._on_enter)
        self._input.bind("<Control-Return>", lambda e: self._on_send())

        # Right side: send button
        right_col = tk.Frame(row, bg=T.BG)
        right_col.pack(side="right")

        self._send_btn = tk.Label(
            right_col, text="  Send  ", font=FONT_B,
            fg="#111210", bg=T.ACCENT, padx=18, pady=18, cursor="hand2",
        )
        self._send_btn.pack()
        self._send_btn.bind("<Button-1>", lambda e: self._on_btn_click())
        self._send_btn.bind("<Enter>",
                            lambda e: self._send_btn.configure(bg=T.ACCENT_HOV) if not self._busy else None)
        self._send_btn.bind("<Leave>",
                            lambda e: self._send_btn.configure(bg=T.ACCENT) if not self._busy else None)

        # Hint below input
        tk.Label(outer, text="Enter to send  |  Shift+Enter for newline  |  Ctrl+N new session",
                 font=FONT_SM, fg=T.FG_DIM, bg=T.BG).pack(pady=(0, 8))

        # Placeholder
        self._ph_text = "Describe a task, ask for a review, or inspect the workspace..."
        self._show_ph()

    def _show_ph(self):
        self._input.delete("1.0", "end")
        self._input.insert("1.0", self._ph_text)
        self._input.configure(fg=T.FG_DIM)
        self._placeholder = True

    def _clear_ph(self):
        if self._placeholder:
            self._input.delete("1.0", "end")
            self._input.configure(fg=T.FG)
            self._placeholder = False

    def _on_focus_in(self, e):
        self._clear_ph()
        self._wrapper.configure(bg=T.INPUT_BORDER_FOCUS)

    def _on_focus_out(self, e):
        if not self._input.get("1.0", "end-1c").strip():
            self._show_ph()
        self._wrapper.configure(bg=T.INPUT_BORDER_NORMAL)

    def _on_enter(self, e):
        if e.state & 0x1:       # Shift held → newline
            return
        self._on_send()
        return "break"

    def _focus_input(self):
        self._input.focus_set()
        self._wrapper.configure(bg=T.INPUT_BORDER_FOCUS)

    # ────────────────────────────── Status Bar ────────────────────

    def _build_status_bar(self, parent):
        bar = tk.Frame(parent, bg=T.SURFACE, height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self._status = tk.Label(bar, text="  Ready", font=FONT_SM,
                                fg=T.FG_DIM, bg=T.SURFACE, anchor="w")
        self._status.pack(fill="x", padx=10)

    def _set_status(self, text):
        self._status.configure(text=f"  {text}")

    # ────────────────────────────── Chat Helpers ──────────────────

    def _put(self, text, *tags):
        self._chat.configure(state="normal")
        tag = tags[0] if tags else ""
        self._chat.insert("end", text + "\n", tag)
        self._chat.configure(state="disabled")
        self._chat.see("end")

    def _put_raw(self, text, tag=""):
        """Insert without trailing newline."""
        self._chat.configure(state="normal")
        self._chat.insert("end", text, tag)
        self._chat.configure(state="disabled")
        self._chat.see("end")

    def _timestamp(self):
        return datetime.now().strftime("%H:%M")

    def _show_welcome(self):
        self._put("")
        self._put("What are we building?", "hero")
        self._put("Plan, implement, review and safely rewind workspace changes.", "sub")
        self._put("")
        self._put("Ctrl+R opens Recovery Center  |  Ctrl+N starts a new task", "tips")
        self._put("")

    def _show_user(self, text):
        self._put("", "sep")
        self._put("  You", "u_name")
        self._put(text, "u_msg")
        self._put(f"  {self._timestamp()}", "u_time")

    def _show_ai(self, text):
        self._put("")
        self._put("  UndoMesh", "a_name")
        self._put(text, "a_msg")
        self._put(f"  {self._timestamp()}", "a_time")

    def _show_tool_start(self, name, params):
        p = json.dumps(params, ensure_ascii=False)
        if len(p) > 80:
            p = p[:80] + " ..."
        self._put(f"    {name}({p})", "t_run")

    def _show_tool_end(self, name, ok, preview):
        icon = "OK" if ok else "ERR"
        tag = "t_ok" if ok else "t_err"
        self._put(f"    {icon}  {name}", tag)
        if preview:
            line = preview.split("\n")[0]
            if len(line) > 80:
                line = line[:80] + " ..."
            self._put(f"      {line}", "t_pre")

    def _show_error(self, msg):
        self._put(f"  Error: {msg}", "t_err")

    # ────────────────────────────── Thinking Animation ────────────

    def _start_thinking(self):
        self._dots = 0
        self._put("")
        self._put("  UndoMesh", "a_name")
        self._put_raw("  Thinking", "think")
        self._animate_dots()

    def _animate_dots(self):
        if not self._busy:
            return
        self._dots = (self._dots % 3) + 1
        dots = "." * self._dots + "   "  # pad to clear previous
        # Update the last line's dots
        self._chat.configure(state="normal")
        # Find the "Thinking" text and update dots after it
        # We'll just append/update via tag
        try:
            # Remove old dots tag content
            last = self._chat.index("end-2c linestart")
            # find "Thinking" position
            pos = self._chat.search("Thinking", "end-10l", stopindex="end")
            if pos:
                line_end = f"{pos} lineend"
                # Delete from after "Thinking" to end of that line
                think_end = f"{pos}+8c"
                self._chat.delete(think_end, line_end)
                self._chat.insert(think_end, dots, "think")
        except Exception:
            pass
        self._chat.configure(state="disabled")
        self._chat.see("end")
        self._thinking_id = self.root.after(400, self._animate_dots)

    def _stop_thinking(self):
        if self._thinking_id:
            self.root.after_cancel(self._thinking_id)
            self._thinking_id = None
        # Clean up the thinking line — remove dots
        self._chat.configure(state="normal")
        try:
            pos = self._chat.search("Thinking", "end-10l", stopindex="end")
            if pos:
                think_end = f"{pos}+8c"
                line_end = f"{pos} lineend"
                self._chat.delete(think_end, line_end)
        except Exception:
            pass
        self._chat.configure(state="disabled")

    # ────────────────────────────── Events ────────────────────────

    def _on_btn_click(self):
        if self._busy:
            self._on_stop()
        else:
            self._on_send()

    def _on_send(self):
        if self._busy:
            return
        text = self._input.get("1.0", "end-1c").strip()
        if not text or self._placeholder:
            return

        self._input.delete("1.0", "end")
        self._input.configure(fg=T.FG)
        self._placeholder = False

        self._show_user(text)

        self._busy = True
        # Button → Stop (red)
        self._send_btn.configure(text=" Stop ", bg=T.RED, cursor="hand2")
        self._send_btn.bind("<Button-1>", lambda e: self._on_btn_click())
        self._send_btn.bind("<Enter>",
                            lambda e: self._send_btn.configure(bg="#ff4060"))
        self._send_btn.bind("<Leave>",
                            lambda e: self._send_btn.configure(bg=T.RED))
        self._set_status("Thinking ...")
        self._start_thinking()
        self._run_async(text)

    def _on_stop(self):
        """Cancel the running query."""
        if not self._busy:
            return
        # Cancel the asyncio task
        if self._task and not self._task.done():
            self._task.cancel()
        self._stop_thinking()
        self._busy = False
        self._task = None
        # Restore Send button
        self._restore_send_btn()
        self._put("  [Stopped by user]", "sys")
        self._set_status("Stopped   |   Ready")
        self.root.after(50, self._focus_input)

    def _on_close(self):
        if self._busy:
            if not messagebox.askokcancel("Quit", "Query is running. Quit?"):
                return
        if self._app and self._loop:
            asyncio.run_coroutine_threadsafe(self._app.shutdown(), self._loop)
        self.root.destroy()

    def _new_session(self):
        if self._busy:
            self._set_status("Stop the current task before starting a new one")
            return
        if self._app and self._app.context:
            self._app.start_new_session()
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")
        self._show_welcome()
        self._show_ph()
        self._set_status("New session started")
        self._refresh_sessions()
        self.root.after(50, self._focus_input)

    def _clear(self):
        self._chat.configure(state="normal")
        self._chat.delete("1.0", "end")
        self._chat.configure(state="disabled")

    # ────────────────────────────── Async ─────────────────────────

    def _start_bg(self):
        def _():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()
        threading.Thread(target=_, daemon=True).start()

    def _run_async(self, text):
        async def go():
            try:
                await self._ensure_app()
                result = await self._app.run_query(text)
                self.root.after(0, self._on_done, result)
            except asyncio.CancelledError:
                self.root.after(0, self._on_cancelled)
            except Exception as e:
                logger.error("Query failed: %s", e, exc_info=True)
                self.root.after(0, self._on_err, str(e))

        if self._loop:
            self._task = asyncio.run_coroutine_threadsafe(go(), self._loop)

    def _on_cancelled(self):
        """Called when task is cancelled from stop button."""
        pass  # _on_stop already handled UI reset

    def _on_init(self):
        if self._app and self._app.config:
            self._model_lbl.configure(text=self._app.config.model)
        self._refresh_sessions()

    def _restore_send_btn(self):
        """Restore button to Send state."""
        self._send_btn.configure(text="  Send  ", fg="#111210", bg=T.ACCENT, cursor="hand2")
        self._send_btn.bind("<Button-1>", lambda e: self._on_btn_click())
        self._send_btn.bind("<Enter>",
                            lambda e: self._send_btn.configure(bg=T.ACCENT_HOV) if not self._busy else None)
        self._send_btn.bind("<Leave>",
                            lambda e: self._send_btn.configure(bg=T.ACCENT) if not self._busy else None)

    def _on_done(self, result):
        self._stop_thinking()
        self._busy = False
        self._task = None
        self._restore_send_btn()
        if self._app and self._app.query_loop:
            u = self._app.query_loop.token_usage
            self._set_status(
                f"Tokens {u.get('input_tokens',0):,} in + "
                f"{u.get('output_tokens',0):,} out   |   "
                f"iter {self._app.query_loop.iteration_count}   |   Ready"
            )
        self.root.after(50, self._focus_input)

    def _on_err(self, err):
        self._stop_thinking()
        self._busy = False
        self._task = None
        self._restore_send_btn()
        self._show_error(err)
        self._set_status(f"Error: {err[:60]}")
        self.root.after(50, self._focus_input)

    # ── Callbacks from async thread ──────────────────────────────

    def _cb_tool(self, name, params):
        self.root.after(0, self._show_tool_start, name, params)

    def _cb_tool_done(self, name, ok, preview):
        self.root.after(0, self._show_tool_end, name, ok, preview)

    def _cb_think(self, text):
        self.root.after(0, self._on_ai_text, text)

    def _on_ai_text(self, text):
        self._stop_thinking()
        self._show_ai(text)
        # Restart thinking animation if more iterations may follow
        if self._busy:
            self._start_thinking()

    # ────────────────────────────── Run ───────────────────────────

    def run(self):
        self._show_welcome()
        self._set_status("Ready   |   Enter: Send   |   Shift+Enter: Newline   |   Ctrl+N: New session")
        self.root.mainloop()


def launch_desktop() -> None:
    DesktopApp().run()
