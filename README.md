# ASOP RockCrawler

A small overlay for Star Citizen. It reads the mining signature number off your HUD and
shows what it is right next to it, for example `2× Laranite`, `Hand Minable` or
`ROC · 2× Wreckage`. It uses ASOP's own signature list (the same data and rules as the
Scanner on the Mining page at asop.f919.org), so when the game data updates, the overlay
follows.

Windows 10/11 only. It reads the screen like a screenshot does, so it never touches the
game's memory or files.

## Install
1. Download **RockCrawler-Setup.exe** from the
   [latest release](https://github.com/Harvey-Ryan/ASOP_RockCrawler/releases/latest) and run it.
   It installs for your user only (no admin prompt), adds Start menu and desktop shortcuts, and starts RockCrawler.
   - Windows may say "Windows protected your PC" because the installer isn't code-signed yet:
     click **More info → Run anyway**.
2. In Star Citizen, set **Display mode → Borderless**, because overlays can't draw over exclusive fullscreen.

**Updates install themselves.** RockCrawler checks for a new release every few hours and
downloads it in the background. Click **Restart to update** in the main window, or just
carry on: it installs the next time RockCrawler starts. Click the version number to check now.

**Coming from the old Install.bat version** (or "ASOP HUD Lookup")? Run the Setup once. It
brings your settings and box positions over, then removes the old install and its shortcuts.

Uninstall from Windows **Settings → Apps**. Your settings stay in `%APPDATA%\ASOP-RockCrawler`.

## Using it
The **main window** stays open while RockCrawler runs: what it's reading, where you're
mining, signature count, hotkeys, version, and the controls.
- **Edit layout**: drag the **READ** box tightly over the signature number and the
  **DISPLAY** box to where the result should appear (drag the edges to resize), then click **Done editing**.
- **Settings**: font, size, brightness, opacity, colour, reading sensitivity, where you're
  mining, game window and hotkeys, with a live preview of what's being read.
- **Hide overlay** / **Quit**. Closing the main window quits too.

| Default key | What it does |
|---|---|
| Ctrl+Shift+F8 | Edit layout on/off |
| Ctrl+Shift+F9 | Hide/show the overlay |
| Ctrl+Shift+F10 | Quit (it also quits by itself about 10 s after Star Citizen closes) |

Hotkeys are optional. Change them under Settings → Game & hotkeys; the main window warns you if another app already uses one.

## What it shows
- **Ship rocks:** `2× Laranite`. A count above the most that rock ever clusters in isn't a
  match (legendary 2, epic 3, rare 4, uncommon 5, common 6), so every ship-ore reading has
  a single answer: 19,200 is `5× Aslarite`, never "6× Savrilium".
- **Hand gems:** `Hand Minable`. All hand gems share one signature, so they can't be told apart.
- **ROC gems:** `ROC Minable`, for the same reason.
- **Salvage:** `3× Wreckage`. Whole derelict hulls count as wreckage too.
- **A reading that fits several kinds shows them all, in short form:** `Hand · 3× Wreckage`
  (6,000), `ROC · 2× Wreckage` (4,000), `Hand · ROC · 6× Wreckage` (12,000).
- **`???`**: a clear number that isn't any known rock or wreck. Blank means it couldn't read the number.
- **Where you're mining** (Settings → Game & hotkeys) drops rocks that don't spawn there and
  lists the likeliest first. Wreckage can turn up anywhere, so it always stays.
- Members' approved corrections from the ASOP site are applied, as the website does by default.

The wording is in `config.json` under `"labels"` and `"no_match_text"`.

## How it blends in
- **Match HUD colour** (default): the text takes its colour from the HUD it is reading, so it
  follows each ship's HUD. Use Brightness to sit it above or below the HUD.
- Click-through, no taskbar entry for the overlay, and it never takes focus from the game.
- It only shows while Star Citizen is in front, and hides when you alt-tab. The boxes are
  pinned to the game window and scale if you change resolution.
- The overlay is kept out of screen captures so it never reads its own text. Set
  `"hide_from_capture": false` in `config.json` if you want it in OBS.

## Files
In `%APPDATA%\ASOP-RockCrawler`:
- `config.json`: your settings and box positions.
- `signatures_cache.json`: the last list from ASOP (cluster limits, places, corrections).
  Refreshes every 12 hours or with **Update signatures**; works offline from this copy.
- `extra_signatures.csv`: optional extra rows (`signature,name,method`) on top of ASOP's list.
- `error.log`: anything that went wrong.

## Troubleshooting
- **Reads nothing:** make the READ box tight around the digits only. Settings → Reading shows exactly what the OCR sees.
- **"No Windows OCR language":** Windows Settings → Time & language → Language → add English (US).
- **Hotkey taken:** another app owns it. Pick another under Settings → Game & hotkeys.

---

## Development
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python rockcrawler.py     # runs from source: settings live next to the code, no self-updates
python tests\test_core.py                 # the pure logic (parsing, matching, colour); runs anywhere
```
| File | What it is |
|---|---|
| `rockcrawler.py` | The app: capture, OCR, overlay, main and settings windows |
| `core.py` | Pure logic: number parsing, signature matching and labels, image clean-up, colour |
| `ui_theme.py` | ASOP styling (Event Manager look), bundled fonts |
| `updater.py` | Velopack self-updates from this repo's Releases |
| `scripts/build.ps1` | PyInstaller build + self-test (`-Pack` also makes a local Setup.exe; needs `vpk`) |
