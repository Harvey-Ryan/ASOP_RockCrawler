"""ASOP styling for RockCrawler's windows, matching the Event Manager on asop.f919.org:
gold hazard stripe, #212121 header bar with a wide-tracked condensed title and gold pill
tabs, #333333 header rows with gold condensed labels, gold highlight rows with dark text.
Colours from apps/web/src/index.css; fonts are the site's (Barlow, Barlow Condensed,
IBM Plex Mono), bundled in assets/ and loaded for this app only."""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

PAGE = "#171717"       # page behind panels
BG = "#181818"         # --background: window body
CARD = "#212121"       # --card: header bar, fields, previews
BAND = "#333333"       # header rows (--secondary / --border)
LINE = "#333333"       # --border
FG = "#cccccc"         # --foreground
DIM = "#8c8c8c"        # --muted-foreground
HI = "#f8f8f8"         # titles
ACC = "#b98a13"        # --primary (brand gold)
ACC_DARK = "#987210"   # --accent (gold hover / active nav)
ACC_FG = "#171717"     # text on gold
BAND_HOVER = "#3d3d3d"
GOOD = "#4ade80"       # text-green-400
BAD = "#f87171"        # text-red-400
FAINT = CARD           # kept for older call sites

FONT_FILES = ["Barlow-Medium.ttf", "Barlow-SemiBold.ttf", "BarlowCondensed-SemiBold.ttf",
              "BarlowCondensed-Bold.ttf", "BarlowCondensed-ExtraBold.ttf", "IBMPlexMono-Medium.ttf"]

# Filled in by apply(), with Windows fallbacks if the bundled fonts can't load.
BODY = SEMI = TITLE = HEAD = LABEL = PILL = MONO = MONO_BIG = None


def assets_dir(app_dir: Path) -> Path:
    return Path(getattr(sys, "_MEIPASS", app_dir)) / "assets"


def load_fonts(app_dir: Path) -> None:
    """Make the bundled fonts available to this process only (nothing is installed)."""
    for name in FONT_FILES:
        p = assets_dir(app_dir) / name
        if p.exists():
            try:
                ctypes.windll.gdi32.AddFontResourceExW(str(p), 0x10, 0)  # FR_PRIVATE
            except Exception:  # noqa: BLE001
                pass


def unload_fonts(app_dir: Path) -> None:
    """Release the font files on exit, so the folder can be updated or deleted."""
    for name in FONT_FILES:
        try:
            ctypes.windll.gdi32.RemoveFontResourceExW(str(assets_dir(app_dir) / name), 0x10, 0)
        except Exception:  # noqa: BLE001
            pass


def _pick(families: set, *names: str) -> str:
    return next((n for n in names if n in families), "Segoe UI")


