"""ASOP RockCrawler — reads the mining signature number off the Star Citizen HUD and shows
the rock type right next to it, styled to blend in with the HUD.

  Ctrl+Shift+F8  edit mode: move/resize the READ and DISPLAY boxes, size, brightness…
  Ctrl+Shift+F9  hide/show the overlay

Windows 10/11 only. Run the game in Borderless (overlays can't draw over exclusive fullscreen).
"""
from __future__ import annotations

import updater

if __name__ == "__main__":
    updater.run_velopack_hooks()  # first: Velopack may run install/update tasks and exit

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import io
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, ttk

import mss
from PIL import Image, ImageTk

import core
import ui_theme as theme

APP_NAME = "ASOP RockCrawler"
FROZEN = getattr(sys, "frozen", False)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
# Installed copies keep settings in %APPDATA%: Velopack replaces the program folder on
# every update. Copies run from source keep them next to the code.
DATA_DIR = (Path(os.environ.get("APPDATA") or APP_DIR) / "ASOP-RockCrawler") if FROZEN else APP_DIR
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
CACHE_PATH = DATA_DIR / "signatures_cache.json"
EXTRAS_PATH = DATA_DIR / "extra_signatures.csv"
# Where the old Install.bat versions lived, to bring settings over and tidy up.
OLD_INSTALLS = [Path(os.environ.get("LOCALAPPDATA") or "~") / n for n in ("ASOP-RockCrawler", "ASOP-HUD-Lookup")]

DEFAULTS: dict = {
    "hotkey_edit": "ctrl+shift+f8",
    "hotkey_toggle": "ctrl+shift+f9",
    "hotkey_quit": "ctrl+shift+f10",
    "exit_with_game": True,  # quit by itself once Star Citizen has been closed
    "start_minimized": False,
    # Overlay only shows while this window is in front. Blank both to always show.
    "target_process": "StarCitizen.exe",
    "target_title": "",
    # Boxes are [x, y, w, h] relative to the game window's client area, at ref_client size.
    "ref_client": None,
    "read_box": [1180, 640, 160, 40],
    "display_box": [1180, 686, 340, 40],
    # Reading
    "scan_ms": 250,
    "confirm_reads": 2,  # matching reads before a first result shows
    "switch_reads": 3,  # matching reads in a row before it changes to a different result
    "hold_ms": 4000,  # how long a result lingers through unreadable frames
    "empty_clear_ms": 800,  # how fast it clears once the number is really gone
    "fade_ms": 450,
    "min_value": 100,
    "max_value": 500000,
    "upscale": 3,
    "invert": False,
    "threshold": 0,  # 0 = auto; otherwise 1-254 on the contrast-stretched image
    "remove_lines": True,
    # Image variants OCR'd each time the number changes: [channel, level]. level raises
    # the cut toward white so only digit cores (not their glow) are kept.
    "variants": [["max", 0.4], ["lum", 0.35], ["lum", 0.55]],
    "ocr_engine": "auto",  # auto | windows | tesseract
    # Lookup
    "data_url": "https://asop.f919.org/api/game-tools/mining",
    "corrections_url": "https://asop.f919.org/api/corrections/live",  # members' fixes, as on the site; "" = game files only
    "refresh_hours": 12,
    "max_cluster": 8,  # only for extra_signatures.csv rows; ASOP's rows carry their own limit
    "methods": ["ship", "hand", "roc", "salvage"],
    "place": "",  # where you're mining ("" = anywhere): rocks that don't spawn there drop out
    # Wording on the overlay; the _short forms are used when one reading fits several kinds.
    "labels": {"hand": "Hand Minable", "roc": "ROC Minable", "wreckage": "Wreckage", "hand_short": "Hand", "roc_short": "ROC"},
    "config_version": 2,
    "short_names": True,
    "show_single_count": True,
    "max_hits": 3,
    "no_match_text": "???",  # a clear number that isn't any known rock or wreck
    # Look
    "font_family": "Bahnschrift SemiBold",
    "font_size": 22,
    "brightness": 1.0,
    "opacity": 0.92,
    "color_mode": "auto",  # auto = take the colour from the HUD text being read
    "custom_color": "#8fd3ff",
    "align": "left",
    "shadow": True,
    "hide_from_capture": True,
}

KEY_COLOR = "#010101"  # transparent colour key for the display window
EDIT_BG = theme.BG
ACCENT = theme.ACC  # ASOP gold

# ── Win32 ─────────────────────────────────────────────────────────────────────

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor: Tk, mss and Win32 all agree on pixels
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype = wt.HWND
user32.GetAncestor.restype = wt.HWND
user32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
user32.GetParent.restype = wt.HWND
user32.GetParent.argtypes = [wt.HWND]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, wt.LONG]
user32.SetWindowDisplayAffinity.argtypes = [wt.HWND, wt.DWORD]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

GWL_EXSTYLE = -20
WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x80000, 0x20, 0x80, 0x08000000
WS_EX_APPWINDOW = 0x40000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED
WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000

_exe_cache: dict[int, str] = {}


def hwnd_of(win: tk.Misc) -> int:
    win.update_idletasks()
    child = win.winfo_id()
    return user32.GetAncestor(child, 2) or user32.GetParent(child) or child


def window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_exe(hwnd) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in _exe_cache:
        return _exe_cache[pid.value]
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value)
        kernel32.CloseHandle(h)
    if len(_exe_cache) > 256:
        _exe_cache.clear()
    _exe_cache[pid.value] = name
    return name


def client_rect(hwnd) -> tuple[int, int, int, int] | None:
    r, p = wt.RECT(), wt.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(r)) or not user32.ClientToScreen(hwnd, ctypes.byref(p)):
        return None
    if r.right <= 0 or r.bottom <= 0:
        return None
    return p.x, p.y, r.right, r.bottom


def list_windows() -> list[tuple[str, str]]:
    found = []
    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            t = window_title(hwnd)
            if t.strip():
                found.append((window_exe(hwnd), t))
        return True

    user32.EnumWindows(proc(cb), 0)
    return sorted(set(found), key=lambda x: (x[0].lower(), x[1].lower()))


