"""
ExperimentHarness v1 — measurement framework for DeepSeek extraction.
Usage:
    python analysis/experiment_harness.py <url> [url2]
Output:
    raw/experiment_report_{chat_id}.json
    stdout summary
"""
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exporters.base import Browser
from exporters.deepseek import DeepSeekExporter
from exporters.playwright_browser import BrowserPlaywright


class ExperimentHarness:
    def __init__(self, url, url2=None):
        self.url = url
        self.url2 = url2
        self.phases = []
        self.decision = {}

    def _run_trials(self, phase_name, n, **exporter_kwargs):
        trials = []
        pw = BrowserPlaywright(headless=False)
        pw.start()

        for i in range(n):
            page = pw.new_page()
            browser = Browser(window=None, mode="playwright", log=None)
            browser._playwright_page = pw.page

            t0 = time.time()
            result_container = []
            exporter = DeepSeekExporter(browser, url=self.url, **exporter_kwargs)
            exporter.start(lambda r: result_container.append(r))
            elapsed = time.time() - t0

            result = result_container[0] if result_container else None
            count = len(result.get("messages", [])) if result else 0

            trials.append({
                "trial": i + 1,
                "e2e": round(elapsed, 1),
                "count": count,
                "status": "OK" if result else "FAIL",
            })

            page.close()

        pw.close()

        counts = [t["count"] for t in trials]
        times = [t["e2e"] for t in trials]

        return {
            "phase": phase_name,
            "n": n,
            "trials": trials,
            "count_mean": round(statistics.mean(counts), 1),
            "count_std": round(statistics.stdev(counts), 1) if n > 1 else 0,
            "time_mean": round(statistics.mean(times), 1),
            "time_std": round(statistics.stdev(times), 1) if n > 1 else 0,
        }

    def _test_navigation_strategy(self, label, action):
        """Test ONE navigation strategy. Returns {label, nav_type, re_init}.
        `action` is a callable(page) that performs navigation."""
        pw = BrowserPlaywright(headless=False)
        pw.start()
        page = pw.new_page()

        # Phase A: load URL 1 (establish SPA session) — quick, no scroll
        page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
        # Wait for SPA to initialize (React mount, virtual list init)
        for _ in range(30):
            has_msgs = page.evaluate("""
                () => document.querySelectorAll(
                    '.ds-virtual-list [data-message-id], .ds-message'
                ).length > 0
            """)
            if has_msgs:
                break
            page.wait_for_timeout(1000)
        page.wait_for_timeout(1000)

        # Phase B: navigate to URL 2 with network monitoring
        network_urls = []
        page.on("request", lambda r: network_urls.append(r.url))

        t0 = time.time()
        url_before = page.url
        action(page)
        # Wait for any navigation to settle
        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        self._wait_for_stable_dom(page)
        url_after = page.url
        elapsed = time.time() - t0

        # Navigation type detection via Performance API
        nav_type = self._classify_nav(network_urls, page)
        if url_before == url_after and label != "goto":
            nav_type = "NO_NAV"
        msgs = self._count_messages(page)

        page.close()
        pw.close()

        return {
            "strategy": label,
            "nav_type": nav_type,
            "re_init_seconds": round(elapsed, 1),
            "messages_in_sink": msgs,
        }

    def _classify_nav(self, network_urls, page):
        full_reload_indicators = [
            r'/static/main\.', r'/static/default-vendors\.',
            r'create_pow_challenge',
        ]
        for url in network_urls:
            for pattern in full_reload_indicators:
                if re.search(pattern, url):
                    return "FULL_RELOAD"
        try:
            nav_entry = page.evaluate("""
                () => {
                    var e = performance.getEntriesByType('navigation');
                    return e.length ? e[0].type : null;
                }
            """)
            if nav_entry == "navigate":
                return "FULL_RELOAD"
        except Exception:
            pass
        js_count = sum(1 for u in network_urls if '.js' in u)
        if js_count > 2:
            return "PARTIAL"
        return "SOFT_NAV"

    def _wait_for_stable_dom(self, page, stable_ms=600):
        last_count = -1
        stable_start = None
        while True:
            count = page.evaluate("""
                () => document.querySelectorAll(
                    '.ds-virtual-list [data-message-id], .ds-message'
                ).length
            """)
            if count == last_count:
                if stable_start is None:
                    stable_start = time.time()
                if time.time() - stable_start > stable_ms / 1000:
                    return
            else:
                last_count = count
                stable_start = None
            page.wait_for_timeout(100)

    def _count_messages(self, page):
        try:
            raw = page.evaluate("""(function(){
                var r = {messages:[]};
                document.querySelectorAll('div.ds-message').forEach(function(el){
                    if(!el.textContent.trim())return;
                    var role = el.querySelector('.ds-assistant-message-main-content') ? 'assistant' : 'user';
                    r.messages.push({role:role});
                });
                return r.messages.length;
            })()""")
            return raw
        except Exception:
            return -1

    def run(self):
        print("=== ExperimentHarness v1 ===")
        print(f"URL: {self.url}\n")

        print("[PHASE 1] Baseline (3 runs, stock config)")
        phase1 = self._run_trials("baseline", 3)
        self.phases.append(phase1)
        self._print_phase(phase1)

        print("\n[PHASE 2] No diagnostics (3 runs, capture_*=False)")
        phase2 = self._run_trials("no_diagnostics", 3,
            capture_ssr=False, capture_cache=False,
            capture_bundles=False, capture_state=False)
        self.phases.append(phase2)
        self._print_phase(phase2)

        self._make_decision()
        self._print_decision()

        if self.url2:
            print("\n[PHASE 3] Navigation test")
            phase3 = self._run_navigation_tests()
            self.phases.append(phase3)
            self._print_nav_results(phase3)

        self._save_report()

    def _run_navigation_tests(self):
        escape_js = lambda s: s.replace("'", "\\'")
        u2 = self.url2
        strategies = [
            ("goto",           lambda p: p.goto(u2)),
            ("href",           lambda p: p.evaluate(f"window.location.href = '{escape_js(u2)}'")),
            ("custom_event",   lambda p: p.evaluate(f"window.dispatchEvent(new CustomEvent('navigate', {{detail: '{escape_js(u2)}'}}))")),
            ("next_router",    lambda p: p.evaluate("window.next && window.next.router && window.next.router.push('{0}')".format(escape_js(u2)))),
        ]
        results = []
        for label, action in strategies:
            print(f"  testing: {label} ...")
            r = self._test_navigation_strategy(label, action)
            results.append(r)
            print(f"    nav_type={r['nav_type']} re_init={r['re_init_seconds']}s msgs={r['messages_in_sink']}")
        return {"phase": "navigation", "strategies": results}

    def _print_nav_results(self, phase):
        print("  Navigation results:")
        for s in phase["strategies"]:
            print(f"    {s['strategy']}: {s['nav_type']} {s['re_init_seconds']}s msgs={s['messages_in_sink']}")

    def _print_phase(self, phase):
        for t in phase["trials"]:
            print(f"  run {t['trial']}: {t['count']} msgs {t['e2e']}s {t['status']}")
        print(f"  count: {phase['count_mean']} +/- {phase['count_std']}")
        print(f"  time:  {phase['time_mean']}s +/- {phase['time_std']}s")

    def _make_decision(self):
        b = self.phases[0]
        n = self.phases[1]
        count_diff = b["count_mean"] - n["count_mean"]
        time_saved = b["time_mean"] - n["time_mean"]
        stable = abs(count_diff) < 2

        self.decision = {
            "stable": stable,
            "count_diff": count_diff,
            "time_saved_seconds": round(time_saved, 1),
            "recommendation": "safe_to_skip_diagnostics" if stable else "investigate_regression",
        }

    def _print_decision(self):
        d = self.decision
        print("\n=== DECISION ===")
        print(f"  stable (count diff < 2): {d['stable']}")
        print(f"  count diff: {d['count_diff']}")
        print(f"  time saved: {d['time_saved_seconds']}s")
        print(f"  recommendation: {d['recommendation']}")

    def _save_report(self):
        chat_id = self.url.rstrip("/").rsplit("/", 1)[-1][:8]
        path = f"raw/experiment_report_{chat_id}.json"
        report = {
            "url": self.url,
            "phases": self.phases,
            "decision": self.decision,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nreport saved: {path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    phase_override = None
    if "--phase" in args:
        idx = args.index("--phase")
        phase_override = int(args[idx + 1])
        args = args[:idx] + args[idx + 2:]
    url = args[0] if args else input("URL 1: ")
    url2 = args[1] if len(args) > 1 else None
    h = ExperimentHarness(url, url2)
    if phase_override == 3:
        print(f"=== Phase 3 only: Navigation test ===")
        phase3 = h._run_navigation_tests()
        print("  Navigation results:")
        for s in phase3["strategies"]:
            print(f"    {s['strategy']}: {s['nav_type']} {s['re_init_seconds']}s msgs={s['messages_in_sink']}")
    else:
        h.run()