def apply(root: tk.Tk) -> None:
    global BODY, SEMI, TITLE, HEAD, LABEL, PILL, MONO, MONO_BIG
    fams = set(tkfont.families(root))
    body = _pick(fams, "Barlow Medium", "Barlow", "Segoe UI")
    semi = _pick(fams, "Barlow SemiBold", "Segoe UI Semibold")
    cond = _pick(fams, "Barlow Condensed", "Bahnschrift Condensed", "Segoe UI")
    xbold = _pick(fams, "Barlow Condensed ExtraBold", "Barlow Condensed", "Bahnschrift Condensed", "Segoe UI")
    mono = _pick(fams, "IBM Plex Mono Medium", "IBM Plex Mono", "Cascadia Mono", "Consolas")
    BODY, SEMI = (body, 10), (semi, 10)
    TITLE = (xbold, 20) if xbold != cond else (cond, 20, "bold")   # "EVENT MANAGER"
    HEAD = (xbold, 11) if xbold != cond else (cond, 11, "bold")    # gold column heads
    LABEL = (cond, 10, "bold")
    PILL = (cond, 10, "bold")
    MONO, MONO_BIG = (mono, 10), (mono, 14)

    root.configure(bg=BG)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", HI)
    root.option_add("*TCombobox*Listbox.selectBackground", ACC)
    root.option_add("*TCombobox*Listbox.selectForeground", ACC_FG)
    root.option_add("*TCombobox*Listbox.font", BODY)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=BG, foreground=FG, fieldbackground=CARD, bordercolor=LINE,
                lightcolor=BG, darkcolor=BG, troughcolor=CARD, focuscolor=BG, selectbackground=ACC,
                selectforeground=ACC_FG, insertcolor=HI, arrowcolor=DIM, font=BODY)
    s.map(".", foreground=[("disabled", DIM)])
    for name, bg in (("TFrame", BG), ("Card.TFrame", CARD), ("Band.TFrame", BAND), ("Gold.TFrame", ACC)):
        s.configure(name, background=bg)
    s.configure("TLabel", background=BG, foreground=FG)
    s.configure("Dim.TLabel", foreground=DIM)
    s.configure("Bad.TLabel", foreground=BAD)
    s.configure("Good.TLabel", foreground=GOOD)
    s.configure("Key.TLabel", font=HEAD, foreground=ACC)                 # gold column-head labels
    s.configure("Mono.TLabel", font=MONO, foreground=HI)
    s.configure("Card.TLabel", background=CARD, foreground=FG)
    s.configure("CardDim.TLabel", background=CARD, foreground=DIM)
    s.configure("GoldKey.TLabel", background=ACC, foreground=ACC_FG, font=HEAD)
    s.configure("GoldValue.TLabel", background=ACC, foreground=ACC_FG, font=MONO_BIG)

    # Buttons: gold primary, #333 secondary (shadcn "secondary"), no outlines
    base = dict(padding=(14, 7), relief="flat", borderwidth=0, font=SEMI)
    s.configure("TButton", background=BAND, foreground=FG, bordercolor=BAND, lightcolor=BAND, darkcolor=BAND, **base)
    s.map("TButton", background=[("pressed", LINE), ("active", BAND_HOVER)], foreground=[("active", HI)],
          bordercolor=[("active", BAND_HOVER)], lightcolor=[("active", BAND_HOVER)], darkcolor=[("active", BAND_HOVER)])
    s.configure("Accent.TButton", background=ACC, foreground=ACC_FG, bordercolor=ACC, lightcolor=ACC, darkcolor=ACC, **base)
    s.map("Accent.TButton", background=[("pressed", ACC_DARK), ("active", ACC_DARK)], foreground=[("active", ACC_FG)],
          bordercolor=[("active", ACC_DARK)], lightcolor=[("active", ACC_DARK)], darkcolor=[("active", ACC_DARK)])

    for w in ("TCheckbutton", "TRadiobutton"):
        s.configure(w, background=BG, foreground=FG, indicatorbackground=CARD, indicatorforeground=ACC,
                    indicatormargin=(0, 0, 8, 0), padding=(0, 2))
        s.map(w, background=[("active", BG)], foreground=[("active", HI)],
              indicatorbackground=[("pressed", BAND), ("selected", CARD)], indicatorforeground=[("selected", ACC)])

    s.configure("Horizontal.TScale", background=ACC, troughcolor=CARD, bordercolor=LINE, lightcolor=ACC,
                darkcolor=ACC, sliderthickness=14, gripcount=0)
    s.map("Horizontal.TScale", background=[("active", ACC_DARK)])

    field = dict(fieldbackground=CARD, foreground=HI, bordercolor=LINE, lightcolor=CARD, darkcolor=CARD,
                 insertcolor=HI, padding=(6, 4))
    s.configure("TEntry", **field)
    s.map("TEntry", bordercolor=[("focus", ACC)])
    s.configure("TCombobox", background=CARD, arrowcolor=DIM, **field)
    s.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", HI)],
          background=[("active", CARD)], arrowcolor=[("active", ACC)], bordercolor=[("focus", ACC)],
          selectbackground=[("readonly", CARD)], selectforeground=[("readonly", HI)])
    s.configure("TSeparator", background=LINE)


# ── Pieces ────────────────────────────────────────────────────────────────────

class Tracked(tk.Canvas):
    """Text drawn letter by letter so it can be letter-spaced like the site's
    `uppercase tracking-widest` (Tk labels can't letter-space)."""

    def __init__(self, parent, text: str, font, fg: str, bg: str, spacing: float = 0.1, upper: bool = True):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self._font = tkfont.Font(root=parent, font=font)
        self._fg, self._spacing, self._upper = fg, spacing, upper
        self.set_text(text)

    def set_text(self, text: str, fg: str | None = None) -> None:
        if fg:
            self._fg = fg
        text = text.upper() if self._upper else text
        self.delete("all")
        size = abs(self._font.cget("size"))
        gap = size * self._spacing * (1.35 if self._font.cget("size") > 0 else 1)
        x = 0.0
        for ch in text:
            self.create_text(x, 0, text=ch, anchor="nw", font=self._font, fill=self._fg)
            x += self._font.measure(ch) + gap
        self.configure(width=max(1, int(x - gap + 1)), height=self._font.metrics("linespace"))


