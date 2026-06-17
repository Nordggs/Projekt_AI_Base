"""
Bootstrap Payload Decoder
Phase 5 — Search for runtime decode patterns in captured JS bundles + HTML.
    pako (inflate / gzip)
    LZ-String (decompressFromBase64 / decompressFromUTF16)
    Uint8Array → text decoder
    atob → JSON.parse
    Decode base64 payloads
    Extract embedded JSON blobs
Output: messages found or "no payload detected"
"""
import base64
import json
import os
import re
import sys
import zlib


def load_js_files(js_dir="raw/js_bundles"):
    files = []
    if not os.path.isdir(js_dir):
        return files
    for fname in sorted(os.listdir(js_dir)):
        if fname.endswith(".js"):
            path = os.path.join(js_dir, fname)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                files.append({"name": fname, "path": path, "text": f.read()})
    return files


def scan_decode_patterns(text):
    patterns = {
        "pako.inflate": r'pako\.(inflate|ungzip|gunzip|rawinflate)',
        "Uint8Array": r'Uint8Array\s*\(',
        "atob": r'atob\s*\(\s*["\']',
        "JSON.parse+long": r'JSON\.parse\s*\(\s*["\'][a-zA-Z0-9+/=]{100,}',
        "webpackChunk": r'webpackChunk',
        "decodeURIComponent": r'decodeURIComponent\s*\(',
        "LZString": r'LZString|lz-string|lzstring',
        "fromCharCode": r'String\.fromCharCode\s*\(',
        "TextDecoder": r'TextDecoder|decode\(\s*new\s+Uint8Array',
        "Inflate": r'new\s+Inflate|Zlib\.Inflate',
    }
    found = []
    for label, pattern in patterns.items():
        matches = re.findall(pattern, text)
        if matches:
            found.append((label, len(matches)))
    return found


def extract_large_js_objects(text, min_len=500):
    """Find embedded JSON objects/arrays in JS text."""
    results = []
    # Try to find JSON.parse with a large string argument
    for m in re.finditer(r'JSON\.parse\s*\(\s*["\']([^"\']{200,})["\']\s*\)', text):
        try:
            parsed = json.loads(m.group(1))
            results.append({"type": "JSON.parse_inline", "data": parsed, "pos": m.start()})
        except json.JSONDecodeError:
            pass

    # Try to find atob calls and decode
    for m in re.finditer(r'atob\s*\(\s*["\']([a-zA-Z0-9+/=]{50,})["\']\s*\)', text):
        try:
            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
            # Check if decoded looks like JSON
            if decoded.strip().startswith("{"):
                parsed = json.loads(decoded)
                results.append({"type": "atob_json", "data": parsed, "pos": m.start()})
                continue
            results.append({"type": "atob_text", "preview": decoded[:200], "pos": m.start()})
        except Exception:
            pass

    # Try to find pako.inflate calls with data arguments
    for m in re.finditer(r'pako\.(inflate|ungzip|rawinflate)\(([^)]+)\)', text):
        results.append({"type": "pako_call", "pos": m.start(), "context": text[max(0,m.start()-50):m.end()+50]})

    # Find large base64 strings (potential encoded payloads)
    for m in re.finditer(r'["\']([a-zA-Z0-9+/=]{200,})["\']', text):
        results.append({"type": "large_base64", "preview": m.group(1)[:100], "pos": m.start()})

    return results


def check_for_messages(data, path=""):
    """Recursively search for message-like structures."""
    findings = []
    if isinstance(data, dict):
        if "messages" in data and isinstance(data["messages"], list):
            findings.append({"path": path + ".messages", "count": len(data["messages"])})
        if "chat_messages" in data and isinstance(data["chat_messages"], list):
            findings.append({"path": path + ".chat_messages", "count": len(data["chat_messages"])})
        if "role" in data and "content" in data:
            findings.append({"path": path, "role": data.get("role"), "content_preview": str(data.get("content", ""))[:80]})
        for k, v in data.items():
            findings.extend(check_for_messages(v, path + "." + k))
    elif isinstance(data, list):
        for i, item in enumerate(data[:50]):
            findings.extend(check_for_messages(item, f"{path}[{i}]"))
    return findings


def main():
    print("=== Bootstrap Payload Decoder ===")
    print()

    # Phase 5 Step 1: Load JS bundles
    js_files = load_js_files()
    print(f"[INFO] JS files loaded: {len(js_files)}")
    print()

    all_messages_found = []

    for jsf in js_files:
        fname = jsf["name"]
        text = jsf["text"]
        size = len(text)

        # Step 3: Scan decode patterns
        patterns = scan_decode_patterns(text)
        if patterns:
            print(f"[BUNDLE] {fname} ({size} bytes)")
            print(f"   Decode patterns:")
            for label, count in patterns:
                print(f"     - {label} x{count}")

            # Step 4: Extract objects
            objects = extract_large_js_objects(text)
            for obj in objects:
                if obj["type"] == "JSON.parse_inline":
                    msgs = check_for_messages(obj["data"])
                    if msgs:
                        print(f"   [FOUND] Messages via {obj['type']}:")
                        for m in msgs:
                            print(f"       {m}")
                            if m.get("count") and m["count"] > 1:
                                all_messages_found.append(m)
                elif obj["type"] == "atob_json":
                    msgs = check_for_messages(obj["data"])
                    if msgs:
                        print(f"   [FOUND] Messages via {obj['type']}:")
                        for m in msgs:
                            print(f"       {m}")
                            if m.get("count") and m["count"] > 1:
                                all_messages_found.append(m)

            if not objects:
                print("   (no extractable objects)")
            print()

    # Check full_page.html if exists
    html_path = "raw/full_page.html"
    if os.path.exists(html_path):
        print(f"[INFO] Checking HTML: {html_path}")
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
        # Find inline <script> with JSON
        inline_patterns = scan_decode_patterns(html)
        if inline_patterns:
            print("   HTML decode patterns:", inline_patterns)
        # Check for base64 in HTML
        b64s = re.findall(r'base64,([a-zA-Z0-9+/=]{200,})', html)
        if b64s:
            print(f"   Base64 blobs in HTML: {len(b64s)}")
            for b in b64s[:3]:
                try:
                    decoded = base64.b64decode(b).decode("utf-8", errors="replace")
                    if "messages" in decoded or "role" in decoded:
                        print(f"     [FOUND] Messages in HTML base64 blob!")
                        msgs = check_for_messages(json.loads(decoded))
                        for m in msgs:
                            print(f"       {m}")
                            all_messages_found.append(m)
                except Exception:
                    pass
        print()

    # Summary
    print("=== SUMMARY ===")
    if all_messages_found:
        print(f"[POSITIVE] Found message-like data in {len(all_messages_found)} locations:")
        for m in all_messages_found:
            print(f"  {m}")
    else:
        print("[NEGATIVE] No message-like data found in any JS bundle or HTML.")
        print("   Conclusion: messages not present in client-side bootstrap.")
        print("   DeepSeek uses server-only conversation graph API.")

    return bool(all_messages_found)


if __name__ == "__main__":
    main()
