"""File > Import from YouTube: search, analyse with guitar-transcriber, open the result as a tab."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .transcriber import Transcriber, TranscriberError, song_from_analysis

STAGES = [("download", "Downloading the audio"), ("separate", "Separating the guitar from the mix (slowest step)"),
          ("beats", "Finding the beat and bar lines"), ("notes", "Finding the chords and notes"),
          ("positions", "Working out fret positions and the capo"), ("pictures", "Drawing spectrum pictures"),
          ("check", "Checking the result"), ("done", "Done")]
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES)}


def _length(seconds) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}" if seconds else ""


class ImportDialog:
    def __init__(self, app):
        self.app = app
        self.root = app.root
        self.transcriber = Transcriber.find(app.prefs.get("transcriber_dir"))
        self.items: dict[str, dict] = {}          # tree row id -> video/library entry
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.process = None
        self.stopping = False

        win = self.win = tk.Toplevel(self.root)
        win.title("Import from YouTube")
        win.transient(self.root)
        win.geometry("1020x620")
        win.minsize(640, 420)
        win.configure(background=app.pal["ui"])
        win.protocol("WM_DELETE_WINDOW", self.close)
        frame = ttk.Frame(win, padding=14)
        frame.pack(fill="both", expand=True)
        self.frame = frame

        if self.transcriber is None:
            self._not_found()
        else:
            self._build()
            self.show_library()
        win.after(100, self._poll)

    # ------------------------------------------------------------------ layout
    def _not_found(self) -> None:
        f = self.frame
        ttk.Label(f, text="guitar-transcriber was not found", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(f, wraplength=760, justify="left", text=(
            "Importing from YouTube uses the guitar-transcriber project (it separates the guitar and finds "
            "the chords on your GPU). It was expected at D:\\guitar-transcriber, with its environment in "
            ".venv. Point to its folder if it lives somewhere else.")).pack(anchor="w", pady=(6, 12))
        ttk.Button(f, text="Choose folder…", command=self.choose_folder).pack(anchor="w")

    def choose_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self.win, title="guitar-transcriber folder")
        if not folder:
            return
        found = Transcriber(folder)
        if not found.available():
            messagebox.showerror("Import from YouTube", "That folder has no guitar-transcriber project with a "
                                 ".venv environment.", parent=self.win)
            return
        self.app.prefs["transcriber_dir"] = folder
        self.app.save_prefs()
        self.transcriber = found
        for child in self.frame.winfo_children():
            child.destroy()
        self._build()
        self.show_library()

    def _build(self) -> None:
        f = self.frame
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.query = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.search())
        entry.focus_set()
        self.search_btn = ttk.Button(top, text="Search YouTube", command=self.search)
        self.search_btn.pack(side="left", padx=(8, 0))
        ttk.Button(top, text="Analysed songs", command=self.show_library).pack(side="left", padx=(6, 0))
        ttk.Label(f, text="Type a song name or paste a YouTube link. Songs analysed before open straight away; "
                          "new ones take a few minutes on the GPU.", style="Status.TLabel",
                  wraplength=960).pack(anchor="w", pady=(4, 8))

        table = ttk.Frame(f)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("title", "by", "length", "status"), show="headings",
                                 selectmode="browse")
        for col, text, width, stretch in (("title", "Title", 420, True), ("by", "Artist / channel", 190, True),
                                          ("length", "Length", 70, False), ("status", "Status", 220, False)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        bar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())
        self.tree.bind("<Double-1>", lambda e: self.open_selected())

        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(10, 0))
        self.use_capo = tk.BooleanVar(value=True)
        self.arrows = tk.BooleanVar(value=True)
        self.rhythm = tk.IntVar(value=8)
        ttk.Checkbutton(opts, text="Use capo shapes when a capo is found", variable=self.use_capo).pack(side="left")
        ttk.Checkbutton(opts, text="Strum arrows", variable=self.arrows).pack(side="left", padx=(14, 0))
        ttk.Label(opts, text="Rhythm:").pack(side="left", padx=(14, 4))
        ttk.Radiobutton(opts, text="Eighths (easier to read)", value=8, variable=self.rhythm).pack(side="left")
        ttk.Radiobutton(opts, text="Sixteenths (detailed)", value=16, variable=self.rhythm).pack(side="left", padx=(6, 0))

        prog = ttk.Frame(f)
        prog.pack(fill="x", pady=(12, 0))
        self.status = ttk.Label(prog, text="", style="Status.TLabel")
        self.status.pack(anchor="w")
        self.bar = ttk.Progressbar(prog, maximum=len(STAGES) - 1, mode="determinate")

        buttons = ttk.Frame(f)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        self.stop_btn = ttk.Button(buttons, text="Stop analysis", command=self.stop)
        self.open_btn = ttk.Button(buttons, text="Open in editor", command=self.open_selected)
        self.open_btn.pack(side="right", padx=(0, 8))
        self._update_buttons()

    # ------------------------------------------------------------------ lists
    def _fill(self, entries: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        self.items.clear()
        analysed = {e.get("id"): e["slug"] for e in self.transcriber.library() if e.get("id")}
        for entry in entries:
            entry = dict(entry)
            if not entry.get("slug") and entry.get("id") in analysed:
                entry["slug"] = analysed[entry["id"]]
            if entry.get("slug"):
                status = "Analysed — opens now"
            elif entry.get("too_long"):
                status = "Too long (over 10 min)"
            else:
                status = "Needs analysis (minutes)"
            row = self.tree.insert("", "end", values=(entry.get("title", ""), entry.get("channel", ""),
                                                      _length(entry.get("duration")), status))
            self.items[row] = entry
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
        self._update_buttons()

    def show_library(self) -> None:
        songs = self.transcriber.library()
        self._fill(songs)
        self.status.configure(text=f"{len(songs)} song{'s' if len(songs) != 1 else ''} analysed so far."
                              if songs else "No songs analysed yet. Search YouTube above.")

    def search(self) -> None:
        query = self.query.get().strip()
        if not query or self.busy:
            return
        self.search_btn.state(["disabled"])
        self.status.configure(text="Searching YouTube…")

        def work():
            try:
                self.events.put(("results", self.transcriber.search(query)))
            except TranscriberError as exc:
                self.events.put(("search_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.items.get(sel[0]) if sel else None

    def _update_buttons(self) -> None:
        if not hasattr(self, "open_btn"):
            return
        item = self.selected()
        if self.busy:
            self.open_btn.state(["disabled"])
            return
        ok = bool(item) and not (item.get("too_long") and not item.get("slug"))
        self.open_btn.state(["!disabled"] if ok else ["disabled"])
        self.open_btn.configure(text="Open in editor" if not item or item.get("slug") else "Analyse and open")

    # ------------------------------------------------------------------ actions
    def open_selected(self) -> None:
        item = self.selected()
        if not item or self.busy:
            return
        if item.get("slug"):
            self._load(item["slug"])
        elif not item.get("too_long"):
            self._analyse(item)

    def _analyse(self, item: dict) -> None:
        self.busy, self.stopping = True, False
        self._update_buttons()
        self.bar.pack(fill="x", pady=(6, 0))
        self.bar["value"] = 0
        self.stop_btn.pack(side="right", padx=(0, 8))
        self.status.configure(text=f"Analysing “{item.get('title', '')}”: starting…")

        def work():
            try:
                slug = self.transcriber.analyse(item, on_stage=lambda s: self.events.put(("stage", s)),
                                                on_process=lambda p: setattr(self, "process", p))
                self.events.put(("analysed", slug))
            except (TranscriberError, OSError) as exc:
                self.events.put(("analyse_error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.stopping = True
            self.process.terminate()

    def _load(self, slug: str) -> None:
        try:
            song = song_from_analysis(self.transcriber.song_dir(slug), use_capo=self.use_capo.get(),
                                      arrows=self.arrows.get(), rhythm=self.rhythm.get())
        except TranscriberError as exc:
            messagebox.showerror("Import from YouTube", str(exc), parent=self.win)
            return
        if self.app.load_imported(song):
            self.win.destroy()

    def _poll(self) -> None:
        if not self.win.winfo_exists():
            return
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "results":
                    self.search_btn.state(["!disabled"])
                    self._fill(value)
                    self.status.configure(text=f"{len(value)} result{'s' if len(value) != 1 else ''}."
                                          if value else "Nothing found.")
                elif kind == "search_error":
                    self.search_btn.state(["!disabled"])
                    self.status.configure(text=value)
                elif kind == "stage":
                    i = STAGE_INDEX.get(value)
                    if i is not None:
                        self.bar["value"] = i
                        self.status.configure(text=f"Step {i + 1} of {len(STAGES) - 1}: {STAGES[i][1]}…")
                elif kind in ("analysed", "analyse_error"):
                    self.busy, self.process = False, None
                    self.bar.pack_forget()
                    self.stop_btn.pack_forget()
                    self._update_buttons()
                    if kind == "analysed":
                        self.status.configure(text="Analysis finished.")
                        self._load(value)
                    else:
                        self.status.configure(text="Stopped." if self.stopping else value)
        except queue.Empty:
            pass
        if self.win.winfo_exists():
            self.win.after(150, self._poll)

    def close(self) -> None:
        if self.busy:
            if not messagebox.askyesno("Import from YouTube", "An analysis is running. Stop it and close?",
                                       parent=self.win):
                return
            self.stop()
        self.win.destroy()