def set_overlay_style(hwnd, click_through: bool, hide_from_capture: bool) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    style &= ~WS_EX_APPWINDOW
    style = (style | WS_EX_TRANSPARENT) if click_through else (style & ~WS_EX_TRANSPARENT)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ctypes.c_long(style).value)
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)
    # Keep our own text out of screen captures, so the reader never reads the overlay.
    user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if hide_from_capture else WDA_NONE)


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # reliable GetLastError for the mutex check
_k32.CreateMutexW.restype = wt.HANDLE
_k32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateEventW.restype = wt.HANDLE
kernel32.CreateEventW.argtypes = [wt.LPVOID, wt.BOOL, wt.BOOL, wt.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
kernel32.SetEvent.argtypes = kernel32.ResetEvent.argtypes = kernel32.ReleaseMutex.argtypes = [wt.HANDLE]


class SingleInstance:
    """Only one copy runs. Starting a new copy asks the running one to quit and takes over,
    so re-running the installer or a shortcut never leaves two overlays on screen."""

    def __init__(self):
        self.quit_event = kernel32.CreateEventW(None, True, False, "Local\\ASOP-RockCrawler-Quit")
        self.mutex = _k32.CreateMutexW(None, True, "Local\\ASOP-RockCrawler")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: another copy owns it
            kernel32.SetEvent(self.quit_event)
            kernel32.WaitForSingleObject(self.mutex, 8000)  # it releases on exit
        kernel32.ResetEvent(self.quit_event)

    def asked_to_quit(self) -> bool:
        return kernel32.WaitForSingleObject(self.quit_event, 0) == 0

    def release(self):
        kernel32.ReleaseMutex(self.mutex)


def migrate_old_install() -> None:
    """First run of an installed (Setup.exe) copy: bring settings over from the old
    Install.bat versions, then remove their shortcuts and folders. Only touches the old
    folders and shortcuts that point into them, never Velopack's own install."""
    if not FROZEN:
        return
    olds = [o for o in OLD_INSTALLS if o.exists() and o != APP_DIR and o not in APP_DIR.parents]
    if not olds:
        return
    for old in olds:
        for name in ("config.json", "extra_signatures.csv", "signatures_cache.json"):
            if (old / name).exists() and not (DATA_DIR / name).exists():
                try:
                    shutil.copy2(old / name, DATA_DIR / name)
                except OSError:
                    pass

    def tidy():
        time.sleep(3)  # let the old copy finish quitting (SingleInstance asked it to)
        script = (
            "$sh = New-Object -ComObject WScript.Shell; "
            "foreach ($d in [Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop')) { "
            "Get-ChildItem -LiteralPath $d -Filter *.lnk -ErrorAction SilentlyContinue | ForEach-Object { "
            "$t = $sh.CreateShortcut($_.FullName).TargetPath; "
            "if ($t -like '*\\ASOP-RockCrawler\\.venv\\*' -or $t -like '*\\ASOP-HUD-Lookup\\.venv\\*') "
            "{ Remove-Item -LiteralPath $_.FullName -Force } } }"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           creationflags=0x08000000, timeout=60)  # CREATE_NO_WINDOW
        except Exception:  # noqa: BLE001
            pass
        for old in olds:
            shutil.rmtree(old, ignore_errors=True)

    threading.Thread(target=tidy, daemon=True).start()


def desktop_dir() -> Path:
    """The real Desktop folder (follows OneDrive / redirected desktops)."""
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf) == 0:  # CSIDL_DESKTOPDIRECTORY
        return Path(buf.value)
    return Path.home() / "Desktop"


def desktop_shortcut() -> Path:
    return desktop_dir() / f"{APP_NAME}.lnk"  # same name Setup.exe uses for its shortcuts


def has_desktop_shortcut() -> bool:
    try:
        return desktop_shortcut().exists()
    except Exception:  # noqa: BLE001
        return False


def set_desktop_shortcut(on: bool) -> bool:
    """Add or remove the desktop shortcut; returns whether one exists afterwards.
    Only for installed copies: it points at this exe, which updates keep in place."""
    if not FROZEN:
        return has_desktop_shortcut()
    lnk = desktop_shortcut()
    try:
        if not on:
            lnk.unlink(missing_ok=True)
        else:
            exe = Path(sys.executable)
            q = lambda p: str(p).replace("'", "''")  # noqa: E731 - PowerShell single-quote escaping
            script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(lnk)}'); "
                      f"$s.TargetPath = '{q(exe)}'; $s.WorkingDirectory = '{q(exe.parent)}'; "
                      f"$s.IconLocation = '{q(exe)},0'; $s.Description = '{APP_NAME}'; $s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           creationflags=0x08000000, timeout=30)  # CREATE_NO_WINDOW
    except Exception:  # noqa: BLE001
        _log_error("desktop shortcut")
    return has_desktop_shortcut()


class HotkeyThread(threading.Thread):
    """Global hotkeys via RegisterHotKey; works while the game has focus. No extra packages."""

    def __init__(self, keys: dict[int, str], out: queue.Queue):
        super().__init__(daemon=True)
        self.keys, self.out = keys, out

    def run(self):
        for hid, spec in self.keys.items():
            if not spec.strip():
                self.out.put(("hotkey_status", (hid, spec, False, "off")))
                continue
            try:
                mods, vk = core.parse_hotkey(spec)
            except ValueError as e:
                self.out.put(("hotkey_status", (hid, spec, False, str(e))))
                continue
            if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                self.out.put(("hotkey_status", (hid, spec, True, "ok")))
            else:
                self.out.put(("hotkey_status", (hid, spec, False, "taken by another app")))
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.out.put(("hotkey", int(msg.wParam)))
        for hid in self.keys:
            user32.UnregisterHotKey(None, hid)

    def stop(self):
        if self.native_id:
            user32.PostThreadMessageW(self.native_id, WM_QUIT, 0, 0)


# ── OCR ───────────────────────────────────────────────────────────────────────

class WindowsOcr:
    """The OCR engine built into Windows 10/11: fast, offline, nothing else to install."""
    name = "Windows OCR"

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        self._Decoder, self._Writer, self._Stream = BitmapDecoder, DataWriter, InMemoryRandomAccessStream
        self.engine = OcrEngine.try_create_from_user_profile_languages() or OcrEngine.try_create_from_language(Language("en-US"))
        if self.engine is None:
            raise RuntimeError("No Windows OCR language installed (Settings → Time & language → Language → add English)")
        self.loop = asyncio.new_event_loop()

    def read(self, img: Image.Image) -> str:
        return self.loop.run_until_complete(self._read(img))

    async def _read(self, img: Image.Image) -> str:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "BMP")
        data = buf.getvalue()
        stream = self._Stream()
        writer = self._Writer(stream)
        try:
            writer.write_bytes(data)
        except TypeError:
            writer.write_bytes(list(data))
        await writer.store_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await self._Decoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await self.engine.recognize_async(bitmap)
        return result.text or ""


class TesseractOcr:
    name = "Tesseract"

    def __init__(self):
        import pytesseract
        pytesseract.get_tesseract_version()
        self.t = pytesseract

    def read(self, img: Image.Image) -> str:
        return self.t.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=0123456789,.")


def make_ocr(choice: str):
    order = {"windows": [WindowsOcr], "tesseract": [TesseractOcr]}.get(choice, [WindowsOcr, TesseractOcr])
    errors = []
    for cls in order:
        try:
            return cls(), None
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cls.name}: {e}")
    return None, "No OCR engine available. " + " | ".join(errors)


# ── Overlay boxes ─────────────────────────────────────────────────────────────

