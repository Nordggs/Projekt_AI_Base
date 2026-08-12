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
  document.getElementById("pvDeepSeek").className = "pv-dot dot-on";
  setBridgeStatus("connected");
}

function addDeepSeekAccount() {
  log("connecting DeepSeek...");
  if (window.pywebview) {
    window.pywebview.api.connect_deepseek().then(
      function() {
        log("DeepSeek connected");
        setConnected();
      },
      function(err) {
        log("DeepSeek connect failed: " + err);
      }
    );
  }
}

function addAccount(provider) {
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

// ── Stop / Cancel ──

function cancelAll() {
  document.querySelectorAll('.danger').forEach(function(b) {
    b.disabled = true; b.textContent = "⏹ Останавливаю...";
  });
  if (window.pywebview) {
    window.pywebview.api.cancel_all()
      .then(function() { resetStopButtons(); })
      .catch(function() { resetStopButtons(); });
  }
}
function resetStopButtons() {
  document.querySelectorAll('.danger').forEach(function(b) {
    b.disabled = false; b.textContent = "⏹ Остановить";
  });
}

// ── Output directory ──

function refreshOutputDir() {
  if (!window.pywebview) return;
  window.pywebview.api.get_output_dir().then(function(path) {
    const el = document.getElementById("outputDirPath");
    if (!el) return;
    el.textContent = path;
    el.title = path;
    renderLog(`[INFO] Output directory: ${path}`);
  });
}

function chooseOutputDir() {
  log("choosing output directory...");
  if (window.pywebview) {
    window.pywebview.api.choose_output_dir().then(function(path) {
      refreshOutputDir();
      log("output directory: " + path);
    }, function(err) {
      log("choose output dir failed: " + err);
    });
  }
}

function openOutputDir() {
  if (window.pywebview) {
    window.pywebview.api.open_output_dir().then(function() {}, function(err) {
      log("open output dir failed: " + err);
    });
  }
}

// ── CDP Bar ──

function refreshCdpStatus() {
  if (window.pywebview) {
    window.pywebview.api.get_cdp_status().then(function(state) {
      updateCdpBar(state);
    });
  }
}

function updateCdpBar(state) {
  var indicator = document.getElementById("cdpIndicator");
  var btnStart = document.getElementById("btnStartCdp");
  var btnStop = document.getElementById("btnStopCdp");
  var btnRestart = document.getElementById("btnRestartCdp");
  if (state === "running") {
    indicator.innerHTML = '🔗 CDP: <span class="status-dot" style="color:#0ea56a;">●</span> запущен';
    btnStart.classList.add("hidden");
    btnStop.classList.remove("hidden");
    btnRestart.classList.remove("hidden");
    btnStart.disabled = false;
    btnStart.textContent = "▶ Запустить Chrome";
  } else if (state === "starting") {
    indicator.innerHTML = '🔗 CDP: <span class="status-dot" style="color:#f0b400;">◐</span> запускается...';
    btnStart.disabled = true;
    btnStart.textContent = "Запуск...";
    btnStart.classList.remove("hidden");
    btnStop.classList.add("hidden");
    btnRestart.classList.add("hidden");
  } else {
    indicator.innerHTML = '🔗 CDP: <span class="status-dot" style="color:#4a5568;">○</span> не запущен';
    btnStart.disabled = false;
    btnStart.textContent = "▶ Запустить Chrome";
    btnStart.classList.remove("hidden");
    btnStop.classList.add("hidden");
    btnRestart.classList.add("hidden");
  }
}

function startCdp() {
  log("starting Chrome CDP...");
  refreshCdpStatus();
  if (window.pywebview) {
    window.pywebview.api.launch_chrome().then(
      function() { refreshCdpStatus(); },
      function(err) { log("CDP start failed: " + err); refreshCdpStatus(); }
    );
  }
}

function stopCdp() {
  log("stopping Chrome...");
  if (window.pywebview) {
    window.pywebview.api.close_chrome().then(
      function() { refreshCdpStatus(); },
      function(err) { log("CDP stop failed: " + err); }
    );
  }
}

function restartCdp() {
  log("restarting Chrome CDP...");
  stopCdp();
  setTimeout(startCdp, 2000);
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
  if (window.pywebview) {
    window.pywebview.api.sync_provider(JSON.stringify(urls)).then(
      function() {
        btn.disabled = false;
        btn.textContent = btn.id === "btnSyncAll"
          ? "Синхронизировать DeepSeek"
          : "Синхронизировать выбранный чат";
        log("export complete");
        setBridgeStatus("ready");
      },
      function(err) {
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
  log("sync all providers...");
  if (window.pywebview) {
    window.pywebview.api.sync_all();
  }
}

function setSyncRunning(running) {
  var btn = document.querySelector('.sync-all');
  if (btn) {
    btn.disabled = running;
    btn.textContent = running ? "Синхронизация..." : "Синхронизировать всё";
  }
}

function setProviderSync(provider, status) {
  var map = { 'gemini':'Gemini', 'qwen':'Qwen', 'chatgpt':'ChatGPT', 'claude':'Claude', 'deepseek':'DeepSeek' };
  var dot = document.getElementById('pv' + map[provider]);
  if (!dot) return;
  var cls = { 'idle':'dot-off', 'running':'dot-syncing', 'done':'dot-on', 'failed':'dot-failed' };
  dot.className = 'pv-dot ' + (cls[status] || 'dot-off');
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

function addGeminiAccount() {
  log("connecting Gemini...");
  if (window.pywebview) {
    window.pywebview.api.connect_gemini().then(
      function() {
        log("Gemini connected");
        setGeminiConnected();
      },
      function(err) {
        log("Gemini connect failed: " + err);
      }
    );
  }
}

function setGeminiConnected() {
  document.querySelector(".gemini .badge").textContent = "1 подключено";
  document.querySelector(".gemini .badge").className = "badge ok";
  document.getElementById("gmEmpty").style.display = "none";
  document.getElementById("gmAccount").classList.remove("hidden");
  document.getElementById("geminiUrls").classList.remove("hidden");
  document.getElementById("btnGeminiSyncAll").disabled = false;
  document.getElementById("btnGeminiSyncSelected").disabled = false;
  document.getElementById("pvGemini").className = "pv-dot dot-on";
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
  if (window.pywebview) {
    window.pywebview.api.sync_gemini(JSON.stringify(urls))
      .then(function(resp) {
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
  log("reconnect Gemini");
  addGeminiAccount();
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
  document.getElementById("pvQwen").className = "pv-dot dot-on";
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
      let url = lines[i].trim();
      if (url) {
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        return url;
      }
      break;
    }
  }
  for (let i = 0; i < lines.length; i++) {
    let url = lines[i].trim();
    if (url) {
      if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
      return url;
    }
  }
  return "";
}

function _runQwenSync(urls, btn) {
  btn.textContent = "Exporting...";
  if (window.pywebview) {
    window.pywebview.api.sync_qwen(JSON.stringify(urls))
      .then(function(resp) {
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

// ── ChatGPT ──

function addChatGPTAccount() {
  log("connecting ChatGPT...");
  if (window.pywebview) {
    window.pywebview.api.connect_chatgpt().then(
      function() {
        log("ChatGPT connected");
        setChatGPTConnected();
      },
      function(err) {
        log("ChatGPT connect failed: " + err);
      }
    );
  }
}

function setChatGPTConnected() {
  document.querySelector(".chatgpt .badge").textContent = "1 подключено";
  document.querySelector(".chatgpt .badge").className = "badge ok";
  document.getElementById("cgEmpty").style.display = "none";
  document.getElementById("cgAccount").classList.remove("hidden");
  document.getElementById("chatgptUrls").classList.remove("hidden");
  document.getElementById("btnChatGPTSyncAll").disabled = false;
  document.getElementById("btnChatGPTSyncSelected").disabled = false;
  document.getElementById("pvChatGPT").className = "pv-dot dot-on";
  setBridgeStatus("chatgpt connected");
}

function getActiveChatGPTUrl() {
  const textarea = document.getElementById("cgUrls");
  const start = textarea.selectionStart;
  const lines = textarea.value.split("\n");
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    pos += lines[i].length + 1;
    if (start <= pos) {
      let url = lines[i].trim();
      if (url) {
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        return url;
      }
      break;
    }
  }
  for (let i = 0; i < lines.length; i++) {
    let url = lines[i].trim();
    if (url) {
      if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
      return url;
    }
  }
  return "";
}

function _runChatGPTSync(urls, btn) {
  btn.textContent = "Exporting...";
  if (window.pywebview) {
    window.pywebview.api.sync_chatgpt(JSON.stringify(urls))
      .then(function(resp) {
        btn.textContent = btn.id === "btnChatGPTSyncAll"
          ? "Синхронизировать ChatGPT"
          : "Синхронизировать выбранный чат";
        if (resp && resp.ok) {
          log("[CHATGPT] exported: " + resp.count + " msgs → " + resp.path);
        } else {
          log("[CHATGPT] export queued");
        }
        setBridgeStatus("ready");
      })
      .catch(function(err) {
        btn.textContent = btn.id === "btnChatGPTSyncAll"
          ? "Синхронизировать ChatGPT"
          : "Синхронизировать выбранный чат";
        log("[CHATGPT ERROR] " + (err.message || err));
      });
  }
}

function syncChatGPTAll() {
  log("ChatGPT sync all: auto-discovering URLs from sidebar...");
  _runChatGPTSync([], document.getElementById("btnChatGPTSyncAll"));
}

function syncChatGPTSelected() {
  const url = getActiveChatGPTUrl();
  if (!url) {
    log("no ChatGPT URL selected");
    return;
  }
  log("ChatGPT sync selected: " + url.slice(0, 50) + "...");
  _runChatGPTSync([url], document.getElementById("btnChatGPTSyncSelected"));
}

function reconnectChatGPT() {
  log("reconnect ChatGPT");
  addChatGPTAccount();
}

// ── Claude ──

function addClaudeAccount() {
  log("connecting Claude...");
  if (window.pywebview) {
    window.pywebview.api.connect_claude().then(
      function() {
        log("Claude connected");
        setClaudeConnected();
      },
      function(err) {
        log("Claude connect failed: " + err);
      }
    );
  }
}

function setClaudeConnected() {
  document.querySelector(".claude .badge").textContent = "1 подключено";
  document.querySelector(".claude .badge").className = "badge ok";
  document.getElementById("clEmpty").style.display = "none";
  document.getElementById("clAccount").classList.remove("hidden");
  document.getElementById("claudeUrls").classList.remove("hidden");
  document.getElementById("btnClaudeSyncAll").disabled = false;
  document.getElementById("btnClaudeSyncSelected").disabled = false;
  document.getElementById("pvClaude").className = "pv-dot dot-on";
  setBridgeStatus("claude connected");
}

function getActiveClaudeUrl() {
  const textarea = document.getElementById("clUrls");
  const start = textarea.selectionStart;
  const lines = textarea.value.split("\n");
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    pos += lines[i].length + 1;
    if (start <= pos) {
      let url = lines[i].trim();
      if (url) {
        if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
        return url;
      }
      break;
    }
  }
  for (let i = 0; i < lines.length; i++) {
    let url = lines[i].trim();
    if (url) {
      if (!url.startsWith("http://") && !url.startsWith("https://")) url = "https://" + url;
      return url;
    }
  }
  return "";
}

function _runClaudeSync(urls, btn) {
  btn.textContent = "Exporting...";
  if (window.pywebview) {
    window.pywebview.api.sync_claude(JSON.stringify(urls))
      .then(function(resp) {
        btn.textContent = btn.id === "btnClaudeSyncAll"
          ? "Синхронизировать Claude"
          : "Синхронизировать выбранный чат";
        if (resp && resp.ok) {
          log("[CLAUDE] exported: " + resp.count + " msgs → " + resp.path);
        } else {
          log("[CLAUDE] export queued");
        }
        setBridgeStatus("ready");
      })
      .catch(function(err) {
        btn.textContent = btn.id === "btnClaudeSyncAll"
          ? "Синхронизировать Claude"
          : "Синхронизировать выбранный чат";
        log("[CLAUDE ERROR] " + (err.message || err));
      });
  }
}

function syncClaudeAll() {
  log("Claude sync all: auto-discovering URLs from sidebar...");
  _runClaudeSync([], document.getElementById("btnClaudeSyncAll"));
}

function syncClaudeSelected() {
  const url = getActiveClaudeUrl();
  if (!url) {
    log("no Claude URL selected");
    return;
  }
  log("Claude sync selected: " + url.slice(0, 50) + "...");
  _runClaudeSync([url], document.getElementById("btnClaudeSyncSelected"));
}

function reconnectClaude() {
  log("reconnect Claude");
  addClaudeAccount();
}

document.addEventListener("DOMContentLoaded", function() {
  setTimeout(fadeSplash, 2500);
  setTimeout(refreshCdpStatus, 500);
  setTimeout(refreshOutputDir, 300);

  // Qwen DnD fix for pywebview — explicit event handlers for textarea
  const qw = document.getElementById("qwUrls");
  if (qw) {
    qw.addEventListener("dragover", function(e) { e.preventDefault(); });
    qw.addEventListener("drop", function(e) {
      e.preventDefault();
      const text = (e.dataTransfer.getData("text/plain") || e.dataTransfer.getData("text/uri-list") || "").trim();
      if (text) {
        const start = this.selectionStart;
        const end = this.selectionEnd;
        this.value = this.value.substring(0, start) + text + this.value.substring(end);
        this.selectionStart = this.selectionEnd = start + text.length;
        this.dispatchEvent(new Event("input", { bubbles: true }));
        this.dispatchEvent(new Event("change", { bubbles: true }));
        return;
      }
      // Fallback: file drop (blob or .txt)
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        const reader = new FileReader();
        reader.onload = function(ev) {
          const content = ev.target.result;
          const start = qw.selectionStart;
          const end = qw.selectionEnd;
          qw.value = qw.value.substring(0, start) + content + qw.value.substring(end);
          qw.selectionStart = qw.selectionEnd = start + content.length;
          qw.dispatchEvent(new Event("input", { bubbles: true }));
          qw.dispatchEvent(new Event("change", { bubbles: true }));
        };
        reader.readAsText(files[0]);
      }
    });
  }
});
