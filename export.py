"""
CLI batch exporter for DeepSeek chats — thin orchestration layer.

Usage:
    python export.py <url> [url2 ...]
    python export.py --file urls.txt
    python export.py --headless <url>

Behaviour:
    - diagnostics OFF (capture_*=False)
    - pipeline order unchanged
    - one browser session per URL (session reuse impossible — proven)
    - no harness involvement
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from exporters.base import Browser
from exporters.deepseek import DeepSeekExporter
from exporters.playwright_browser import BrowserPlaywright
from exporters.writer import ExportWriter


def export_url(url, writer, headless=False):
    pw = BrowserPlaywright(headless=headless)
    pw.start()
    try:
        browser = Browser(window=None, mode="playwright", log=None)
        browser._playwright_page = pw.page
        result = []
        exporter = DeepSeekExporter(browser, url=url,
            capture_ssr=False, capture_cache=False,
            capture_bundles=False, capture_state=False)
        exporter.start(lambda r: result.append(r))
        data = result[0] if result else None
        if data and "error" not in data:
            path = writer.write(data)
            return path, len(data.get("messages", []))
        return None, 0
    finally:
        pw.close()


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python export.py <url> [url2 ...]")
        print("   or: python export.py --file <path>")
        print("   or: python export.py --headless <url>")
        sys.exit(1)

    headless = False
    if "--headless" in args:
        headless = True
        args.remove("--headless")

    urls = []
    if args[0] == "--file":
        if len(args) < 2:
            print("Error: --file requires a path argument")
            sys.exit(1)
        with open(args[1]) as f:
            urls = [line.strip() for line in f if line.strip()]
    else:
        urls = [u for u in args if u.startswith("http")]

    if not urls:
        print("No valid URLs provided")
        sys.exit(1)

    writer = ExportWriter()
    total_ok = 0
    total_msgs = 0
    total_time = 0.0

    print(f"Exporting {len(urls)} chat(s) {'(headless)' if headless else '(visible)'}")
    print(f"{'─' * 60}")

    for i, url in enumerate(urls, 1):
        short = url.rstrip("/").rsplit("/", 1)[-1][:12]
        print(f"[{i}/{len(urls)}] {short} ...", end=" ", flush=True)
        t0 = time.time()
        path, count = export_url(url, writer, headless=headless)
        elapsed = time.time() - t0
        total_time += elapsed

        if path:
            print(f"OK  {count} msgs  {elapsed:.0f}s  → {path.name}")
            total_ok += 1
            total_msgs += count
        else:
            print(f"ERR  {elapsed:.0f}s")
            print(f"      (check login / URL validity)")

    print(f"{'─' * 60}")
    print(f"Done: {total_ok}/{len(urls)} exported, {total_msgs} total messages, {total_time:.0f}s total")


if __name__ == "__main__":
    main()