class Box(tk.Toplevel):
    """A borderless always-on-top box that can be dragged and resized in edit mode."""
    EDGE = 8

    def __init__(self, app: "App", kind: str):
        super().__init__(app.root)
        self.app, self.kind = app, kind
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.geom = (0, 0, 10, 10)
        self._drag = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Configure>", lambda e: self.app.redraw())
        self.withdraw()
        self.hwnd = None

    def place_at(self, x, y, w, h):
        g = (int(x), int(y), max(24, int(w)), max(14, int(h)))
        if g != self.geom:
            self.geom = g
            self.geometry(f"{g[2]}x{g[3]}+{g[0]}+{g[1]}")

    def show(self):
        if self.state() != "normal":
            self.deiconify()
            self.attributes("-topmost", True)
            if self.hwnd is None:
                self.hwnd = hwnd_of(self)
            self.apply_style()

    def hide(self):
        if self.state() == "normal":
            self.withdraw()

    def apply_style(self):
        if self.hwnd:
            # The READ box always stays out of captures (it sits over what we read); the display
            # box follows the setting, in case you want the overlay to show in OBS/recordings.
            hide = True if self.kind == "read" else bool(self.app.cfg["hide_from_capture"])
            set_overlay_style(self.hwnd, click_through=not self.app.editing, hide_from_capture=hide)

    def _zone(self, x, y):
        w, h, e = self.geom[2], self.geom[3], self.EDGE
        z = ("n" if y < e else "s" if y > h - e else "") + ("w" if x < e else "e" if x > w - e else "")
        return z or "move"

    def _hover(self, ev):
        if not self.app.editing:
            return
        cursors = {"move": "fleur", "n": "sb_v_double_arrow", "s": "sb_v_double_arrow", "e": "sb_h_double_arrow",
                   "w": "sb_h_double_arrow", "ne": "size_ne_sw", "sw": "size_ne_sw", "nw": "size_nw_se", "se": "size_nw_se"}
        self.canvas.configure(cursor=cursors[self._zone(ev.x, ev.y)])

    def _press(self, ev):
        if self.app.editing:
            self._drag = (self._zone(ev.x, ev.y), ev.x_root, ev.y_root, self.geom)

    def _motion(self, ev):
        if not self._drag:
            return
        zone, x0, y0, (gx, gy, gw, gh) = self._drag
        dx, dy = ev.x_root - x0, ev.y_root - y0
        if zone == "move":
            self.place_at(gx + dx, gy + dy, gw, gh)
            return
        x, y, w, h = gx, gy, gw, gh
        if "e" in zone:
            w = gw + dx
        if "s" in zone:
            h = gh + dy
        if "w" in zone:
            x, w = gx + dx, gw - dx
        if "n" in zone:
            y, h = gy + dy, gh - dy
        self.place_at(x, y, max(24, w), max(14, h))

    def _release(self, _ev):
        if self._drag:
            self._drag = None
            self.app.redraw()


# ── App ───────────────────────────────────────────────────────────────────────

