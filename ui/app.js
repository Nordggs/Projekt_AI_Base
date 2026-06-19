let currentProvider = null;

function renderLog(msg) {
  const el1 = document.getElementById("log");
  const el2 = document.getElementById("sidebarLog");
  if (el1) { el1.textContent += msg + "\n"; el1.scrollTop = el1.scrollHeight; }
  if (el2) { el2.textContent += msg + "\n"; el2.scrollTop = el2.scrollHeight; }
}

function log(msg) {
  renderLog(`[UI] ${msg}`);
}

function setBridgeStatus(status) {
  document.getElementById("bridgeStatus").textContent = status;
  if (status === "ready" || status === "connected") {
    fadeSplash();
  }
}

function fadeSplash() {
  const s = document.getElementById("splash");
  if (s && !s.classList.contains("fade-out")) {
    s.classList.add("fade-out");
    setTimeout(function() { s.style.display = "none"; }, 600);
  }
}

// ── Modal ──

function setConnected() {
  document.querySelector(".deepseek .badge").textContent = "1 подключено";
  document.querySelector(".deepseek .badge").className = "badge ok";
  document.querySelector(".deepseek").classList.remove("disabled");
  document.getElementById("dsEmpty").style.display = "none";
  document.getElementById("dsAccount").classList.remove("hidden");
  document.getElementById("deepseekUrls").classList.remove("hidden");
  document.getElementById("btnSyncAll").disabled = false;
  document.getElementById("btnSyncSelected").disabled = false;
  setBridgeStatus("connected");
}

function addAccount(provider) {
  if (provider === 'gemini') {
    openCdpModal();
    return;
  }
  currentProvider = provider;
  document.getElementById("modalTitle").textContent = "Подключить аккаунт";
  document.getElementById("modalLabel").textContent = "URL DeepSeek:";
  document.getElementById("accountUrl").value = "https://chat.deepseek.com/";
  document.getElementById("accountUrl").placeholder = "https://chat.deepseek.com/";
  document.getElementById("modal").classList.remove("hidden");
  document.getElementById("modalError").classList.add("hidden");
}

function closeModal() {
  document.getElementById("modal").classList.add("hidden");
  currentProvider = null;
}

function submitAccount() {
  const url = document.getElementById("accountUrl").value.trim();
  if (!url) return;

  document.getElementById("modalError").classList.add("hidden");
  log(`add account ${currentProvider}: ${url.slice(0, 50)}...`);

  const btn = document.querySelector("#modal .primary");
  btn.disabled = true;
  btn.textContent = "Connecting...";

  if (window.pywebview) {
    window.pywebview.api.add_account(url).then(
      function(res) {
        log("account connected");
        btn.disabled = false;
        btn.textContent = "Подключить";
        setConnected();
        closeModal();
      },
      function(err) {
        log("connection failed: " + err);
        btn.disabled = false;
        btn.textContent = "Подключить";
        document.getElementById("modalError").textContent = "Ошибка: " + err;
        document.getElementById("modalError").classList.remove("hidden");
      }
    );
  }
}

// ── CDP Modal for Gemini ──

function openCdpModal() {
  document.getElementById("cdpModal").classList.remove("hidden");
  document.getElementById("cdpStatus").textContent = "";
  document.getElementById("cdpStatus").className = "cdp-status";
  document.getElementById("btnCdpCheck").disabled = false;
  document.getElementById("btnCdpCheck").textContent = "Проверить подключение";
}

function closeCdpModal() {
  document.getElementById("cdpModal").classList.add("hidden");
}

function checkGeminiCDP() {
  const status = document.getElementById("cdpStatus");
  status.textContent = "⏳ Проверка CDP...";
  status.className = "cdp-status waiting";

  if (window.pywebview) {
    window.pywebview.api.check_cdp_status().then(
      function(state) {
        if (state === "connected") {
          status.textContent = "✅ Подключено";
          status.className = "cdp-status ok";
          setGeminiConnected();
          setTimeout(closeCdpModal, 1500);
        } else if (state === "connecting") {
          status.textContent = "⏳ Подключаюсь к Gemini...";
          status.className = "cdp-status waiting";
        } else if (state === "cdp_ready") {
          status.textContent = "⏳ Подключаюсь к Gemini...";
          status.className = "cdp-status waiting";
          window.pywebview.api.start_gemini_connect();
        } else {
          status.textContent = "⏳ Chrome ещё не готов...";
          status.className = "cdp-status waiting";
        }
      },
      function(err) {
        status.textContent = "❌ Ошибка: " + (err || "CDP не отвечает");
        status.className = "cdp-status error";
      }
    );
  }
}

