import glob
import json
import os
import subprocess
import sys
import time
import urllib.request


class CDPContext:
    """Duck-typing for DeepSeek worker — exposes .page / .new_page() / .close()."""

    def __init__(self, pw, browser):
        self._pw = pw
        self._browser = browser
        self._ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        self._ctx.set_default_navigation_timeout(60_000)
        self._ctx.set_default_timeout(60_000)
        self._page = self._ctx.new_page()

    @property
    def page(self):
        return self._page

    @property
    def _context(self):
        return self._ctx

    def new_page(self):
        return self._ctx.new_page()

    def close(self):
        try:
            self._pw.stop()
        except Exception:
            pass


class CDPManager:
    """Manages Chrome process lifecycle only. No Playwright, no pages, no providers."""

    def __init__(self, log):
        self.log = log
        self._chrome_dir = os.path.expanduser("~/.ai_pipeline/chrome_gemini")
        self._state = "stopped"  # stopped | starting | running

    @property
    def state(self):
        return self._state

    def start(self):
        """Launch Chrome and wait for CDP HTTP endpoint. Idempotent if already running."""
        if self._state == "running":
            self.log.add("[CDP] already running")
            return
        self._state = "starting"
        self._launch_chrome()
        for _ in range(30):
            if self._check_alive():
                break
            time.sleep(0.5)
        else:
            self._state = "stopped"
            raise RuntimeError("Chrome did not start in time")
        self._state = "running"
        self.log.add("[CDP] Chrome ready")

    def stop(self):
        """Kill Chrome."""
        self.log.add("[CDP] stopping...")
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            capture_output=True,
        )
        self._state = "stopped"
        self.log.add("[CDP] stopped")

    def healthcheck(self):
        return self._check_alive()

    def _check_alive(self):
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:9222/json/version", timeout=1
            ) as r:
                return "webSocketDebuggerUrl" in json.load(r)
        except Exception:
            return False

    def _bundled_chrome_path(self):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        matches = []
        for pattern in (
            os.path.join(exe_dir, "ms-playwright", "chromium-*", "chrome-win64", "chrome.exe"),
            os.path.join(exe_dir, "ms-playwright", "chromium-*", "chrome-win", "chrome.exe"),
        ):
            matches += glob.glob(pattern)
        if not matches:
            return None

        def version_key(p):
            try:
                seg = p.split(os.sep)
                for s in seg:
                    if s.startswith("chromium-"):
                        return int(s.split("-", 1)[1])
            except Exception:
                pass
            return 0

        return sorted(matches, key=version_key)[-1]

    def _launch_chrome(self):
        os.makedirs(self._chrome_dir, exist_ok=True)
        paths = []
        bundled = self._bundled_chrome_path()
        if bundled:
            paths.append(bundled)
        paths += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe"
            ),
        ]
        for p in paths:
            if os.path.exists(p):
                self.log.add(f"[CDP] Launching Chrome from {p}")
                subprocess.Popen([
                    p, "--remote-debugging-port=9222",
                    f"--user-data-dir={self._chrome_dir}",
                    "--no-first-run",
                    "--disable-session-crashed-bubble",
                    "--disable-features=InfiniteSessionRestore",
                ])
                return
        raise RuntimeError("Chrome not found in standard paths")
