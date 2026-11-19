"""Self-updating through Velopack and the GitHub repo's Releases.

Installed copies check for a new release in the background, download it quietly, and show
"Restart to update" in the main window. If you don't restart, the update is applied the
next time RockCrawler starts. Copies run from source ("dev") never update themselves.
"""
from __future__ import annotations

import threading
import time

REPO_URL = "https://github.com/Harvey-Ryan/ASOP_RockCrawler"
CHECK_EVERY = 6 * 3600  # seconds


class Updater:
    def __init__(self, repo_url: str = REPO_URL):
        self.state = "dev"        # dev | checking | up to date | downloading | ready | offline
        self.version = "dev"      # this copy's version
        self.target: str | None = None  # the version waiting to be installed
        self._info = None
        self._mgr = None
        self._last = 0.0
        try:
            import velopack
            self._mgr = velopack.UpdateManager(velopack.GithubSource(repo_url))
            self.version = self._mgr.get_current_version()
            self.state = "idle"
        except Exception:  # noqa: BLE001 - not installed through Setup.exe (running from source)
            self._mgr = None

    @property
    def installed(self) -> bool:
        return self._mgr is not None

    def status(self) -> str:
        v = f"v{self.version}" if self.version != "dev" else "dev copy"
        extra = {
            "checking": "checking for updates…",
            "up to date": "up to date",
            "downloading": f"downloading v{self.target}…",
            "ready": f"v{self.target} ready",
            "offline": "couldn't check for updates",
        }.get(self.state)
        return f"{v} · {extra}" if extra else v

    def check(self, force: bool = False) -> None:
        """Check (and download) in the background; at most every CHECK_EVERY unless forced."""
        if not self._mgr or self.state in ("checking", "downloading", "ready"):
            return
        if not force and time.time() - self._last < CHECK_EVERY:
            return
        self._last = time.time()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.state = "checking"
            info = self._mgr.check_for_updates()
            if not info:
                self.state = "up to date"
                return
            self.target = info.TargetFullRelease.Version
            self.state = "downloading"
            self._mgr.download_updates(info)
            self._info = info
            self.state = "ready"
        except Exception:  # noqa: BLE001 - offline, GitHub rate limit, etc.: try again later
            self.state = "offline"

    def apply_and_restart(self) -> None:
        """Install the downloaded update and restart. This process exits."""
        if self._mgr and self._info:
            self._mgr.apply_updates_and_restart(self._info)


def run_velopack_hooks() -> None:
    """Must be the first thing the app does: Velopack may run install/update tasks and exit."""
    try:
        import velopack
        velopack.App().run()
    except Exception:  # noqa: BLE001 - not installed through Setup.exe
        pass
