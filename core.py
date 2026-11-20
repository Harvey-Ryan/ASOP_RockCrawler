"""Pure logic for ASOP RockCrawler: no Windows or Tk imports, so it can be tested anywhere.

- parse_number:  OCR text -> the signature number (or None)
- match/format:  number -> "2× Laranite" using ASOP's mining signatures (same rule as
                 the Scanner on the website: one rock's signature × cluster size 1..8)
- preprocess:    raw HUD crop -> clean black-on-white image for OCR
- hud_color:     raw HUD crop -> the HUD's text colour, so the overlay can match it
- signatures:    fetch from ASOP, cache to disk, merge local extras
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

# ── Number parsing ────────────────────────────────────────────────────────────

# Letters OCR commonly returns in place of digits. Only applied inside tokens
# that are mostly digits, so a label like "RS" next to the number is left alone.
_OCR_FIXES = str.maketrans({
    "O": "0", "o": "0", "D": "0", "Q": "0", "U": "0",
    "I": "1", "l": "1", "|": "1", "i": "1", "!": "1", "]": "1", "[": "1",
    "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2",
    "g": "9", "q": "9", "G": "6", "b": "6", "T": "7",
})
_SEPARATORS = ",.  '"


def parse_number(text: str, min_value: int = 1, max_value: int = 10_000_000) -> int | None:
    """Pull the signature number out of OCR text such as '7,650', 'RS 7 650' or '765O'."""
    if not text:
        return None
    fixed = []
    for tok in text.split():
        core = tok.strip(_SEPARATORS)
        digits = sum(c.isdigit() for c in core)
        if digits and digits / max(1, len(core)) >= 0.5:
            tok = tok.translate(_OCR_FIXES)
        fixed.append(tok)
    s = " ".join(fixed)
    # Join thousands groups: "7,650" / "7 650" / "12.300" -> "7650"
    s = re.sub(r"(\d)[ ,.  '](?=\d{3}(?!\d))", r"\1", s)
    runs = re.findall(r"\d+", s)
    if not runs:
        return None
    value = int(max(runs, key=len))
    return value if min_value <= value <= max_value else None


def digit_run(text: str) -> str:
    """Longest digit run after OCR fixes and thousands-separator joining ('' if none)."""
    n = parse_number(text, 0, 10**12)
    return str(n) if n is not None else ""


def pick_value(texts: list[str], is_known, min_value: int = 1, max_value: int = 10_000_000) -> int | None:
    """Choose the reading from several OCR passes (different image variants).

    Each pass offers its digits plus trimmed versions (an icon next to the number often
    reads as an extra leading digit, and a stray mark as a trailing one). Only values
    that are a real signature (is_known) count; a value agreed by more passes, and
    needing less trimming, wins. Without a table, falls back to the first plain reading.
    """
    scores: dict[int, float] = {}
    for t in texts:
        d = digit_run(t)
        if not d:
            continue
        options = [(d, 3.0)]
        if len(d) >= 4:
            options += [(d[1:], 1.5), (d[:-1], 1.0)]
        for digits, weight in options:
            v = int(digits)
            if digits[0] != "0" and min_value <= v <= max_value and (is_known is None or is_known(v)):
                scores[v] = scores.get(v, 0) + weight
    if scores:
        return max(scores, key=lambda v: (scores[v], v))
    if is_known is not None:
        # Nothing known: if at least two passes read the same clean number, it's a real
        # reading that just isn't a rock we know (the overlay shows "???").
        plain = [parse_number(t, min_value, max_value) for t in texts]
        plain = [v for v in plain if v is not None]
        best = max(set(plain), key=plain.count) if plain else None
        return best if best is not None and plain.count(best) >= 2 else None
    if is_known is None:  # no signature table loaded yet: take the plain reading
        for t in texts:
            v = parse_number(t, min_value, max_value)
            if v is not None:
                return v
    return None


class Stabilizer:
    """Turns a noisy stream of readings into a steady display value.

    - A first value appears after `confirm` matching reads in a row.
    - Switching to a different value takes `switch` matching reads in a row, so a
      one-frame misread never replaces a good result.
    - Unreadable frames (glare, bloom, motion) don't clear it: it lingers `hold` seconds
      after the last read that agreed with it.
    - If the read box is truly empty (the HUD element went away) it clears after `empty_hold`.
    """

    def __init__(self, confirm: int = 2, switch: int = 3, hold: float = 4.0, empty_hold: float = 0.8):
        self.confirm, self.switch, self.hold, self.empty_hold = confirm, switch, hold, empty_hold
        self.shown: int | None = None
        self.recent: list[int] = []
        self.last_support = 0.0
        self.empty_since: float | None = None

    def update(self, value: int | None, empty: bool, now: float) -> int | None:
        self.empty_since = (self.empty_since or now) if empty else None
        if value is not None:
            self.recent = (self.recent + [value])[-max(self.confirm, self.switch):]
            if value == self.shown:
                self.last_support = now
            else:
                need = self.confirm if self.shown is None else self.switch
                tail = self.recent[-need:]
                if len(tail) == need and all(v == value for v in tail):
                    self.shown, self.last_support = value, now
        if self.shown is not None:
            gone = self.empty_since is not None and now - self.empty_since >= self.empty_hold
            if gone or now - self.last_support >= self.hold:
                self.clear()
        return self.shown

    def clear(self) -> None:
        self.shown, self.recent = None, []


# ── Signature matching (mirrors packages/shared/src/mining.ts matchSignature) ──

METHOD_ORDER = {"ship": 0, "hand": 1, "roc": 2, "salvage": 3}
DEFAULT_LABELS = {
    "hand": "Hand Minable",        # every hand gem shares one signature: no count, no gem names
    "roc": "ROC Minable",          # same for ROC gems
    "wreckage": "Wreckage",        # all salvage, panels and whole derelict hulls alike
    # Short forms, used when one reading fits several kinds: "Hand · ROC · 6× Wreckage"
    "hand_short": "Hand",
    "roc_short": "ROC",
}


def match_signature(value: int, signatures: list[dict], max_count: int = 8,
                    methods: list[str] | None = None, place: str | None = None,
                    chances: dict | None = None) -> list[dict]:
    """Readings that add up to `value`: `count` rocks of one signature, grouped like ASOP's
    scanner. A count above the rock's maxCluster (the most it ever clusters in, from the
    game files) isn't a match; `max_count` is only for rows without one (local extras).

    With a `place`, rocks that don't spawn there drop out (salvage turns up anywhere, and
    rows with no place list, like local extras, are kept), and rocks sharing a signature
    are listed most likely first using `chances[(place, rock, method)]`."""
    if not value or value <= 0:
        return []
    hits: dict[tuple, dict] = {}
    for s in signatures:
        method = s.get("method") or "ship"
        if methods and method not in methods:
            continue
        sig = int(s.get("signature") or 0)
        if sig <= 0:
            continue
        n, rem = divmod(value, sig)
        if rem or n < 1 or n > int(s.get("maxCluster") or max_count):
            continue
        if place and method != "salvage" and s.get("places") and place not in s["places"]:
            continue
        h = hits.setdefault((method, sig, n), {"count": n, "each": sig, "method": method, "rocks": [], "chance": {}})
        h["rocks"].append(s["rock"])
        h["chance"][s["rock"]] = (chances or {}).get((place, s["rock"], method), 0.0) if place else 0.0
    out = list(hits.values())
    for h in out:
        h["rocks"].sort(key=lambda r: (-h["chance"][r], r))
        h["rock"] = h["rocks"][0]  # older callers
    out.sort(key=lambda h: (METHOD_ORDER.get(h["method"], 9), h["count"], h["rock"]))
    return out


def short_name(rock: str) -> str:
    """'Laranite (Raw)' -> 'Laranite'"""
    return re.sub(r"\s*\([^)]*\)", "", rock).strip() or rock


def hit_label(h: dict, short_names: bool = True, show_single_count: bool = True,
              labels: dict | None = None, short: bool = False) -> str:
    """Words for one reading.
    ship    -> "2× Laranite"           (rocks sharing a signature: "2× Copper / Corundum")
    hand    -> "Hand Minable"          ("Hand" in a combined line)
    roc     -> "ROC Minable"           ("ROC" in a combined line)
    salvage -> "3× Wreckage"           (whole derelict hulls count as wreckage too)
    """
    L = {**DEFAULT_LABELS, **(labels or {})}
    count = f"{h['count']}× " if (h["count"] > 1 or show_single_count) else ""
    if h["method"] in ("hand", "roc"):
        return L[h["method"] + "_short"] if short else L[h["method"]]
    if h["method"] == "salvage":
        return count + L["wreckage"]
    names = [short_name(r) if short_names else r for r in h["rocks"]]
    return count + " / ".join(dict.fromkeys(names))


def format_hits(hits: list[dict], short_names: bool = True, show_single_count: bool = True,
                max_hits: int = 3, joiner: str = " · ", labels: dict | None = None) -> str:
    """All the readings for one number: "Hand Minable", or "Hand · 3× Wreckage" when it fits several kinds."""
    full = list(dict.fromkeys(hit_label(h, short_names, show_single_count, labels) for h in hits))
    if len(full) <= 1:
        return full[0] if full else ""
    parts = list(dict.fromkeys(hit_label(h, short_names, show_single_count, labels, short=True) for h in hits))
    extra = len(parts) - max_hits
    text = joiner.join(parts[:max_hits])
    return text + (f" +{extra}" if extra > 0 else "")


# ── Image processing ──────────────────────────────────────────────────────────

def _otsu(hist: list[int]) -> int:
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    wb = sb = 0
    best, t = -1.0, 127
    for i in range(256):
        wb += hist[i]
        if wb == 0:
            continue
        wf = total - wb
        if wf == 0:
            break
        sb += i * hist[i]
        mb, mf = sb / wb, (sum_all - sb) / wf
        var = wb * wf * (mb - mf) ** 2
        if var > best:
            best, t = var, i
    return t


def to_gray(img: Image.Image, channel: str = "max") -> Image.Image:
    """max: brightest channel (any HUD colour stands out). min: darkest channel, which keeps
    only near-white digit cores and drops coloured glow, chromatic fringing and icons."""
    r, g, b = img.convert("RGB").split()
    if channel == "min":
        return ImageChops.darker(ImageChops.darker(r, g), b)
    if channel == "lum":
        return img.convert("L")
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _components(mask: list[bool], w: int, h: int) -> list[list[int]]:
    """8-connected groups of set pixels (indices into mask)."""
    seen = bytearray(len(mask))
    groups = []
    for i, on in enumerate(mask):
        if not on or seen[i]:
            continue
        seen[i] = 1
        stack, group = [i], []
        while stack:
            j = stack.pop()
            group.append(j)
            x, y = j % w, j // w
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= h:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < w:
                        k = ny * w + nx
                        if mask[k] and not seen[k]:
                            seen[k] = 1
                            stack.append(k)
        groups.append(group)
    return groups


def _remove_thin_lines(mask: list[bool], w: int, h: int) -> None:
    """Clear long, thin horizontal lines (HUD pill outlines, scanlines) but keep the
    parts where they cross a digit's stroke."""
    min_run, max_thick = max(8, int(w * 0.4)), max(2, h // 12)
    for y in range(h):
        row = y * w
        x = 0
        while x < w:
            if not mask[row + x]:
                x += 1
                continue
            x0 = x
            while x < w and mask[row + x]:
                x += 1
            if x - x0 < min_run:
                continue
            for cx in range(x0, x):
                up = down = 0
                while up < max_thick + 1 and y - up - 1 >= 0 and mask[(y - up - 1) * w + cx]:
                    up += 1
                while down < max_thick + 1 and y + down + 1 < h and mask[(y + down + 1) * w + cx]:
                    down += 1
                if up + down + 1 <= max_thick:
                    for yy in range(y - up, y + down + 1):
                        mask[yy * w + cx] = False


def _clean_mask(mask: list[bool], w: int, h: int, remove_lines: bool) -> tuple[list[bool], int, float]:
    """Drop lines, specks and short bits. Returns (mask, digit height, stroke width)."""
    if remove_lines:
        _remove_thin_lines(mask, w, h)
    groups = _components(mask, w, h)
    if not groups:
        return mask, 0, 0.0
    info = []
    for grp in groups:
        ys = [j // w for j in grp]
        info.append((grp, max(ys) - min(ys) + 1))
    big = [hh for grp, hh in info if len(grp) >= 4]
    tallest = max(big) if big else 0
    min_area = max(4, (w * h) // 400)
    area = perim = 0
    for grp, hh in info:
        if len(grp) < min_area or hh < tallest * 0.45:
            for j in grp:
                mask[j] = False
            continue
        area += len(grp)
        for j in grp:  # edge pixels: a set pixel with an unset 4-neighbour
            x, y = j % w, j // w
            if x == 0 or y == 0 or x == w - 1 or y == h - 1 or not (mask[j - 1] and mask[j + 1] and mask[j - w] and mask[j + w]):
                perim += 1
    stroke = (2 * area / perim) if perim else 0.0
    return mask, tallest, stroke


def preprocess(img: Image.Image, upscale: int = 3, invert: bool = False, threshold: int = 0,
               remove_lines: bool = True, min_contrast: int = 40, channel: str = "max",
               level: float = 0.0) -> Image.Image | None:
    """HUD crop -> clean black digits on white, enlarged, ready for OCR. None if the box looks empty.

    - channel: max = brightest channel (any HUD colour), min = only near-white digit cores
      (drops coloured glow, chromatic fringing and icons), lum = plain brightness.
    - threshold 0 = auto (Otsu, raised by `level` toward white: bloom makes HUD digits
      fat, which closes the holes in 6/8/9/0 so OCR reads letters). Otherwise a fixed
      1-254 on the contrast-stretched image.
    - Removes thin horizontal lines, specks and anything much shorter than the digits.
    - The final cut is made on the enlarged image so curves and holes survive.
    """
    gray = to_gray(img, channel)
    if invert:
        gray = ImageOps.invert(gray)
    lo, hi = gray.getextrema()
    if hi - lo < min_contrast:
        return None
    gray = ImageOps.autocontrast(gray, cutoff=1)
    w, h = gray.size
    px = list(gray.getdata())

    if threshold > 0:
        t = int(threshold)
    else:
        # Otsu splits background from "HUD stuff"; `level` then moves the cut up toward
        # white to keep only the digit cores, not their bloom (0 = plain Otsu).
        t0 = _otsu(gray.histogram())
        t = int(t0 + (255 - t0) * level)
    mask, _, _ = _clean_mask([p > t for p in px], w, h, remove_lines)

    if not any(mask):
        return None
    keep = Image.new("L", (w, h))
    keep.putdata([255 if m else 0 for m in mask])
    if upscale > 1:
        size = (w * upscale, h * upscale)
        big = gray.resize(size, Image.LANCZOS).point(lambda p: 255 if p > t else 0)
        # Only keep what survived the clean-up, allowing a little slack around it.
        keep = keep.resize(size, Image.NEAREST).filter(ImageFilter.MaxFilter(upscale + (1 - upscale % 2)))
        text = ImageChops.multiply(big, keep)
    else:
        text = keep
    return ImageOps.expand(ImageOps.invert(text), border=12, fill=255)


def hud_color(img: Image.Image, min_sat: float = 0.3) -> tuple[int, int, int] | None:
    """The HUD's colour, as a vivid RGB.

    The brightest pixels are the white-hot digit cores, so averaging them gives a washed-out
    tan. Instead: take the reasonably bright, clearly coloured pixels (glow, pill, outline),
    find their most common hue (weighted by how strong each pixel is, so the thin red/blue
    chromatic fringing loses to the HUD's real tint), and return that hue fully bright.
    """
    hsv = list(img.convert("RGB").convert("HSV").getdata())
    if not hsv:
        return None
    vmax = max(p[2] for p in hsv)
    if vmax < 60:
        return None
    bins = [0.0] * 36
    hues: list[tuple[int, float]] = []
    sats: list[int] = []
    for h, s_, v in hsv:
        if v >= vmax * 0.35 and s_ >= min_sat * 255:
            wgt = (s_ / 255) * (v / 255)
            bins[(h * 36) // 256] += wgt
            hues.append((h, wgt))
            sats.append(s_)
    if not hues or max(bins) <= 0:
        return None
    peak = max(range(36), key=lambda i: bins[i] + 0.5 * (bins[i - 1] + bins[(i + 1) % 36]))
    centre = (peak + 0.5) * 256 / 36
    # weighted circular mean of the hues near the peak
    import math
    sx = sy = 0.0
    for h, wgt in hues:
        d = (h - centre + 128) % 256 - 128
        if abs(d) <= 256 / 36 * 1.5:
            a = h / 256 * 2 * math.pi
            sx, sy = sx + math.cos(a) * wgt, sy + math.sin(a) * wgt
    hue = int((math.atan2(sy, sx) / (2 * math.pi) * 256) % 256) if (sx or sy) else int(centre)
    sat = max(190, sorted(sats)[len(sats) // 2])
    return Image.new("HSV", (1, 1), (hue, sat, 255)).convert("RGB").getpixel((0, 0))


def blend(a: tuple, b: tuple, t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def apply_brightness(rgb: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    """k < 1 dims toward black; k > 1 brightens toward white (so it never just clips)."""
    if k <= 1:
        return tuple(max(0, min(255, round(c * k))) for c in rgb)  # type: ignore[return-value]
    t = min(1.0, k - 1)
    return tuple(max(0, min(255, round(c + (255 - c) * t))) for c in rgb)  # type: ignore[return-value]


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def from_hex(s: str) -> tuple[int, int, int]:
    s = s.lstrip("#")
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


# ── Hotkeys ───────────────────────────────────────────────────────────────────

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_NAMED_KEYS = {
    "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "pause": 0x13,
    "scrolllock": 0x91, "space": 0x20, "tab": 0x09, "backquote": 0xC0, "`": 0xC0,
}


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+shift+f8' -> (modifiers, virtual-key code) for RegisterHotKey."""
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", part):
            vk = 0x70 + int(part[1:]) - 1
        elif re.fullmatch(r"(numpad|num)\d", part):
            vk = 0x60 + int(part[-1])
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"Unknown key '{part}' in hotkey '{spec}'")
    if vk is None:
        raise ValueError(f"Hotkey '{spec}' has no key")
    return mods, vk


# ── Signature table ───────────────────────────────────────────────────────────

def _get_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ASOP-RockCrawler/1.1", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def apply_corrections(sigs: list[dict], corrections: list[dict]) -> int:
    """Apply ASOP members' approved corrections (kind 'rock-signature', keyed 'Rock|method'),
    as the website does by default. Returns how many values changed."""
    by_key = {f"{s['rock']}|{s['method']}": s for s in sigs}
    n = 0
    for c in corrections:
        if c.get("kind") != "rock-signature" or c.get("field") not in ("signature", "maxCluster"):
            continue
        s = by_key.get(str(c.get("recordKey")))
        if s is not None and c.get("value") is not None:
            s[c["field"]] = int(c["value"])
            n += 1
    return n


def fetch_signatures(url: str, timeout: float = 15, corrections_url: str | None = None) -> dict:
    """GET ASOP's /api/game-tools/mining (plus live corrections) and keep what the overlay needs."""
    data = _get_json(url, timeout).get("data") or {}
    sigs = [
        {"rock": s["rock"], "ore": s.get("ore"), "method": s.get("method") or "ship",
         "signature": int(s["signature"]), "maxCluster": int(s.get("maxCluster") or 0) or None,
         "places": s.get("places") or []}
        for s in data.get("signatures", []) if s.get("signature")
    ]
    if not sigs:
        raise ValueError("ASOP returned no signatures")
    fixed = None
    if corrections_url:
        try:
            live = _get_json(corrections_url, timeout).get("data") or {}
            fixed = apply_corrections(sigs, live.get("corrections") or [])
        except Exception:  # noqa: BLE001 - corrections are a bonus; the game values still work
            fixed = None
    spots = [
        {"place": p["place"], "system": p.get("system"), "rock": p["rock"], "method": p.get("method"),
         "chance": float(p.get("chance") or 0)}
        for p in data.get("spots", []) if p.get("place") and p.get("rock")
    ]
    return {"build": data.get("build"), "fetched_at": time.time(), "source": url,
            "corrections": fixed, "signatures": sigs, "spots": spots}


def places_of(table: dict) -> list[tuple[str, str | None]]:
    """(place, system) pairs where any rock in the table spawns, A-Z by place name
    (ignoring capitalization)."""
    seen: dict[str, str | None] = {}
    for p in table.get("spots", []):
        seen.setdefault(p["place"], p.get("system"))
    for s in table.get("signatures", []):
        for pl in s.get("places") or []:
            seen.setdefault(pl, None)
    return sorted(seen.items(), key=lambda kv: (kv[0].casefold(), kv[0]))


def chance_index(table: dict) -> dict:
    """{(place, rock, method): chance} for ranking rocks that share a signature."""
    out: dict = {}
    for p in table.get("spots", []):
        k = (p["place"], p["rock"], p.get("method"))
        out[k] = max(out.get(k, 0.0), p.get("chance") or 0.0)
    return out


def load_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def save_cache(path: Path, table: dict) -> None:
    path.write_text(json.dumps(table, indent=1), "utf-8")


def load_extras(path: Path) -> list[dict]:
    """Optional extra_signatures.csv: signature,name[,method] — added on top of ASOP's list."""
    if not path.exists():
        return []
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                sig = int(str(row.get("signature", "")).replace(",", "").strip())
            except ValueError:
                continue
            name = (row.get("name") or "").strip()
            if sig > 0 and name:
                out.append({"rock": name, "method": (row.get("method") or "").strip() or None, "signature": sig})
    return out
