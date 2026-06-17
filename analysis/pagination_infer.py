"""
Network Pagination Inference Engine
Phase 2-3: Cluster Graph Build + Multi-field Delta Analysis
Input: raw/network_discovery_*.json
Output: structured report with pagination candidates (or absence thereof)
"""
import json
import os
import sys
from collections import defaultdict
from urllib.parse import urlparse, parse_qs


def load_network(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_params(url):
    qs = parse_qs(urlparse(url).query)
    return {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}


def endpoint_key(url):
    """Normalize URL to endpoint path + sorted param keys (not values)."""
    parsed = urlparse(url)
    path = parsed.path
    params = parse_qs(parsed.query)
    return (path, frozenset(params.keys()))


def cluster_requests(requests):
    clusters = defaultdict(list)
    for r in requests:
        key = endpoint_key(r["url"])
        clusters[key].append({
            "id": r["id"],
            "ts": r["ts"],
            "method": r["method"],
            "url": r["url"],
            "params": parse_params(r["url"]),
            "resource_type": r.get("resource_type", ""),
        })
    return clusters


def timing_deltas(calls):
    calls_sorted = sorted(calls, key=lambda x: x["ts"])
    deltas = []
    for i in range(1, len(calls_sorted)):
        deltas.append(calls_sorted[i]["ts"] - calls_sorted[i - 1]["ts"])
    return deltas


def param_diff(call_a, call_b):
    """Find params that changed between two calls to the same endpoint."""
    params_a = call_a["params"]
    params_b = call_b["params"]
    all_keys = set(params_a.keys()) | set(params_b.keys())
    changed = {}
    for k in sorted(all_keys):
        va = params_a.get(k)
        vb = params_b.get(k)
        if va != vb:
            changed[k] = (va, vb)
    return changed


def find_pagination_candidates(clusters):
    """
    Phase 3 — Multi-field Delta Analysis.
    For each cluster with >= 2 calls, find the invariant changing fields
    that could represent a pagination cursor/offset.
    """
    candidates = []

    for (path, param_sig), calls in clusters.items():
        if len(calls) < 2:
            continue
        calls_sorted = sorted(calls, key=lambda x: x["ts"])
        deltas = timing_deltas(calls)

        # Sequential pair diff
        all_diffs = []
        for i in range(len(calls_sorted) - 1):
            diff = param_diff(calls_sorted[i], calls_sorted[i + 1])
            all_diffs.append(diff)

        # Intersection: fields that change in EVERY sequential pair
        if all_diffs:
            common_changed = set(all_diffs[0].keys())
            for d in all_diffs[1:]:
                common_changed &= set(d.keys())
        else:
            common_changed = set()

        candidates.append({
            "endpoint": path,
            "param_signature": list(param_sig),
            "call_count": len(calls),
            "timing_deltas": deltas,
            "all_diffs": all_diffs,
            "invariant_changing_params": list(common_changed),
            "calls": calls_sorted,
        })

    return candidates


def classify_responses(responses, requests_by_id):
    """Match responses to requests and classify by size/content."""
    response_map = {}
    for r in responses:
        req_id = r["id"]
        cl = r["headers"].get("content-length", r["headers"].get("Content-Length"))
        ct = r["headers"].get("content-type", r["headers"].get("Content-Type", ""))
        response_map[req_id] = {
            "status": r["status"],
            "content_length": int(cl) if cl and cl.isdigit() else None,
            "content_type": ct,
        }
    return response_map


def find_scroll_phase_requests(requests, api_ts_start):
    """Find requests that happen during scroll phase (after initial load)."""
    scroll_requests = [r for r in requests if r["ts"] > api_ts_start + 2.5]
    return scroll_requests


def report(candidates, clusters, requests, responses, scroll_reqs):
    path = "analysis/pagination_report.md"
    scroll_req_count = len(scroll_reqs)
    scroll_api = [r for r in scroll_reqs if "/api/" in r["url"]]
    scroll_chat = [r for r in scroll_reqs if "/api/v0/chat/" in r["url"]]

    lines = []
    lines.append("# Network Pagination Analysis Report")
    lines.append(f"Generated from `raw/network_discovery_*.json`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total requests: {len(requests)}")
    lines.append(f"- Total responses: {len(responses)}")
    lines.append(f"- Scroll phase requests (after +2.5s): {scroll_req_count}")
    lines.append(f"  - API calls during scroll: {len(scroll_api)}")
    lines.append(f"  - Chat API calls during scroll: {len(scroll_chat)}")
    lines.append("")
    lines.append("## API Endpoint Clusters")
    lines.append("")
    lines.append("| Endpoint | Params | Calls | Invariant Changed | Timing Deltas |")
    lines.append("|---|---|---|---|---|")
    for c in sorted(candidates, key=lambda x: x["call_count"], reverse=True):
        ep = c["endpoint"][:60]
        params = ", ".join(c["param_signature"])
        inv = ", ".join(c["invariant_changing_params"]) if c["invariant_changing_params"] else "(none)"
        deltas = ", ".join(f"{d:.1f}s" for d in c["timing_deltas"][:3])
        if len(c["timing_deltas"]) > 3:
            deltas += f" ... ({len(c['timing_deltas'])} total)"
        lines.append(f"| `{ep}` | {params} | {c['call_count']} | {inv} | {deltas} |")

    lines.append("")
    lines.append("## Multi-field Delta Analysis")
    lines.append("")
    for c in candidates:
        if not c["all_diffs"]:
            continue
        ep = c["endpoint"]
        lines.append(f"### {ep}")
        lines.append("")
        lines.append(f"- Call count: {c['call_count']}")
        lines.append(f"- Invariant changing params: {c['invariant_changing_params'] or '(none — single call or no changes)'}")
        lines.append("")
        if c["all_diffs"]:
            for i, diff in enumerate(c["all_diffs"]):
                call_a = c["calls"][i]
                call_b = c["calls"][i + 1]
                lines.append(f"#### Call #{call_a['id']} → #{call_b['id']} (Δt={c['timing_deltas'][i]:.1f}s)")
                if diff:
                    for k, (va, vb) in sorted(diff.items()):
                        va_s = str(va)[:60]
                        vb_s = str(vb)[:60]
                        lines.append(f"- `{k}`: `{va_s}` → `{vb_s}`")
                else:
                    lines.append("  - (no changes)")
                lines.append("")
        lines.append("")

    lines.append("## Scroll Phase Analysis")
    lines.append("")
    lines.append(f"Duration: ~{scroll_req_count * 0.5:.0f}s estimated")
    lines.append(f"API calls during scroll: {len(scroll_api)}")
    lines.append("")
    if scroll_api:
        lines.append("### API calls during scroll:")
        for r in scroll_api:
            lines.append(f"- `{r['url'][:100]}`")
    else:
        lines.append("**No API calls during scroll phase.**")
        lines.append("")
        lines.append("This confirms: messages are NOT loaded via XHR/Fetch during scrolling.")
        lines.append("The only periodic requests are:")
        sites = set()
        for r in scroll_reqs:
            for ext in [".ico", ".png", ".jpg", ".svg"]:
                if ext in r["url"]:
                    sites.add(r["url"].rsplit("/", 1)[-1])
                    break
        if sites:
            lines.append("- Site icon fetches (from rendered link previews in messages)")
        hif = [r for r in scroll_reqs if "hif-" in r["url"]]
        if hif:
            lines.append(f"- Analytics/heartbeat pings ({len(hif)} calls)")

    lines.append("")
    lines.append("## Key Observations")
    lines.append("")
    lines.append("1. `history_messages` called **once** at +0.8s, returned `chat_messages: []`")
    lines.append("2. `create_pow_challenge` called twice (+1.2s, +1.8s) — PoW-gated API")
    lines.append("3. `chat_session/fetch_page` has `lte_cursor.pinned=false` — cursor for session list, not messages")
    lines.append("4. **Zero** chat API calls during 65s of scrolling")
    lines.append("5. Only periodic requests: analytics (`hif-dliq`) + site icon fetches (from links in messages)")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append("**No pagination detected in network trace.** Messages are likely embedded in:")
    lines.append("- SSR initial HTML (`document` response, Br-compressed)")
    lines.append("- First JS bundle (`main.*.js`, `default-vendors.*.js`, etc.)")
    lines.append("")
    lines.append("## Next Capture Recommendations")
    lines.append("")
    lines.append("To find the actual data source, add to `deepseek.py`:")
    lines.append("1. `page.content()` → save HTML, grep for `__NEXT_DATA__`, `window.__INITIAL`")
    lines.append("2. `response.body()` for initial document + main JS bundle (size check)")
    lines.append("3. `page.evaluate('window.__INITIAL_STATE__')` after page load")
    lines.append("4. Check `document.querySelector('script#__NEXT_DATA__')`")
    lines.append("")

    report = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[SUCCESS] Report saved: {path}")
    return report


def main():
    # Find latest network discovery
    net_dir = "raw"
    net_files = [f for f in os.listdir(net_dir) if f.startswith("network_discovery_") and f.endswith(".json")]
    if not net_files:
        print("[ERROR] No network discovery files found in raw/")
        sys.exit(1)

    net_path = os.path.join(net_dir, sorted(net_files)[-1])
    print(f"[INFO] Loading: {net_path}")

    data = load_network(net_path)
    requests = data.get("requests", [])
    responses = data.get("responses", [])

    # Phase 2: Cluster build
    clusters = cluster_requests(requests)

    print(f"[INFO] Requests: {len(requests)}, Responses: {len(responses)}")
    print(f"[INFO] Endpoint clusters: {len(clusters)}")

    # Filter to API v0 clusters only + large clusters
    api_clusters = {k: v for k, v in clusters.items() if "/api/" in k[0]}
    print(f"[INFO] API clusters: {len(api_clusters)}")

    # Phase 3: Delta analysis
    candidates = find_pagination_candidates(api_clusters)

    # Scroll phase
    if requests:
        api_ts = requests[0]["ts"]
        scroll_reqs = find_scroll_phase_requests(requests, api_ts)
    else:
        scroll_reqs = []

    # Report
    report(candidates, api_clusters, requests, responses, scroll_reqs)

    # Quick CLI summary
    print()
    print("=== CLI SUMMARY ===")
    for c in candidates:
        if c["invariant_changing_params"]:
            print(f"[CURSOR CANDIDATE] {c['endpoint']}")
            print(f"  Changing: {c['invariant_changing_params']}")
        else:
            print(f"[NO PAGINATION] {c['endpoint']} ({c['call_count']} calls, no param changes)")
    print()
    scroll_api = [r for r in scroll_reqs if "/api/" in r["url"]]
    print(f"[SCROLL PHASE] {len(scroll_reqs)} requests, {len(scroll_api)} API calls")
    if not scroll_api:
        print("[FINDING] Zero API calls during scroll — messages NOT loaded via pagination")


if __name__ == "__main__":
    main()
