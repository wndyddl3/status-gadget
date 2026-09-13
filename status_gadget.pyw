"""Status Gadget - a tiny always-on-top system monitor with a pet.

Double-click to run. Drag to move, drag the bottom-right corner to resize,
right-click for the menu. Settings are saved next to this file.

Performance notes (the monitor itself must stay light):
- Numbers are read and the bars redrawn once per second.
- Only the pet layer is redrawn per frame: 20 fps while it moves or casts,
  8 fps while it dozes, and not at all while it stands still.
- The yard (sky, sun/moon, floor) is baked into ONE image, rebuilt only on
  resize or time-of-day change; one image repaints far cheaper than the ~100
  shapes it replaces. Night stars/fireflies update at 5 fps.
- Transparency is faked by mixing colors with the sky behind them (cached).
- With the pet hidden (or in the one-line view) the frame loop only wakes twice a
  second; the one-line bar runs at 20 fps only while its width glides.
- Transparent mode checks the cursor 10x a second to reveal the panel on hover.
"""
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import random
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from functools import lru_cache

from metrics import GpuMonitor, HoudiniMonitor, SystemCpu, read_ram
from tray import TrayIcon

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(HERE, "settings.json")

# ---------- sizes (logical pixels; multiplied by the DPI scale when drawn) ----------
W_MIN, W_MAX, W_DEF = 220, 400, 280
YH_MIN, YH_MAX, YH_DEF = 56, 130, 80
ROWS_FULL, ROWS_COMPACT, H_HOU = 82, 32, 24
HOU_LINE, HOU_MAX_LINES = 18, 3 # one line per Houdini session; more than 3 -> 2 lines + "+N more"
SZ_MIN, SZ_MAX = 75, 150        # status size in percent (text, bars, rows; not the pet or yard)
XMIN = 30                       # pet walking range starts here; ends at W - 30
# One-line view: bar height, view button + resize handle, padding (14 left + 8 right), gap between items.
LINE_H, LINE_BTN, LINE_PAD, ITEM_GAP = 28, 40, 22, 12
NEXT_VIEW = {"full": "compact", "compact": "line", "line": "full"}   # the view button cycles these

# ---------- behaviour ----------
BUSY_AT = 85                    # CPU, GPU or RAM at/above this: casting magic
SLEEP_BELOW = 15                # CPU and GPU below this ...
SLEEP_AFTER = 60                # ... for this many seconds: dozing
MOVE_MS, SLEEP_MS, REST_MS = 50, 125, 200   # frame delay: moving / dozing / standing still
MAX_SPARKS = 16
HOVER_MS = 100                  # transparent mode: cursor check interval (reveals the panel)

# ---------- palette ----------
KEY = "#FF00FF"                 # window color made fully transparent
# Transparent background: the key turns dark so anti-aliased text edges blend to a faint
# dark fringe instead of pink. (No outline: the user prefers plain text, 2026-09-13.)
KEY_CLEAR = "#0F0D16"
DLG = {"bg": "#272333", "edge": "#3A3548", "dim": "#A39DB5", "hover": "#34304A"}   # Settings window
C = {
    "panel": "#1E1B26", "edge": "#34303F", "btn": "#272333", "track": "#2E2A3A",
    "label": "#8F88A8", "ink": "#ECE9F3", "ok": "#6FCF9C", "warn": "#F2C166", "crit": "#F08B7E",
    "pet": "#6CC486", "pet_dark": "#55AA6E", "eye": "#1E1B26", "cheek": "#F4A9A0",
    "hat": "#9585F0", "star": "#FFD86B", "magic": "#B9A8FF", "heart": "#F27C8E",
    "z": "#E6E1F5", "wand": "#C9A27A", "sweat": "#7DB8F0",
}
SKY = {
    "morning": ("#21474F", "#9C7482", "#C9A3AE"),   # top, bottom, floor dots
    "day": ("#34617F", "#5E8BA8", "#9DBDD2"),
    "evening": ("#3E3160", "#B8714F", "#D89A78"),
    "night": ("#10132A", "#1C2140", "#3A4170"),
}
# Star / firefly positions as fractions of the yard, so they follow resizing.
STARS = ((.08, .15), (.18, .33), (.31, .1), (.46, .25), (.6, .08), (.73, .3), (.84, .15), (.25, .58), (.68, .55))
FLIES = ((.2, .52, 0), (.42, .4, 1.7), (.64, .6, 3.1), (.84, .46, 4.4))
# Unit 4-point star outline (8 points), precomputed once.
STAR_SHAPE = tuple((math.cos(i * math.pi / 4 - math.pi / 2) * (0.4 if i % 2 else 1),
                    math.sin(i * math.pi / 4 - math.pi / 2) * (0.4 if i % 2 else 1)) for i in range(8))


def _rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


@lru_cache(maxsize=4096)
def _mix_q(a, b, q):
    (ra, ga, ba), (rb, gb, bb), k = _rgb(a), _rgb(b), q / 32
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * k), round(ga + (gb - ga) * k), round(ba + (bb - ba) * k))


def mix(a, b, k):
    """Color between a (k=0) and b (k=1); k is rounded to 32 steps so results cache well."""
    return _mix_q(a, b, max(0, min(32, round(k * 32))))


def level_color(pct):
    if pct is None:
        return C["track"]
    return C["crit"] if pct >= BUSY_AT else C["warn"] if pct >= 60 else C["ok"]


def time_phase():
    h = time.localtime().tm_hour
    return "morning" if 6 <= h < 10 else "day" if 10 <= h < 17 else "evening" if 17 <= h < 20 else "night"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Pet:
    __slots__ = ("x", "dir", "phase", "pause", "glance", "target", "jump", "hearts", "sparks")

    def __init__(self, x):
        self.x, self.dir, self.phase = x, random.choice((-1, 1)), 0.0
        self.pause = self.glance = 0.0
        self.target = None
        self.jump = -1.0                 # seconds into a hop, -1 when not hopping
        self.hearts = []                 # [x, y, age, wobble offset]
        self.sparks = []                 # [x, y, age, life, size]


