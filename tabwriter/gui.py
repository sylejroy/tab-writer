"""Desktop editor (tkinter): click on the tab, type frets, drag notes, print."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .editing import CHORD_SHAPES, TIME_SIGNATURES, Cursor, TabEditor
from .export import ExportError, html_to_pdf
from .layout import GAP, TEXT_STYLES, WIDTH, SystemLayout, layout_song
from .model import STANDARD_TUNING, Song
from .parser import parse
from . import player as playback
from .render import header_lines, render_html
from .serialize import serialize

ACCENT = "#b5001b"
PAPER_SIDE = 50.0       # page margins in layout units
PAPER_TOP = 50.0
FAMILIES = {"sans": "Helvetica", "serif": "Times"}
LINE_STYLES = {"staff": ("#8f8f8f", 0.7), "bar": ("#222222", 1.0), "stem": ("#111111", 1.1),
               "arrow": ("#111111", 1.3), "flag": ("#111111", 1.3)}
FILLS = {"ink": "#111111", "ko": "#ffffff"}
PREFS = Path.home() / ".tabwriter.json"

# Screen colours. The printed sheet is always black on white; these only affect the editor.
PALETTES = {
    "light": {
        "ui": "#f3f3f5", "field": "#ffffff", "fg": "#1b1b1f", "muted": "#5f5f68", "border": "#c9c9d0",
        "hover_ui": "#e6e6ea", "canvas": "#d6d6db", "paper": "#ffffff", "paper_edge": "#bdbdc4", "shadow": "#c2c2c8",
        "ink": "#111111", "staff": "#8f8f8f", "bar": "#222222", "mnum": "#333333", "bad": "#d0021b",
        "sub": "#444444", "meta": "#555555", "cursor": ACCENT, "column": "#ecc3c9", "hover": "#e8a1ab",
        "sel_fill": "#fbe1e6", "sel_line": ACCENT,
    },
    "dark": {
        "ui": "#1c1d20", "field": "#2a2b30", "fg": "#e6e6e9", "muted": "#9a9aa3", "border": "#3a3b41",
        "hover_ui": "#34353b", "canvas": "#121214", "paper": "#1f2023", "paper_edge": "#2e2f34", "shadow": "#0a0a0b",
        "ink": "#e4e4e7", "staff": "#5d5e66", "bar": "#b9b9c0", "mnum": "#a1a1aa", "bad": "#ff6b7a",
        "sub": "#b4b4bb", "meta": "#9a9aa3", "cursor": "#ff5c70", "column": "#5a2a33", "hover": "#8a3b48",
        "sel_fill": "#3b1e25", "sel_line": "#ff5c70",
    },
}
# Map the print colours used by the layout onto the screen palette.
INK_ROLES = {"#111111": "ink", "#111": "ink", "#222222": "bar", "#8f8f8f": "staff", "#333": "mnum",
             "#d0021b": "bad", "#444": "sub", "#555": "meta", "#ffffff": "paper"}


def _load_prefs() -> dict:
    try:
        return json.loads(PREFS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        PREFS.write_text(json.dumps(prefs), encoding="utf-8")
    except OSError:
        pass
DURATIONS = [(1, "Whole"), (2, "Half"), (4, "♩ 1/4"), (8, "♪ 1/8"), (16, "1/16"), (32, "1/32")]
HINT = ("Click a string, type 0–9 for a fret  ·  x mute  ·  ←→ beats  ·  ↑↓ strings  ·  u/d strum  ·  "
        "[ ] duration  ·  Ctrl+D repeat beat  ·  drag notes to move (Ctrl+drag copies)")

SHORTCUTS = """\
Mouse
  Click            put the cursor on a string at a beat
  Drag a note      move it to another string or beat (hold Ctrl to copy)
  Double-click     above the staff: edit the chord name
  Drag across      select measures (Shift+click extends, Ctrl+A selects all)
  Right-click      measure menu: delete, copy, duplicate, clear
  Ctrl+wheel       zoom

Playback (tabs imported from YouTube)
  Ctrl+Space       play / pause from the cursor (the playhead follows the music)
  Click            while playing: jump there

Notes
  0–9              fret (type two digits quickly for 10–24)
  x                muted note (X)
  Delete           remove the note; on an empty beat, remove the beat
  Shift+Delete     remove the whole beat
  Alt+↑ / Alt+↓    move the note to the next string

Beats
  ← → / Space      previous / next beat (moving past the end adds a measure)
  ↑ ↓              previous / next string
  Alt+← / Alt+→    move the beat left / right
  [  ]             longer / shorter note
  .  t             dotted / triplet
  r                turn the beat into a rest
  u  d             up / down strum arrow
  c                edit the chord name
  Insert           insert a beat before the cursor
  Ctrl+D           copy this beat onto the next one

Measures
  Ctrl+← / Ctrl+→  previous / next measure
  Enter            add a measure after this one
  Ctrl+C / Ctrl+V  copy / paste measures (pasted after the current one)
  Delete           with measures selected: delete them
  Escape           clear the selection

File
  Ctrl+N / O / S   new / open / save       Ctrl+Shift+S  save as
  Ctrl+P           print                   Ctrl+Z / Ctrl+Y  undo / redo
