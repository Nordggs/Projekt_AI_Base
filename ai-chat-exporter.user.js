// ==UserScript==
// @name         AI Chat Exporter
// @namespace    http://tampermonkey.net/
// @version      0.5
// @description  Сохраняет AI-чаты через локальный сервер или браузер. Панель с чекбоксами: This chat / All chats. Хоткей Ctrl+Shift+S.
// @author       you
// @match        https://chat.deepseek.com/*
// @match        https://gemini.google.com/*
// @match        https://*.glm5.ai/*
// @match        https://chat.qwen.ai/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_xmlhttpRequest
// @grant        GM_notification
// @connect      localhost
// @connect      127.0.0.1
// @icon         data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💾</text></svg>
// ==/UserScript==

(function () {
    'use strict';

    // ─── Служебные константы ──────────────────────────────────────────────
    const CFG_KEY = 'ai_exporter_cfg';

    function defaultCfg() {
        return { serverUrl: 'http://localhost:18888', fallback: true };
    }

    function loadCfg() {
        try { return Object.assign(defaultCfg(), JSON.parse(GM_getValue(CFG_KEY, '{}'))); }
        catch { return defaultCfg(); }
    }

    function saveCfg(cfg) { GM_setValue(CFG_KEY, JSON.stringify(cfg)); }

    // ─── Сервисы ───────────────────────────────────────────────────────────
    const SERVICES = {
        'chat.deepseek.com': {
            name: 'deepseek',
            chatItemSelector: 'a[href*="/chat/"]',
            getChatUrl: (el) => el.href,
            getChatTitle: () => {
                const links = document.querySelectorAll('a[href*="/chat/"]');
                const curPath = location.pathname;
                for (const link of links) {
                    if (link.getAttribute('href') === curPath) {
                        const t = link.querySelector('.c08e6e93');
                        if (t) return t.textContent.trim();
                    }
                }
                const first = document.querySelector('a[href*="/chat/"]');
                if (first) {
                    const t = first.querySelector('.c08e6e93');
                    if (t) return t.textContent.trim();
                }
                return 'untitled';
            },
            getChatId: (url) => { const m = url.match(/\/a\/chat\/s\/([a-f0-9-]+)/); return m ? m[1] : null; },
            messageSelector: 'div.ds-message',
            userSelector: 'div.ds-message[data-nav-id]',
            assistantSelector: 'div.ds-message:not([data-nav-id])',
            contentSelector: (el, role) => {
                if (role === 'user') {
                    const c = el.querySelector('.fbb737a4');
                    return c ? c.textContent.trim() : el.textContent.trim();
                }
                const parts = [];
                const think = el.querySelector('.ds-think-content');
                if (think && think.textContent.trim()) {
                    parts.push('### Thinking\n' + think.textContent.trim());
                }
                const main = el.querySelector('.ds-assistant-message-main-content');
                if (main) parts.push(main.textContent.trim());
                return parts.join('\n\n') || el.textContent.trim();
            },
            readySelector: 'div.ds-message',
            isListPage: () => /^https:\/\/chat\.deepseek\.com\/?$/.test(location.href),
            isChatPage: () => /\/a\/chat\/s\/[a-f0-9-]+/.test(location.pathname),
            listPageUrl: 'https://chat.deepseek.com/',
        },
        'gemini.google.com': {
            name: 'gemini',
            chatItemSelector: 'a[href*="/app/"], [class*="conversation-item"] a, [role="listitem"] a',
            getChatUrl: (el) => el.href,
            getChatTitle: (el) => el.textContent.trim() || 'untitled',
            getChatId: (url) => { const m = url.match(/\/app\/([a-zA-Z0-9_-]+)/); return m ? m[1] : url.replace(/[^a-zA-Z0-9]/g, '').slice(-12); },
            messageSelector: '[class*="message"], [class*="response"], [class*="conversation"]',
            userSelector: '[class*="user"], [class*="human"]',
            assistantSelector: '[class*="model"], [class*="assistant"]',
            contentSelector: '[class*="content"], [class*="text"]',
            readySelector: '[class*="message"]',
            isListPage: () => /^https:\/\/gemini\.google\.com\/?$/.test(location.href),
            isChatPage: () => /\/app\/[a-zA-Z0-9_-]+/.test(location.pathname),
            listPageUrl: 'https://gemini.google.com/',
        },
        'glm5.ai': {
            name: 'glm5',
            chatItemSelector: 'a[href*="chat"], [class*="history"] a, [class*="chat-item"]',
            getChatUrl: (el) => el.href,
            getChatTitle: (el) => el.textContent.trim() || 'untitled',
            getChatId: (url) => { const m = url.match(/\/chat\/([a-zA-Z0-9_-]+)/); return m ? m[1] : url.replace(/[^a-zA-Z0-9]/g, '').slice(-12); },
            messageSelector: '[class*="message"], [class*="chat-message"]',
            userSelector: '[class*="user"]',
            assistantSelector: '[class*="assistant"], [class*="ai"]',
            contentSelector: '[class*="content"], [class*="text"]',
            readySelector: '[class*="message"]',
            isListPage: () => /\/?$/.test(location.pathname) || /\/chat\/?$/.test(location.pathname),
            isChatPage: () => /\/chat\/[a-zA-Z0-9_-]+/.test(location.pathname),
            listPageUrl: '/',
        },
        'chat.qwen.ai': {
            name: 'qwen',
            chatItemSelector: 'a[href*="/c/"], a[href*="/chat/"], [class*="history"] a, [class*="chat-item"]',
            getChatUrl: (el) => el.href,
            getChatTitle: (el) => el.textContent.trim() || 'untitled',
            getChatId: (url) => { const m = url.match(/\/(?:c|chat)\/([a-zA-Z0-9_-]+)/); return m ? m[1] : null; },
            messageSelector: '[class*="message"], [class*="chat-msg"]',
            userSelector: '[class*="user"]',
            assistantSelector: '[class*="assistant"], [class*="ai"]',
            contentSelector: '[class*="content"], [class*="text"], [class*="markdown"]',
            readySelector: '[class*="message"]',
            isListPage: () => location.pathname === '/' || location.pathname === '',
            isChatPage: () => /\/(?:c|chat)\/[a-zA-Z0-9_-]+/.test(location.pathname),
            listPageUrl: 'https://chat.qwen.ai/',
        },
    };

    const hostname = location.hostname;
    const serviceKey = Object.keys(SERVICES).find((k) => hostname.includes(k));
    if (!serviceKey) return;
    const SVC = SERVICES[serviceKey];

    // ─── Утилиты ────────────────────────────────────────────────────────────
    function waitForEl(selector, timeout = 25000) {
        return new Promise((resolve, reject) => {
            const el = document.querySelector(selector);
            if (el && el.children.length > 0) return resolve(el);
            const start = Date.now();
            const obs = new MutationObserver(() => {
                const found = document.querySelector(selector);
                if (found && found.children.length > 0) { obs.disconnect(); resolve(found); }
                if (Date.now() - start > timeout) { obs.disconnect(); reject(new Error('Timeout: ' + selector)); }
            });
            obs.observe(document.body, { childList: true, subtree: true });
        });
    }

    function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

    function shortHash(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) { const chr = str.charCodeAt(i); hash = ((hash << 5) - hash) + chr; hash |= 0; }
        return Math.abs(hash).toString(16).slice(0, 6);
    }

    function pad2(n) { return String(n).padStart(2, '0'); }

    function today() { const d = new Date(); return `${d.getFullYear()}-${pad2(d.getMonth()+1)}-${pad2(d.getDate())}`; }

    function sanitizeTitle(title) {
        return title
            .replace(/[<>:"\/\\|?*]/g, '')
            .replace(/\s+/g, '-')
            .replace(/-+/g, '-')
            .replace(/^-+|-+$/g, '')
            .substring(0, 30)
            .toLowerCase() || 'untitled';
    }

    function makeFilename(chatId, chatTitle) {
        const titlePart = chatTitle ? sanitizeTitle(chatTitle) + '_' : '';
        return `${titlePart}${SVC.name}_${today()}_${chatId}.md`;
    }

    // ─── Извлечение сообщений ──────────────────────────────────────────────
    function extractMessages() {
        const blocks = document.querySelectorAll(SVC.messageSelector);
        const messages = [];
        blocks.forEach((el) => {
            if (!el.textContent.trim()) return;
            const role = el.matches(SVC.userSelector) ? 'user' : el.matches(SVC.assistantSelector) ? 'assistant' : 'unknown';
            let content = '';
            if (typeof SVC.contentSelector === 'function') {
                content = SVC.contentSelector(el, role);
            } else if (SVC.contentSelector) {
                const cont = el.querySelector(SVC.contentSelector);
                content = cont ? cont.textContent.trim() : el.textContent.trim();
            } else { content = el.textContent.trim(); }
            if (content) messages.push({ role, content });
        });
        return messages;
    }

    function buildMarkdown(messages, chatId, chatTitle) {
        let md = `<!-- SOURCE_URL: ${location.href} -->\n<!-- CHAT_ID: ${SVC.name}_${chatId} -->\n\n`;
        md += `# ${SVC.name}: ${chatTitle}\n\n`;
        messages.forEach((m) => {
            const prefix = m.role === 'user' ? '#### 👤 Вы' : m.role === 'assistant' ? '#### 🤖 AI' : '#### ?';
            md += prefix + '\n\n' + m.content + '\n\n';
        });
        return md;
    }

    // ─── Сохранение через POST на локальный сервер ─────────────────────────
    function saveViaServer(serverUrl, filename, content) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'POST',
                url: serverUrl.replace(/\/+$/, '') + '/save',
                data: JSON.stringify({ filename, content }),
                headers: { 'Content-Type': 'application/json' },
                onload: (resp) => { if (resp.status === 200) resolve(); else reject(new Error('HTTP ' + resp.status)); },
                onerror: reject,
                ontimeout: reject,
                timeout: 10000,
            });
        });
    }

    // ─── Сохранение через браузер (fallback) ───────────────────────────────
    function saveViaBrowser(filename, content) {
        return new Promise((resolve, reject) => {
            try {
                const blob = new Blob([content], { type: 'text/markdown' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                setTimeout(() => { URL.revokeObjectURL(url); resolve(); }, 100);
            } catch (e) { reject(e); }
        });
    }

    // ─── Сохранение (автовыбор метода) ──────────────────────────────────────
    async function saveFile(filename, content) {
        const cfg = loadCfg();
        if (cfg.serverUrl) {
            try {
                await saveViaServer(cfg.serverUrl, filename, content);
                return;
            } catch (e) {
                console.warn('[AI] Сервер недоступен:', cfg.serverUrl, e);
                GM_notification({ text: '⚠️ Сервер недоступен, сохранено в загрузки браузера', timeout: 3000 });
                if (!cfg.fallback) throw new Error('Сервер недоступен, fallback отключён');
            }
        }
        await saveViaBrowser(filename, content);
    }

    // ─── Скролл для подгрузки виртуального списка (DeepSeek) ────────────────
    async function scrollToLoadAll() {
        if (SVC.name !== 'deepseek') return;
        const container = document.querySelector('.ds-virtual-list._2bd7b35');
        if (!container) return;
        let prevCount = 0;
        let stable = 0;
        while (stable < 5) {
            const items = container.querySelectorAll('[data-virtual-list-item-key]');
            const count = items.length;
            if (count === prevCount) stable++;
            else stable = 0;
            prevCount = count;
            container.scrollTop = 1;
            await sleep(400);
        }
    }

    // ─── Экспорт текущего чата ─────────────────────────────────────────────
    async function exportCurrentChat() {
        try {
            await waitForEl(SVC.readySelector);
            await sleep(800);
            await scrollToLoadAll();
            const messages = extractMessages();
            if (messages.length === 0) { console.warn('[AI] Нет сообщений'); return null; }
            const chatId = SVC.getChatId(location.href) || shortHash(location.href);
            const chatTitle = SVC.getChatTitle(document.querySelector(SVC.chatItemSelector) || document.body);
            const md = buildMarkdown(messages, chatId, chatTitle);
            const name = makeFilename(chatId, chatTitle);
            await saveFile(name, md);
            console.log('[AI] Сохранён:', name);
            if (!GM_getValue('exportInProgress', false)) {
                GM_notification({ text: `✅ ${name}`, timeout: 2000 });
            }
            return chatId;
        } catch (err) {
            console.error('[AI] Ошибка:', err);
            GM_notification({ text: '❌ Ошибка: ' + (err.message || err), timeout: 4000 });
            return null;
        }
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  UI — панель
    // ═════════════════════════════════════════════════════════════════════════

    const PANEL_ID = 'ai-exporter-panel';
    const STYLE_ID = 'ai-exporter-style';

    function injectStyles() {
        if (document.getElementById(STYLE_ID)) return;
        const css = document.createElement('style');
        css.id = STYLE_ID;
        css.textContent = `
#${PANEL_ID} { all:initial; position:fixed; bottom:24px; right:24px; z-index:999999;
  font:13px/1.5 system-ui,-apple-system,sans-serif; color:#eee; user-select:none; }
#${PANEL_ID} * { box-sizing:border-box; }
.ai-panel-body { background:#1a1a2e; border:1px solid #333; border-radius:12px;
  padding:10px 14px; box-shadow:0 8px 32px rgba(0,0,0,0.5); min-width:170px; }
.ai-panel-body label { display:flex; align-items:center; gap:8px; padding:4px 0;
  cursor:pointer; color:#ccc; font-size:13px; }
.ai-panel-body label:hover { color:#fff; }
.ai-panel-body input[type="checkbox"] { accent-color:#10a37f; cursor:pointer; margin:0; }
.ai-panel-actions { display:flex; justify-content:flex-end; gap:6px; margin-top:6px; padding-top:6px;
  border-top:1px solid #333; }
.ai-panel-actions button { background:#10a37f; color:#fff; border:none; border-radius:6px;
  padding:4px 14px; cursor:pointer; font-size:15px; line-height:1.6; transition:background .15s; }
.ai-panel-actions button:hover { background:#0d8c6e; }
.ai-panel-actions .ai-gear { background:transparent; color:#888; font-size:16px; padding:4px 8px; }
.ai-panel-actions .ai-gear:hover { color:#ddd; background:transparent; }
.ai-panel-body .ai-disabled { opacity:0.4; pointer-events:none; }
.ai-panel-body .ai-count { color:#888; font-size:11px; margin-left:auto; }

/* Settings modal */
.ai-overlay { position:fixed; inset:0; z-index:1000000; background:rgba(0,0,0,0.6);
  display:flex; align-items:center; justify-content:center; }
.ai-modal { background:#1a1a2e; border:1px solid #333; border-radius:12px; padding:20px 24px;
  min-width:320px; box-shadow:0 8px 32px rgba(0,0,0,0.6); }
.ai-modal h3 { margin:0 0 14px 0; font-size:16px; color:#eee; }
.ai-modal label { display:flex; align-items:center; gap:8px; color:#ccc; font-size:13px; margin:8px 0; }
.ai-modal input[type="text"] { background:#111; border:1px solid #444; border-radius:6px;
  color:#eee; padding:6px 10px; font:13px monospace; width:100%; margin-top:4px; }
.ai-modal input[type="checkbox"] { accent-color:#10a37f; margin:0; }
.ai-modal .ai-modal-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:14px; }
.ai-modal button { background:#10a37f; color:#fff; border:none; border-radius:6px;
  padding:6px 16px; cursor:pointer; font-size:13px; }
.ai-modal button:hover { background:#0d8c6e; }

/* Export progress overlay (popup) */
#ai-export-progress { all:initial; position:fixed; top:0; left:0; right:0; z-index:999999;
  font:13px/1.5 system-ui,-apple-system,sans-serif; color:#eee;
  background:#1a1a2e; border-bottom:2px solid #10a37f;
  padding:12px 16px; box-shadow:0 4px 16px rgba(0,0,0,0.5);
  max-height:40vh; overflow-y:auto; }
#ai-export-progress .ai-prog-header { font-size:14px; font-weight:600; margin-bottom:6px; }
#ai-export-progress .ai-prog-bar { font-family:monospace; font-size:13px; margin-bottom:6px; color:#10a37f; }
#ai-export-progress .ai-prog-log { font-family:monospace; font-size:11px; line-height:1.4; color:#aaa;
  max-height:200px; overflow-y:auto; margin-top:4px; }
#ai-export-progress .ai-prog-log .ai-log-line { padding:1px 0; }
#ai-export-progress .ai-prog-close { color:#888; font-size:11px; margin-top:4px; font-style:italic; }
`;
        document.head.appendChild(css);
    }

    function buildPanel() {
        if (document.getElementById(PANEL_ID)) return;
        injectStyles();

        const p = document.createElement('div');
        p.id = PANEL_ID;

        const isChat = SVC.isChatPage();
        const isList = SVC.isListPage();
        const sidebarLinks = document.querySelectorAll(SVC.chatItemSelector);
        const hasSidebarChats = sidebarLinks.length > 0;
        const allCount = new Set(Array.from(sidebarLinks).map(el => SVC.getChatUrl(el)).filter(Boolean)).size;

        p.innerHTML = `
<div class="ai-panel-body">
  <label class="${isChat?'':'ai-disabled'}">
    <input type="checkbox" id="ai-cb-this" ${isChat?'checked':''} ${isChat?'':'disabled'}> This chat
  </label>
  <label class="${hasSidebarChats?'':'ai-disabled'}">
    <input type="checkbox" id="ai-cb-all" ${hasSidebarChats?'':'disabled'}> All chats
    ${hasSidebarChats ? '<span class="ai-count">('+allCount+')</span>' : ''}
  </label>
  <div class="ai-panel-actions">
    <button id="ai-btn-save" title="Save selected">💾</button>
    <button class="ai-gear" id="ai-btn-gear" title="Settings">⚙</button>
  </div>
</div>`;
        document.body.appendChild(p);

        document.getElementById('ai-btn-save').addEventListener('click', onSave);
        document.getElementById('ai-btn-gear').addEventListener('click', openSettings);
    }

    function onSave() {
        const doThis = document.getElementById('ai-cb-this')?.checked;
        const doAll = document.getElementById('ai-cb-all')?.checked;

        if (doThis && doAll) {
            // Сначала сохраняем этот чат, потом запускаем Save All
            exportCurrentChat().then((id) => {
                if (id) setTimeout(startSaveAll, 500);
            });
        } else if (doThis) {
            exportCurrentChat();
        } else if (doAll) {
            startSaveAll();
        } else {
            GM_notification({ text: 'Выберите This chat или All chats', timeout: 2000 });
        }
    }

    // ─── Settings ───────────────────────────────────────────────────────────
    function openSettings() {
        const cfg = loadCfg();
        const overlay = document.createElement('div');
        overlay.className = 'ai-overlay';
        overlay.innerHTML = `
<div class="ai-modal">
  <h3>⚙ Settings</h3>
  <label>Server URL
    <input type="text" id="ai-cfg-url" value="${cfg.serverUrl}">
  </label>
  <label>
    <input type="checkbox" id="ai-cfg-fb" ${cfg.fallback?'checked':''}> Browser download fallback
  </label>
  <div class="ai-modal-actions">
    <button id="ai-cfg-close">Close</button>
  </div>
</div>`;
        document.body.appendChild(overlay);

        overlay.querySelector('#ai-cfg-close').addEventListener('click', () => {
            const url = overlay.querySelector('#ai-cfg-url').value.trim();
            const fb = overlay.querySelector('#ai-cfg-fb').checked;
            saveCfg({ serverUrl: url || 'http://localhost:18888', fallback: fb });
            GM_notification({ text: 'Settings saved', timeout: 1500 });
            document.body.removeChild(overlay);
        });

        overlay.addEventListener('click', (e) => { if (e.target === overlay) document.body.removeChild(overlay); });
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  SAVE ALL — пакетный экспорт через popup-окно с прогрессом
    // ═════════════════════════════════════════════════════════════════════════

    function collectChatUrls() {
        const links = document.querySelectorAll(SVC.chatItemSelector);
        const seen = new Set();
        const urls = [];
        links.forEach((el) => {
            const url = SVC.getChatUrl(el);
            if (!url || seen.has(url)) return;
            const id = SVC.getChatId(url) || shortHash(url);
            if (!id) return;
            seen.add(url);
            urls.push({ url, id, title: SVC.getChatTitle(el) });
        });
        return urls;
    }

    async function startSaveAll() {
        if (GM_getValue('exportInProgress', false)) {
            GM_notification({ text: 'Экспорт уже запущен', timeout: 2000 });
            return;
        }
        const queue = collectChatUrls();
        if (queue.length === 0) {
            GM_notification({ text: 'Не найдено чатов', timeout: 3000 });
            return;
        }

        const panel = document.getElementById(PANEL_ID);
        if (panel) panel.style.display = 'none';

        GM_setValue('chatExportQueue', JSON.stringify(queue));
        GM_setValue('exportInProgress', true);
        GM_setValue('exportTotal', queue.length);
        GM_setValue('exportDone', 0);
        GM_setValue('exportLog', JSON.stringify([]));

        const popup = window.open(queue[0].url, 'ai-exporter-bg',
            'width=700,height=500,scrollbars=yes,resizable=yes,menubar=no,status=no');

        if (!popup) {
            GM_notification({ text: `🔄 Начинаю: ${queue.length} чатов (в текущей вкладке)`, timeout: 2000 });
            window.location.href = queue[0].url;
        }
    }

    async function handleQueuedExport() {
        if (!GM_getValue('exportInProgress', false)) return;

        const queue = JSON.parse(GM_getValue('chatExportQueue', '[]'));
        if (queue.length === 0) { GM_setValue('exportInProgress', false); return; }

        const current = queue[0];
        const done = GM_getValue('exportDone', 0);
        const total = GM_getValue('exportTotal', queue.length);

        const ok = await exportCurrentChat();
        if (ok) {
            const newDone = done + 1;
            GM_setValue('exportDone', newDone);
            appendExportLog(`✅ ${current.title || current.id} (${newDone}/${total})`);
        } else {
            appendExportLog(`❌ ${current.title || current.id} — ошибка`);
        }

        queue.shift();
        GM_setValue('chatExportQueue', JSON.stringify(queue));

        await sleep(1200);

        if (queue.length > 0) {
            window.location.href = queue[0].url;
        } else {
            GM_setValue('exportInProgress', false);
            appendExportLog(`🏁 Готово: ${total} чатов`);
            if (window.name === 'ai-exporter-bg') {
                setTimeout(() => { if (window.name === 'ai-exporter-bg') window.close(); }, 5000);
            } else {
                GM_notification({ text: `✅ Готово: ${total} чатов`, timeout: 5000 });
                if (SVC.listPageUrl && !SVC.isListPage()) window.location.href = SVC.listPageUrl;
            }
        }
    }

    function appendExportLog(text) {
        const log = JSON.parse(GM_getValue('exportLog', '[]'));
        log.push({ text, time: Date.now() });
        if (log.length > 100) log.splice(0, log.length - 100);
        GM_setValue('exportLog', JSON.stringify(log));
    }

    // ─── Оверлей прогресса (показывается в popup-окне) ──────────────────────
    function showExportProgress() {
        if (document.getElementById('ai-export-progress')) return;

        const el = document.createElement('div');
        el.id = 'ai-export-progress';

        const updater = setInterval(() => {
            const inProgress = GM_getValue('exportInProgress', false);
            const done = GM_getValue('exportDone', 0);
            const total = GM_getValue('exportTotal', 0);
            const log = JSON.parse(GM_getValue('exportLog', '[]'));

            const pct = total > 0 ? Math.round((done / total) * 100) : 0;
            const barLen = 20;
            const filled = Math.round((pct / 100) * barLen);
            const bar = '█'.repeat(filled) + '░'.repeat(barLen - filled);

            const logHtml = log.slice(-15).map(e =>
                `<div class="ai-log-line">${e.text}</div>`
            ).join('');

            if (!inProgress && done > 0) {
                el.innerHTML = `
<div class="ai-prog-header">✅ Экспорт завершён (${SVC.name})</div>
<div class="ai-prog-bar">${bar} ${done}/${total}</div>
<div class="ai-prog-log">${logHtml}</div>
<div class="ai-prog-close">Окно закроется через 5 секунд...</div>`;
                clearInterval(updater);
            } else if (inProgress) {
                el.innerHTML = `
<div class="ai-prog-header">🔄 Экспорт чатов — ${SVC.name}</div>
<div class="ai-prog-bar">${bar} ${done}/${total}</div>
<div class="ai-prog-log">${logHtml}</div>`;
            } else if (done === 0) {
                clearInterval(updater);
                if (window.name === 'ai-exporter-bg') window.close();
            }
        }, 500);

        document.body.appendChild(el);
    }

    // ═════════════════════════════════════════════════════════════════════════
    //  INIT
    // ═════════════════════════════════════════════════════════════════════════

    const isExportPopup = window.name === 'ai-exporter-bg';

    // ─── Popup-окно пакетного экспорта ──────────────────────────────────────
    if (isExportPopup) {
        if (GM_getValue('exportInProgress', false)) {
            setTimeout(() => {
                injectStyles();
                showExportProgress();
                handleQueuedExport();
            }, 500);
        } else {
            window.close();
        }
        return;
    }

    // ─── Основное окно ──────────────────────────────────────────────────────

    // Хоткей Ctrl+Shift+S — сохраняет текущий чат
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.shiftKey && (e.key === 'S' || e.key === 's')) {
            e.preventDefault();
            e.stopPropagation();
            if (!GM_getValue('exportInProgress', false)) exportCurrentChat();
        }
    }, true);

    // Вкладка, в которой идёт fallback-экспорт
    if (GM_getValue('exportInProgress', false)) {
        setTimeout(() => { injectStyles(); showExportProgress(); }, 500);
        handleQueuedExport();
    } else {
        GM_setValue('exportInProgress', false);

        const isChat = SVC.isChatPage();
        const isList = SVC.isListPage();
        const hasSidebarLinks = document.querySelectorAll(SVC.chatItemSelector).length > 0;
        if (isChat || isList || hasSidebarLinks) {
            setTimeout(buildPanel, isChat ? 2000 : 1000);
        }
    }

})();