class App:
    HK_EDIT, HK_TOGGLE, HK_QUIT = 1, 2, 3

    def __init__(self, instance: SingleInstance):
        self.instance = instance
        migrate_old_install()
        first_run = not CONFIG_PATH.exists()
        self.updater = updater.Updater()
        self.cfg = self._load_config()
        theme.load_fonts(APP_DIR)
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        theme.apply(self.root)
        self.icon = None
        try:
            ico = Image.open(theme.assets_dir(APP_DIR) / "asop.png").convert("RGBA")
            ico.thumbnail((64, 64))
            self.icon = ImageTk.PhotoImage(ico)
            self.root.iconphoto(True, self.icon)
        except Exception:  # noqa: BLE001 - icon is cosmetic
            pass
        self.q: queue.Queue = queue.Queue()
        self.sct = mss.mss()
        self.ocr, self.ocr_error = make_ocr(self.cfg["ocr_engine"])

        self.editing = False
        self.user_hidden = False
        self.active = False
        self.target_hwnd = None
        self.last_client = None
        self.hk_status: dict[int, tuple] = {}

        self.text = ""  # what the display shows
        self.value = None
        self.stab = core.Stabilizer()
        self.fading, self.fade_job = False, None
        self.last_hash = None
        self.last_parsed: tuple = (None, "")
        self.hud_rgb: tuple[int, int, int] | None = None
        self.debug = {"raw": "", "value": None, "img": None, "match": ""}

        self.table = core.load_cache(CACHE_PATH) or {"signatures": [], "build": None, "fetched_at": 0}
        self.chances = core.chance_index(self.table)
        self.extras = core.load_extras(EXTRAS_PATH)
        self.table_status = "loading…"
        stale = time.time() - (self.table.get("fetched_at") or 0) > self.cfg["refresh_hours"] * 3600
        old_format = bool(self.table["signatures"]) and ("maxCluster" not in self.table["signatures"][0] or "spots" not in self.table)
        if stale or old_format or not self.table["signatures"]:
            self.refresh_table()
        else:
            self.table_status = "cached"

        self.read_box = Box(self, "read")
        self.disp_box = Box(self, "display")

        self.closing = False
        self.closed = threading.Event()
        self.game_seen = False
        self.game_gone_since = None
        self.messages: list[str] = []
        self.hotkeys = None
        self.start_hotkeys()

        self.main = MainWindow(self)
        self.root.after(30, self._poll_queue)
        self.root.after(60, self._tick_focus)
        self.root.after(120, self._tick_scan)
        if first_run:
            self.root.after(400, self.enter_edit)
        if FROZEN and not self.cfg.get("asked_desktop_shortcut"):
            self.root.after(900, self._ask_desktop_shortcut)
        elif self.cfg["start_minimized"]:
            self.root.iconify()

    HOTKEY_KEYS = {HK_EDIT: "hotkey_edit", HK_TOGGLE: "hotkey_toggle", HK_QUIT: "hotkey_quit"}

    def start_hotkeys(self):
        """(Re)register the global hotkeys from config. Safe to call again after changing them."""
        if self.hotkeys:
            self.hotkeys.stop()
            self.hotkeys.join(timeout=1)
        self.hk_status = {}
        self.hotkeys = HotkeyThread({hid: self.cfg[key] for hid, key in self.HOTKEY_KEYS.items()}, self.q)
        self.hotkeys.start()

    def _ask_desktop_shortcut(self):
        """Setup.exe only adds a Start menu shortcut; ask once whether to add one on the desktop."""
        self.cfg["asked_desktop_shortcut"] = True
        self.save_config()
        if has_desktop_shortcut():  # older installs already have one
            return
        d = tk.Toplevel(self.root)
        d.title(APP_NAME)
        d.resizable(False, False)
        d.transient(self.root)
        theme.hazard_stripe(d).pack(fill="x")
        f = ttk.Frame(d, padding=(20, 16, 20, 16))
        f.pack(fill="both")
        ttk.Label(f, text="Add a RockCrawler shortcut to your desktop?").pack(anchor="w")
        ttk.Label(f, text="You can change this any time under Game & hotkeys → App.", style="Dim.TLabel").pack(anchor="w", pady=(4, 14))
        bf = ttk.Frame(f)
        bf.pack(fill="x")

        def answer(yes):
            if yes:
                self.main.settings.shortcut_var.set(set_desktop_shortcut(True))
            d.destroy()

        ttk.Button(bf, text="Add shortcut", style="Accent.TButton", command=lambda: answer(True)).pack(side="left")
        ttk.Button(bf, text="No thanks", command=lambda: answer(False)).pack(side="left", padx=8)
        d.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - d.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + 120
        d.geometry(f"+{x}+{y}")
        theme.style_window(d, hwnd_of(d), self.icon)
        d.grab_set()

    def toggle_edit(self):
        self.exit_edit() if self.editing else self.enter_edit()

    def restart_to_update(self):
        """Install the downloaded update now (RockCrawler restarts on the new version)."""
        try:
            if self.editing:
                self.exit_edit()
            self.save_config()
        finally:
            self.updater.apply_and_restart()

    def toggle_hidden(self):
        self.user_hidden = not self.user_hidden

    # config
    def _load_config(self) -> dict:
        cfg = dict(DEFAULTS)
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
        except (OSError, ValueError):
            pass
        # Settings from before v2: salvage on, "???" for unknown numbers, new wording.
        if int(cfg.get("config_version") or 1) < 2:
            if sorted(cfg.get("methods") or []) == ["hand", "roc", "ship"]:
                cfg["methods"] = DEFAULTS["methods"]
            if cfg.get("no_match_text", "") == "":
                cfg["no_match_text"] = DEFAULTS["no_match_text"]
            cfg["labels"] = {}
            cfg["config_version"] = 2
        cfg["labels"] = {k: v for k, v in {**DEFAULTS["labels"], **(cfg.get("labels") or {})}.items() if k != "hull"}
        return cfg

    def save_config(self):
        CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2), "utf-8")

    # signature table
    @property
    def signatures(self) -> list[dict]:
        return self.table.get("signatures", []) + self.extras

    def matches(self, v: int) -> list[dict]:
        return core.match_signature(v, self.signatures, int(self.cfg["max_cluster"]), self.cfg["methods"] or None,
                                    place=self.cfg["place"] or None, chances=self.chances)

    def is_known(self, v: int) -> bool:
        return bool(self.matches(v))

    def refresh_table(self):
        self.table_status = "updating…"
        url = self.cfg["data_url"]

        def work():
            try:
                self.q.put(("table", core.fetch_signatures(url, corrections_url=self.cfg["corrections_url"] or None)))
            except Exception as e:  # noqa: BLE001
                self.q.put(("table_error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    # geometry: boxes are stored relative to the game's client area, scaled if its size changes
    def frame(self):
        r = client_rect(self.target_hwnd) if self.target_hwnd and user32.IsWindow(self.target_hwnd) else None
        if r:
            self.last_client = r
        r = r or self.last_client
        if not r:
            return 0, 0, 1.0, 1.0, None
        x0, y0, cw, ch = r
        ref = self.cfg.get("ref_client")
        sx, sy = (cw / ref[0], ch / ref[1]) if ref else (1.0, 1.0)
        return x0, y0, sx, sy, (cw, ch)

    def to_screen(self, box):
        x0, y0, sx, sy, _ = self.frame()
        return x0 + box[0] * sx, y0 + box[1] * sy, box[2] * sx, box[3] * sy

    def capture_boxes(self):
        """Store the boxes' current screen positions back into config (at scale 1)."""
        x0, y0, sx, sy, size = self.frame()
        self.cfg["font_size"] = max(6, round(self.cfg["font_size"] * sy))
        self.cfg["ref_client"] = list(size) if size else None
        for key, b in (("read_box", self.read_box), ("display_box", self.disp_box)):
            x, y, w, h = b.geom
            self.cfg[key] = [x - x0, y - y0, w, h]

    def font_px(self) -> int:
        return max(6, round(self.cfg["font_size"] * self.frame()[3]))

    # target window
    def is_target(self, hwnd) -> bool:
        proc, title = self.cfg["target_process"].strip().lower(), self.cfg["target_title"].strip().lower()
        if not hwnd or (not proc and not title):
            return False
        if proc and window_exe(hwnd).lower() != proc:
            return False
        return not title or title in window_title(hwnd).lower()

    def own_hwnds(self) -> set:
        hs = {self.read_box.hwnd, self.disp_box.hwnd, self.main.hwnd}
        return {h for h in hs if h}

    def _tick_focus(self):
        try:
            fg = user32.GetForegroundWindow()
            targeted = bool(self.cfg["target_process"].strip() or self.cfg["target_title"].strip())
            if not targeted:
                self.target_hwnd, self.active = None, True
            elif self.is_target(fg):
                self.target_hwnd, self.active = fg, True
            elif fg in self.own_hwnds() and self.target_hwnd and user32.IsWindow(self.target_hwnd):
                self.active = True
            else:
                self.active = False
            if self.active and self.target_hwnd and user32.IsIconic(self.target_hwnd):
                self.active = False
            if self._game_closed():
                self.quit()
                return

            if self.editing:
                self.read_box.show()
                self.disp_box.show()
            else:
                self.read_box.hide()
                if self.active and not self.user_hidden:
                    self.disp_box.place_at(*self.to_screen(self.cfg["display_box"]))
                    self.disp_box.show()
                else:
                    self.disp_box.hide()
        finally:
            if not self.closing:
                self.root.after(250, self._tick_focus)

    # reading
    def _game_closed(self) -> bool:
        """True once the game window we've been following is gone for good (10 s grace,
        so a window being recreated during loading doesn't count)."""
        if not (self.cfg["exit_with_game"] and self.cfg["target_process"].strip()) or self.editing:
            return False
        if self.target_hwnd and user32.IsWindow(self.target_hwnd):
            self.game_seen, self.game_gone_since = True, None
            return False
        if not self.game_seen:
            return False
        proc = self.cfg["target_process"].strip().lower()
        if any(exe.lower() == proc for exe, _ in list_windows()):
            self.game_gone_since = None
            return False
        now = time.monotonic()
        self.game_gone_since = self.game_gone_since or now
        return now - self.game_gone_since > 10

    def _tick_scan(self):
        try:
            if (self.active or self.editing) and self.ocr:
                self._scan()
        except Exception as e:  # noqa: BLE001
            self.debug["raw"] = f"error: {e}"
        finally:
            if not self.closing:
                self.root.after(max(50, int(self.cfg["scan_ms"])), self._tick_scan)

    def _scan(self):
        if self.editing:
            x, y, w, h = self.read_box.geom
        else:
            x, y, w, h = (round(v) for v in self.to_screen(self.cfg["read_box"]))
        shot = self.sct.grab({"left": x, "top": y, "width": max(1, w), "height": max(1, h)})
        raw = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        now = time.monotonic()
        # Several cleaned-up variants of the crop; each gets an OCR pass and the reading
        # that is a real signature wins (see core.pick_value).
        variants = [core.preprocess(raw, upscale=int(self.cfg["upscale"]), invert=bool(self.cfg["invert"]),
                                    threshold=int(self.cfg["threshold"]), remove_lines=bool(self.cfg["remove_lines"]),
                                    channel=ch, level=float(lv)) for ch, lv in self.cfg["variants"]]
        variants = [v for v in variants if v is not None]
        clean = variants[0] if variants else None
        if not variants:
            value, text = None, ""
            self.last_hash = None
        else:
            h = hashlib.md5(b"".join(v.tobytes() for v in variants)).digest()
            if h == self.last_hash:
                # Same pixels as last time: reuse the last result instead of re-running OCR.
                # It still counts as a read, so a number that sits still stays on screen.
                value, text = self.last_parsed
            else:
                self.last_hash = h
                texts = [self.ocr.read(v) for v in variants]
                text = "  |  ".join(t.strip() for t in texts)
                value = core.pick_value(texts, self.is_known if self.signatures else None,
                                        int(self.cfg["min_value"]), int(self.cfg["max_value"]))
                self.last_parsed = (value, text)
        self.debug["crop"] = raw

        self.debug.update(raw=text.strip(), value=value, img=clean)
        self._show(self.stab.update(value, empty=clean is None, now=now), raw if value is not None else None)

    def _show(self, value, raw=None):
        st = self.stab
        st.confirm, st.switch = int(self.cfg["confirm_reads"]), int(self.cfg["switch_reads"])
        st.hold, st.empty_hold = self.cfg["hold_ms"] / 1000, self.cfg["empty_clear_ms"] / 1000
        if raw is not None and value is not None:
            c = core.hud_color(raw)
            if c:
                self.hud_rgb = c if self.hud_rgb is None else core.blend(self.hud_rgb, c, 0.3)
        if value is None:
            if self.text and not self.fading:
                self._fade_out()
            return
        if self.fading:  # it came back while fading: snap back
            self._cancel_fade()
        if value != self.value:
            self.value = value
            hits = self.matches(value)
            self.text = core.format_hits(hits, self.cfg["short_names"], self.cfg["show_single_count"], int(self.cfg["max_hits"]),
                                         labels=self.cfg["labels"]) \
                or self.cfg["no_match_text"]
            self.debug["match"] = self.text or "(no match)"
            self.redraw()

    def _fade_out(self):
        steps = max(1, int(self.cfg["fade_ms"]) // 40)
        self.fading = True
        base = float(self.cfg["opacity"])

        def step(i):
            if not self.fading:
                return
            if i >= steps or self.editing:
                self.fading = False
                self.text, self.value = "", None
                self.redraw()
                return
            try:
                self.disp_box.attributes("-alpha", base * (1 - (i + 1) / steps))
            except tk.TclError:
                return
            self.fade_job = self.root.after(40, step, i + 1)

        step(0)

    def _cancel_fade(self):
        self.fading = False
        if self.fade_job:
            self.root.after_cancel(self.fade_job)
            self.fade_job = None
        self.redraw()  # restores normal opacity

    # drawing
    def text_color(self) -> str:
        base = self.hud_rgb if (self.cfg["color_mode"] == "auto" and self.hud_rgb) else core.from_hex(self.cfg["custom_color"])
        return core.to_hex(core.apply_brightness(base, float(self.cfg["brightness"])))

    def redraw(self):
        if not hasattr(self, "disp_box"):
            return
        for b in (self.read_box, self.disp_box):
            b.canvas.delete("all")
        w, h = self.disp_box.geom[2], self.disp_box.geom[3]
        c = self.disp_box.canvas
        if self.editing:
            self.disp_box.attributes("-transparentcolor", "")
            self.disp_box.attributes("-alpha", 0.9)
            c.configure(bg=EDIT_BG)
            c.create_rectangle(1, 1, w - 2, h - 2, outline=ACCENT, dash=(4, 3))
            c.create_text(4, 2, text="DISPLAY", anchor="nw", fill=ACCENT, font=theme.LABEL)
            text = self.text
        else:
            self.disp_box.attributes("-transparentcolor", KEY_COLOR)
            self.disp_box.attributes("-alpha", float(self.cfg["opacity"]))
            c.configure(bg=KEY_COLOR)
            text = self.text
        if text:
            align = self.cfg["align"]
            anchor, x = {"left": ("w", 6), "center": ("center", w / 2), "right": ("e", w - 6)}.get(align, ("w", 6))
            font = (self.cfg["font_family"], -self.font_px())
            if self.cfg["shadow"]:
                c.create_text(x + 1, h / 2 + 1, text=text, anchor=anchor, font=font, fill="#000000")
            c.create_text(x, h / 2, text=text, anchor=anchor, font=font, fill=self.text_color())

        if self.editing:
            rw, rh = self.read_box.geom[2], self.read_box.geom[3]
            rc = self.read_box.canvas
            self.read_box.attributes("-alpha", 0.45)
            rc.configure(bg="#0b2f3a")
            rc.create_rectangle(1, 1, rw - 2, rh - 2, outline="#4fd1ff", width=2)
            rc.create_text(4, 2, text="READ", anchor="nw", fill="#4fd1ff", font=theme.LABEL)

    # modes
    def enter_edit(self):
        if self.editing:
            return
        self.editing = True
        self.read_box.place_at(*self.to_screen(self.cfg["read_box"]))
        self.disp_box.place_at(*self.to_screen(self.cfg["display_box"]))
        self.read_box.show()
        self.disp_box.show()
        self.capture_boxes()  # normalise to the current game size so dragging is 1:1
        for b in (self.read_box, self.disp_box):
            b.apply_style()
        # Keep the window over the game while you line things up, on the tab with the preview.
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.main.settings.pills.select("Reading")
        self.redraw()

    def exit_edit(self):
        if not self.editing:
            return
        self.capture_boxes()
        self.editing = False
        self.save_config()
        for b in (self.read_box, self.disp_box):
            b.apply_style()
            b.canvas.configure(cursor="")
        self.read_box.hide()
        self.root.attributes("-topmost", False)
        self.last_hash = None
        self.redraw()

    def _poll_queue(self):
        if self.instance.asked_to_quit():  # a newer copy started: hand over to it
            self.quit()
            return
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "hotkey" and payload == self.HK_EDIT:
                    self.toggle_edit()
                elif (kind == "hotkey" and payload == self.HK_QUIT) or kind == "quit":
                    self.quit()
                    return
                elif kind == "hotkey" and payload == self.HK_TOGGLE:
                    self.toggle_hidden()
                elif kind == "hotkey_status":
                    self.hk_status[payload[0]] = payload[1:]
                elif kind == "table":
                    self.table = payload
                    self.chances = core.chance_index(payload)
                    core.save_cache(CACHE_PATH, payload)
                    fixes = payload.get("corrections")
                    self.table_status = "up to date" + (f", {fixes} member fix{'es' if fixes != 1 else ''}" if fixes else "")
                    self.value, self.last_hash = None, None  # re-match with the new table
                elif kind == "table_error":
                    self.table_status = f"offline ({payload})" if self.table["signatures"] else f"FAILED: {payload}"
                elif kind == "error":
                    self.messages.append(payload)
        except queue.Empty:
            pass
        if not self.closing:
            self.root.after(50, self._poll_queue)

    def quit(self):
        """Clean shutdown: save settings, free the hotkeys, close the windows and the
        screen grabber, and release the single-instance lock. Safe to call twice."""
        if self.closing:
            return
        self.closing = True
        try:
            if self.editing:
                self.exit_edit()
            self.save_config()
        except Exception:  # noqa: BLE001 - still shut down
            _log_error("saving on quit")
        self.hotkeys.stop()
        self.hotkeys.join(timeout=1)
        for close in (self.sct.close, self.root.destroy, lambda: theme.unload_fonts(APP_DIR), self.instance.release):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
        self.closed.set()

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.mainloop()


# ── Edit panel ────────────────────────────────────────────────────────────────

FONTS = ["Barlow SemiBold", "Barlow Condensed SemiBold", "IBM Plex Mono Medium", "Bahnschrift SemiBold", "Bahnschrift", "Bahnschrift SemiCondensed", "Segoe UI Semibold",
         "Segoe UI", "Consolas", "Arial Narrow", "Electrolize", "Orbitron", "Rajdhani SemiBold"]


class MainWindow:
    """The one window (the Tk root), layout C: status and the main actions on the left,
    settings tabs on the right, the current reading in the gold row across the top.
    Closing it quits the app; minimise it to keep it out of the way."""

    def __init__(self, app: App):
        self.app = app
        r = app.root
        r.resizable(False, False)
        _, slot = theme.header(r, "RockCrawler")
        self.game = tk.Label(slot, text="●  —", bg=theme.CARD, fg=theme.DIM, font=theme.PILL, width=24, anchor="e")
        self.game.pack(side="right")

        # Gold highlight row: the current reading (like a selected Event Manager row)
        gold = tk.Frame(r, bg=theme.ACC, padx=18, pady=10)
        gold.pack(fill="x")
        ttk.Label(gold, text="READING", style="GoldKey.TLabel", width=11).pack(side="left")
        self.reading = ttk.Label(gold, text="—", style="GoldValue.TLabel", width=34, anchor="w")
        self.reading.pack(side="left")

        # Footer first (packed to the bottom), so the body takes the space above it.
        foot = tk.Frame(r, bg=theme.CARD, padx=16, pady=8)
        foot.pack(fill="x", side="bottom")
        tk.Frame(r, bg=theme.LINE, height=1).pack(fill="x", side="bottom")  # border above the footer
        sm = tk.BooleanVar(value=app.cfg["start_minimized"])
        tk.Checkbutton(foot, text="Start minimized", variable=sm, bg=theme.CARD, fg=theme.FG, selectcolor=theme.BG,
                       activebackground=theme.CARD, activeforeground=theme.HI, font=theme.BODY, bd=0, highlightthickness=0,
                       command=lambda: (app.cfg.__setitem__("start_minimized", sm.get()), app.save_config())).pack(side="left")
        ttk.Button(foot, text="Quit", command=app.quit, width=10).pack(side="right")
        # Version and updates: click the version to check now.
        self.version = tk.Label(foot, text="—", bg=theme.CARD, fg=theme.DIM, font=theme.BODY, width=30, anchor="e",
                                cursor="hand2")
        self.version.pack(side="right", padx=(0, 14))
        self.version.bind("<Button-1>", lambda _e: app.updater.check(force=True))
        self.update_btn = ttk.Button(foot, text="Restart to update", style="Accent.TButton", command=app.restart_to_update)

        body = tk.Frame(r, bg=theme.BG)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body, padding=(18, 14, 18, 14))
        left.pack(side="left", fill="y")
        theme.band(left, "Status").pack(fill="x", pady=(0, 8))
        kv = ttk.Frame(left)
        kv.pack(anchor="w", padx=4)
        self.rows = {}
        for i, name in enumerate(("Game", "Mining at", "Signatures", "Hotkeys")):
            ttk.Label(kv, text=name.upper(), style="Key.TLabel", width=11).grid(row=i, column=0, sticky="nw", pady=3)
            if name == "Hotkeys":  # one line per hotkey, so only a failing one turns red
                lab = ttk.Frame(kv)
                self.hk_labels = [ttk.Label(lab, text="—", width=24, anchor="w") for _ in range(3)]
                for hl in self.hk_labels:
                    hl.pack(anchor="w")
            else:
                lab = ttk.Label(kv, text="—", width=24, anchor="w")
            lab.grid(row=i, column=1, sticky="w", pady=3)
            self.rows[name] = lab
        ttk.Frame(left, height=12).pack()
        self.edit_btn = ttk.Button(left, command=app.toggle_edit, style="Accent.TButton")
        self.edit_btn.pack(fill="x", pady=(0, 6))
        self.hide_btn = ttk.Button(left, command=app.toggle_hidden)
        self.hide_btn.pack(fill="x")
        tk.Frame(body, bg=theme.LINE, width=1).pack(side="left", fill="y")
        self.settings = SettingsPanel(body, app)
        self.settings.pack(side="left", fill="both", expand=True)

        r.update_idletasks()
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"+{(sw - r.winfo_reqwidth()) // 2}+{max(20, (sh - r.winfo_reqheight()) // 3)}")
        self.hwnd = hwnd_of(r)
        theme.style_window(r, self.hwnd)
        r.after(200, self._refresh)

    def _refresh(self):
        a = self.app
        if a.closing:
            return
        if not a.target_hwnd and (a.cfg["target_process"].strip() or a.cfg["target_title"].strip()):
            game, col = "Not seen yet", theme.DIM
        elif a.active:
            game, col = ("Active", theme.GOOD) if not a.user_hidden else ("Overlay hidden", theme.ACC)
        else:
            game, col = "In background", theme.DIM
        self.game.configure(text=f"●  {game.upper()}", fg=col)
        self.rows["Game"].configure(text=f"●  {game}", foreground=col)
        self.reading.configure(text=theme.fit(f"{a.value:,}  →  {a.text or 'no match'}", 34) if a.value else "—")
        self.rows["Signatures"].configure(text=theme.fit(f"{len(a.signatures)}  ({a.table_status})", 24))
        self.rows["Mining at"].configure(text=theme.fit(a.cfg["place"] or "Anywhere", 24))
        for lab, (hid, key) in zip(self.hk_labels, a.HOTKEY_KEYS.items()):
            spec, ok, msg = a.hk_status.get(hid, (a.cfg[key], None, "starting…"))
            what = {"hotkey_edit": "edit", "hotkey_toggle": "hide", "hotkey_quit": "quit"}[key]
            if msg == "off" or not spec:
                lab.configure(text=f"—  {what} (off)", style="Dim.TLabel")
                continue
            lab.configure(text=theme.fit(f"{spec.upper()}  {what}" + ("" if ok is not False else f"  ⚠ {msg}"), 24),
                          style="Bad.TLabel" if ok is False else "TLabel")
        self.edit_btn.configure(text="Done editing" if a.editing else "Edit layout")
        self.hide_btn.configure(text="Show overlay" if a.user_hidden else "Hide overlay")
        u = a.updater
        u.check()  # rate-limited inside: on start, then every few hours
        self.version.configure(text=theme.fit(u.status(), 30))
        if u.state == "ready" and not self.update_btn.winfo_ismapped():
            self.update_btn.pack(side="right", padx=(0, 10), before=self.version)
        a.root.after(300, self._refresh)


class SettingsPanel(ttk.Frame):
    """The settings, as tabs on the right of the main window (Reading / Look / Game & hotkeys)."""

    def __init__(self, parent, app: App):
        super().__init__(parent)
        self.app = app
        cfg = app.cfg
        pad = {"padx": 8, "pady": 3}
        names = ["Reading", "Look", "Game & hotkeys"]
        # Tab bar: a #333 header row with gold pills, then the tabs stacked in one cell, so
        # the window keeps the size of the largest tab and never jumps when you switch.
        bar = tk.Frame(self, bg=theme.BAND, padx=12, pady=6)
        bar.pack(fill="x")
        body = ttk.Frame(self, padding=(18, 12, 18, 12))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tabs = {}
        for name in names:
            tabs[name] = ttk.Frame(body)
            tabs[name].columnconfigure(1, weight=1)
            tabs[name].grid(row=0, column=0, sticky="nsew")
        self.pills = theme.Pills(bar, names, lambda n: tabs[n].tkraise(), bg=theme.BAND)
        self.pills.pack(side="left")
        f, row, cur, rows = None, 0, None, {}

        def tab(name):
            nonlocal f, row, cur
            if cur:
                rows[cur] = row
            f, row, cur = tabs[name], rows.get(name, 0), name

        def section(text):
            nonlocal row
            theme.band(f, text).grid(row=row, column=0, columnspan=3, sticky="we", pady=(12 if row else 0, 6))
            row += 1

        def note(text, column=0, span=3):
            nonlocal row
            ttk.Label(f, text=text, style="Dim.TLabel", wraplength=380 if column == 0 else 280, justify="left").grid(
                row=row, column=column, columnspan=span, sticky="w", padx=8, pady=(0, 4))
            row += 1

        def labeled(text, widget):
            nonlocal row
            ttk.Label(f, text=text).grid(row=row, column=0, sticky="w", **pad)
            widget.grid(row=row, column=1, columnspan=2, sticky="we", **pad)
            row += 1

        def scale(key, lo, hi, text, fmt="{:.2f}", cast=float):
            var = tk.DoubleVar(value=cfg[key])
            lab = ttk.Label(f, width=9, style="Mono.TLabel")

            def on(_=None):
                cfg[key] = cast(var.get())
                lab.configure(text=("auto" if key == "threshold" and cfg[key] == 0 else fmt.format(cfg[key])))
                app.last_hash = None
                app.redraw()

            nonlocal row
            ttk.Label(f, text=text).grid(row=row, column=0, sticky="w", **pad)
            ttk.Scale(f, from_=lo, to=hi, variable=var, command=on, length=220).grid(row=row, column=1, sticky="we", **pad)
            lab.grid(row=row, column=2, sticky="w")
            row += 1
            on()

        def check(key, text):
            var = tk.BooleanVar(value=cfg[key])

            def on():
                cfg[key] = var.get()
                app.value = None
                app.last_hash = None
                app.redraw()

            nonlocal row
            ttk.Checkbutton(f, text=text, variable=var, command=on).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
            row += 1

        # live preview of what's being read
        tab("Reading")
        section("Reading now")
        fr, self.crop_preview = theme.framed(f, 380, 62)
        fr.grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        fr, self.preview = theme.framed(f, 380, 62)
        fr.grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        # One value per line, fixed width, "—" until there's something to show,
        # so nothing shifts while it reads.
        sf = ttk.Frame(f)
        sf.grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1
        self.status = {}
        for i, key in enumerate(("OCR text", "Value", "Shows", "Engine")):
            ttk.Label(sf, text=key.upper(), style="Key.TLabel", width=12).grid(row=i, column=0, sticky="w", pady=1)
            v = ttk.Label(sf, text="—", style="Mono.TLabel", width=34, anchor="w")
            v.grid(row=i, column=1, sticky="w", pady=1)
            self.status[key] = v
        note("Edit layout: drag READ over the number, DISPLAY where the answer goes.")

        tab("Look")
        section("Text")
        font_var = tk.StringVar(value=cfg["font_family"])
        fc = ttk.Combobox(f, textvariable=font_var, values=FONTS)
        fc.bind("<<ComboboxSelected>>", lambda e: (cfg.__setitem__("font_family", font_var.get()), app.redraw()))
        fc.bind("<Return>", lambda e: (cfg.__setitem__("font_family", font_var.get()), app.redraw()))
        labeled("Font", fc)
        scale("font_size", 8, 72, "Size", "{:.0f}px", lambda v: int(round(v)))
        scale("brightness", 0.3, 2.0, "Brightness")
        scale("opacity", 0.15, 1.0, "Opacity")

        cm = tk.StringVar(value=cfg["color_mode"])
        cf = ttk.Frame(f)
        self.swatch = tk.Label(cf, width=3, bg=cfg["custom_color"], highlightthickness=1,
                               highlightbackground=theme.LINE, bd=0)

        def set_mode():
            cfg["color_mode"] = cm.get()
            app.redraw()

        def pick():
            c = colorchooser.askcolor(cfg["custom_color"], parent=app.root)[1]
            if c:
                cfg["custom_color"] = c
                self.swatch.configure(bg=c)
                cm.set("custom")
                set_mode()

        ttk.Radiobutton(cf, text="Match HUD", value="auto", variable=cm, command=set_mode).pack(side="left")
        ttk.Radiobutton(cf, text="Custom", value="custom", variable=cm, command=set_mode).pack(side="left", padx=(8, 4))
        self.swatch.pack(side="left")
        ttk.Button(cf, text="Pick…", command=pick, width=6).pack(side="left", padx=4)
        labeled("Colour", cf)

        al = tk.StringVar(value=cfg["align"])
        ac = ttk.Combobox(f, textvariable=al, values=["left", "center", "right"], state="readonly", width=8)
        ac.bind("<<ComboboxSelected>>", lambda e: (cfg.__setitem__("align", al.get()), app.redraw()))
        labeled("Align", ac)
        check("shadow", "Drop shadow")
        check("short_names", "Short names (Laranite, not Laranite (Raw))")
        check("show_single_count", "Show 1× for single rocks")

        tab("Reading")
        section("Tuning")
        scale("scan_ms", 100, 1000, "Scan every", "{:.0f} ms", lambda v: int(round(v)))
        scale("hold_ms", 500, 10000, "Linger", "{:.0f} ms", lambda v: int(round(v / 100) * 100))
        scale("switch_reads", 2, 6, "Steadiness", "{:.0f} reads", lambda v: int(round(v)))
        scale("threshold", 0, 254, "Sensitivity", "{:.0f}", lambda v: int(round(v)))
        note("Sensitivity 0 = auto. Raise it if digits blur, lower it if they break up.")
        check("remove_lines", "Remove HUD lines and specks")
        check("invert", "HUD text is darker than its background")

        tab("Game & hotkeys")
        section("Where you're mining")
        note("Rocks that don't spawn there drop out. Wreckage is found anywhere.")
        ANY = "Anywhere"
        place_names: dict[str, str] = {}

        def load_places():
            # Rebuilt each time the list opens: the signature list may arrive after the window does.
            place_names.clear()
            place_names.update({f"{p}  ({sysname})" if sysname else p: p for p, sysname in core.places_of(app.table)})
            pc.configure(values=[ANY] + list(place_names))

        pv = tk.StringVar(value=cfg["place"] or ANY)
        pc = ttk.Combobox(f, textvariable=pv, state="readonly", height=20, postcommand=lambda: load_places())
        load_places()
        pv.set(next((k for k, v in place_names.items() if v == cfg["place"]), cfg["place"] or ANY))

        def chose_place(_):
            cfg["place"] = place_names.get(pv.get(), "")
            app.value, app.last_hash = None, None  # re-match with the new place
            app.save_config()

        pc.bind("<<ComboboxSelected>>", chose_place)
        labeled("Place", pc)

        section("Game window")
        # (A window-title match is still possible with "target_title" in config.json.)
        proc = tk.StringVar(value=cfg["target_process"])
        pe = ttk.Entry(f, textvariable=proc)
        proc.trace_add("write", lambda *_: cfg.__setitem__("target_process", proc.get()))
        labeled("Process", pe)
        wins = ttk.Combobox(f, state="readonly", postcommand=lambda: wins.configure(
            values=[f"{e} — {t[:60]}" for e, t in list_windows()]))

        def chose(_):
            proc.set(wins.get().partition(" — ")[0])

        wins.bind("<<ComboboxSelected>>", chose)
        labeled("Pick a window", wins)
        note("The overlay shows only while this game is in front. Blank = always show.", column=1, span=2)

        section("Hotkeys")
        hk_vars = {}
        for hid, key, label in ((App.HK_EDIT, "hotkey_edit", "Edit layout"), (App.HK_TOGGLE, "hotkey_toggle", "Hide/show"),
                                (App.HK_QUIT, "hotkey_quit", "Quit")):
            v = tk.StringVar(value=cfg[key])
            hk_vars[key] = v
            labeled(label, ttk.Entry(f, textvariable=v))
        self.hk_msg = ttk.Label(f, text="Optional. Blank one to turn it off.", style="Dim.TLabel", wraplength=300, justify="left")
        self.hk_msg.grid(row=row, column=0, columnspan=2, sticky="w", padx=8)

        def apply_hotkeys():
            for key, v in hk_vars.items():
                spec = v.get().strip().lower()
                if spec:
                    try:
                        core.parse_hotkey(spec)
                    except ValueError as e:
                        self.hk_msg.configure(text=str(e), style="Bad.TLabel")
                        return
                cfg[key] = spec
            app.save_config()
            app.start_hotkeys()
            self.hk_msg.configure(text="Applied. e.g. ctrl+alt+k, shift+f7, ctrl+numpad5", style="Good.TLabel")

        ttk.Button(f, text="Apply", command=apply_hotkeys).grid(row=row, column=2, sticky="e", padx=8)
        row += 1

        section("App")
        self.shortcut_var = tk.BooleanVar(value=has_desktop_shortcut())
        sc = ttk.Checkbutton(f, text="Desktop shortcut", variable=self.shortcut_var,
                             command=lambda: self.shortcut_var.set(set_desktop_shortcut(self.shortcut_var.get())))
        sc.grid(row=row, column=0, columnspan=2, sticky="w", **pad)
        if not FROZEN:
            sc.state(["disabled"])  # running from source: nothing installed to point a shortcut at
        ttk.Button(f, text="Update signatures", command=app.refresh_table).grid(row=row, column=2, sticky="e", padx=8)
        row += 1
        ttk.Checkbutton(f, text="Quit when Star Citizen closes", variable=(ew := tk.BooleanVar(value=cfg["exit_with_game"])),
                        command=lambda: (cfg.__setitem__("exit_with_game", ew.get()), app.save_config())
                        ).grid(row=row, column=0, columnspan=3, sticky="w", **pad)
        row += 1

        self.pills.select("Reading")
        self._photo = self._crop_photo = None
        self.after(100, self._refresh)

    def _refresh(self):
        if not self.winfo_exists():
            return
        a, d = self.app, self.app.debug
        crop = d.get("crop")
        if crop is not None:
            crop = crop.copy()
            k = max(1, min(4, 376 // max(1, crop.width), 58 // max(1, crop.height)))
            crop = crop.resize((crop.width * k, crop.height * k), Image.NEAREST)
            crop.thumbnail((376, 58))
            self._crop_photo = ImageTk.PhotoImage(crop)
            self.crop_preview.configure(image=self._crop_photo)
        img = d.get("img")
        if img is not None:
            img = img.copy()
            img.thumbnail((376, 58))
            self._photo = ImageTk.PhotoImage(img)
            self.preview.configure(image=self._photo, text="")
        else:
            self.preview.configure(image="", text="box looks empty")
        values = {
            "OCR text": d["raw"] or "—",
            "Value": f"{d['value']:,}" if d.get("value") is not None else "—",
            "Shows": a.text or "—",
            "Engine": a.ocr.name if a.ocr else (a.ocr_error or "—"),
        }
        for key, lab in self.status.items():
            lab.configure(text=theme.fit(values[key], 34))
        self.after(250, self._refresh)


def _log_error(where: str) -> str:
    import traceback
    msg = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {where}\n{traceback.format_exc()}\n"
    try:
        with open(DATA_DIR / "error.log", "a", encoding="utf-8") as f:
            f.write(msg)
    except OSError:
        pass
    return msg


def self_test(out: str) -> None:
    """`RockCrawler.exe --selftest result.txt`: the release build checks that everything it
    needs made it into the package (OCR, Velopack, fonts, Tk), without opening the app."""
    lines, ok = [], True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"ok   {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            ok = False
            lines.append(f"FAIL {name}: {type(e).__name__}: {e}")

    def ocr_imports():
        import winrt.windows.media.ocr  # noqa: F401
        import winrt.windows.graphics.imaging  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        import winrt.windows.globalization  # noqa: F401
        try:
            return f"engine {'ready' if WindowsOcr() else '?'}"
        except RuntimeError as e:  # no OCR language on the build machine is fine
            return f"imports fine ({e})"

    def ui():
        theme.load_fonts(APP_DIR)
        root = tk.Tk()
        theme.apply(root)
        fonts = (theme.TITLE[0], theme.BODY[0], theme.MONO[0])
        root.destroy()
        if any(f in ("Segoe UI", "Consolas") for f in fonts):
            raise RuntimeError(f"bundled fonts missing, got {fonts}")
        return fonts

    check("velopack", lambda: __import__("velopack").__name__)
    check("ocr", ocr_imports)
    check("capture", lambda: type(mss.mss()).__name__)
    check("assets", lambda: sorted(p.name for p in theme.assets_dir(APP_DIR).iterdir()))
    check("ui", ui)
    check("core", lambda: core.format_hits(core.match_signature(6000, [
        {"rock": "Aphorite", "method": "hand", "signature": 3000, "maxCluster": 21},
        {"rock": "Wreckage", "method": "salvage", "signature": 2000, "maxCluster": 15}])))
    Path(out).write_text(("PASS" if ok else "FAIL") + "\n" + "\n".join(lines) + "\n", "utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        self_test(sys.argv[sys.argv.index("--selftest") + 1])
    try:
        app = App(SingleInstance())
        # Errors inside timers/clicks go to error.log instead of vanishing silently.
        app.root.report_callback_exception = lambda *_: _log_error("callback")

        # Started from a terminal: Ctrl+C or closing the terminal shuts down cleanly too.
        HandlerRoutine = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)

        @HandlerRoutine
        def _console_close(_event):
            app.q.put(("quit", None))
            app.closed.wait(4)  # Windows ends the process once this returns
            return True

        kernel32.SetConsoleCtrlHandler(_console_close, True)
        app.run()
    except Exception:  # noqa: BLE001 - pythonw has no console, so show startup failures
        details = _log_error("startup")
        ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} couldn't start.\n\n{details[-1500:]}\n"
                                         f"Details saved to {DATA_DIR / 'error.log'}", APP_NAME, 0x10)
