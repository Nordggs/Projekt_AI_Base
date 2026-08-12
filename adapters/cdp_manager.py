import glob
import json
import os
import shutil
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


_CORRUPT_CACHE_DIRS = (
    "Code Cache",
    "GPUCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GrShaderCache",
    "ShaderCache",
    "GraphiteDawnCache",
)


class CDPManager:
    """Manages Chrome process lifecycle only. No Playwright, no pages, no providers."""

    def __init__(self, log, file_log=None):
        self.log = log
        self._file_log = file_log
        self._chrome_dir = os.path.expanduser("~/.ai_pipeline/chrome_gemini")
        self._state = "stopped"  # stopped | starting | running

    @property
    def state(self):
        return self._state

    def _log(self, msg):
        self.log.add(msg)
        if self._file_log is not None:
            try:
                self._file_log.write(msg + "\n")
            except Exception:
                pass

    def start(self):
        """Launch Chrome and wait for CDP HTTP endpoint.

        On first failure purges profile caches that corrupt after hard kills
        (V8 Code Cache is the known culprit — makes Chrome exit with 0x80000003
        during startup) and retries once. Cookies/logins are never touched.
        """
        if self._state == "running":
            self._log("[CDP] already running")
            return
        self._state = "starting"
        for attempt in (1, 2):
            try:
                self._launch_and_wait()
                self._state = "running"
                self._log("[CDP] Chrome ready")
                return
            except RuntimeError as e:
                self._log(f"[CDP] attempt {attempt} failed: {e}")
                if attempt == 2:
                    break
                purged = self._purge_profile_caches()
                if not purged:
                    break
        self._state = "stopped"
        raise RuntimeError("Chrome did not start in time (CDP endpoint unavailable)")

    def stop(self):
        """Kill Chrome."""
        self._log("[CDP] stopping...")
        subprocess.run(
            ["taskkill", "/F", "/IM", "chrome.exe"],
            capture_output=True,
        )
        self._state = "stopped"
        self._log("[CDP] stopped")

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

    def _purge_profile_caches(self):
        """Remove Chrome caches that corrupt after hard kills (taskkill /F).

        Only removes regenerable caches — never cookies, logins, localStorage,
        history or other user data.
        """
        removed = []
        for base in (self._chrome_dir, os.path.join(self._chrome_dir, "Default")):
            for name in _CORRUPT_CACHE_DIRS:
                path = os.path.join(base, name)
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(name)
        if removed:
            names = ", ".join(sorted(set(removed)))
            self._log(f"[CDP] purged corrupt caches: {names}")
        else:
            self._log("[CDP] no caches to purge")
        return removed

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

    def _chrome_candidates(self):
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
        return [p for p in paths if os.path.exists(p)]

    def _launch_candidate(self, path):
        """Start Chrome from path.

        Returns the Popen handle if the process survives a short bootstrap
        window, or None if it exits immediately (single-instance handoff,
        blocked executable, etc.) so the caller can try the next candidate.
        """
        self._log(f"[CDP] launching Chrome from {path}")
        proc = subprocess.Popen([
            path,
            "--remote-debugging-port=9222",
            f"--user-data-dir={self._chrome_dir}",
            "--no-first-run",
            "--disable-session-crashed-bubble",
            "--disable-features=InfiniteSessionRestore",
        ])
        for _ in range(10):
            if proc.poll() is not None:
                self._log(f"[CDP] {path} exited immediately (code {proc.returncode})")
                return None
            time.sleep(0.3)
        return proc

    def _launch_and_wait(self):
        # If a live Chrome already holds the CDP endpoint (single-instance handoff),
        # nothing to launch — treat as ready.
        if self._check_alive():
            self._log("[CDP] endpoint already available")
            return
        candidates = self._chrome_candidates()
        if not candidates:
            raise RuntimeError("Chrome not found in standard paths")
        proc = None
        for path in candidates:
            proc = self._launch_candidate(path)
            if proc is not None:
                break
        if proc is None:
            raise RuntimeError("Chrome exe exists but never stays running")
        for _ in range(60):
            if self._check_alive():
                return
            if proc.poll() is not None:
                raise RuntimeError(f"Chrome exited during startup (code {proc.returncode})")
            time.sleep(0.5)
        raise RuntimeError("CDP endpoint 127.0.0.1:9222 did not open in time")