function launchChromeCDP() {
  window._connect_triggered = false;
  const btn = document.getElementById("btnLaunchChrome");
  const status = document.getElementById("cdpStatus");
  btn.disabled = true;
  btn.textContent = "Запуск...";
  status.textContent = "⏳ Запуск Chrome...";
  status.className = "cdp-status waiting";

  if (window.pywebview) {
    window.pywebview.api.launch_chrome().then(
      function(resp) {
        status.textContent = "✅ Chrome запущен, ждём CDP...";
        let tries = 0;
        let delay = 800;
        const timer = setInterval(function() {
          tries++;
          setTimeout(function() {
            if (document.getElementById("cdpModal").classList.contains("hidden")) {
              clearInterval(timer);
              return;
            }
            window.pywebview.api.check_cdp_status().then(
              function(state) {
                if (state === "connected") {
                  status.textContent = "✅ Подключено";
                  status.className = "cdp-status ok";
                  clearInterval(timer);
                  btn.disabled = false;
                  btn.textContent = "🚀 Запустить Chrome";
                  setGeminiConnected();
                  setTimeout(closeCdpModal, 1500);
                } else if (state === "connecting") {
                  status.textContent = "⏳ Подключаюсь к Gemini...";
                  status.className = "cdp-status waiting";
                } else if (state === "cdp_ready") {
                  status.textContent = "⏳ Подключаюсь к Gemini...";
                  status.className = "cdp-status waiting";
                  if (!window._connect_triggered) {
                    window._connect_triggered = true;
                    window.pywebview.api.start_gemini_connect();
                  }
                } else if (tries >= 10) {
                  status.textContent = "⏳ Таймаут — попробуйте Проверить подключение";
                  status.className = "cdp-status waiting";
                  clearInterval(timer);
                  btn.disabled = false;
                  btn.textContent = "🚀 Запустить Chrome";
                }
              },
              function(err) {
                if (tries >= 10) {
                  status.textContent = "❌ Ошибка CDP: " + (err || "таймаут");
                  status.className = "cdp-status error";
                  clearInterval(timer);
                  btn.disabled = false;
                  btn.textContent = "🚀 Запустить Chrome";
                }
              }
            );
          }, Math.random() * 200);
          delay = Math.min(delay * 1.2, 2000);
        }, delay);
      },
      function(err) {
        status.textContent = "❌ Ошибка: " + (err || "Chrome не найден");
        status.className = "cdp-status error";
        btn.disabled = false;
        btn.textContent = "🚀 Запустить Chrome";
      }
    );
  }
}

// ── Stop / Cancel ──

let exportStateDS = "idle"; // idle | running | cancelling
let exportStateGM = "idle";
let exportStateQW = "idle";

function cancelDeepSeek() {
  if (exportStateDS !== "running") return;
  exportStateDS = "cancelling";
  document.getElementById("btnStopDS").disabled = true;
  document.getElementById("btnStopDS").textContent = "⏹ Останавливаю...";
  Promise.resolve(
    window.pywebview?.api?.cancel_deepseek_export?.()
  ).finally(() => {
    log("DeepSeek cancel requested");
    hideStopButtonDS();
  });
}

function cancelGemini() {
  if (exportStateGM !== "running") return;
  exportStateGM = "cancelling";
  document.getElementById("btnStopGM").disabled = true;
  document.getElementById("btnStopGM").textContent = "⏹ Останавливаю...";
  Promise.resolve(
    window.pywebview?.api?.cancel_gemini_export?.()
  ).finally(() => {
    log("Gemini cancel requested");
    hideStopButtonGM();
  });
}

function showStopButtonDS() {
  exportStateDS = "running";
  const btn = document.getElementById("btnStopDS");
  btn.classList.remove("hidden"); btn.disabled = false; btn.textContent = "⏹ Остановить";
}
function hideStopButtonDS() {
  exportStateDS = "idle";
  document.getElementById("btnStopDS").classList.add("hidden");
}
function showStopButtonGM() {
  exportStateGM = "running";
  const btn = document.getElementById("btnStopGM");
  btn.classList.remove("hidden"); btn.disabled = false; btn.textContent = "⏹ Остановить";
}
function hideStopButtonGM() {
  exportStateGM = "idle";
  document.getElementById("btnStopGM").classList.add("hidden");
}

function cancelQwen() {
  if (exportStateQW !== "running") return;
  exportStateQW = "cancelling";
  document.getElementById("btnStopQW").disabled = true;
  document.getElementById("btnStopQW").textContent = "⏹ Останавливаю...";
  Promise.resolve(
    window.pywebview?.api?.cancel_qwen_export?.()
  ).finally(() => {
    log("Qwen cancel requested");
    hideStopButtonQW();
  });
}
function showStopButtonQW() {
  exportStateQW = "running";
  const btn = document.getElementById("btnStopQW");
  btn.classList.remove("hidden"); btn.disabled = false; btn.textContent = "⏹ Остановить";
}
function hideStopButtonQW() {
  exportStateQW = "idle";
  document.getElementById("btnStopQW").classList.add("hidden");
}

