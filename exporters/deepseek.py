import hashlib
import json
import os
import re
import time
from threading import Lock

from .base import Exporter

EXTRACT_JS = """(function(){
function htmlToMarkdown(html){
  var d=document.createElement('div');d.innerHTML=html;
  function walk(n){
    var out='';
    for(var i=0;i<n.childNodes.length;i++){
      var ch=n.childNodes[i];
      if(ch.nodeType===3){out+=ch.textContent;continue;}
      if(ch.nodeType!==1)continue;
      var tag=ch.tagName.toLowerCase();
      if(tag==='pre'){
        var code=ch.querySelector('code');
        var lang='';
        if(code){var cls=code.getAttribute('class')||'';var m=cls.match(/language-(\\w+)/);if(m)lang=m[1];}
        out+='```'+lang+'\\n'+(code?code.textContent:ch.textContent)+'\\n```\\n';
        continue;}
      if(tag==='br'){out+='\\n';continue;}
      if(tag==='hr'){out+='---\\n\\n';continue;}
      var inner=walk(ch);
      if(tag==='strong'||tag==='b'){out+='**'+inner.trim()+'**';}
      else if(tag==='em'||tag==='i'){out+='*'+inner.trim()+'*';}
      else if(tag==='code'){out+='`'+ch.textContent+'`';}
      else if(tag==='p'){out+=inner.trim()+'\\n\\n';}
      else if(tag==='div'){out+=inner.trim()+'\\n';}
      else if(tag==='ul'){
        var items=ch.children;
        for(var j=0;j<items.length;j++){if(items[j].tagName==='LI')out+='- '+walk(items[j]).trim()+'\\n';}}
      else if(tag==='ol'){
        var items=ch.children;
        for(var j=0;j<items.length;j++){if(items[j].tagName==='LI')out+='1. '+walk(items[j]).trim()+'\\n';}}
      else if(tag==='li'){out+=inner.trim()+'\\n';}
      else if(tag.match(/^h[1-6]$/)){out+='#'.repeat(parseInt(tag[1]))+' '+inner.trim()+'\\n\\n';}
      else if(tag==='a'){out+='['+inner.trim()+']('+(ch.getAttribute('href')||'')+')';}
      else if(tag==='img'){out+='!['+(ch.getAttribute('alt')||'')+']('+(ch.getAttribute('src')||'')+')';}
      else if(tag==='blockquote'){out+='> '+inner.trim().replace(/\\n/g,'\\n> ')+'\\n\\n';}
      else if(tag==='table'){
        var rows=ch.querySelectorAll('tr');
        for(var ri=0;ri<rows.length;ri++){
          var cells=rows[ri].querySelectorAll('th,td');
          var rowText=[];
          for(var ci=0;ci<cells.length;ci++){rowText.push(walk(cells[ci]).trim());}
          out+='| '+rowText.join(' | ')+' |\\n';
          if(ri===0&&rows[0].querySelector('th')){
            var sep=[];for(var ci=0;ci<cells.length;ci++){sep.push('---');}
            out+='| '+sep.join(' | ')+' |\\n';}}
        out+='\\n';}
      else{out+=inner;}
    }
    return out;
  }
  return walk(d).trim();
}
var r={title:'',titleSource:'',messages:[]};
// 1 — sidebar link matching current path
document.querySelectorAll('a[href*="/chat/"]').forEach(function(a){
if(a.getAttribute('href')===location.pathname){
var t=a.querySelector('.c08e6e93');if(t&&t.textContent.trim()){r.title=t.textContent.trim();r.titleSource='sidebar';}}});
// 2 — document title
if(!r.title){var dt=document.title.replace(/\\s*[–-]\\s*DeepSeek.*/,'').trim();if(dt){r.title=dt;r.titleSource='doc-title';}}
// 3 — first user message (first 40 chars)
if(!r.title){var u=document.querySelector('div.ds-message[data-nav-id]');
if(u){var uc=u.querySelector('.fbb737a4,.ds-message-content,[class*="message-content"],[data-testid="user_message"]')||u;var ut=uc.textContent.trim().substring(0,40);if(ut){r.title=ut;r.titleSource='first-msg';}}}
if(!r.title){r.title='chat';r.titleSource='default';}
// Extract messages
document.querySelectorAll('div.ds-message').forEach(function(el){
if(!el.textContent.trim())return;
var c='',role='assistant';
var a=el.querySelector('.ds-assistant-message-main-content');
if(a){var p=[];var k=el.querySelector('.ds-think-content');
if(k&&k.textContent.trim())p.push('> [!thought] Мысли модели\\n> '+htmlToMarkdown(k.innerHTML).replace(/\\n/g,'\\n> '));
p.push(htmlToMarkdown(a.innerHTML));c=p.join('\\n\\n');}
else{role='user';var u=el.querySelector('.fbb737a4,.ds-message-content,[class*="message-content"],[data-testid="user_message"]');
if(!u){var kids=el.querySelectorAll(':scope>div');for(var j=0;j<kids.length;j++){var t=kids[j].textContent.trim();if(t&&!kids[j].querySelector('.ds-assistant-message-main-content,.ds-think-content')){u=kids[j];break;}}}
c=u?u.textContent.trim():el.textContent.trim();}
if(c){
  var navId=el.getAttribute('data-nav-id')||(el.closest('[data-nav-id]')?el.closest('[data-nav-id]').getAttribute('data-nav-id'):'');
  r.messages.push({role:role,content:c,navId:navId});
}});
var id=(location.pathname.match(/\\/a\\/chat\\/s\\/([a-f0-9-]+)/)||[])[1]||'';
return JSON.stringify({title:r.title,titleSource:r.titleSource,chatId:id,messages:r.messages,sourceUrl:location.href});
})()"""