class Gadget:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Status Gadget")
        self.S = self.root.winfo_fpixels("1i") / 96.0

        st = self.settings = self._load_settings()
        self.W = clamp(st.get("w", W_DEF), W_MIN, W_MAX)
        self.YH = clamp(st.get("yard_h", YH_DEF), YH_MIN, YH_MAX)
        view = st.get("view") or ("compact" if st.get("compact") else "full")   # older settings had "compact"
        self.view = tk.StringVar(value=view if view in NEXT_VIEW else "full")
        lw = st.get("line_w")
        self.line_w = lw if isinstance(lw, (int, float)) else None   # one-line target width; None = everything
        self.line_disp = None                  # drawn one-line width (glides toward the content width)
        self._mw = {}                          # status text widths in status units (cleared on font change)
        self.topmost = tk.BooleanVar(value=st.get("topmost", True))
        self.ram_as_pct = tk.BooleanVar(value=st.get("ram_pct", False))
        self.show_pet = tk.BooleanVar(value=st.get("show_pet", True))
        self.transparent = tk.BooleanVar(value=st.get("transparent", False))
        self.status_size = tk.IntVar(value=round(clamp(st.get("status_size", 100), SZ_MIN, SZ_MAX) / 5) * 5)
        self.sz = self.status_size.get() / 100
        sx, sy = st.get("settings_x"), st.get("settings_y")
        self.settings_pos = (sx, sy) if sx is not None and sy is not None else None
        self.settings_win = self.size_lbl = self.sdrag = None
        self.key = None

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", self.topmost.get())
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.canvas.pack()

        px = lambda n: -round(n * self.S)  # negative font size = pixels
        self.f10 = tkfont.Font(family="Segoe UI", size=px(10), weight="bold")
        self.f8 = tkfont.Font(family="Segoe UI", size=px(8), weight="bold")
        self.f_heart = tkfont.Font(family="Segoe UI Symbol", size=px(9))
        self.f_row = tkfont.Font(family="Segoe UI", size=px(10), weight="bold")   # scaled by status size
        self.f_ui = tkfont.Font(family="Segoe UI", size=px(12))
        self.f_ui_b = tkfont.Font(family="Segoe UI", size=px(12), weight="bold")
        self.f_x = tkfont.Font(family="Segoe UI", size=px(16))

        # metrics
        self.cpu_reader, self.gpu_reader, self.hou_reader = SystemCpu(), GpuMonitor(), HoudiniMonitor()
        self.cpu = self.gpu = self.ram_pct = None
        self.ram_used, self.ram_total = 0.0, None
        self.hou = []                           # one {'cpu', 'ram_gb'} per Houdini session
        self.hou_rows = []                      # (name, cpu, ram_gb) lines actually shown
        self.load = 0.0
        self.ram_level = 0

        # animation state
        self.pet = Pet(random.uniform(XMIN + 20, self.W - XMIN - 20))
        self.mood, self.calm_since = "walk", None
        self.t, self.last = 0.0, time.perf_counter()
        self.last_sky = -1.0
        self.phase = time_phase()
        self.hover_grip = False
        self.drag = None
        self.height = None
        self.panel_id = self.glow_id = self.yard_img = None
        self.drawn_key = None                  # what the resting pet looked like when last drawn
        self.pulsing = False
        self.hover = False                     # transparent mode: cursor over the gadget (panel shown)
        self.hover_job = self.frame_job = self.yard_key = None

        self._build_menu()
        try:                                   # tray icon: shows it's running; right-click -> Quit
            self.tray = TrayIcon("Status Gadget", on_quit=lambda: self.root.after(0, self.quit))
        except OSError:
            self.tray = None                   # a nicety only; the gadget runs without it
        self._bind_events()
        self._place_initial()
        self._refresh(save=False)
        self.tick_stats()
        self.tick_frame()

    # ---------- geometry (logical pixels) ----------
    @property
    def is_line(self):
        return self.view.get() == "line"

    @property
    def yard_bottom(self):                  # 0 when the pet (and its yard) is hidden or in one-line view
        return 8 + self.YH if self.show_pet.get() and not self.is_line else 0

    @property
    def ground(self):                       # pet body centre when standing
        return 8 + self.YH - 24

    @property
    def xmax(self):
        return self.W - XMIN

    def rows_h(self):
        v = self.view.get()
        return LINE_H if v == "line" else ROWS_COMPACT if v == "compact" else ROWS_FULL

    def base_h(self):
        return self.yard_bottom + self.rows_h() * self.sz

    def cur_h(self):
        n = 0 if self.is_line else len(self.hou_rows)     # one-line view shows sessions on its line
        return self.base_h() + (H_HOU + (n - 1) * HOU_LINE) * self.sz if n else self.base_h()

    def w_min(self):                        # bigger status text needs a wider gadget
        return max(W_MIN, math.ceil(W_MIN * self.sz))

    def ram_text(self, gb):
        if self.ram_as_pct.get() and self.ram_total:
            return f"{gb / self.ram_total * 100:.0f}%"
        return f"{gb:.1f}G"

    def gw(self):                           # current gadget width
        if self.is_line:
            return self.line_disp if self.line_disp is not None else self.line_width()
        return self.W

    def btn_pos(self):                      # view button: top-right of the yard, or the bar's right end
        return (self.gw() - 38, (self.cur_h() - 20) / 2) if self.is_line else (self.W - 33, 10)

    def stat_rows(self):
        return (("CPU", self.cpu, "—" if self.cpu is None else f"{self.cpu:.0f}%"),
                ("GPU", self.gpu, "—" if self.gpu is None else f"{self.gpu:.0f}%"),
                ("RAM", self.ram_pct, "—" if self.ram_pct is None else self.ram_text(self.ram_used)))

    # ---------- one-line view: items that fit ----------
    def _tw(self, s):                       # text width in status units (cached; values repeat a lot)
        w = self._mw.get(s)
        if w is None:
            if len(self._mw) > 512:
                self._mw.clear()
            w = self._mw[s] = self.f_row.measure(s) / (self.S * self.sz)
        return w

    def _items(self, hou):
        """[dot color, [[text, color, gap before], ...]] for CPU/GPU/RAM plus the given Houdini lines."""
        ink, lab = C["ink"], C["label"]
        items = [[level_color(pct), [[label, lab, 0], [val, ink, 4]]] for label, pct, val in self.stat_rows()]
        for name, cpu, ram in hou:
            more = name[0] == "+"
            items.append([None if more else C["hat"], [[name, lab if more else C["hat"], 0], ["CPU", lab, 6],
                          [f"{cpu:.0f}%", ink, 4], ["RAM", lab, 8], [self.ram_text(ram), ink, 4]]])
        return items

    def _items_w(self, items):
        tw = self._tw
        return (sum((10 if dot else 0) + sum(g + tw(s) for s, _, g in parts) for dot, parts in items)
                + ITEM_GAP * (len(items) - 1))

    def _nat_w(self, items):                # bar width that fits these items exactly
        return math.ceil((LINE_PAD + self._items_w(items)) * self.sz) + LINE_BTN

    def line_min_w(self):
        return self._nat_w(self._items(()))

    def line_nat_w(self):                   # everything shown; also the widest the bar gets
        return self._nat_w(self._items(self.hou_rows))

    def line_target(self):
        return clamp(self.line_w if self.line_w is not None else math.inf, self.line_min_w(), self.line_nat_w())

    def line_items(self):
        """What fits the target width. Trimmed from the end: session names shorten first
        ("Houdini 2" -> "Hou 2"), then values drop one at a time (a session's RAM, then its
        CPU and name). CPU, GPU and RAM always stay."""
        avail = (self.line_target() - LINE_BTN) / self.sz - LINE_PAD
        items = self._items(self.hou_rows)
        for it in reversed(items[3:]):
            if self._items_w(items) <= avail:
                break
            name = it[1][0]
            if name[0].startswith("Houdini"):
                name[0] = "Hou" + name[0][7:]
        while len(items) > 3 and self._items_w(items) > avail:
            parts = items[-1][1]
            if len(parts) > 3:
                del parts[3:]
            else:
                items.pop()
        return items

    def line_width(self):                   # the bar hugs what it shows
        return self._nat_w(self.line_items())

    def sky_at(self, y):
        top, bottom, _ = SKY[self.phase]
        return mix(top, bottom, (y - 8) / self.YH)

    # ---------- drawing primitives (take logical pixels) ----------
    def oval(self, cx, cy, rx, ry, fill, tag, **kw):
        S = self.S
        return self.canvas.create_oval((cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S,
                                       fill=fill, outline="", tags=tag, **kw)

    def line(self, pts, fill, width, tag, **kw):
        S = self.S
        return self.canvas.create_line([v * S for v in pts], fill=fill, width=width * S,
                                       capstyle="round", tags=tag, **kw)

    def curve(self, x0, y0, cx, cy, x1, y1, fill, width, tag):
        """Quadratic curve from (x0, y0) to (x1, y1) bent toward the control point (cx, cy)."""
        return self.line((x0, y0, cx, cy, x1, y1), fill, width, tag, smooth=True)

    def poly(self, pts, fill, tag, **kw):
        S = self.S
        return self.canvas.create_polygon([v * S for v in pts], fill=fill, outline="", tags=tag, **kw)

    def rrect(self, x0, y0, x1, y1, r, fill, tag, outline=""):
        pts = (x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
               x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0)
        S = self.S
        return self.canvas.create_polygon([v * S for v in pts], smooth=True, fill=fill,
                                          outline=outline, width=max(1, round(S)), tags=tag)

    def text(self, x, y, s, fill, tag, anchor="w", font=None):
        return self.canvas.create_text(x * self.S, y * self.S, text=s, fill=fill, anchor=anchor,
                                       font=font or self.f10, tags=tag)

    def star(self, x, y, r, fill, tag):
        S = self.S
        pts = []
        for ux, uy in STAR_SHAPE:
            pts += ((x + ux * r) * S, (y + uy * r) * S)
        return self.canvas.create_polygon(pts, fill=fill, outline="", tags=tag)

    def text_w(self, s):
        return self.f10.measure(s) / self.S

    # ---------- settings ----------
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        data = {"view": self.view.get(), "line_w": self.line_w, "topmost": self.topmost.get(),
                "w": round(self.W), "yard_h": round(self.YH),
                "x": self.root.winfo_x(), "y": self.root.winfo_y(),
                "ram_pct": self.ram_as_pct.get(), "show_pet": self.show_pet.get(),
                "transparent": self.transparent.get(), "status_size": self.status_size.get()}
        if self.settings_pos:
            data["settings_x"], data["settings_y"] = self.settings_pos
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    # ---------- window ----------
    def _default_pos(self):
        sw = self.root.winfo_screenwidth()
        return int(sw - (self.gw() + 24) * self.S), int(24 * self.S)

    @staticmethod
    def _on_screen(x, y):
        gsm = ctypes.windll.user32.GetSystemMetrics
        vx, vy, vw, vh = gsm(76), gsm(77), gsm(78), gsm(79)   # whole virtual desktop
        return vx <= x < vx + vw - 60 and vy <= y < vy + vh - 60

    def _place_initial(self):
        x, y = self.settings.get("x"), self.settings.get("y")
        if x is None or y is None or not self._on_screen(x, y):
            x, y = self._default_pos()
        self.root.geometry(f"+{x}+{y}")

    def _refresh(self, save=True):
        """Applies the view options (pet, transparency, status size) and redraws everything."""
        key = KEY_CLEAR if self.transparent.get() else KEY
        if key != self.key:
            self.key = key
            self.root.configure(bg=key)
            self.canvas.configure(bg=key)
            self.root.attributes("-transparentcolor", key)
        self.sz = self.status_size.get() / 100
        self.f_row.configure(size=-round(10 * self.sz * self.S))
        self._mw.clear()
        self.W = clamp(self.W, self.w_min(), W_MAX)
        if not self.transparent.get():
            self.hover = False
        elif not self.hover_job:
            self._poll_hover()
        if self.size_lbl:
            self.size_lbl.config(text=f"{self.status_size.get()}%")
        self.relayout()
        if save:
            self._save_settings()

    def relayout(self):
        """Full redraw: call after size, view, Houdini row or time-of-day changes."""
        h = self.cur_h()
        w_px, h_px = round(self.gw() * self.S), round(h * self.S)
        if (w_px, h_px) != self.height:
            self.height = (w_px, h_px)
            self.canvas.config(width=w_px, height=h_px)
            self.root.geometry(f"{w_px}x{h_px}")
        p = self.pet
        p.x = clamp(p.x, XMIN, self.xmax)
        if p.target is not None:
            p.target = clamp(p.target, XMIN, self.xmax)
        if not self.yard_bottom:
            self.canvas.delete("pet", "sky")
        self.draw_base()
        self.draw_rows()
        self.draw_grip()
        self.last_sky = -1.0
        self.drawn_key = None

    # ---------- menu & input ----------
    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        m.add_checkbutton(label="Always on top", variable=self.topmost, command=self._apply_topmost)
        m.add_separator()
        for label, value in (("Full view", "full"), ("Compact view", "compact"), ("One line", "line")):
            m.add_radiobutton(label=label, variable=self.view, value=value, command=self._on_view_change)
        m.add_separator()
        m.add_checkbutton(label="Show RAM as %", variable=self.ram_as_pct, command=self._on_pct_change)
        m.add_checkbutton(label="Show pet", variable=self.show_pet, command=self._refresh)
        m.add_checkbutton(label="Transparent background", variable=self.transparent, command=self._refresh)
        m.add_command(label="Settings…", command=self.open_settings)
        m.add_separator()
        m.add_command(label="Reset position", command=self._reset_position)
        m.add_command(label="Reset size", command=self._reset_size)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        self.menu = m

    def _apply_topmost(self):
        self.root.attributes("-topmost", self.topmost.get())
        if self.settings_win:
            self.settings_win.attributes("-topmost", self.topmost.get())
        self._save_settings()

    def _on_view_change(self):
        self.line_disp = None                  # a fresh one-line bar starts at its content width
        self.relayout()
        self._save_settings()

    def _on_pct_change(self):
        self.draw_rows()
        if self.is_line:
            self._kick_frame()                 # the values changed width: glide to fit
        self._save_settings()

    def _reset_position(self):
        x, y = self._default_pos()
        self.root.geometry(f"+{x}+{y}")
        self._save_settings()

    def _reset_size(self):
        self.W, self.YH, self.line_w = W_DEF, YH_DEF, None
        self.status_size.set(100)
        self._refresh()
        if self.is_line:
            self._kick_frame()                 # glide to the full content width

    # ---------- Settings window ----------
    def open_settings(self):
        if self.settings_win:
            self.settings_win.lift()
            return
        S, bg = self.S, DLG["bg"]
        px = lambda n: round(n * S)
        win = self.settings_win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", self.topmost.get())
        win.configure(bg=bg, highlightthickness=1, highlightbackground=DLG["edge"], highlightcolor=DLG["edge"])
        body = tk.Frame(win, bg=bg, padx=px(14), pady=px(8))
        body.pack(fill="both")

        head = tk.Frame(body, bg=bg, cursor="fleur")
        head.pack(fill="x")
        title = tk.Label(head, text="Settings", bg=bg, fg=C["ink"], font=self.f_ui_b, cursor="fleur")
        title.pack(side="left")
        close = tk.Label(head, text="×", bg=bg, fg=DLG["dim"], font=self.f_x, cursor="hand2", padx=px(5))
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.close_settings())
        close.bind("<Enter>", lambda e: close.config(bg=DLG["hover"], fg=C["ink"]))
        close.bind("<Leave>", lambda e: close.config(bg=bg, fg=DLG["dim"]))
        for w in (head, title):                  # drag the window by its title row
            w.bind("<ButtonPress-1>", self._settings_press)
            w.bind("<B1-Motion>", self._settings_drag)
            w.bind("<ButtonRelease-1>", self._settings_release)

        row = tk.Frame(body, bg=bg)
        row.pack(fill="x", pady=(px(6), 0))
        tk.Label(row, text="Status size", bg=bg, fg=DLG["dim"], font=self.f_ui).pack(side="left")
        self.size_lbl = tk.Label(row, text=f"{self.status_size.get()}%", bg=bg, fg=C["ink"], font=self.f_ui)
        self.size_lbl.pack(side="right")
        tk.Scale(body, from_=SZ_MIN, to=SZ_MAX, resolution=5, orient="horizontal", showvalue=False,
                 variable=self.status_size, command=lambda v: self._refresh(), length=px(208),
                 width=px(10), sliderlength=px(16), sliderrelief="flat", bd=0, highlightthickness=0,
                 bg=C["hat"], activebackground=C["magic"], troughcolor=C["track"]).pack(fill="x", pady=px(4))
        for label, var in (("Show pet", self.show_pet), ("Transparent background", self.transparent)):
            tk.Checkbutton(body, text=label, variable=var, command=self._refresh, anchor="w", font=self.f_ui,
                           bg=bg, fg=C["ink"], activebackground=bg, activeforeground=C["ink"],
                           selectcolor=C["panel"], bd=0, highlightthickness=0,
                           padx=0, pady=px(2)).pack(fill="x")

        win.update_idletasks()
        pos = self.settings_pos
        if not pos or not self._on_screen(*pos):   # default: left of the gadget, else below it
            ww, gap = win.winfo_reqwidth(), px(12)
            x, y = self.root.winfo_x() - ww - gap, self.root.winfo_y()
            if not self._on_screen(x, y):
                x, y = self.root.winfo_x(), self.root.winfo_y() + self.root.winfo_height() + gap
            pos = (x, y)
        win.geometry(f"+{pos[0]}+{pos[1]}")

    def close_settings(self):
        if self.settings_win:
            self.settings_pos = (self.settings_win.winfo_x(), self.settings_win.winfo_y())
            self.settings_win.destroy()
            self.settings_win = self.size_lbl = None
            self._save_settings()

    def _settings_press(self, e):
        w = self.settings_win
        self.sdrag = (e.x_root, e.y_root, w.winfo_x(), w.winfo_y())

    def _settings_drag(self, e):
        if self.sdrag:
            sx, sy, wx, wy = self.sdrag
            self.settings_win.geometry(f"+{wx + e.x_root - sx}+{wy + e.y_root - sy}")

    def _settings_release(self, e):
        if self.sdrag:
            self.sdrag = None
            self.settings_pos = (self.settings_win.winfo_x(), self.settings_win.winfo_y())
            self._save_settings()

    def _bind_events(self):
        c = self.canvas
        c.bind("<ButtonPress-1>", self._on_press)
        c.bind("<B1-Motion>", self._on_drag)
        c.bind("<ButtonRelease-1>", self._on_release)
        c.bind("<Motion>", self._on_hover)
        c.bind("<Leave>", lambda e: self._set_hover(False))
        c.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))

    def _in_grip(self, x, y):
        if self.is_line:                        # sideways handle at the bar's right end
            return x >= self.gw() - 12
        return x >= self.W - 18 and y >= self.cur_h() - 18

    def _set_hover(self, on):
        if on != self.hover_grip and not self.drag:
            self.hover_grip = on
            self.canvas.config(cursor=("sb_h_double_arrow" if self.is_line else "size_nw_se") if on else "")
            self.draw_grip()

    def _on_hover(self, e):
        if not self.drag:
            self._set_hover(self._in_grip(e.x / self.S, e.y / self.S))

    def _on_press(self, e):
        mode = "resize" if self._in_grip(e.x / self.S, e.y / self.S) else "move"
        self.drag = {"mode": mode, "sx": e.x_root, "sy": e.y_root, "wx": self.root.winfo_x(),
                     "wy": self.root.winfo_y(), "W": self.W, "YH": self.YH,
                     "LW": self.line_target() if self.is_line else None, "moved": False}

    def _on_drag(self, e):
        d = self.drag
        if not d:
            return
        dx, dy = e.x_root - d["sx"], e.y_root - d["sy"]
        if abs(dx) + abs(dy) > 3:
            d["moved"] = True
        if not d["moved"]:
            return
        if d["mode"] == "resize" and self.is_line:          # sideways only; the bar follows the pointer
            self.line_w = clamp(d["LW"] + dx / self.S, self.line_min_w(), self.line_nat_w())
            self.line_disp = self.line_w
            self.relayout()
        elif d["mode"] == "resize":
            w = clamp(d["W"] + dx / self.S, self.w_min(), W_MAX)
            yh = clamp(d["YH"] + dy / self.S, YH_MIN, YH_MAX) if self.show_pet.get() else self.YH
            if (round(w), round(yh)) != (round(self.W), round(self.YH)):
                self.W, self.YH = w, yh
                self.relayout()
        else:
            self.root.geometry(f"+{d['wx'] + dx}+{d['wy'] + dy}")

    def _on_release(self, e):
        d, self.drag = self.drag, None
        if not d:
            return
        if d["mode"] == "resize" and self.is_line:
            self.line_w = self.line_width()    # snapped, so the next drag starts at the bar's end
            self._kick_frame()                 # glide in to hug what's shown
        if d["moved"]:
            self.draw_grip()
            self._save_settings()
            return
        if d["mode"] == "resize":
            return
        self._on_click(e.x / self.S, e.y / self.S)

    def _on_click(self, x, y):
        W, p, sz = self.W, self.pet, self.sz
        bx, by = self.btn_pos()
        if (self.yard_bottom or self.is_line) and bx - 2 <= x <= bx + 24 and by - 2 <= y <= by + 22:
            self.view.set(NEXT_VIEW[self.view.get()])        # view button: full -> compact -> one line
            self._on_view_change()
            return
        # the numbers (right-hand values; anywhere on compact / one-line / Houdini lines): GB <-> %
        if y > self.yard_bottom and (self.view.get() != "full" or y > self.base_h() - 6 * sz or x >= W - 70 * sz):
            self.ram_as_pct.set(not self.ram_as_pct.get())
            self._on_pct_change()
            return
        if not self.yard_bottom:
            return
        py = self.ground + 2 if self.mood == "sleep" else self.ground
        if math.hypot(x - p.x, y - py) < 15:                 # the pet: hearts + a hop
            p.jump, p.pause = 0.0, 1.5
            self.calm_since = time.monotonic()
            for i in range(3):
                p.hearts.append([p.x + (i - 1) * 7, py - 12 - i * 2, -i * 0.12, i * 2])
            return
        if 8 <= x <= W - 8 and 8 <= y <= self.yard_bottom:   # the yard: walk there
            p.target = clamp(x, XMIN, self.xmax)
            self.calm_since = time.monotonic()

    def quit(self):
        if self.tray:
            self.tray.remove()
        self.close_settings()
        self._save_settings()
        self.gpu_reader.close()
        self.root.destroy()

    # ---------- stats (once per second) ----------
    def tick_stats(self):
        self.cpu = self.cpu_reader.sample()
        self.gpu = self.gpu_reader.sample()
        ram = read_ram()
        if ram:
            self.ram_used, self.ram_total = ram
            self.ram_pct = self.ram_used / self.ram_total * 100
        n_rows = len(self.hou_rows)
        self.hou = self.hou_reader.sample() or []
        self.hou_rows = self._hou_rows(self.hou)

        self.load = max(self.cpu or 0, self.gpu or 0, self.ram_pct or 0)
        r = self.ram_pct or 0
        self.ram_level = 4 if r >= 95 else 3 if r >= 85 else 2 if r >= 70 else 1 if r >= 50 else 0
        self._update_mood()

        phase = time_phase()
        if self.height is None or phase != self.phase or n_rows != len(self.hou_rows):
            self.phase = phase
            self.relayout()
        else:
            self.draw_rows()
        if self.is_line and self.line_disp is not None and abs(self.line_width() - self.line_disp) >= 0.3:
            self._kick_frame()                     # the content width changed: glide to it
        self.root.after(1000, self.tick_stats)

    @staticmethod
    def _hou_rows(sessions):
        n = len(sessions)
        rows = [("Houdini" if n == 1 else f"Houdini {i + 1}", s["cpu"], s["ram_gb"]) for i, s in enumerate(sessions)]
        if n > HOU_MAX_LINES:
            rest = sessions[2:]
            rows = rows[:2] + [(f"+{len(rest)} more", sum(s["cpu"] for s in rest), sum(s["ram_gb"] for s in rest))]
        return rows

    def _update_mood(self):
        if self.load >= BUSY_AT:
            self.mood, self.calm_since = "busy", None
            return
        if (self.cpu or 0) < SLEEP_BELOW and (self.gpu or 0) < SLEEP_BELOW:
            now = time.monotonic()
            if self.calm_since is None:
                self.calm_since = now
            if now - self.calm_since >= SLEEP_AFTER and self.pet.target is None:
                self.mood = "sleep"
                return
        else:
            self.calm_since = None
        self.mood = "walk"

    # ---------- drawing: background (rebuilt only on layout changes) ----------
    def draw_base(self):
        cv, tag, S = self.canvas, "base", self.S
        cv.delete(tag, "sky")                              # night stars are redrawn by the frame loop
        W, h = self.gw(), self.cur_h()
        self.panel_id = None
        if not self.transparent.get() or self.hover:       # transparent: panel only while hovered
            self.panel_id = self.rrect(0.5, 0.5, W - 0.5, h - 0.5, min(16, h / 2), C["panel"], tag,
                                       outline=C["edge"])
        if self.yard_bottom:
            key = (self.W, self.YH, self.phase, self.key, self.S)
            if key != self.yard_key:                       # bake only when the yard itself changes
                self.yard_img, self.yard_key = self._render_yard(), key
            cv.create_image(round(8 * S), round(8 * S), anchor="nw", image=self.yard_img, tags=tag)
            bx = W - 33                                    # view toggle button
            self.rrect(bx, 10, bx + 22, 30, 6, C["btn"], tag, outline=C["edge"])
            for y in ((20,) if self.view.get() == "compact" else (16, 20, 24)):
                self.line((bx + 6, y, bx + 16, y), C["label"], 1.4, tag)
        if cv.find_withtag(tag):
            cv.tag_lower(tag)

    def _render_yard(self):
        """Bakes sky gradient, sun/moon/clouds, floor dots and rounded corners into one image.

        Pure-Python pixels, but only on layout changes; shapes get soft (anti-aliased) edges.
        Coordinates below are logical pixels relative to the yard's top-left corner.
        """
        S, W, YH = self.S, self.W, self.YH
        w, h = round((W - 16) * S), round(YH * S)
        top, bottom, floor = (_rgb(c) for c in SKY[self.phase])
        rows = []
        for iy in range(h):
            k = (iy + 0.5) / h
            rows.append(bytearray(bytes(round(a + (b - a) * k) for a, b in zip(top, bottom)) * w))

        def blend(ix, iy, col, a):
            row, i = rows[iy], ix * 3
            row[i] = round(row[i] + (col[0] - row[i]) * a)
            row[i + 1] = round(row[i + 1] + (col[1] - row[i + 1]) * a)
            row[i + 2] = round(row[i + 2] + (col[2] - row[i + 2]) * a)

        def disc(cx, cy, rx, ry, color, alpha=1.0):
            col, cx, cy, rx, ry = _rgb(color), cx * S, cy * S, rx * S, ry * S
            edge = min(rx, ry)
            for iy in range(max(0, int(cy - ry - 1)), min(h, int(cy + ry + 2))):
                dy = (iy + 0.5 - cy) / ry
                for ix in range(max(0, int(cx - rx - 1)), min(w, int(cx + rx + 2))):
                    cover = 0.5 - (math.hypot((ix + 0.5 - cx) / rx, dy) - 1) * edge
                    if cover > 0:
                        blend(ix, iy, col, alpha * min(1.0, cover))

        if self.phase == "night":
            disc(36, 18, 7, 7, "#FFF0BE")
            disc(39.5, 16, 6, 6, self.sky_at(24))                    # crescent cut-out
        elif self.phase == "morning":
            disc(32, YH - 10, 11, 11, "#FFD3A1", 0.9)
        elif self.phase == "evening":
            disc(44, YH - 8, 13, 13, "#F2A06B")
        else:
            disc(38, 18, 6, 6, "#FFE39C")
            for cx, cy, rx, ry in ((82, 18, 9, 5), (91, 16, 7, 5), (W - 92, 30, 8, 4)):
                disc(cx, cy, rx, ry, "#E8F1F8", 0.55)
        floor_hex = "#%02x%02x%02x" % floor
        for fx in range(10, int(W - 26) + 1, 6):                    # dotted floor
            disc(fx, YH - 8, 0.8, 0.8, floor_hex)

        # Rounded corners: fade to the panel color (or the see-through key) outside an 11 px radius.
        panel, R = _rgb(self.key if self.transparent.get() else C["panel"]), 11 * S
        for iy in range(min(h, math.ceil(R))):
            for ix in range(min(w, math.ceil(R))):
                d = math.hypot(R - (ix + 0.5), R - (iy + 0.5))
                out = min(1.0, max(0.0, d - R + 0.5))
                if out > 0:
                    for px, py in ((ix, iy), (w - 1 - ix, iy), (ix, h - 1 - iy), (w - 1 - ix, h - 1 - iy)):
                        blend(px, py, panel, out)
        return tk.PhotoImage(master=self.root, data=b"P6 %d %d 255\n" % (w, h) + b"".join(rows), format="PPM")

    # ---------- drawing: numbers (once per second) ----------
    def draw_rows(self):
        """Status area in its own units: origin at its top-left, scaled by the status size."""
        cv, tag = self.canvas, "rows"
        cv.delete(tag)
        self.glow_id = None
        k, oy = self.S * self.sz, self.yard_bottom * self.S
        W = self.gw() / self.sz                               # local width shrinks as the scale grows
        font, ink, lab = self.f_row, C["ink"], C["label"]

        def line(x0, y, x1, fill, width):
            return cv.create_line(x0 * k, oy + y * k, x1 * k, oy + y * k, fill=fill, width=width * k,
                                  capstyle="round", tags=tag)

        def text(x, y, s, fill, anchor="w"):
            cv.create_text(x * k, oy + y * k, text=s, fill=fill, anchor=anchor, font=font, tags=tag)

        def dot(cx, cy, fill):
            cv.create_oval((cx - 3) * k, oy + (cy - 3) * k, (cx + 3) * k, oy + (cy + 3) * k,
                           fill=fill, outline="", tags=tag)

        def items(its, x, y):                                 # dot + label/value parts, fixed gaps
            for dot_col, parts in its:
                if dot_col:
                    dot(x + 3, y, dot_col)
                    x += 10
                for s, col, gap in parts:
                    x += gap
                    text(x, y, s, col)
                    x += tw(s)
                x += ITEM_GAP

        tw = self._tw
        rows = self.stat_rows()
        view = self.view.get()
        if view == "line":
            items(self.line_items(), 14, LINE_H / 2)
        elif view == "compact":
            items(self._items(()), 16, 16)
        else:
            x0, x1 = 54, W - 62
            for i, (label, pct, val) in enumerate(rows):
                y = 22 + i * 19
                text(16, y, label, lab)
                line(x0, y, x1, C["track"], 7)
                if pct is not None:
                    end = x0 + (x1 - x0) * clamp(pct, 0, 100) / 100 + 0.01
                    if label == "RAM" and self.ram_level >= 3:       # glow behind the RAM bar
                        self.glow_id = line(x0, y, end, mix(C["panel"], level_color(pct), 0.45), 12)
                    line(x0, y, end, level_color(pct), 7)
                text(W - 16, y, val, ink, "e")
        if self.hou_rows and view != "line":
            base = self.rows_h()
            line(16, base - 6, W - 16, C["track"], 1)
            for i, (name, cpu, ram) in enumerate(self.hou_rows):
                y, more = base + 8 + i * HOU_LINE, name[0] == "+"
                if not more:
                    dot(19, y, C["hat"])
                text(16 if more else 27, y, name, lab if more else C["hat"])
                x = W - 16
                for s, col in ((self.ram_text(ram), ink), ("RAM", lab), (f"{cpu:.0f}%", ink), ("CPU", lab)):
                    text(x, y, s, col, "e")
                    x -= tw(s) + (4 if col == ink else 12)
        if view == "line":                                    # view button, on top of the items
            bx, by = self.btn_pos()
            self.rrect(bx, by, bx + 22, by + 20, 6, C["btn"], tag, outline=C["edge"])
            for d in (-4, 0, 4):
                self.oval(bx + 11 + d, by + 10, 1.3, 1.3, C["label"], tag)

    def draw_grip(self):
        cv, tag = self.canvas, "grip"
        cv.delete(tag)
        W, H = self.gw(), self.cur_h()
        if self.transparent.get():                 # shown with the panel while the cursor is over the gadget
            if not self.hover:
                return
        elif not self.hover_grip and not (self.drag and self.drag["mode"] == "resize"):
            return
        if self.is_line:                           # one-line view: a sideways handle at the right end
            for dx in (-1.5, 1.5):
                self.line((W - 7 + dx, H / 2 - 4, W - 7 + dx, H / 2 + 4), C["label"], 1.2, tag)
            return
        for a, b in ((4, 10), (4, 6), (4, 14)):
            self.line((W - b, H - a, W - a, H - b), C["label"], 1.3, tag)

    # ---------- animation loop ----------
    def tick_frame(self):
        now = time.perf_counter()
        dt = min(now - self.last, 0.25)            # cap only guards against long stalls
        self.last = now
        self.t += dt
        if self.yard_bottom:
            self._move(dt)
            self._update_particles(dt)
            p = self.pet
            moving = (self.mood == "busy" or p.jump >= 0 or p.hearts or p.sparks or p.target is not None
                      or (self.mood == "walk" and p.pause <= 0))
            if moving or self.mood == "sleep":
                self.draw_pet()
                self.drawn_key = None
                delay = MOVE_MS if moving else SLEEP_MS
            else:                                  # standing still: redraw only if its look changed
                key = (p.x, p.dir, p.glance > 0, self.ram_level, bool(self.hou))
                if key != self.drawn_key:
                    self.draw_pet()
                    self.drawn_key = key
                delay = REST_MS
            if self.phase == "night" and self.t - self.last_sky >= 0.2:
                self.draw_night_sky()
        else:                                      # no pet: only the danger pulse or a gliding bar needs frames
            gliding = self.is_line and self._glide(dt)
            delay = MOVE_MS if gliding or self.ram_level >= 4 else 500
        if self.ram_level >= 4:                    # danger: border and RAM glow pulse red
            a = abs(math.sin(self.t * 5))
            if self.panel_id:
                self.canvas.itemconfig(self.panel_id, outline=mix(C["panel"], C["crit"], 0.45 + 0.45 * a))
            if self.glow_id:
                self.canvas.itemconfig(self.glow_id, fill=mix(C["panel"], C["crit"], 0.25 + 0.4 * a))
        elif self.pulsing and self.panel_id:
            self.canvas.itemconfig(self.panel_id, outline=C["edge"])
        self.pulsing = self.ram_level >= 4
        self.frame_job = self.root.after(delay, self.tick_frame)

    def _kick_frame(self):
        """Runs the frame loop now (e.g. to start a glide) instead of at its next, possibly slow, tick."""
        if self.frame_job:
            self.root.after_cancel(self.frame_job)
        self.last = time.perf_counter() - MOVE_MS / 1000   # a normal first step, not a jump
        self.tick_frame()

    def _glide(self, dt):
        """One-line view: eases the drawn bar width toward the content width; True while moving."""
        if self.drag and self.drag["mode"] == "resize":
            return False                           # while dragging, the bar follows the pointer
        goal, disp = self.line_width(), self.line_disp
        if disp is None or abs(goal - disp) < 0.3:
            if disp != goal:
                self.line_disp = goal
                self.relayout()
            return False
        self.line_disp = disp + (goal - disp) * min(1.0, dt * 14)
        self.relayout()
        return True

    # ---------- transparent mode: reveal the panel while the cursor is over the gadget ----------
    def _poll_hover(self):
        self.hover_job = None
        if not self.transparent.get():
            return
        self._set_panel_hover(bool(self.drag) or self._cursor_inside())
        self.hover_job = self.root.after(HOVER_MS, self._poll_hover)

    def _cursor_inside(self):
        pt = wt.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        r = self.root
        x, y = r.winfo_rootx(), r.winfo_rooty()
        return x <= pt.x < x + r.winfo_width() and y <= pt.y < y + r.winfo_height()

    def _set_panel_hover(self, on):
        if on != self.hover:
            self.hover = on
            self.draw_base()
            self.draw_grip()
            self.last_sky = -1.0                   # night stars were cleared with the base layer

    def _move(self, dt):
        p = self.pet
        if p.jump >= 0:
            p.jump += dt
            if p.jump > 0.45:
                p.jump = -1.0
        if p.target is not None:
            d = p.target - p.x
            if d:
                p.dir = 1 if d > 0 else -1
            step = 55 * dt
            if abs(d) <= step:
                p.x, p.target, p.jump, p.pause = p.target, None, 0.0, 1.2
            else:
                p.x += p.dir * step
                p.phase += step * 0.28
            return
        if p.glance > 0:
            p.glance -= dt
        if self.mood == "sleep":
            return                                 # busy keeps wandering while casting
        if p.pause > 0:
            p.pause -= dt
            return
        if self.ram_level >= 1 and random.random() < 0.18 * dt:
            p.glance = p.pause = 1.2               # glance down at the bars
            return
        step = 28 * dt * (0.6 if self.ram_level >= 2 else 1)
        p.x += p.dir * step
        p.phase += step * 0.28
        if p.x <= XMIN:
            p.x, p.dir = XMIN, 1
        elif p.x >= self.xmax:
            p.x, p.dir = self.xmax, -1
        elif random.random() < 0.25 * dt:
            p.pause = 1 + random.random() * 2
        elif random.random() < 0.08 * dt:
            p.dir *= -1

    def _update_particles(self, dt):
        p = self.pet
        if p.hearts:
            for h in p.hearts:
                h[2] += dt
            p.hearts = [h for h in p.hearts if h[2] < 1.1]
        if self.mood == "busy" and len(p.sparks) < MAX_SPARKS:
            inten = (self.load - BUSY_AT) / (100 - BUSY_AT)
            if random.random() < (6 + inten * 10) * dt:
                p.sparks.append([p.x + (random.random() - 0.5) * 34, self.ground + 10, 0.0,
                                 0.9 + random.random() * 0.6, 1 + random.random() * 1.4])
        if p.sparks:
            for s in p.sparks:
                s[2] += dt
            p.sparks = [s for s in p.sparks if s[2] < s[3]]

    def draw_night_sky(self):
        self.last_sky = self.t
        cv, tag, t = self.canvas, "sky", self.t
        cv.delete(tag)
        fx = lambda f: 8 + f * (self.W - 16)
        fy = lambda f: 8 + f * self.YH
        for i, (sx, sy) in enumerate(STARS):
            y = fy(sy)
            self.star(fx(sx), y, 1.6, mix(self.sky_at(y), "#FFF6D8", 0.35 + 0.45 * abs(math.sin(t * 1.3 + i))), tag)
        for x, y, o in FLIES:
            px, py = fx(x) + math.sin(t * 0.7 + o) * 8, fy(y) + math.cos(t * 0.9 + o) * 5
            a, bg = 0.35 + 0.5 * abs(math.sin(t * 1.6 + o)), self.sky_at(py)
            self.oval(px, py, 3, 3, mix(bg, "#D9F58A", a * 0.2), tag)
            self.oval(px, py, 1.3, 1.3, mix(bg, "#EFFFB0", a), tag)
        cv.tag_raise(tag, "base")

    # ---------- drawing: pet (per frame) ----------
    def draw_pet(self):
        cv, tag, p, t = self.canvas, "pet", self.pet, self.t
        cv.delete(tag)
        x, d, gy, lvl = p.x, p.dir, self.ground, self.ram_level
        walking = p.target is not None or (self.mood != "sleep" and p.pause <= 0)
        casting = self.mood == "busy"
        inten = max(0.0, (self.load - BUSY_AT) / (100 - BUSY_AT))
        jump = math.sin(math.pi * p.jump / 0.45) * 11 if p.jump >= 0 else 0
        bob = -abs(math.sin(p.phase)) * 2 if walking else (math.sin(t * 2) - 1.5 if casting else 0)
        y = gy + bob - jump
        if casting:
            self._draw_magic_circle(x, inten)

        if self.mood == "sleep" and p.target is None and jump == 0:
            sy = gy + 2
            self.oval(x, sy, 12, 8, C["pet"], tag)
            for ex in (-4, 4):
                self.curve(x + ex - 2, sy, x + ex, sy + 2.5, x + ex + 2, sy, C["eye"], 1.3, tag)
            zt = (t % 2.4) / 2.4
            for s, zx, zy, font in (("z", 12, 8, self.f8), ("Z", 19, 16, self.f10)):
                yy = sy - zy - zt * 8
                self.text(x + zx + zt * 4, yy, s, mix(self.sky_at(yy), C["z"], 1 - zt * 0.6), tag, font=font)
            if self.hou:
                self._draw_hat(x, sy - 6)
            self._draw_particles()
            return

        step = math.sin(p.phase * 2) * 2 if walking else 0
        fy = gy + 9.5 - jump * 0.6
        self.oval(x - 5 + step, fy, 2, 2.4, C["pet_dark"], tag)
        self.oval(x + 5 - step, fy, 2, 2.4, C["pet_dark"], tag)
        self.oval(x, y, 11, 9, C["pet"], tag)
        ex = 0 if casting else d * 1.2
        if casting or p.hearts:                    # happy closed eyes + cheeks
            for o in (-3.5, 3.5):
                self.curve(x + o + ex - 1.8, y - 0.5, x + o + ex, y - 3.2, x + o + ex + 1.8, y - 0.5,
                           C["eye"], 1.3, tag)
            self.oval(x - 6 + ex, y + 3, 2, 1.2, C["cheek"], tag)
            self.oval(x + 6 + ex, y + 3, 2, 1.2, C["cheek"], tag)
        else:
            gdy = 1.3 if p.glance > 0 else 0       # glancing down at the bars
            self.oval(x - 3.5 + ex, y - 1.5 + gdy, 1.5, 1.5, C["eye"], tag)
            self.oval(x + 3.5 + ex, y - 1.5 + gdy, 1.5, 1.5, C["eye"], tag)
        self.curve(x - 2 + ex, y + 2.5, x + ex, y + 4.5, x + 2 + ex, y + 2.5, C["eye"], 1, tag)

        if lvl >= 2:                               # sweat drop
            sx = x - d * 12
            self.poly((sx, y - 12, sx + 3, y - 6, sx, y - 4.5, sx - 3, y - 6), C["sweat"], tag, smooth=True)
        if lvl >= 3:
            self._draw_sign(x - d * 17, y - 30, lvl)
        if self.hou:
            self._draw_hat(x, y - 8)
            if casting:                            # wand, gently swaying
                tx, ty = x + d * 21, y - 9 + math.sin(t * 3) * 2
                self.line((x + d * 10, y + 3, tx, ty), C["wand"], 1.6, tag)
                self.star(tx + d * 2, ty - 3, 3.2,
                          mix(self.sky_at(ty - 3), C["star"], 0.6 + 0.4 * abs(math.sin(t * 8))), tag)
        self._draw_particles()

    def _draw_magic_circle(self, x, inten):
        tag, a, cy = "pet", self.t * (1.2 + inten * 2.5), self.ground + 13
        self.oval(x, cy, 26, 7, mix(self.sky_at(cy), C["magic"], 0.2 + inten * 0.25), tag)
        S = self.S
        for rx, ry in ((21, 5.2), (13, 3.2)):
            self.canvas.create_oval((x - rx) * S, (cy - ry) * S, (x + rx) * S, (cy + ry) * S,
                                    outline=C["magic"], width=1.2 * S, tags=tag)
        for k in range(8):
            g = a + k * math.pi / 4
            self.oval(x + math.cos(g) * 17, cy + math.sin(g) * 4.2, 1.2, 1.2, C["magic"], tag)

    def _draw_sign(self, sx, sy, lvl):
        tag, danger = "pet", lvl >= 4
        pulse = 1 + math.sin(self.t * 10) * 0.08 if danger else 1
        w, h = (20 if danger else 14) * pulse, 13 * pulse
        sx = clamp(sx, 8 + w / 2 + 2, self.W - 8 - w / 2 - 2)          # keep it inside the yard
        self.line((sx, sy + h / 2, sx, sy + h / 2 + 11), C["wand"], 1.4, tag)
        self.rrect(sx - w / 2, sy - h / 2, sx + w / 2, sy + h / 2, 4, C["crit"] if danger else C["warn"], tag)
        self.text(sx, sy + 0.5, "!!" if danger else "!", C["panel"], tag, anchor="center")

    def _draw_hat(self, x, base):
        tag = "pet"
        self.poly((x - 8, base, x + 9, base, x + 4, base - 17, x - 1, base - 16), C["hat"], tag)
        self.oval(x + 0.5, base, 10, 2.2, C["hat"], tag)
        self.star(x + 2, base - 7, 2.6, C["star"], tag)

    def _draw_particles(self):
        p, tag = self.pet, "pet"
        top = 14                                   # sparks fade out before the yard's top edge
        for sx, sy, age, life, size in p.sparks:
            k = age / life
            y = sy - k * min(48, sy - top)
            self.star(sx, y, size * 2, mix(self.sky_at(y), C["star"] if k < 0.5 else C["magic"], 1 - k), tag)
        for hx, hy, age, o in p.hearts:
            if age < 0:
                continue
            k = age / 1.1
            y = hy - k * 26
            self.text(hx + math.sin(k * 6 + o) * 3, y, "♥", mix(self.sky_at(y), C["heart"], 1 - k), tag,
                      anchor="center", font=self.f_heart)

    def run(self):
        self.root.mainloop()


def _single_instance():
    """Returns False if another Status Gadget is already running."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    global _MUTEX                                  # keep the handle alive for the whole run
    _MUTEX = k32.CreateMutexW(None, False, "Local\\StatusGadget.SingleInstance")
    return ctypes.get_last_error() != 183          # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if _single_instance():
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        Gadget().run()
