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
  currentProvider = provider;
  document.getElementById("modal").classList.remove("hidden");
  document.getElementById("modalError").classList.add("hidden");
  document.getElementById("accountUrl").value = "https://chat.deepseek.com/";
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