// ── Sync ──

function getActiveUrl() {
  const textarea = document.getElementById("dsUrls");
  const start = textarea.selectionStart;
  const lines = textarea.value.split("\n");

  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    pos += lines[i].length + 1;
    if (start <= pos) {
      const url = lines[i].trim();
      if (url) return url;
      break;
    }
  }
  // fallback: first non-empty line
  for (let i = 0; i < lines.length; i++) {
    const url = lines[i].trim();
    if (url) return url;
  }
  return "";
}

function _runSync(urls, btn) {
  btn.disabled = true;
  btn.textContent = "Exporting...";
  showStopButtonDS();

  if (window.pywebview) {
    window.pywebview.api.sync_provider(JSON.stringify(urls)).then(
      function() {
        hideStopButtonDS();
        btn.disabled = false;
        btn.textContent = btn.id === "btnSyncAll"
          ? "Синхронизировать DeepSeek"
          : "Синхронизировать выбранный чат";
        log("export complete");
        setBridgeStatus("ready");
      },
      function(err) {
        hideStopButtonDS();
        btn.disabled = false;
        btn.textContent = btn.id === "btnSyncAll"
          ? "Синхронизировать DeepSeek"
          : "Синхронизировать выбранный чат";
        log("export error: " + err);
      }
    );
  }
}

function syncAllDeepSeek() {
  const el = document.getElementById("dsUrls");
  const urls = el.value.split("\n").map(function(s) { return s.trim(); }).filter(Boolean);

  if (urls.length === 0) {
    log("sync all: auto-discovering URLs from sidebar...");
  } else {
    log("sync all: " + urls.length + " urls");
  }

  _runSync(urls, document.getElementById("btnSyncAll"));
}

function syncSelected() {
  const url = getActiveUrl();

  if (!url) {
    log("no URL selected — put cursor on a line or add at least one URL");
    return;
  }

  log("sync selected: " + url.slice(0, 50) + "...");
  _runSync([url], document.getElementById("btnSyncSelected"));
}

function syncAll() {
  const el = document.getElementById("dsUrls");
  const urls = el.value.split("\n").map(function(s) { return s.trim(); }).filter(Boolean);
  log("sync all: " + urls.length + " urls");
  if (window.pywebview) {
    window.pywebview.api.sync_all(JSON.stringify(urls));
  }
}

function reconnect() {
  log("reconnect DeepSeek");
  if (window.pywebview) {
    window.pywebview.api.reconnect().then(
      function() {
        log("reconnect complete");
      },
      function(err) {
        log("reconnect failed: " + err);
      }
    );
  }
}

// ── Sidebar ──

function copyLogContent() {
  const text = document.getElementById("log").textContent;
  navigator.clipboard.writeText(text).catch(function() {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  });
}

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

function saveChatLog() {
  const text = document.getElementById("log").textContent;
  if (window.pywebview) {
    window.pywebview.api.save_ui_snapshot(text);
  }
}

// ── Log feed handler (called from Python via evaluate_js) ──

function pushLog(msg) {
  renderLog(msg);
}

function addChatUrl(url) {
  const ta = document.getElementById("dsUrls");
  const lines = ta.value.split("\n").map(function(s) { return s.trim(); }).filter(Boolean);
  if (lines.indexOf(url) === -1) {
    lines.push(url);
    ta.value = lines.join("\n");
    log("URL: " + url.slice(0, 50) + "...");
  }
}

function autoRestore() {
  if (window.pywebview) {
    window.pywebview.api.add_account('https://chat.deepseek.com/')
      .then(function(res) {
        if (res === 'OK') {
          log('auto-restore OK');
          setConnected();
        }
      })
      .catch(function(err) { log('auto-restore: ' + err); });
  }
}

function setWaiting(seconds) {
  const badge = document.querySelector(".deepseek .badge");
  badge.textContent = "ожидание " + seconds + "s";
  badge.className = "badge waiting";
}

// ── Gemini ──

function setGeminiConnected() {
  document.querySelector(".gemini .badge").textContent = "1 подключено";
  document.querySelector(".gemini .badge").className = "badge ok";
  document.getElementById("gmEmpty").style.display = "none";
  document.getElementById("gmAccount").classList.remove("hidden");
  document.getElementById("geminiUrls").classList.remove("hidden");
  document.getElementById("btnGeminiSyncAll").disabled = false;
  document.getElementById("btnGeminiSyncSelected").disabled = false;
  setBridgeStatus("gemini connected");
}

function getActiveGeminiUrl() {
  const textarea = document.getElementById("gmUrls");
  const start = textarea.selectionStart;
  const lines = textarea.value.split("\n");
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    pos += lines[i].length + 1;
    if (start <= pos) {
      const url = lines[i].trim();
      if (url) return url;
      break;
    }
  }
  for (let i = 0; i < lines.length; i++) {
    const url = lines[i].trim();
    if (url) return url;
  }
  return "";
}