"""


def _ensure_playable(song: Song) -> Song:
    if not any(True for _ in song.measures()):
        TabEditor(song)          # adds a section with one empty measure
    return song


class ScrollFrame(ttk.Frame):
    """A vertically scrollable frame for the tool panel."""

    def __init__(self, master, width: int):
        super().__init__(master)
        self.canvas = tk.Canvas(self, width=width, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=(10, 8, 10, 10))
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _wheel(self, event):
        over = self.winfo_containing(event.x_root, event.y_root)
        if over is not None and str(over).startswith(str(self)) and \
                self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class TabWriterApp:
    def __init__(self, root: tk.Tk, path: str | None = None):
        self.root = root
        self.path: Path | None = None
        self.source_text: str | None = None     # original file text, kept until the first visual edit
        self.editor = TabEditor()
        self.zoom = 1.0
        self.dpi = root.winfo_fpixels("1i") / 96.0
        self.frames: list[tuple[SystemLayout, float]] = []   # (system, y offset in page units)
        self.section_bars: list[tuple[int, float, float]] = []
        self.paper_x = 0.0
        self.paper_y = 20.0
        self.drag: dict | None = None
        self.fonts: dict = {}
        self._chord_target: Cursor | None = None
        self._section_target: Cursor | None = None
        self._source_job = None
        self._source_updating = False
        self.prefs = _load_prefs()
        self.dark = tk.BooleanVar(value=self.prefs.get("dark", True))
        self.pal = PALETTES["dark" if self.dark.get() else "light"]
        self.sel: tuple[int, int] | None = None      # selected measure range (first, last)
        self.player: playback.Player | None = None
        self.timeline: playback.Timeline | None = None
        self.play_job = None
        self.play_from: Cursor | None = None
        self.slot_cache: dict = {}
        self.sel_anchor: int | None = None

        root.title("Tab Writer")
        # A large, ordinary window: most of the screen, centred, not maximised.
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        width, height = max(900, int(screen_w * 0.85)), max(600, int(screen_h * 0.85))
        root.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 2 - 20)}")
        root.minsize(900, 600)
        self._style()
        self._menu()
        self._songbar()
        self._body()
        self._statusbar()
        self._context_menu()
        self._bindings()
        self.apply_theme()
        root.protocol("WM_DELETE_WINDOW", self.quit)

        if path:
            self.open_path(Path(path), create=True)
        else:
            self.refresh()
        self.canvas.focus_set()

    # ------------------------------------------------------------------ UI construction
    def _style(self) -> None:
        self.style = ttk.Style(self.root)
        self.light_theme = self.style.theme_use()

    def apply_theme(self) -> None:
        """Switch the whole window between light and dark."""
        dark = self.dark.get()
        pal = self.pal = PALETTES["dark" if dark else "light"]
        st = self.style
        if dark:
            st.theme_use("clam")
            st.configure(".", background=pal["ui"], foreground=pal["fg"], fieldbackground=pal["field"],
                         bordercolor=pal["border"], lightcolor=pal["ui"], darkcolor=pal["ui"],
                         troughcolor=pal["ui"], focuscolor=pal["cursor"], selectbackground=ACCENT,
                         selectforeground="#ffffff", insertcolor=pal["fg"], arrowcolor=pal["fg"])
            st.configure("TButton", background=pal["field"], bordercolor=pal["border"])
            st.map("TButton", background=[("pressed", pal["border"]), ("active", pal["hover_ui"])])
            st.configure("Toolbutton", background=pal["ui"], padding=(4, 3))
            st.map("Toolbutton", background=[("selected", ACCENT), ("active", pal["hover_ui"])],
                   foreground=[("selected", "#ffffff")])
            for w in ("TEntry", "TCombobox", "TSpinbox"):
                st.configure(w, fieldbackground=pal["field"], foreground=pal["fg"], background=pal["field"],
                             insertcolor=pal["fg"], arrowcolor=pal["fg"])
                st.map(w, fieldbackground=[("readonly", pal["field"])], foreground=[("disabled", pal["muted"])])
            st.configure("TCheckbutton", background=pal["ui"], indicatorbackground=pal["field"])
            st.map("TCheckbutton", background=[("active", pal["ui"])], indicatorbackground=[("selected", ACCENT)])
            st.configure("TLabelframe", background=pal["ui"], bordercolor=pal["border"])
            st.configure("TScrollbar", background=pal["field"], troughcolor=pal["ui"], bordercolor=pal["ui"],
                         arrowcolor=pal["muted"])
            st.map("TScrollbar", background=[("active", pal["hover_ui"])])
            st.configure("TSeparator", background=pal["border"])
            st.configure("Treeview", background=pal["field"], fieldbackground=pal["field"], foreground=pal["fg"],
                         bordercolor=pal["border"])
            st.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
            st.configure("Treeview.Heading", background=pal["ui"], foreground=pal["fg"], bordercolor=pal["border"])
            st.map("Treeview.Heading", background=[("active", pal["hover_ui"])])
            st.configure("TRadiobutton", background=pal["ui"], indicatorbackground=pal["field"])
            st.map("TRadiobutton", background=[("active", pal["ui"])], indicatorbackground=[("selected", ACCENT)])
            st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=pal["field"],
                         bordercolor=pal["border"], lightcolor=ACCENT, darkcolor=ACCENT)
            st.configure("TLabelframe.Label", background=pal["ui"], foreground=pal["fg"])
        else:
            st.theme_use(self.light_theme)
        st.configure("Section.TLabelframe.Label", font=("Segoe UI", 9, "bold"))
        st.configure("Small.TButton", padding=(2, 1))
        st.configure("Status.TLabel", foreground=pal["muted"])
        st.configure("Bad.TLabel", foreground=pal["bad"])

        r = self.root
        menu_colors = ((pal["field"], pal["fg"], ACCENT) if dark else ("SystemMenu", "SystemMenuText", "SystemHighlight"))
        for pattern, value in (("*TCombobox*Listbox.background", pal["field"]),
                               ("*TCombobox*Listbox.foreground", pal["fg"]),
                               ("*TCombobox*Listbox.selectBackground", ACCENT),
                               ("*Menu.background", menu_colors[0]),
                               ("*Menu.foreground", menu_colors[1]),
                               ("*Menu.activeBackground", menu_colors[2])):
            r.option_add(pattern, value)
        r.configure(background=pal["ui"])
        self.tools.canvas.configure(background=pal["ui"])
        self.canvas.configure(background=pal["canvas"])
        for w in (self.problems, self.source):
            w.configure(background=pal["field"], foreground=pal["fg"], selectbackground=ACCENT,
                        selectforeground="#ffffff")
        self.source.configure(insertbackground=pal["fg"])
        self.context.configure(background=menu_colors[0], foreground=menu_colors[1], activebackground=ACCENT,
                               activeforeground="#ffffff")
        self._dark_titlebar(dark)
        self.prefs["dark"] = dark
        self.save_prefs()
        self.redraw()

    def _dark_titlebar(self, dark: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(1 if dark else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def save_prefs(self) -> None:
        _save_prefs(self.prefs)

    def ink(self, color: str) -> str:
        """Screen colour for a print colour."""
        role = INK_ROLES.get(color.lower())
        return self.pal[role] if role else color

    def _context_menu(self) -> None:
        self.context = tk.Menu(self.root, tearoff=False)

    def _menu(self) -> None:
        bar = tk.Menu(self.root)
        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="New", accelerator="Ctrl+N", command=self.new)
        m.add_command(label="Open…", accelerator="Ctrl+O", command=self.open)
        m.add_command(label="Import from YouTube…", accelerator="Ctrl+I", command=self.import_youtube)
        m.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        m.add_command(label="Save As…", accelerator="Ctrl+Shift+S", command=self.save_as)
        m.add_separator()
        m.add_command(label="Print…", accelerator="Ctrl+P", command=self.print_sheet)
        m.add_command(label="Export PDF…", command=self.export_pdf)
        m.add_command(label="Export HTML…", command=self.export_html)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        bar.add_cascade(label="File", menu=m)

        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Undo", accelerator="Ctrl+Z", command=lambda: self.run(self.editor.undo))
        m.add_command(label="Redo", accelerator="Ctrl+Y", command=lambda: self.run(self.editor.redo))
        m.add_separator()
        m.add_command(label="Copy measure(s)", accelerator="Ctrl+C", command=self.copy_measure)
        m.add_command(label="Paste measure", accelerator="Ctrl+V", command=lambda: self.run(self.editor.paste_measure))
        m.add_command(label="Add measure", accelerator="Enter", command=lambda: self.run(self.editor.add_measure))
        m.add_command(label="Delete measure(s)", accelerator="Del", command=self.delete_measures)
        m.add_command(label="Select all measures", accelerator="Ctrl+A", command=self.select_all)
        bar.add_cascade(label="Edit", menu=m)

        m = tk.Menu(bar, tearoff=False)
        self.show_source = tk.BooleanVar(value=False)
        m.add_checkbutton(label="Text source", variable=self.show_source, command=self.toggle_source)
        m.add_checkbutton(label="Dark mode", variable=self.dark, command=self.apply_theme)
        m.add_separator()
        m.add_command(label="Zoom in", accelerator="Ctrl+=", command=lambda: self.set_zoom(self.zoom * 1.15))
        m.add_command(label="Zoom out", accelerator="Ctrl+-", command=lambda: self.set_zoom(self.zoom / 1.15))
        m.add_command(label="Actual size", accelerator="Ctrl+0", command=lambda: self.set_zoom(1.0))
        bar.add_cascade(label="View", menu=m)

        m = tk.Menu(bar, tearoff=False)
        m.add_command(label="Keyboard shortcuts", accelerator="F1", command=self.show_shortcuts)
        bar.add_cascade(label="Help", menu=m)
        self.root.config(menu=bar)

    def _songbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 8, 10, 6))
        bar.pack(side="top", fill="x")
        self.vars = {k: tk.StringVar() for k in ("title", "subtitle", "artist", "capo", "time", "tempo", "per_line")}
        self.field_widgets: dict = {}

        def field(label, key, width, widget="entry"):
            box = ttk.Frame(bar)
            box.pack(side="left", padx=(0, 12))
            ttk.Label(box, text=label, style="Status.TLabel").pack(anchor="w")
            if widget == "spin":
                w = ttk.Spinbox(box, textvariable=self.vars[key], width=width, from_=0,
                                to={"capo": 24, "per_line": 8}.get(key, 400), command=lambda: self.commit_field(key))
            elif widget == "combo":
                w = ttk.Combobox(box, textvariable=self.vars[key], width=width, values=TIME_SIGNATURES)
                w.bind("<<ComboboxSelected>>", lambda e: self.commit_field(key))
            else:
                w = ttk.Entry(box, textvariable=self.vars[key], width=width)
            w.pack(anchor="w")
            self.field_widgets[key] = w
            w.bind("<Return>", lambda e: (self.commit_field(key), self.canvas.focus_set()))
            w.bind("<FocusOut>", lambda e: self.commit_field(key))
            return w

        field("Title", "title", 28)
        field("Subtitle", "subtitle", 18)
        field("Artist", "artist", 18)
        field("Capo", "capo", 5, "spin")
        field("Time", "time", 6, "combo")
        field("Tempo (bpm)", "tempo", 6, "spin")
        field("Measures per line", "per_line", 4, "spin")
        ttk.Separator(self.root, orient="horizontal").pack(side="top", fill="x")

    def _body(self) -> None:
        body = ttk.Frame(self.root)
        body.pack(side="top", fill="both", expand=True)
        self.body_frame = body
        self.tools = ScrollFrame(body, width=262)
        self.tools.pack(side="left", fill="y")
        ttk.Separator(body, orient="vertical").pack(side="left", fill="y")
        self._tool_panel(self.tools.inner)

        self.source_frame = ttk.Frame(body)
        ttk.Label(self.source_frame, text="Text source (edits apply as you type)",
                  style="Status.TLabel", padding=(8, 6)).pack(side="top", anchor="w")
        self.source = tk.Text(self.source_frame, width=48, wrap="none", undo=True, font=("Consolas", 10),
                              borderwidth=0, padx=8, pady=6)
        sx = ttk.Scrollbar(self.source_frame, orient="horizontal", command=self.source.xview)
        sy = ttk.Scrollbar(self.source_frame, orient="vertical", command=self.source.yview)
        self.source.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.source.pack(side="left", fill="both", expand=True)
        self.source.bind("<<Modified>>", self.on_source_modified)

        sheet = ttk.Frame(body)
        sheet.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(sheet, background=PALETTES["dark"]["canvas"], highlightthickness=0, takefocus=True)
        vbar = ttk.Scrollbar(sheet, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(sheet, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self._transport(sheet)
        vbar.pack(side="right", fill="y")
        hbar.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.sheet_frame = sheet

    def _transport(self, parent) -> None:
        bar = self.transport = ttk.Frame(parent, padding=(10, 6))
        self.play_btn = ttk.Button(bar, text="\u25b6  Play", width=10, command=self.toggle_play, takefocus=False)
        self.play_btn.pack(side="left")
        ttk.Button(bar, text="\u25a0  Stop", width=8, command=self.stop_playback, takefocus=False).pack(side="left", padx=(6, 0))
        ttk.Label(bar, text="Track").pack(side="left", padx=(16, 4))
        self.track_var = tk.StringVar(value=playback.TRACKS["mix"])
        track = ttk.Combobox(bar, textvariable=self.track_var, values=list(playback.TRACKS.values()), width=12,
                             state="readonly")
        track.pack(side="left")
        track.bind("<<ComboboxSelected>>", lambda e: (self.change_track(), self.canvas.focus_set()))
        self.follow_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Follow along", variable=self.follow_var, takefocus=False).pack(side="left", padx=(16, 0))
        self.time_label = ttk.Label(bar, text="", style="Status.TLabel")
        self.time_label.pack(side="left", padx=(16, 0))
        ttk.Label(bar, text="Plays from the cursor  ·  Ctrl+Space play/pause  ·  click the tab to jump",
                  style="Status.TLabel").pack(side="right")
        self._transport_shown = False

    def _group(self, parent, title: str) -> ttk.Labelframe:
        f = ttk.Labelframe(parent, text=title, style="Section.TLabelframe", padding=(8, 4, 8, 8))
        f.pack(side="top", fill="x", pady=(0, 8))
        return f

    def _buttons(self, parent, specs, columns: int, style="Small.TButton"):
        for i, (label, cmd) in enumerate(specs):
            b = ttk.Button(parent, text=label, command=cmd, style=style, takefocus=False)
            b.grid(row=i // columns, column=i % columns, sticky="ew", padx=1, pady=1)
        for c in range(columns):
            parent.columnconfigure(c, weight=1, uniform="col")

    def _tool_panel(self, p) -> None:
        g = self._group(p, "Fret")
        frets = [(str(i), lambda i=i: self.run(self.editor.set_fret_value, i)) for i in range(13)]
        frets += [("X", lambda: self.run(self.editor.mute)), ("Del", lambda: self.run(self.editor.delete))]
        self._buttons(g, frets, 5)

        g = self._group(p, "Duration")
        self.base_var = tk.IntVar(value=4)
        row = ttk.Frame(g)
        row.pack(fill="x")
        for i, (base, label) in enumerate(DURATIONS):
            rb = ttk.Radiobutton(row, text=label, value=base, variable=self.base_var, style="Toolbutton",
                                 takefocus=False, command=lambda b=base: self.run(self.editor.set_duration, base=b))
            rb.grid(row=i // 3, column=i % 3, sticky="ew", padx=1, pady=1)
        for c in range(3):
            row.columnconfigure(c, weight=1, uniform="d")
        row2 = ttk.Frame(g)
        row2.pack(fill="x", pady=(4, 0))
        self.dotted_var, self.triplet_var = tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(row2, text="Dotted", variable=self.dotted_var, style="Toolbutton", takefocus=False,
                        command=lambda: self.run(self.editor.set_duration, dotted=self.dotted_var.get())
                        ).grid(row=0, column=0, sticky="ew", padx=1)
        ttk.Checkbutton(row2, text="Triplet", variable=self.triplet_var, style="Toolbutton", takefocus=False,
                        command=lambda: self.run(self.editor.set_duration, triplet=self.triplet_var.get())
                        ).grid(row=0, column=1, sticky="ew", padx=1)
        ttk.Button(row2, text="Rest", style="Small.TButton", takefocus=False,
                   command=lambda: self.run(self.editor.make_rest)).grid(row=0, column=2, sticky="ew", padx=1)
        for c in range(3):
            row2.columnconfigure(c, weight=1, uniform="d2")

        g = self._group(p, "Strum arrow")
        self.up_var, self.down_var = tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(g, text="↑ Up", variable=self.up_var, style="Toolbutton", takefocus=False,
                        command=lambda: self.run(self.editor.toggle_strum, "up")).grid(row=0, column=0, sticky="ew", padx=1)
        ttk.Checkbutton(g, text="↓ Down", variable=self.down_var, style="Toolbutton", takefocus=False,
                        command=lambda: self.run(self.editor.toggle_strum, "down")).grid(row=0, column=1, sticky="ew", padx=1)
        g.columnconfigure(0, weight=1, uniform="s")
        g.columnconfigure(1, weight=1, uniform="s")

        g = self._group(p, "Chord")
        top = ttk.Frame(g)
        top.pack(fill="x")
        ttk.Label(top, text="Name above beat").pack(side="left")
        self.chord_var = tk.StringVar()
        self.chord_entry = ttk.Entry(top, textvariable=self.chord_var, width=10)
        self.chord_entry.pack(side="right")
        self.chord_entry.bind("<FocusIn>", lambda e: setattr(self, "_chord_target", Cursor(**vars(self.editor.cursor))))
        self.chord_entry.bind("<Return>", lambda e: (self.commit_chord(), self.canvas.focus_set()))
        self.chord_entry.bind("<FocusOut>", lambda e: self.commit_chord())
        self.chord_entry.bind("<Escape>", lambda e: self.canvas.focus_set())
        ttk.Label(g, text="Click a shape to put it on the current beat:", style="Status.TLabel",
                  wraplength=230).pack(anchor="w", pady=(6, 2))
        self.shapes_frame = ttk.Frame(g)
        self.shapes_frame.pack(fill="x")
        self._buttons(self.shapes_frame, [(name, lambda n=name, s=shape: self.run(self.editor.apply_shape, n, s))
                                          for name, shape in CHORD_SHAPES.items()], 4)

        g = self._group(p, "Beat")
        self._buttons(g, [("Insert", lambda: self.run(self.editor.insert_beat)),
                          ("Repeat →", lambda: self.run(self.editor.repeat_beat)),
                          ("Delete", lambda: self.run(self.editor.delete_beat)),
                          ("◀ Move", lambda: self.run(self.editor.swap_beat, -1)),
                          ("Move ▶", lambda: self.run(self.editor.swap_beat, 1))], 3)

        g = self._group(p, "Measure")
        row = ttk.Frame(g)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="Time from here").pack(side="left")
        self.mtime_var = tk.StringVar()
        cb = ttk.Combobox(row, textvariable=self.mtime_var, values=TIME_SIGNATURES, width=6)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda e: self.commit_measure_time())
        cb.bind("<Return>", lambda e: (self.commit_measure_time(), self.canvas.focus_set()))
        grid_row = ttk.Frame(g)
        grid_row.pack(fill="x", pady=(0, 2))
        ttk.Label(grid_row, text="Grid").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.grid_var = tk.IntVar(value=8)
        for i, (grid, label) in enumerate(((1, "1/1"), (2, "1/2"), (4, "♩ 1/4"), (8, "♪ 1/8"))):
            ttk.Radiobutton(grid_row, text=label, value=grid, variable=self.grid_var, style="Toolbutton",
                            takefocus=False, command=lambda v=grid: self.run(self.editor.set_grid, v)
                            ).grid(row=0, column=i + 1, sticky="ew", padx=1)
            grid_row.columnconfigure(i + 1, weight=1, uniform="g")
        ttk.Label(g, text="Empty slots in new measures; also re-divides the rests in this measure.",
                  style="Status.TLabel", wraplength=230).pack(anchor="w", pady=(0, 6))
        btns = ttk.Frame(g)
        btns.pack(fill="x")
        self._buttons(btns, [("Add", lambda: self.run(self.editor.add_measure)),
                             ("Duplicate", self.duplicate_measures),
                             ("Delete", self.delete_measures),
                             ("Copy", self.copy_measure),
                             ("Paste", lambda: self.run(self.editor.paste_measure)),
                             ("Clear", self.clear_measures)], 3)
        self.sel_label = ttk.Label(g, style="Status.TLabel", wraplength=230)
        self.sel_label.pack(anchor="w", pady=(4, 0))
        self.break_var = tk.BooleanVar()
        ttk.Checkbutton(g, text="New line after this measure", variable=self.break_var, takefocus=False,
                        command=lambda: self.run(self.editor.toggle_line_break)).pack(anchor="w", pady=(6, 0))

        g = self._group(p, "Section")
        row = ttk.Frame(g)
        row.pack(fill="x", pady=(0, 4))
        ttk.Label(row, text="Name").pack(side="left")
        self.section_var = tk.StringVar()
        self.section_entry = ttk.Entry(row, textvariable=self.section_var, width=18)
        self.section_entry.pack(side="right")
        self.section_entry.bind("<FocusIn>", lambda e: setattr(self, "_section_target", Cursor(**vars(self.editor.cursor))))
        self.section_entry.bind("<Return>", lambda e: (self.commit_section(), self.canvas.focus_set()))
        self.section_entry.bind("<FocusOut>", lambda e: self.commit_section())
        btns = ttk.Frame(g)
        btns.pack(fill="x")
        self._buttons(btns, [("New section here", self.new_section_here),
                             ("Add at end", lambda: self.new_section_here(at_end=True))], 2)

        g = self._group(p, "Problems")
        self.problems = tk.Listbox(g, height=4, activestyle="none", borderwidth=0, highlightthickness=0)
        self.problems.pack(fill="x")
        self.problems.bind("<<ListboxSelect>>", self.on_problem)
        self.problem_items: list = []

    def _statusbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 3))
        bar.pack(side="bottom", fill="x", before=self.body_frame)
        ttk.Separator(self.root, orient="horizontal").pack(side="bottom", fill="x", before=self.body_frame)
        self.pos_label = ttk.Label(bar, width=46)
        self.pos_label.pack(side="left")
        self.msg_label = ttk.Label(bar, text=HINT, style="Status.TLabel")
        self.msg_label.pack(side="left", fill="x", expand=True)
        self.zoom_label = ttk.Label(bar, style="Status.TLabel")
        self.zoom_label.pack(side="right")

    def _bindings(self) -> None:
        c = self.canvas
        c.bind("<Button-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<Double-Button-1>", self.on_double)
        c.bind("<Motion>", self.on_hover)
        c.bind("<Leave>", lambda e: c.delete("hover"))
        c.bind("<MouseWheel>", self.on_wheel)
        c.bind("<Key>", self.on_key)
        c.bind("<Button-3>", self.on_right_click)
        c.bind("<Configure>", self.on_resize)
        r = self.root
        for seq, fn in (("<Control-n>", self.new), ("<Control-o>", self.open), ("<Control-s>", self.save),
                        ("<Control-S>", self.save_as), ("<Control-i>", self.import_youtube), ("<Control-p>", self.print_sheet), ("<F1>", self.show_shortcuts),
                        ("<Control-equal>", lambda: self.set_zoom(self.zoom * 1.15)),
                        ("<Control-plus>", lambda: self.set_zoom(self.zoom * 1.15)),
                        ("<Control-minus>", lambda: self.set_zoom(self.zoom / 1.15)),
                        ("<Control-0>", lambda: self.set_zoom(1.0))):
            r.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        for seq, fn in (("<Control-z>", self.editor_undo), ("<Control-y>", self.editor_redo), ("<Control-Z>", self.editor_redo)):
            r.bind(seq, lambda e, fn=fn: fn(e))

    # ------------------------------------------------------------------ geometry
    @property
    def scale(self) -> float:
        """Pixels per layout unit. Zoom 100% fits the page to the window width."""
        width = max(300, self.canvas.winfo_width() - 40)
        fit = min(width / (WIDTH + 2 * PAPER_SIDE), 1.5 * self.dpi)
        return fit * self.zoom

    def X(self, u: float) -> float:
        return self.paper_x + (PAPER_SIDE + u) * self.scale

    def Y(self, u: float) -> float:
        return self.paper_y + u * self.scale

    def font(self, family: str, size: float, bold=False, italic=False) -> tkfont.Font:
        px = max(6, round(size * self.scale))
        key = (family, px, bold, italic)
        if key not in self.fonts:
            self.fonts[key] = tkfont.Font(family=FAMILIES.get(family, family), size=-px,
                                          weight="bold" if bold else "normal", slant="italic" if italic else "roman")
        return self.fonts[key]

    # ------------------------------------------------------------------ drawing
    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        song = self.editor.song
        sc = self.scale
        page_w = (WIDTH + 2 * PAPER_SIDE) * sc
        self.paper_x = max(20.0, (c.winfo_width() - page_w) / 2)
        self.frames, self.section_bars = [], []

        items_y = PAPER_TOP
        title, sub, meta = header_lines(song)
        center = WIDTH / 2
        header = []
        if title:
            header.append((items_y + 14, title, ("sans", 26, True, False), "#111"))
            items_y += 36
        if sub:
            header.append((items_y + 8, sub, ("sans", 14, False, False), "#444"))
            items_y += 22
        if meta:
            header.append((items_y + 7, "   ·   ".join(meta), ("sans", 11, False, False), "#555"))
            items_y += 20
        if header:
            items_y += 16

        layout = layout_song(song)
        drawn_sections = 0
        page_items = []
        for sec in layout:
            if sec.title:
                if drawn_sections:
                    items_y += 22
                self.section_bars.append((sec.index, items_y, items_y + 26))
                page_items.append(("bar", sec.title, items_y))
                items_y += 26 + 8
            drawn_sections += 1
            for sys_ in sec.systems:
                self.frames.append((sys_, items_y))
                items_y += sys_.height + 16
        page_h = max(items_y + 40, (WIDTH + 2 * PAPER_SIDE) * 1.414)

        # paper with a soft shadow
        x0, y0 = self.paper_x, self.paper_y
        pal = self.pal
        c.create_rectangle(x0 + 3, y0 + 4, x0 + page_w + 3, y0 + page_h * sc + 4, fill=pal["shadow"], outline="")
        c.create_rectangle(x0, y0, x0 + page_w, y0 + page_h * sc, fill=pal["paper"], outline=pal["paper_edge"],
                           tags="paper")

        for y, text, (fam, size, bold, italic), color in header:
            c.create_text(self.X(center), self.Y(y), text=text, font=self.font(fam, size, bold, italic),
                          fill=self.ink(color))
        for _, text, y in page_items:
            c.create_rectangle(self.X(0), self.Y(y), self.X(WIDTH), self.Y(y + 26), fill=ACCENT, outline="",
                               tags="sectionbar")
            c.create_text(self.X(center), self.Y(y + 13), text=text, fill="white",
                          font=self.font("sans", 16, True), tags="sectionbar")
        for sys_, oy in self.frames:
            self._draw_system(sys_, oy)

        c.configure(scrollregion=(0, 0, max(c.winfo_width(), x0 + page_w + 20), y0 + page_h * sc + 30))
        self.slot_cache = {}
        for sys_, oy in self.frames:
            for h in sys_.hits:
                self.slot_cache[(h.m, h.e)] = (sys_, oy, h.xn, h.x, h.w)
        self.timeline = None
        self.draw_selection()
        self.draw_cursor()
        if self.player and self.player.playing:
            self.draw_playhead(self.player.position())

    def _draw_system(self, sys_: SystemLayout, oy: float) -> None:
        c, sc = self.canvas, self.scale
        X, Y = self.X, (lambda u: self.Y(oy + u))
        for item in sys_.items:
            kind, cls = item[0], item[1]
            if kind == "line":
                color, w = LINE_STYLES.get(cls, ("#111", 1.0))
                c.create_line(X(item[2]), Y(item[3]), X(item[4]), Y(item[5]), fill=self.ink(color),
                              width=max(1.0, w * sc))
            elif kind == "rect":
                _, _, x, y, w, h = item
                c.create_rectangle(X(x), Y(y), X(x + w), Y(y + h), fill=self.ink(FILLS.get(cls, "#111")), outline="",
                                   tags="ko" if cls == "ko" else ())
            elif kind == "poly":
                pts = item[2]
                c.create_polygon([X(v) if i % 2 == 0 else Y(v) for i, v in enumerate(pts)],
                                 fill=self.ink(FILLS.get(cls, "#111")), outline="")
            elif kind == "pline":
                pts = item[2]
                color, w = LINE_STYLES.get(cls, ("#111", 1.3))
                c.create_line([X(v) if i % 2 == 0 else Y(v) for i, v in enumerate(pts)], fill=self.ink(color),
                              width=max(1.0, w * sc), capstyle="round", joinstyle="round")
            elif kind == "circle":
                _, _, cx, cy, r = item
                c.create_oval(X(cx - r), Y(cy - r), X(cx + r), Y(cy + r), fill=self.ink(FILLS.get(cls, "#111")),
                              outline="")
            elif kind == "text":
                _, _, x, y, s, size, anchor = item
                fam, default, bold, italic, color = TEXT_STYLES.get(cls, ("sans", 10, False, False, "#111"))
                size = size or default
                c.create_text(X(x), Y(y - size * 0.36), text=s, font=self.font(fam, size, bold, italic),
                              fill=self.ink(color), anchor="center" if anchor == "middle" else "w")

    def _slot(self, m: int, e: int):
        """Canvas geometry for a cursor position: (system, y offset, x of notes, slot x, slot width)."""
        for sys_, oy in self.frames:
            if not sys_.first - 1 <= m <= sys_.last - 1:
                continue
            for h in sys_.hits:
                if h.m == m and h.e == e:
                    return sys_, oy, h.xn, h.x, h.w
            for h in sys_.hits:
                if h.m == m and h.e is None:
                    return sys_, oy, h.xn, h.xn - 10, 20
        return None

    def _box(self, m: int, e: int, s: int, tag: str, color: str, width: float, dash=None, column=False) -> None:
        found = self._slot(m, e)
        if not found:
            return
        sys_, oy, xn, sx, sw = found
        y = oy + sys_.string_y(s)
        c = self.canvas
        if column:
            top, bot = oy + sys_.staff_top - GAP * 0.9, oy + sys_.string_y(sys_.strings) + GAP * 0.9
            c.create_rectangle(self.X(sx + 1), self.Y(top), self.X(sx + sw - 1), self.Y(bot), outline=self.pal["column"],
                               width=1, dash=(2, 2), tags=tag)
        c.create_rectangle(self.X(xn - 7), self.Y(y - 6.5), self.X(xn + 7), self.Y(y + 6.5), outline=color,
                           width=width, dash=dash, tags=tag)

    def draw_cursor(self, scroll: bool = False) -> None:
        self.canvas.delete("cursor")
        c = self.editor.cursor
        self._box(c.m, c.e, c.s, "cursor", self.pal["cursor"], 2, column=True)
        if scroll:
            self._scroll_to_cursor()

    def _scroll_to_cursor(self) -> None:
        bbox = self.canvas.bbox("cursor")
        if not bbox:
            return
        c = self.canvas
        top, bottom = c.canvasy(0), c.canvasy(c.winfo_height())
        total = float(c.cget("scrollregion").split()[3])
        if bbox[1] < top + 30:
            c.yview_moveto(max(0.0, (bbox[1] - 80) / total))
        elif bbox[3] > bottom - 30:
            c.yview_moveto(max(0.0, (bbox[3] - c.winfo_height() + 80) / total))

    # ------------------------------------------------------------------ mouse
    def locate(self, event) -> Cursor | None:
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        ux = (cx - self.paper_x) / self.scale - PAPER_SIDE
        page_y = (cy - self.paper_y) / self.scale
        for sys_, oy in self.frames:
            uy = page_y - oy
            if not 0 <= uy <= sys_.height:
                continue
            s = max(1, min(sys_.strings, round((uy - sys_.staff_top) / GAP) + 1))
            for h in sys_.hits:
                if h.e is not None and h.x <= ux < h.x + h.w:
                    return Cursor(h.m, h.e, s)
            for h in sys_.hits:
                if h.e is None and h.x <= ux < h.x + h.w:
                    events = [k for k in sys_.hits if k.m == h.m and k.e is not None]
                    after = not events or ux >= max(k.x + k.w for k in events)
                    return Cursor(h.m, len(events) if after else 0, s)
        return None

    def section_at(self, event) -> int | None:
        page_y = (self.canvas.canvasy(event.y) - self.paper_y) / self.scale
        for index, top, bottom in self.section_bars:
            if top <= page_y <= bottom:
                return index
        return None

    def on_press(self, event) -> None:
        self.canvas.focus_set()
        loc = self.locate(event)
        if loc is None:
            self.set_selection(None)
            index = self.section_at(event)
            if index is not None:
                self.goto_section(index)
            return
        if event.state & 0x1:                      # Shift+click extends a measure selection
            anchor = self.sel_anchor if self.sel_anchor is not None else self.editor.cursor.m
            self.editor.set_cursor(loc.m, loc.e, loc.s)
            self.set_selection((anchor, loc.m), anchor)
            self.refresh(full=False)
            self.flash_selection()
            return
        self.set_selection(None)
        self.editor.set_cursor(loc.m, loc.e, loc.s)
        if self.player and self.player.playing:
            self.seek_to_cursor()
        if self.editor.note():
            self.drag = {"src": Cursor(**vars(self.editor.cursor)), "dst": None}
        else:                                      # dragging from an empty spot selects measures
            self.drag = {"select": loc.m, "x": event.x, "y": event.y}
        self.refresh(full=False)

    def on_drag(self, event) -> None:
        if not self.drag:
            return
        if "select" in self.drag:
            if self.sel is None and abs(event.x - self.drag["x"]) + abs(event.y - self.drag["y"]) < 8:
                return
            loc = self.locate(event)
            if loc is not None:
                self.set_selection((self.drag["select"], loc.m), self.drag["select"])
            return
        loc = self.locate(event)
        self.canvas.delete("ghost")
        src = self.drag["src"]
        if loc and (loc.m, loc.e, loc.s) != (src.m, src.e, src.s):
            self.drag["dst"] = loc
            self._box(loc.m, loc.e, loc.s, "ghost", self.pal["cursor"], 2, dash=(3, 2))
            self.canvas.configure(cursor="fleur")
        else:
            self.drag["dst"] = None

    def on_release(self, event) -> None:
        drag, self.drag = self.drag, None
        self.canvas.delete("ghost")
        self.canvas.configure(cursor="")
        if drag and "select" in drag:
            if self.sel:
                self.flash_selection()
            return
        if drag and drag["dst"]:
            copy = bool(event.state & 0x4)
            self.run(self.editor.move_note, drag["src"], drag["dst"], copy)

    def on_double(self, event) -> None:
        loc = self.locate(event)
        if loc is None:
            return
        for sys_, oy in self.frames:
            if sys_.first - 1 <= loc.m <= sys_.last - 1:
                if (self.canvas.canvasy(event.y) - self.paper_y) / self.scale - oy < sys_.staff_top - GAP / 2:
                    self.focus_chord()
                return

    def on_hover(self, event) -> None:
        self.canvas.delete("hover")
        if self.drag:
            return
        loc = self.locate(event)
        if loc:
            self._box(loc.m, loc.e, loc.s, "hover", self.pal["hover"], 1)
            self.canvas.configure(cursor="hand2")
        else:
            self.canvas.configure(cursor="hand2" if self.section_at(event) is not None else "")

    def on_resize(self, event) -> None:
        width = event.width
        if getattr(self, "_last_width", None) != width:
            self._last_width = width
            self.fonts.clear()
            self.redraw()

    def on_wheel(self, event) -> None:
        if event.state & 0x4:
            self.set_zoom(self.zoom * (1.1 if event.delta > 0 else 1 / 1.1))
        else:
            self.canvas.yview_scroll(int(-event.delta / 120) * 3, "units")
        return "break"

    # ------------------------------------------------------------------ keyboard
    def on_key(self, event):
        ed = self.editor
        ctrl = bool(event.state & 0x4)
        shift = bool(event.state & 0x1)
        alt = bool(event.state & (0x20000 if sys.platform == "win32" else 0x8))
        k = event.keysym
        if self.sel and not ctrl and k in ("Delete", "BackSpace"):
            self.delete_measures()
            return "break"
        if k == "Escape":
            self.set_selection(None)
            return "break"
        if ctrl and k in ("a", "A"):
            self.select_all()
            return "break"
        if ctrl and k == "space":
            self.toggle_play()
            return "break"
        if ctrl:
            actions = {"d": ed.repeat_beat, "D": ed.repeat_beat, "v": ed.paste_measure, "V": ed.paste_measure,
                       "Left": lambda: self.nav(ed.move_measure, -1), "Right": lambda: self.nav(ed.move_measure, 1)}
            if k in ("c", "C"):
                self.copy_measure()
            elif k in actions:
                fn = actions[k]
                self.run(fn) if fn in (ed.repeat_beat, ed.paste_measure) else fn()
            else:
                return None
            return "break"
        digit = k[-1] if (k.isdigit() or (k.startswith("KP_") and k[-1].isdigit())) else None
        if digit is not None:
            self.run(ed.set_fret, int(digit))
        elif k in ("x", "X"):
            self.run(ed.mute)
        elif k in ("Delete", "BackSpace"):
            self.run(ed.delete_beat if shift else ed.delete)
        elif k in ("Left", "Right"):
            d = -1 if k == "Left" else 1
            if alt:
                self.run(ed.swap_beat, d)
            else:
                self.nav(ed.move_beat, d)
        elif k in ("Up", "Down"):
            d = -1 if k == "Up" else 1
            if alt:
                self.run(ed.move_note_to_string, d)
            else:
                self.nav(ed.move_string, d)
        elif k == "space":
            self.nav(ed.move_beat, -1 if shift else 1)
        elif k in ("u", "U"):
            self.run(ed.toggle_strum, "up")
        elif k in ("d", "D"):
            self.run(ed.toggle_strum, "down")
        elif k in ("r", "R"):
            self.run(ed.make_rest)
        elif k == "period":
            self.run(ed.set_duration, dotted=not ed.dotted)
        elif k in ("t", "T"):
            self.run(ed.set_duration, triplet=not ed.triplet)
        elif k in ("bracketleft", "bracketright"):
            order = [1, 2, 4, 8, 16, 32]
            i = order.index(ed.base) + (1 if k == "bracketright" else -1)
            if 0 <= i < len(order):
                self.run(ed.set_duration, base=order[i])
        elif k in ("c", "C"):
            self.focus_chord()
        elif k in ("Insert", "i", "I"):
            self.run(ed.insert_beat)
        elif k in ("Return", "KP_Enter"):
            self.run(ed.add_measure)
        else:
            return None
        return "break"

    def editor_undo(self, event):
        if self.root.focus_get() is self.source:
            return None
        self.run(self.editor.undo)
        return "break"

    def editor_redo(self, event):
        if self.root.focus_get() is self.source:
            return None
        self.run(self.editor.redo)
        return "break"

    # ------------------------------------------------------------------ actions
    def run(self, fn, *args, **kwargs) -> None:
        """Run an editing operation and refresh everything if it changed the song."""
        if fn(*args, **kwargs) is not False:
            self.source_text = None          # the model is now the source of truth
            self.refresh(scroll=True)
        else:
            self.refresh(full=False)

    def nav(self, fn, *args) -> None:
        before = len(self.editor.undo_stack)
        fn(*args)
        self.refresh(full=len(self.editor.undo_stack) != before, scroll=True)

    def refresh(self, full: bool = True, scroll: bool = False) -> None:
        if full:
            self.update_transport()
            self.redraw()
            self.update_source_panel()
        else:
            self.draw_cursor()
        if scroll:
            self._scroll_to_cursor()
        self.update_panels()

    def update_panels(self) -> None:
        ed = self.editor
        song = ed.song
        ev, ref = ed.event(), ed.ref()
        refs = ed.refs()
        values = {"title": song.title, "subtitle": song.subtitle, "artist": song.artist,
                  "capo": str(song.capo or 0), "tempo": str(song.tempo or ""), "per_line": str(song.per_line),
                  "time": "%d/%d" % refs[0].measure.time if refs else "4/4"}
        focused = self.root.focus_get()
        for key, value in values.items():
            if focused is not self.field_widgets.get(key):
                self._set_var(key, value)
        self.base_var.set(ed.base)
        self.grid_var.set(song.grid)
        self.dotted_var.set(ed.dotted)
        self.triplet_var.set(ed.triplet)
        self.up_var.set(bool(ev and ev.strum == "up"))
        self.down_var.set(bool(ev and ev.strum == "down"))
        if self.root.focus_get() is not self.chord_entry:
            self.chord_var.set((ev.chord or "") if ev else "")
        if ref:
            self.mtime_var.set("%d/%d" % ref.measure.time)
            self.break_var.set(ed.line_break_after())
            if self.root.focus_get() is not self.section_entry:
                self.section_var.set(ref.section.title or "")
        if self.sel:
            self.sel_label.configure(text=f"Selected: {self._sel_text()} — Delete / Copy / Duplicate / Clear "
                                          "act on the selection")
        else:
            self.sel_label.configure(text="Tip: drag across the sheet to select several measures")
        shapes_ok = song.tuning == STANDARD_TUNING
        for b in self.shapes_frame.winfo_children():
            b.state(["!disabled"] if shapes_ok else ["disabled"])

        c = ed.cursor
        if ref:
            n = len(ref.measure.events)
            beat = f"beat {c.e + 1} of {n}" if c.e < n else "after the last beat"
            note = ed.note()
            what = f"fret {note.label}" if note else ("rest" if ev and ev.rest else "empty")
            self.pos_label.configure(text=f"Measure {ref.measure.number} · {beat} · "
                                          f"string {c.s} ({ed.string_name(c.s)}) · {what}")
        self.zoom_label.configure(text=f"{round(self.zoom * 100)}%")

        self.problems.delete(0, "end")
        self.problem_items = sorted(song.problems, key=lambda p: (p.level != "error", p.measure or 0, p.line))
        for p in self.problem_items:
            where = f"m{p.measure}" if p.measure else f"line {p.line}"
            self.problems.insert("end", f"{'⚠' if p.level == 'warning' else '✖'} {where}: {p.message}")
        if not self.problem_items:
            self.problems.insert("end", "No problems")
        self.update_title()

    def _set_var(self, key: str, value: str) -> None:
        if self.vars[key].get() != value:
            self.vars[key].set(value)

    def update_title(self) -> None:
        name = self.path.name if self.path else "Untitled"
        self.root.title(f"{'• ' if self.editor.dirty else ''}{name} — Tab Writer")

    def flash(self, text: str) -> None:
        self.msg_label.configure(text=text)
        self.root.after(3500, lambda: self.msg_label.configure(text=HINT))

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(0.5, min(3.0, zoom))
        self.fonts.clear()
        self.redraw()
        self.update_panels()

    def commit_field(self, key: str) -> None:
        ed = self.editor
        raw = self.vars[key].get().strip()
        if key in ("title", "subtitle", "artist"):
            if raw != getattr(ed.song, key):
                self.run(ed.set_field, key, raw)
        elif key in ("capo", "tempo", "per_line"):
            value = int(raw) if raw.isdigit() else {"capo": 0, "per_line": 2}.get(key)
            if key == "per_line":
                value = min(value, 8)
            if value != getattr(ed.song, key):
                self.run(ed.set_field, key, value)
        elif key == "time":
            parsed = self._parse_time(raw)
            if parsed:
                self.run(ed.set_time, *parsed, from_start=True)
            else:
                self.update_panels()

    @staticmethod
    def _parse_time(raw: str):
        try:
            num, den = (int(v) for v in raw.split("/"))
        except ValueError:
            return None
        return (num, den) if 0 < num <= 32 and den in (1, 2, 4, 8, 16, 32) else None

    def commit_measure_time(self) -> None:
        parsed = self._parse_time(self.mtime_var.get().strip())
        if parsed:
            self.run(self.editor.set_time, *parsed)

    def _at(self, target: Cursor | None, fn, *args) -> None:
        """Apply an edit at a remembered cursor position (the entry may lose focus after the cursor moved)."""
        ed = self.editor
        if target is None:
            return self.run(fn, *args)
        here = Cursor(**vars(ed.cursor))
        ed.cursor = Cursor(**vars(target))
        ed.clamp()
        changed = fn(*args) is not False
        ed.cursor = here
        ed.clamp()
        if changed:
            self.source_text = None
            self.refresh()

    def commit_chord(self) -> None:
        ev_target = self._chord_target
        self._chord_target = None
        self._at(ev_target, self.editor.set_chord_name, self.chord_var.get())

    def commit_section(self) -> None:
        target = self._section_target
        self._section_target = None
        self._at(target, self.editor.rename_section, self.section_var.get())

    def focus_chord(self) -> None:
        self.chord_entry.focus_set()
        self.chord_entry.select_range(0, "end")

    def new_section_here(self, at_end: bool = False) -> None:
        if at_end:
            self.run(self.editor.add_section, "New section")
        else:
            self.run(self.editor.split_section, "New section")
        self.section_entry.focus_set()
        self.section_entry.select_range(0, "end")

    def goto_section(self, index: int) -> None:
        for i, r in enumerate(self.editor.refs()):
            if r.si == index:
                self.editor.set_cursor(i, 0, self.editor.cursor.s)
                break
        self.refresh(full=False)
        self.section_entry.focus_set()
        self.section_entry.select_range(0, "end")

    # ---- measure selection
    def set_selection(self, span, anchor: int | None = None) -> None:
        span = tuple(sorted(span)) if span else None
        if span == self.sel:
            return
        self.sel = span
        self.sel_anchor = anchor if span else None
        self.draw_selection()
        self.update_panels()

    def select_all(self) -> None:
        self.set_selection((0, len(self.editor.refs()) - 1), 0)
        self.flash_selection()

    def _span(self) -> tuple[int, int]:
        return self.sel if self.sel else (self.editor.cursor.m, self.editor.cursor.m)

    def _sel_text(self) -> str:
        a, b = self._span()
        return f"measure {a + 1}" if a == b else f"measures {a + 1}–{b + 1}"

    def flash_selection(self) -> None:
        a, b = self.sel
        n = b - a + 1
        self.flash(f"{n} measure{'s' if n > 1 else ''} selected — Delete removes "
                   f"{'them' if n > 1 else 'it'}, right-click for more, Esc clears.")

    def draw_selection(self) -> None:
        c = self.canvas
        c.delete("sel")
        for item in c.find_withtag("ko"):
            c.itemconfigure(item, fill=self.pal["paper"])
        if not self.sel:
            return
        a, b = self.sel
        for sys_, oy in self.frames:
            hits = [h for h in sys_.hits if h.e is None and a <= h.m <= b]
            if not hits:
                continue
            x1, x2 = min(h.x for h in hits), max(h.x + h.w for h in hits)
            y1, y2 = oy + sys_.staff_top - 18, oy + sys_.height - 2
            box = (self.X(x1), self.Y(y1), self.X(x2), self.Y(y2))
            fill = c.create_rectangle(*box, fill=self.pal["sel_fill"], outline="", tags="sel")
            c.tag_raise(fill, "paper")
            for item in c.find_overlapping(*box):
                if "ko" in c.gettags(item):
                    c.itemconfigure(item, fill=self.pal["sel_fill"])
            c.create_rectangle(*box, outline=self.pal["sel_line"], width=1.5, tags="sel")
        c.tag_raise("cursor")

    def on_right_click(self, event) -> None:
        self.canvas.focus_set()
        loc = self.locate(event)
        if loc is not None and not (self.sel and self.sel[0] <= loc.m <= self.sel[1]):
            self.set_selection(None)
            self.editor.set_cursor(loc.m, loc.e, loc.s)
            self.refresh(full=False)
        elif loc is None and not self.sel:
            return
        what = self._sel_text()
        menu = self.context
        menu.delete(0, "end")
        menu.add_command(label=f"Delete {what}", command=self.delete_measures)
        menu.add_command(label=f"Copy {what}", command=self.copy_measure)
        menu.add_command(label=f"Duplicate {what}", command=self.duplicate_measures)
        menu.add_command(label=f"Clear {what}", command=self.clear_measures)
        menu.add_separator()
        menu.add_command(label="Paste after this measure", command=lambda: self.run(self.editor.paste_measure),
                         state="normal" if self.editor.clipboard else "disabled")
        menu.add_command(label="Select all measures", command=self.select_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def delete_measures(self) -> None:
        a, b = self._span()
        n = b - a + 1
        self.set_selection(None)
        self.run(self.editor.delete_measure, a, b)
        self.flash(f"Deleted {n} measure{'s' if n > 1 else ''}. Ctrl+Z brings {'them' if n > 1 else 'it'} back.")

    def duplicate_measures(self) -> None:
        a, b = self._span()
        self.set_selection(None)
        self.run(self.editor.duplicate_measure, a, b)

    def clear_measures(self) -> None:
        a, b = self._span()
        self.run(self.editor.clear_measures, a, b)

    def copy_measure(self) -> None:
        what = self._sel_text()
        a, b = self._span()
        self.editor.copy_measure(a, b)
        self.flash(f"Copied {what}. Ctrl+V pastes after the current measure.")

    def on_problem(self, event) -> None:
        sel = self.problems.curselection()
        if not sel or sel[0] >= len(self.problem_items):
            return
        p = self.problem_items[sel[0]]
        if p.measure:
            self.editor.set_cursor(p.measure - 1, 0, self.editor.cursor.s)
            self.refresh(full=False, scroll=True)
        elif self.show_source.get():
            self.source.see(f"{p.line}.0")
            self.source.tag_remove("sel", "1.0", "end")
            self.source.tag_add("sel", f"{p.line}.0", f"{p.line}.end")
        self.canvas.focus_set()

    def show_shortcuts(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Keyboard shortcuts")
        win.transient(self.root)
        win.configure(background=self.pal["ui"])
        text = tk.Text(win, width=72, height=44, font=("Consolas", 10), borderwidth=0, padx=14, pady=12,
                       background=self.pal["field"], foreground=self.pal["fg"])
        text.insert("1.0", SHORTCUTS)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        ttk.Button(win, text="Close", command=win.destroy).pack(pady=8)

    # ------------------------------------------------------------------ text source panel
    def toggle_source(self) -> None:
        if self.show_source.get():
            self.source_frame.pack(side="right", fill="y", before=self.sheet_frame)
            self.update_source_panel(force=True)
        else:
            self.source_frame.pack_forget()

    def current_text(self) -> str:
        return self.source_text if self.source_text is not None else serialize(self.editor.song)

    def update_source_panel(self, force: bool = False) -> None:
        if not self.show_source.get() or (self.root.focus_get() is self.source and not force):
            return
        text = self.current_text()
        if self.source.get("1.0", "end-1c") == text:
            return
        self._source_updating = True
        top = self.source.yview()[0]
        self.source.delete("1.0", "end")
        self.source.insert("1.0", text)
        self.source.yview_moveto(top)
        self.source.edit_modified(False)
        self._source_updating = False

    def on_source_modified(self, event=None) -> None:
        if not self.source.edit_modified():
            return
        self.source.edit_modified(False)
        if self._source_updating:
            return
        if self._source_job:
            self.root.after_cancel(self._source_job)
        self._source_job = self.root.after(500, self.apply_source)

    def apply_source(self) -> None:
        self._source_job = None
        text = self.source.get("1.0", "end-1c")
        song = _ensure_playable(parse(text))
        self.editor.replace_song(song)
        self.source_text = text
        self.redraw()
        self.update_transport()
        self.update_panels()

    # ------------------------------------------------------------------ files
    def confirm_discard(self) -> bool:
        if not self.editor.dirty:
            return True
        answer = messagebox.askyesnocancel("Tab Writer", "Save changes before continuing?", parent=self.root)
        if answer is None:
            return False
        return self.save() if answer else True

    def new(self) -> None:
        if not self.confirm_discard():
            return
        self.path, self.source_text = None, None
        self.close_player()
        self.editor.load(Song())
        self.refresh()

    # ------------------------------------------------------------------ playback
    def update_transport(self) -> None:
        show = playback.has_timing(self.editor.song)
        if show and not self._transport_shown:
            self.transport.pack(side="top", fill="x", before=self.sheet_frame.pack_slaves()[0])
        elif not show and self._transport_shown:
            self.stop_playback()
            self.transport.pack_forget()
        self._transport_shown = show

    def _timeline(self) -> playback.Timeline | None:
        if self.timeline is None:
            self.timeline = playback.timeline(self.editor.song)
        return self.timeline

    def _track(self) -> str:
        return next((k for k, v in playback.TRACKS.items() if v == self.track_var.get()), "mix")

    def _ensure_player(self) -> bool:
        try:
            if self.player is None:
                self.player = playback.Player()
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            path = playback.audio_file(self.editor.song, self._track(), self.prefs.get("transcriber_dir"))
            self.player.load(path)
            return True
        except (playback.PlayerError, OSError) as exc:
            messagebox.showerror("Playback", str(exc), parent=self.root)
            return False
        finally:
            self.root.configure(cursor="")

    def toggle_play(self) -> None:
        if self.player and self.player.playing:
            self.pause_playback()
        else:
            self.start_playback()

    def _cursor_time(self) -> float:
        tl = self._timeline()
        c = self.editor.cursor
        t = tl.time_of(c.m, c.e) if tl else None
        return t if t is not None else (tl.times[0] if tl and tl.times else 0.0)

    def start_playback(self) -> None:
        if not self._timeline() or not self._ensure_player():
            return
        self.play_from = Cursor(**vars(self.editor.cursor))
        try:
            self.player.play(max(0.0, self._cursor_time() - 0.05))
        except playback.PlayerError as exc:
            messagebox.showerror("Playback", str(exc), parent=self.root)
            return
        self.play_btn.configure(text="\u23f8  Pause")
        self._tick()

    def seek_to_cursor(self) -> None:
        try:
            self.player.play(max(0.0, self._cursor_time() - 0.05))
        except playback.PlayerError:
            pass

    def pause_playback(self) -> None:
        """Pause and leave the edit cursor on the beat that was sounding, so Play carries on from there."""
        if not self.player:
            return
        t = self.player.position()
        self.player.pause()
        self._cancel_tick()
        tl = self._timeline()
        if tl:
            i = tl.index_at(t)
            if i >= 0:
                m, e = tl.slots[i]
                self.editor.set_cursor(m, e, self.editor.cursor.s)
        self.play_btn.configure(text="\u25b6  Play")
        self.canvas.delete("playhead")
        self.refresh(full=False)

    def stop_playback(self) -> None:
        if self.player and self.player.playing:
            self.player.pause()
        self._cancel_tick()
        self.canvas.delete("playhead")
        self.play_btn.configure(text="\u25b6  Play")
        if self.play_from is not None:
            c = self.play_from
            self.editor.set_cursor(c.m, c.e, c.s)
            self.play_from = None
            self.refresh(full=False)
        self.time_label.configure(text="")

    def close_player(self) -> None:
        self.stop_playback()
        if self.player:
            self.player.close()

    def change_track(self) -> None:
        if not (self.player and self.player.playing):
            return
        t = self.player.position()
        if self._ensure_player():
            self.player.play(t)

    def _cancel_tick(self) -> None:
        if self.play_job:
            self.root.after_cancel(self.play_job)
            self.play_job = None

    def _tick(self) -> None:
        self.play_job = None
        if not (self.player and self.player.playing):
            return
        t = self.player.position()
        tl = self._timeline()
        if self.player.finished() or (tl and t >= tl.end):
            self.stop_playback()
            return
        self.draw_playhead(t)
        total = self.player.length
        self.time_label.configure(text=f"{int(t // 60)}:{int(t % 60):02d} / {int(total // 60)}:{int(total % 60):02d}")
        self.play_job = self.root.after(30, self._tick)

    def draw_playhead(self, t: float) -> None:
        c = self.canvas
        c.delete("playhead")
        tl = self._timeline()
        if not tl:
            return
        i = tl.index_at(t)
        if i < 0:
            return
        m, e = tl.slots[i]
        here = self.slot_cache.get((m, e))
        if not here:
            return
        sys_, oy, xn, sx, sw = here
        # Glide towards the next beat, or the end of the bar if the next beat is on another line.
        x = xn
        t_next = tl.times[i + 1] if i + 1 < len(tl.times) else tl.end
        nxt = self.slot_cache.get(tl.slots[i + 1]) if i + 1 < len(tl.slots) else None
        if nxt and nxt[0] is sys_:
            x_next = nxt[2]
        else:
            bar = self.slot_cache.get((m, None))
            x_next = bar[3] + bar[4] - 4 if bar else xn
        if t_next > tl.times[i]:
            x = xn + (x_next - xn) * min(1.0, (t - tl.times[i]) / (t_next - tl.times[i]))
        top, bot = oy + sys_.staff_top - 10, oy + sys_.string_y(sys_.strings) + 10
        color = self.pal["cursor"]
        c.create_rectangle(self.X(sx + 1), self.Y(top + 2), self.X(sx + sw - 1), self.Y(bot - 2),
                           outline=self.pal["column"], width=1, tags="playhead")
        c.create_line(self.X(x), self.Y(top), self.X(x), self.Y(bot), fill=color, width=max(2, 1.6 * self.scale),
                      tags="playhead")
        c.create_polygon(self.X(x - 4), self.Y(top - 6), self.X(x + 4), self.Y(top - 6), self.X(x), self.Y(top),
                         fill=color, outline="", tags="playhead")
        if self.follow_var.get():
            y1, y2 = self.Y(top - 30), self.Y(bot + 40)
            view_top, view_bot = c.canvasy(0), c.canvasy(c.winfo_height())
            if y1 < view_top or y2 > view_bot:
                total = float(c.cget("scrollregion").split()[3])
                c.yview_moveto(max(0.0, (y1 - c.winfo_height() * 0.25) / total))

    def import_youtube(self) -> None:
        from .import_dialog import ImportDialog
        ImportDialog(self)

    def load_imported(self, song: Song) -> bool:
        """Open a transcribed song as a new, unsaved tab. Returns False if the user cancelled."""
        if not self.confirm_discard():
            return False
        self.path, self.source_text = None, None
        self.set_selection(None)
        self.close_player()
        self.editor.load(song)
        self.editor.dirty = True
        self.canvas.yview_moveto(0)
        self.refresh()
        bars = len(self.editor.refs())
        self.flash(f"Imported {bars} bars from the transcription. It's a draft — check it by ear and fix what's off.")
        return True

    def open(self) -> None:
        if not self.confirm_discard():
            return
        name = filedialog.askopenfilename(parent=self.root, title="Open tab",
                                          filetypes=[("Tab files", "*.tab"), ("All files", "*.*")])
        if name:
            self.open_path(Path(name))

    def open_path(self, path: Path, create: bool = False) -> None:
        if not path.exists() and create:
            self.path, self.source_text = path, None
            song = Song(title=path.stem.replace("_", " ").replace("-", " ").title())
            self.close_player()
            self.editor.load(song)
            self.editor.dirty = True
        else:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Tab Writer", f"Could not open {path}:\n{exc}", parent=self.root)
                return
            self.path, self.source_text = path, text
            self.close_player()
            self.editor.load(_ensure_playable(parse(text)))
        self.refresh()
        self.update_source_panel(force=True)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        try:
            self.path.write_text(self.current_text(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Tab Writer", f"Could not save:\n{exc}", parent=self.root)
            return False
        self.editor.dirty = False
        self.update_title()
        self.flash(f"Saved {self.path}")
        return True

    def save_as(self) -> bool:
        name = filedialog.asksaveasfilename(parent=self.root, title="Save tab", defaultextension=".tab",
                                            initialfile=self.path.name if self.path else self._default_export(".tab"),
                                            filetypes=[("Tab files", "*.tab"), ("All files", "*.*")])
        if not name:
            return False
        self.path = Path(name)
        return self.save()

    def _default_export(self, ext: str) -> str:
        base = self.path.stem if self.path else (self.editor.song.title or "tab")
        base = "".join(ch for ch in base if ch.isalnum() or ch in " -_'").strip() or "tab"
        return base + ext

    def export_html(self) -> None:
        name = filedialog.asksaveasfilename(parent=self.root, title="Export HTML", defaultextension=".html",
                                            initialfile=self._default_export(".html"),
                                            filetypes=[("HTML", "*.html")])
        if name:
            Path(name).write_text(render_html(self.editor.song), encoding="utf-8")
            self.flash(f"Exported {name}")

    def export_pdf(self) -> None:
        name = filedialog.asksaveasfilename(parent=self.root, title="Export PDF", defaultextension=".pdf",
                                            initialfile=self._default_export(".pdf"), filetypes=[("PDF", "*.pdf")])
        if not name:
            return
        html = render_html(self.editor.song)
        self.msg_label.configure(text="Exporting PDF…")
        self.root.configure(cursor="watch")
        result: dict = {}

        def work():
            try:
                html_to_pdf(html, name)
            except ExportError as exc:
                result["error"] = str(exc)

        def done():
            if thread.is_alive():
                self.root.after(150, done)
                return
            self.root.configure(cursor="")
            if "error" in result:
                messagebox.showerror("Tab Writer", result["error"], parent=self.root)
                self.msg_label.configure(text=HINT)
            else:
                self.flash(f"Exported {name}")
                if messagebox.askyesno("Tab Writer", "PDF exported. Open it now?", parent=self.root):
                    _open_file(name)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.root.after(150, done)

    def print_sheet(self) -> None:
        """Open the printable page in the default browser with its print dialog."""
        fd, name = tempfile.mkstemp(prefix="tabwriter-print-", suffix=".html")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_html(self.editor.song, auto_print=True))
        webbrowser.open(Path(name).as_uri())
        self.flash("Opened the print view in your browser.")

    def quit(self) -> None:
        if self.confirm_discard():
            self.close_player()
            self.root.destroy()


def _open_file(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        webbrowser.open(Path(path).as_uri())


def main(path: str | None = None) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    TabWriterApp(root, path)
    root.mainloop()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