def hazard_stripe(parent, height: int = 8) -> tk.Canvas:
    """The Event Manager's top edge: repeating-linear-gradient(-45deg, #181818 8px, gold 4px)."""
    c = tk.Canvas(parent, height=height, bg=BG, highlightthickness=0, bd=0)

    def draw(_e=None):
        c.delete("all")
        w = c.winfo_width()
        period, band = 17, 6  # 12 px and 4 px measured across the diagonal
        for x in range(-height, w + period, period):
            c.create_polygon(x, height, x + band, height, x + band + height, 0, x + height, 0, fill=ACC, outline="")

    c.bind("<Configure>", draw)
    return c


def header(parent, title: str) -> tuple[tk.Frame, tk.Frame]:
    """Hazard stripe + #212121 header bar with the wide-tracked title.
    Returns (header bar, right-hand slot for pills or status)."""
    hazard_stripe(parent).pack(fill="x")
    bar = tk.Frame(parent, bg=CARD)
    bar.pack(fill="x")
    inner = tk.Frame(bar, bg=CARD, padx=18, pady=12)
    inner.pack(fill="x")
    Tracked(inner, "ASOP", TITLE, ACC, CARD).pack(side="left")
    Tracked(inner, " " + title, TITLE, HI, CARD).pack(side="left")
    slot = tk.Frame(inner, bg=CARD)
    slot.pack(side="left", fill="x", expand=True, padx=(20, 0))
    tk.Frame(parent, bg=LINE, height=1).pack(fill="x")  # border-b
    return bar, slot


def band(parent, text: str) -> tk.Frame:
    """A #333333 header row with a gold condensed label (like DATE / TIME / EVENT)."""
    fr = tk.Frame(parent, bg=BAND, padx=10, pady=5)
    Tracked(fr, text, HEAD, ACC, BAND).pack(side="left")
    return fr


class Pills(tk.Frame):
    """Gold pill tabs (UPCOMING / COMPLETED / PENDING)."""

    def __init__(self, parent, names: list[str], on_select, bg: str = CARD):
        super().__init__(parent, bg=bg)
        self._on, self._labels, self._bg, self.current = on_select, {}, bg, None
        for n in names:
            lab = tk.Label(self, text=n.upper(), font=PILL, padx=12, pady=4, bg=bg, fg=DIM, cursor="hand2")
            lab.pack(side="left", padx=(0, 4))
            lab.bind("<Button-1>", lambda _e, n=n: self.select(n))
            lab.bind("<Enter>", lambda _e, n=n: self._hover(n, True))
            lab.bind("<Leave>", lambda _e, n=n: self._hover(n, False))
            self._labels[n] = lab

    def _hover(self, n, on):
        if n != self.current:
            self._labels[n].configure(fg=FG if on else DIM)

    def select(self, name: str) -> None:
        self.current = name
        for n, lab in self._labels.items():
            lab.configure(bg=ACC if n == name else self._bg, fg=ACC_FG if n == name else DIM)
        self._on(name)


def style_window(win: tk.Misc, hwnd: int | None, icon: tk.PhotoImage | None = None) -> None:
    """Dark title bar (Windows 10 20H1+/11); on Windows 11 the caption and border use the palette too."""
    try:
        win.configure(bg=BG)
    except tk.TclError:
        pass
    if icon is not None:
        try:
            win.iconphoto(False, icon)
        except tk.TclError:
            pass
    if not hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break

        def colorref(hexc):
            r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
            return ctypes.c_int(r | (g << 8) | (b << 16))

        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref(PAGE)), 4)  # caption colour (Win 11)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(colorref(LINE)), 4)  # border colour (Win 11)
        dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(colorref(FG)), 4)    # caption text (Win 11)
    except Exception:  # noqa: BLE001
        pass


def framed(parent: tk.Misc, width: int = 380, height: int = 90, **label_kw) -> tuple[tk.Frame, tk.Label]:
    """A fixed-size box inside a 1 px border, for the image previews. Fixed so the
    window never resizes as the captured images change size."""
    fr = tk.Frame(parent, bg=LINE, padx=1, pady=1)
    inner = tk.Frame(fr, bg=CARD, width=width, height=height)
    inner.pack_propagate(False)
    inner.pack()
    lab = tk.Label(inner, bg=CARD, fg=DIM, font=MONO, text="—", **label_kw)
    lab.pack(expand=True)
    return fr, lab


def fit(text: str, n: int) -> str:
    """Trim to n characters with an ellipsis, so fixed-width fields never wrap or grow."""
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"