function _runGeminiSync(urls, btn) {
  btn.disabled = true;
  btn.textContent = "Exporting...";
  showStopButtonGM();
  if (window.pywebview) {
    window.pywebview.api.sync_gemini(JSON.stringify(urls))
      .then(function(resp) {
        hideStopButtonGM();
        btn.disabled = false;
        btn.textContent = btn.id === "btnGeminiSyncAll"
          ? "Синхронизировать Gemini"
          : "Синхронизировать выбранный чат";
        if (resp && resp.ok) {
          log("[GEMINI] exported: " + resp.count + " msgs → " + resp.path);
        } else {
          log("[GEMINI] export queued");
        }
        setBridgeStatus("ready");
      })
      .catch(function(err) {
        hideStopButtonGM();
        btn.disabled = false;
        btn.textContent = btn.id === "btnGeminiSyncAll"
          ? "Синхронизировать Gemini"
          : "Синхронизировать выбранный чат";
        log("[GEMINI ERROR] " + (err.message || err));
      });
  }
}

function syncGeminiAll() {
  log("Gemini sync all: auto-discovering URLs from sidebar...");
  _runGeminiSync([], document.getElementById("btnGeminiSyncAll"));
}

function syncGeminiSelected() {
  const url = getActiveGeminiUrl();
  if (!url) {
    log("no Gemini URL selected");
    return;
  }
  log("Gemini sync selected: " + url.slice(0, 50) + "...");
  _runGeminiSync([url], document.getElementById("btnGeminiSyncSelected"));
}

function reconnectGemini() {
  window._connect_triggered = false;
  log("reconnect Gemini");
  openCdpModal();
}

// ── Qwen ──

function addQwenAccount() {
  log("connecting Qwen...");
  if (window.pywebview) {
    window.pywebview.api.connect_qwen().then(
      function() {
        log("Qwen connected");
        setQwenConnected();
      },
      function(err) {
        log("Qwen connect failed: " + err);
      }
    );
  }
}

function setQwenConnected() {
  document.querySelector(".qwen .badge").textContent = "1 подключено";
  document.querySelector(".qwen .badge").className = "badge ok";
  document.getElementById("qwEmpty").style.display = "none";
  document.getElementById("qwAccount").classList.remove("hidden");
  document.getElementById("qwenUrls").classList.remove("hidden");
  document.getElementById("btnQwenSyncAll").disabled = false;
  document.getElementById("btnQwenSyncSelected").disabled = false;
  setBridgeStatus("qwen connected");
}

function getActiveQwenUrl() {
  const textarea = document.getElementById("qwUrls");
  const start = textarea.selectionStart;
  const lines = textarea.value.split("\n");
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    pos += lines[i].length + 1;
    if (start <= pos) {
      const url = lines[i].trim();
      if (url) return url;
      break;
    }
  }
  for (let i = 0; i < lines.length; i++) {
    const url = lines[i].trim();
    if (url) return url;
  }
  return "";
}

function _runQwenSync(urls, btn) {
  btn.disabled = true;
  btn.textContent = "Exporting...";
  showStopButtonQW();
  if (window.pywebview) {
    window.pywebview.api.sync_qwen(JSON.stringify(urls))
      .then(function(resp) {
        hideStopButtonQW();
        btn.disabled = false;
        btn.textContent = btn.id === "btnQwenSyncAll"
          ? "Синхронизировать Qwen"
          : "Синхронизировать выбранный чат";
        if (resp && resp.ok) {
          log("[QWEN] exported: " + resp.count + " msgs → " + resp.path);
        } else {
          log("[QWEN] export queued");
        }
        setBridgeStatus("ready");
      })
      .catch(function(err) {
        hideStopButtonQW();
        btn.disabled = false;
        btn.textContent = btn.id === "btnQwenSyncAll"
          ? "Синхронизировать Qwen"
          : "Синхронизировать выбранный чат";
        log("[QWEN ERROR] " + (err.message || err));
      });
  }
}

function syncQwenAll() {
  log("Qwen sync all: auto-discovering URLs from sidebar...");
  _runQwenSync([], document.getElementById("btnQwenSyncAll"));
}

function syncQwenSelected() {
  const url = getActiveQwenUrl();
  if (!url) {
    log("no Qwen URL selected");
    return;
  }
  log("Qwen sync selected: " + url.slice(0, 50) + "...");
  _runQwenSync([url], document.getElementById("btnQwenSyncSelected"));
}

function reconnectQwen() {
  log("reconnect Qwen");
  addQwenAccount();
}

document.addEventListener("DOMContentLoaded", function() {
  setTimeout(fadeSplash, 2500);
});