# ── Scroll engine constants ──
CHUNK_DELAY = 500
MAX_STABLE = 8
NO_NEW_THRESHOLD = 3


def _dedup_key(msg):
    nav = msg.get('navId', '')
    if nav:
        return f'nav:{nav}'
    raw = f"{msg.get('role', '')}|{msg.get('content', '')}"
    return f'sha1:{hashlib.sha1(raw.encode("utf-8")).hexdigest()}'


def _scroll_js(top):
    return f"""(() => {{
    const c = document.querySelector('.ds-virtual-list');
    if (!c) return false;
    c.scrollTop = {top};
    return true;
}})()"""


class DeepSeekExporter(Exporter):
    def __init__(self, browser, url=None,
                 capture_ssr=True, capture_cache=True,
                 capture_bundles=True, capture_state=True):
        super().__init__(browser)
        self._url = url
        self._capture_ssr = capture_ssr
        self._capture_cache = capture_cache
        self._capture_bundles = capture_bundles
        self._capture_state = capture_state

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

    def start(self, on_complete):
        super().start(on_complete)
        if self._browser.mode != "playwright":
            raise RuntimeError("DeepSeekExporter requires playwright mode")
        if not self._url:
            raise RuntimeError("DeepSeekExporter requires a URL")

        page = self._browser.page
        log = self._browser.log.add if self._browser.log else lambda _: None

        # ── Network Discovery v1 + Initial HTML capture ──
        seq = 0
        lock = Lock()
        captured = {"requests": [], "responses": [], "websocket": []}

        def on_request(request):
            with lock:
                nonlocal seq; seq += 1
                captured["requests"].append({
                    "id": seq, "ts": time.time(),
                    "url": request.url, "method": request.method,
                    "headers": dict(request.headers),
                    "resource_type": request.resource_type,
                })

        def on_response(response):
            with lock:
                nonlocal seq; seq += 1
                captured["responses"].append({
                    "id": seq, "ts": time.time(),
                    "url": response.url, "status": response.status,
                    "headers": dict(response.headers),
                })

        def on_websocket(ws):
            with lock:
                captured["websocket"].append({
                    "ts": time.time(), "type": "open", "url": ws.url,
                })
            def on_frame(frame):
                with lock:
                    captured["websocket"].append({
                        "ts": time.time(), "direction": "received",
                        "payload": frame.payload[:20000],
                    })
            ws.on("framereceived", on_frame)
            ws.on("framesent", lambda f: captured["websocket"].append({
                "ts": time.time(), "direction": "sent",
                "payload": f.payload[:20000],
            }))

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("websocket", on_websocket)

        # ── Runtime API Tracer ──
        page.add_init_script("""
(() => {
    window.__INIT_OK__ = true;
    window.__apiPayloads = [];
    const push = (data) => { try { window.__apiPayloads.push(data); } catch(e) {} };
    const parseUrl = (url) => { try { const u = new URL(url, location.origin); return {path: u.pathname, query: u.search}; } catch(e) { return {path: url, query: ''}; } };

    const origFetch = window.fetch;
    window.fetch = function(input, init) {
        const url = (typeof input === 'string') ? input : (input && input.url) || '';
        const p = origFetch.apply(this, arguments);
        if (url.includes('/api/v0/chat/')) {
            p.then(resp => {
                const info = parseUrl(url);
                push({type: 'fetch', url, path: info.path, query: info.query, status: resp.status, ts: Date.now()});
            }).catch(()=>{});
        }
        return p;
    };

    const origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.__url = url;
        return origOpen.apply(this, arguments);
    };
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.send = function() {
        this.addEventListener('load', function() {
            try {
                if (this.__url && this.__url.includes('/api/v0/chat/')) {
                    const info = parseUrl(this.__url);
                    push({type: 'xhr', url: this.__url, path: info.path, query: info.query, status: this.status, ts: Date.now()});
                }
            } catch(e) {}
        });
        return origSend.apply(this, arguments);
    };
})();
""")

        # ── Phase 6: Capture history_messages API body ──
        # Use expect_response around goto() to capture the response body
        # on the main thread (not inside event handler — no event loop blocking)
        with page.expect_response(lambda r: 'history_messages' in r.url, timeout=30000) as resp_info:
            page.goto(self._url)

        log("[DEBUG] WAITING FOR SPA READY")
        ready = False
        for _ in range(30):
            ready = page.evaluate("document.readyState === 'complete'")
            if ready:
                break
            page.wait_for_timeout(1000)
        log(f"[DEBUG] SPA READY = {ready}")

        if not ready:
            discovery_path = f"raw/network_discovery_{'timeout'}.json"
            with open(discovery_path, "w", encoding="utf-8") as f:
                json.dump(captured, f, indent=2, ensure_ascii=False)
            log(f"[SUCCESS] Network discovery saved: {discovery_path}")
            self._on_complete({
                "schema_version": 1, "source": "deepseek",
                "error": "timeout — SPA not ready?",
                "chat_id": "", "title": "", "messages": [],
            })
            return

        # ── Phase 6: Extract history_messages body ──
        try:
            history_resp = resp_info.value
            history_body = history_resp.text()
            log(f"[DEBUG] history_messages response: status={history_resp.status} body={len(history_body)} bytes")
            with open("raw/history_messages_response.json", "w", encoding="utf-8") as f:
                f.write(history_body)
            try:
                hdata = json.loads(history_body)
                msgs = hdata.get('chat_messages', []) or hdata.get('messages', [])
                log(f"[DEBUG] history_messages payload: {len(msgs)} messages")
                if msgs:
                    log(f"[SUCCESS] API returned {len(msgs)} messages directly!")
            except json.JSONDecodeError:
                log("[WARN] history_messages response is not valid JSON")
        except Exception as e:
            log(f"[WARN] Failed to capture history_messages: {e}")

        # ── Init script diagnostics ──
        init_ok = page.evaluate("window.__INIT_OK__ === true")
        log(f"[DEBUG] init_script active = {init_ok}")
        if init_ok:
            arr_ok = page.evaluate("Array.isArray(window.__apiPayloads)")
            log(f"[DEBUG] apiPayloads array = {arr_ok}")

        # ── Collect API Tracer payloads ──
        api_calls = page.evaluate("window.__apiPayloads") or []
        if api_calls:
            with open("raw/api_calls_DEBUG.json", "w", encoding="utf-8") as f:
                json.dump(api_calls, f, indent=2, ensure_ascii=False)
            log(f"[DEBUG] API tracer: captured {len(api_calls)} calls")
            for ac in api_calls:
                log(f"[DEBUG]   {ac['type']} {ac['status']} {ac['path']}{ac['query'][:80]}")
        else:
            log("[WARN] API tracer: no calls captured")

        # ── Timing Lock: wait for stable DOM ──
        page.wait_for_timeout(400)
        self._wait_for_stable_dom(page)

        # ── SSR / Initial State Capture ──
        if self._capture_ssr:
            page.wait_for_timeout(500)
            log("[DEBUG] Capturing SSR HTML snapshot")
            ssr_html = page.content()
            ssr_path = "raw/initial_ssr.html"
            with open(ssr_path, "w", encoding="utf-8") as f:
                f.write(ssr_html)
            log(f"[DEBUG] SSR HTML saved: {len(ssr_html)} bytes → {ssr_path}")

        if self._capture_state:
            for key in ["__NEXT_DATA__", "__INITIAL_STATE__", "__NUXT__", "__REACT_QUERY_STATE__"]:
                try:
                    exists = page.evaluate(f"typeof window.{key} !== 'undefined'")
                    log(f"[DEBUG] window.{key} = {exists}")
                    if exists:
                        data = page.evaluate(f"JSON.stringify(window.{key}, null, 2)")
                        state_name = key.strip("_")
                        state_path = f"raw/state_{state_name}.json"
                        with open(state_path, "w", encoding="utf-8") as f:
                            f.write(data)
                        log(f"[DEBUG]   saved to {state_path}")
                except Exception as e:
                    log(f"[WARN] window.{key} check failed: {e}")

        # ── Phase 4.4: CacheStorage + localStorage dump ──
        if self._capture_cache:
            log("[DEBUG] Dumping localStorage...")
            try:
                ls = page.evaluate("JSON.stringify(window.localStorage)")
                with open("raw/local_storage.json", "w", encoding="utf-8") as f:
                    f.write(ls)
                log(f"[DEBUG] localStorage saved: {len(ls)} bytes")
            except Exception as e:
                log(f"[WARN] localStorage dump failed: {e}")

            log("[DEBUG] Dumping CacheStorage...")
            try:
                cache = page.evaluate("""
(async () => {
  const result = {};
  const names = await caches.keys();
  for (const name of names) {
    const cache = await caches.open(name);
    const requests = await cache.keys();
    const entries = [];
    for (const req of requests) {
      try {
        const resp = await cache.match(req);
        if (!resp) continue;
        const ct = resp.headers.get('content-type') || '';
        if (ct.includes('stream')) continue;
        const clone = resp.clone();
        const text = await clone.text();
        if (text.includes('"role"') || text.includes('"message"') || text.includes('"content"') || text.length > 5000) {
          entries.push({ url: req.url, size: text.length, type: ct, preview: text.slice(0, 2000) });
        }
      } catch(e) {}
    }
    if (entries.length) result[name] = entries;
  }
  return result;
})()
""")
                total_entries = sum(len(v) for v in cache.values())
                with open("raw/cache_dump.json", "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2, ensure_ascii=False)
                log(f"[DEBUG] CacheStorage: {total_entries} entries in {len(cache)} caches saved → raw/cache_dump.json")
                if total_entries:
                    for name, entries in cache.items():
                        for e in entries:
                            log(f"[DEBUG]   cache={name} url={e['url'][:100]} size={e['size']} type={e['type']}")
            except Exception as e:
                log(f"[WARN] CacheStorage dump failed: {e}")

        # ── Phase 5: Bootstrap JS bundle capture ──
        if self._capture_bundles:
            log("[DEBUG] Capturing bootstrap JS bundles...")
            js_dir = "raw/js_bundles"
            os.makedirs(js_dir, exist_ok=True)
            js_captured = 0
            try:
                full_html = page.content()
                src_urls = re.findall(r'<script[^>]*src="([^"]+)"', full_html)
                perf_entries = page.evaluate("""(() => {
                    try {
                        return performance.getEntriesByType('resource')
                            .filter(e => e.name.includes('.js'))
                            .map(e => e.name);
                    } catch(e) { return []; }
                })()""")
                all_js_urls = list(set(src_urls + perf_entries))

                target_patterns = [
                    r'37627', r'8138', r'54994', r'31765', r'52909', r'56710',
                    r'64771', r'36194', r'87321',
                    r'/chunk/', r'prism-', r'katex',
                ]
                target_urls = []
                for u in all_js_urls:
                    if any(re.search(p, u) for p in target_patterns):
                        target_urls.append(u)

                seen_urls = set()
                for u in target_urls:
                    filename = u.rsplit('/', 1)[-1].split('?')[0]
                    if filename in seen_urls:
                        continue
                    seen_urls.add(filename)
                    try:
                        js_text = page.evaluate(f"""(() => {{
                            return fetch('{u}', {{cache: 'force-cache'}})
                                .then(r => r.text())
                                .catch(() => null);
                        }})()""")
                        if js_text and len(js_text) > 100:
                            path = os.path.join(js_dir, filename)
                            with open(path, "w", encoding="utf-8") as f:
                                f.write(js_text)
                            js_captured += 1
                            log(f"[DEBUG] JS bundle saved: {filename} ({len(js_text)} bytes)")
                            patterns_found = []
                            for pat in ['pako.inflate', 'Uint8Array', 'atob', 'webpackChunk',
                                        'JSON.parse', 'decodeURIComponent', 'base64']:
                                if pat in js_text:
                                    patterns_found.append(pat)
                            if patterns_found:
                                log(f"[DEBUG]   decode patterns: {patterns_found}")
                    except Exception as e:
                        log(f"[WARN] JS fetch failed: {filename} — {e}")
            except Exception as e:
                log(f"[WARN] JS bundle capture failed: {e}")
            log(f"[DEBUG] Bootstrap JS bundles captured: {js_captured} files")

        # ── Scroll Engine v3.1 (convergence-based) ──
        page.evaluate("document.querySelector('.ds-virtual-list')?.scrollTo(0,0)")
        page.wait_for_timeout(CHUNK_DELAY)

        merged, seen = [], set()
        title, chat_id, source_url = '', '', ''
        scroll_top = 0
        stable_counter = 0

        while stable_counter < MAX_STABLE:
            scroll_top += 1000
            page.evaluate(_scroll_js(scroll_top))
            page.wait_for_timeout(CHUNK_DELAY)

            raw = page.evaluate(EXTRACT_JS)
            data = json.loads(raw)

            if not title:
                title = data.get('title', '')
            if not chat_id:
                chat_id = data.get('chatId', '')
            if not source_url:
                source_url = data.get('sourceUrl', '')

            new_count = 0
            for msg in data.get('messages', []):
                key = _dedup_key(msg)
                if key not in seen:
                    seen.add(key)
                    merged.append(msg)
                    new_count += 1

            if new_count == 0:
                stable_counter += 1
            else:
                stable_counter = max(0, stable_counter - NO_NEW_THRESHOLD)

            log(f"[INFO] scroll +{new_count} total={len(merged)} stable={stable_counter}/{MAX_STABLE}")

        log(f"[SUCCESS] Extraction complete: {len(merged)} messages")

        discovery_path = f"raw/network_discovery_{chat_id[:8]}.json"
        with open(discovery_path, "w", encoding="utf-8") as f:
            json.dump(captured, f, indent=2, ensure_ascii=False)
        log(f"[SUCCESS] Network discovery saved: {discovery_path}")

        self._on_complete({
            "schema_version": 1,
            "source": "deepseek",
            "chat_id": chat_id,
            "title": title,
            "source_url": source_url,
            "messages": merged,
        })
