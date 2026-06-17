#!/usr/bin/env python3
"""AI Chat Exporter — десктопное приложение"""
import sys, os, json, re, hashlib, urllib.request
from pathlib import Path
from datetime import datetime

from PyQt5.QtCore import Qt, QUrl, QTimer, QProcess, QProcessEnvironment, pyqtSignal, QObject
from PyQt5.QtGui import QIcon, QFont, QPixmap, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QPushButton, QTextEdit, QProgressBar, QSizePolicy, QLabel,
    QSystemTrayIcon, QMenu, QTabWidget, QComboBox, QSlider,
    QFileDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QMessageBox
)
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile, QWebEngineScript

# ─── Пути ────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent
OBSIDIAN_DIR = Path("D:/Main/OpenCode/Obsidian/AI-Chats")
PIPELINE_SCRIPT = PROJECT_DIR / "pipeline.py"
COOKIE_DIR   = str(PROJECT_DIR / ".cookies")
ICON_BIG     = str(PROJECT_DIR / "AI Chat Exporter.png")
ICON_SMALL   = str(PROJECT_DIR / "AI Chat Exporter sm.png")
SERVER_URL   = "http://localhost:18888"


THEMES = {
    'dark': {
        'bg': '#11131f', 'widget_bg': '#1f2335',
        'fg': '#f3f4f6', 'fg_dim': '#8a94a6', 'fg_muted': '#4b526d',
        'accent': '#2563eb', 'accent_hover': '#3b82f6',
        'danger': '#2e151b', 'danger_hover': '#4c1d24',
        'border': '#1f2335', 'border_light': '#262b45',
        'btn_bg': '#171a29', 'btn_hover': '#1f2336',
        'btn_fg': '#f3f4f6', 'btn_fg_hover': '#ffffff', 'disabled': '#4b526d',
        'log_bg': '#07080d', 'log_fg': '#cbd5e1', 'log_ts': '#4b526d',
        'warn': '#fbbf24', 'err': '#f43f5e', 'ok': '#22c55e', 'job': '#3b82f6',
    },
    'light': {
        'bg': '#eff1f5', 'widget_bg': '#e6e9ef',
        'fg': '#4c4f69', 'fg_dim': '#5c5f77', 'fg_muted': '#9ca0b0',
        'accent': '#1e66f5', 'accent_hover': '#04a5e5',
        'danger': '#d20f39', 'danger_hover': '#e64553',
        'border': '#ccd0da', 'border_light': '#bcc0cc',
        'btn_bg': '#e6e9ef', 'btn_hover': '#ccd0da',
        'btn_fg': '#4c4f69', 'btn_fg_hover': '#dc8a78', 'disabled': '#bcc0cc',
        'log_bg': '#dce0e8', 'log_fg': '#5c5f77', 'log_ts': '#9ca0b0',
        'warn': '#df8e1d', 'err': '#d20f39', 'ok': '#40a02b', 'job': '#1e66f5',
    },
}


# ─── JS экстракции (общий для ExportWorker и кнопки «Текущий чат») ──────────

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
if(u){var uc=u.querySelector('.fbb737a4,.ds-message-content,[class*=\"message-content\"],[data-testid=\"user_message\"]')||u;var ut=uc.textContent.trim().substring(0,40);if(ut){r.title=ut;r.titleSource='first-msg';}}}
if(!r.title){r.title='chat';r.titleSource='default';}
// Extract messages
document.querySelectorAll('div.ds-message').forEach(function(el){
if(!el.textContent.trim())return;
var c='',role='assistant';
var a=el.querySelector('.ds-assistant-message-main-content');
if(a){var p=[];var k=el.querySelector('.ds-think-content');
if(k&&k.textContent.trim())p.push('> [!thought] Мысли модели\\n> '+htmlToMarkdown(k.innerHTML).replace(/\\n/g,'\\n> '));
p.push(htmlToMarkdown(a.innerHTML));c=p.join('\\n\\n');}
else{role='user';var u=el.querySelector('.fbb737a4,.ds-message-content,[class*=\"message-content\"],[data-testid=\"user_message\"]');
if(!u){var kids=el.querySelectorAll(':scope>div');for(var j=0;j<kids.length;j++){var t=kids[j].textContent.trim();if(t&&!kids[j].querySelector('.ds-assistant-message-main-content,.ds-think-content')){u=kids[j];break;}}}
c=u?u.textContent.trim():el.textContent.trim();}
if(c)r.messages.push({role:role,content:c});});
var id=(location.pathname.match(/\\/a\\/chat\\/s\\/([a-f0-9-]+)/)||[])[1]||'';
return JSON.stringify({title:r.title,titleSource:r.titleSource,chatId:id,messages:r.messages,sourceUrl:location.href});
})()"""


COPILOT_DARK_CSS = """:root{--background:#0b0d14;--bg-primary:#0b0d14;--bg-chat:#0b0d14;--main-bg:#0b0d14;--bg-secondary:#11131f;--sidebar-background:#11131f;--bg-surface:#171a29;--bg-input:#171a29;--popover:#171a29;--ds-bg-theme-neutral:#0b0d14;--ds-bg-surface-neutral:#171a29;--ds-bg-page-neutral:#0b0d14;--ds-bg-nav-neutral:#11131f;--ds-background-default:#0b0d14;--ds-background-primary:#0b0d14;--ds-background-secondary:#11131f}html,body,main,section,[role="main"]{background-color:#0b0d14!important}aside,nav,[class*="sidebar"],[role="navigation"]{background-color:#11131f!important}pre,code,[class*="code-block"],.md-code-block{background-color:#171a29!important}textarea,input,[class*="input-box"],[class*="chat-input"]{background-color:#171a29!important;border-color:#1f2335!important}*,div{border-color:#1f2335!important}"""


# ═══════════════════════════════════════════════════════════════════════════════
#  LOG TERMINAL
# ═══════════════════════════════════════════════════════════════════════════════

class LogTerminal(QTextEdit):
    COLORS = {'info': '#888', 'ok': '#4caf50', 'warn': '#ff9800', 'err': '#f44336', 'job': '#2196f3'}

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self._log_fs = 10
        self._user_scaled = False
        self.setFont(QFont("Fira Code", self._log_fs))
        self._ts_color = '#555'
        self.setStyleSheet("QTextEdit{background:#07080d;color:#ccc;padding:10px;font-family:'Fira Code',Consolas,monospace}")
        self.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Wheel and ev.modifiers() & Qt.ControlModifier:
            delta = ev.angleDelta().y()
            if delta > 0:
                self._log_fs = min(24, self._log_fs + 1)
            elif delta < 0:
                self._log_fs = max(6, self._log_fs - 1)
            self._user_scaled = True
            self.setFont(QFont("Fira Code", self._log_fs))
            return True
        return super().eventFilter(obj, ev)

    def retheme(self, theme):
        self._ts_color = theme.get('fg_muted', '#555')
        self.COLORS['info'] = theme.get('fg_dim', '#888')
        self.COLORS['ok'] = theme['ok']
        self.COLORS['warn'] = theme['warn']
        self.COLORS['err'] = theme['err']
        self.COLORS['job'] = theme['job']
        self.setStyleSheet(
            f"QTextEdit{{background:{theme['log_bg']};color:{theme['log_fg']};padding:10px;font-family:'Fira Code',Consolas,monospace}}")

    MAX_LINES = 500

    def log(self, msg, level='info'):
        c = self.COLORS.get(level, '#888')
        ts = datetime.now().strftime('%H:%M:%S')
        h = f'<span style="color:{self._ts_color}">[{ts}]</span> <span style="color:{c}">{msg}</span><br>'
        self.insertHtml(h)
        while self.document().blockCount() > self.MAX_LINES:
            cursor = self.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.select(cursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())


# ═══════════════════════════════════════════════════════════════════════════════
#  JS — обнаружение и установка темы DeepSeek
# ═══════════════════════════════════════════════════════════════════════════════

DETECT_THEME_JS = """(function(){
try{
  var raw=localStorage.getItem('__appKit_@deepseek/chat_themePreference');
  if(raw){var p=JSON.parse(raw);if(p&&p.value)return p.value;}
}catch(e){}
return 'unknown';
})()"""

SET_THEME_JS = """(function(){
var t='__THEME__';
try{
  localStorage.setItem('__appKit_@deepseek/chat_themePreference',JSON.stringify({value:t,__version:'0'}));
  var toggles=document.querySelectorAll('[class*=\"theme-toggle\"],[class*=\"themeSwitch\"],button[aria-label*=\"theme\" i]');
  if(toggles.length>0){toggles[0].click();return 'clicked';}
  return 'stored';
}catch(e){return 'fail';}
})()"""


# ═══════════════════════════════════════════════════════════════════════════════
#  DEFAULT PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

DEFAULT_PROMPT = """Ты анализируешь переписку пользователя с AI-ассистентом.

Выполни два действия:
1. Напиши краткое саммари (1-2 предложения) — о чём чат, какие вопросы задавал пользователь, что обсуждали. Будь конкретным.
2. Предложи 3-5 тегов (ключевых слов), отражающих тематику и контекст разговора.

Ответ формулируй на русском языке. Названия технологий, библиотек, языков программирования и продуктов оставляй в оригинальном написании (английском).

Формат ответа строго:

SUMMARY:
<саммари>

TAGS:
тег1, тег2, тег3

Переписка:
---
{content}
---"""


# ═══════════════════════════════════════════════════════════════════════════════
#  DIALOGS — настройки AI, тест промпта, предпросмотр
# ═══════════════════════════════════════════════════════════════════════════════

class PreviewDialog(QDialog):
    def __init__(self, prompt_text, parent=None):
        super().__init__(parent)
        self.setWindowTitle("👁 Предпросмотр итогового промпта")
        self.resize(700, 500)
        lo = QVBoxLayout(self)

        self.edit = QTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setFont(QFont("Fira Code", 10))
        self.edit.setPlainText(prompt_text)
        lo.addWidget(self.edit, 1)

        bx = QHBoxLayout()
        btn_copy = QPushButton("📋 Копировать")
        btn_copy.clicked.connect(self._copy)
        bx.addWidget(btn_copy)
        bx.addStretch()
        btn_close = QPushButton("✕ Закрыть")
        btn_close.clicked.connect(self.accept)
        bx.addWidget(btn_close)
        lo.addLayout(bx)

    def _copy(self):
        QApplication.clipboard().setText(self.edit.toPlainText())


class TestPromptDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧪 Тест промпта")
        self.resize(700, 600)
        lo = QVBoxLayout(self)

        lo.addWidget(QLabel("Тестовый контент:"))
        self.input_edit = QTextEdit()
        self.input_edit.setFont(QFont("Fira Code", 10))
        self.input_edit.setPlaceholderText("Вставьте фрагмент чата для тестирования промпта…")
        lo.addWidget(self.input_edit, 1)

        bx = QHBoxLayout()
        self.btn_run = QPushButton("▶ Запустить")
        self.btn_run.clicked.connect(self._run)
        bx.addWidget(self.btn_run)
        bx.addStretch()
        self.btn_close = QPushButton("✕ Закрыть")
        self.btn_close.clicked.connect(self.accept)
        bx.addWidget(self.btn_close)
        lo.addLayout(bx)

        lo.addWidget(QLabel("Результат:"))
        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setFont(QFont("Fira Code", 10))
        lo.addWidget(self.result_edit, 1)

    def _run(self):
        content = self.input_edit.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "Нет контента", "Введите тестовый контент")
            return
        prompt = self.parent().prompt_edit.toPlainText().strip() if hasattr(self.parent(), 'prompt_edit') else DEFAULT_PROMPT
        self.result_edit.setPlainText("⏳ Отправка…")
        QApplication.processEvents()
        try:
            payload = json.dumps({"prompt": prompt, "content": content}).encode()
            req = urllib.request.Request(f"{SERVER_URL}/prompt/test", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            resp = urllib.request.urlopen(req, timeout=180)
            data = json.loads(resp.read())
            result = data.get("result", "Нет ответа")
            self.result_edit.setPlainText(result)
        except Exception as e:
            self.result_edit.setPlainText(f"Ошибка: {e}")


class PromptDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._main = parent
        self.setWindowTitle("⚙ Настройки AI-обработки")
        self.resize(700, 550)
        lo = QVBoxLayout(self)

        lo.addWidget(QLabel("Промпт (шаблонные переменные: {content}, {title}, {filename}, {chat_id}):"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setFont(QFont("Fira Code", 10))
        self.prompt_edit.setPlainText(DEFAULT_PROMPT)
        lo.addWidget(self.prompt_edit, 1)

        tx = QHBoxLayout()
        tx.addWidget(QLabel("🌡 Температура:"))
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(0.2)
        self.temp_spin.setDecimals(1)
        tx.addWidget(self.temp_spin)
        tx.addStretch()
        lo.addLayout(tx)

        # Кнопки
        bx = QHBoxLayout()
        btn_save = QPushButton("💾 Сохранить")
        btn_save.clicked.connect(self._save)
        bx.addWidget(btn_save)

        btn_preview = QPushButton("👁 Предпросмотр")
        btn_preview.clicked.connect(self._preview)
        bx.addWidget(btn_preview)

        btn_test = QPushButton("🧪 Тест")
        btn_test.clicked.connect(self._test)
        bx.addWidget(btn_test)

        btn_reset = QPushButton("↺ Сбросить")
        btn_reset.clicked.connect(self._reset)
        bx.addWidget(btn_reset)

        btn_close = QPushButton("✕ Закрыть")
        btn_close.clicked.connect(self.accept)
        bx.addWidget(btn_close)

        lo.addLayout(bx)

        # Загрузка текущих настроек
        QTimer.singleShot(0, self._load_settings)

    def _load_settings(self):
        try:
            req = urllib.request.Request(f"{SERVER_URL}/settings", method="GET")
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            prompt = data.get("ollama", {}).get("prompt", "")
            temp = data.get("ollama", {}).get("options", {}).get("temperature", 0.2)
            if prompt:
                self.prompt_edit.setPlainText(prompt)
            self.temp_spin.setValue(temp)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка загрузки",
                f"Не удалось загрузить настройки с сервера:\n{e}\n\nРедактор открыт с локальными значениями.")

    def _resolve_prompt(self):
        return self.prompt_edit.toPlainText().strip() or DEFAULT_PROMPT

    def _resolve_settings_patch(self):
        return {
            "ollama": {
                "prompt": self._resolve_prompt(),
                "options": {
                    "temperature": self.temp_spin.value(),
                }
            }
        }

    def _save(self):
        patch = self._resolve_settings_patch()
        try:
            payload = json.dumps(patch).encode()
            req = urllib.request.Request(f"{SERVER_URL}/settings", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
            temp = self.temp_spin.value()
            self._main.log.log(f"📝 Настройки AI сохранены (🌡 {temp})", "ok")
            QMessageBox.information(self, "Сохранено", "Настройки AI успешно сохранены.")
        except Exception as e:
            self._main.log.log(f"Ошибка сохранения настроек: {e}", "err")
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить настройки:\n{e}")

    def _preview(self):
        prompt = self._resolve_prompt()
        subs = {
            "{content}": "Тестовое содержимое для проверки промпта…",
            "{filename}": "test.md",
            "{title}": "Тестовый чат",
            "{chat_id}": "preview",
        }
        for k, v in subs.items():
            prompt = prompt.replace(k, v)
        dlg = PreviewDialog(prompt, self)
        dlg.exec_()

    def _test(self):
        dlg = TestPromptDialog(self)
        dlg.exec_()

    def _reset(self):
        ret = QMessageBox.question(self, "Сброс промпта",
            "Сбросить промпт к заводскому значению?\n\nВсе изменения будут потеряны.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret == QMessageBox.Yes:
            self.prompt_edit.setPlainText(DEFAULT_PROMPT)


# ═══════════════════════════════════════════════════════════════════════════════
#  SCROLL EXTRACTOR  (пошаговая экстракция true-windowed virtual scroller)
# ═══════════════════════════════════════════════════════════════════════════════

class ScrollExtractor:
    """Пошаговая экстракция для true-windowed virtual scroller DeepSeek.

    scrollTop=0 → EXTRACT_JS → scroll down → EXTRACT_JS → ... → atBottom → merge → callback(data)
    """

    CHUNK_DELAY = 800
    MAX_STEPS = 200
    SCROLL_STEP_FACTOR = 0.8
    MAX_NO_GROWTH = 5
    MAX_CATCHUP = 10

    def __init__(self, page, log_func):
        self._page = page
        self._log = log_func
        self._callback = None
        self._cancelled = False
        self._reset()

    def _reset(self):
        self._merged = []
        self._seen = set()
        self._title = ''
        self._chat_id = ''
        self._source_url = ''
        self._step_size = 0
        self._step = 0
        self._no_growth = 0
        self._catchup_attempts = 0
        self._after_catchup = False
        self._pending_catchup_log = False

    def cancel(self):
        self._cancelled = True
        self._callback = None

    def start(self, callback):
        if self._callback is not None:
            return
        self._callback = callback
        self._cancelled = False
        self._log("⬆ Экстракция...", 'info')
        js = """(function(){
var c=document.querySelector('.ds-virtual-list._2bd7b35');
if(!c)return JSON.stringify({error:'no_container'});
c.scrollTop=0;
return JSON.stringify({ch:c.clientHeight,sh:c.scrollHeight,su:location.href});
})()"""
        self._page.runJavaScript(js, self._on_init)

    def _on_init(self, raw):
        if self._cancelled: return
        try: d = json.loads(raw)
        except: d = {}
        if d.get('error'):
            self._callback({title:'',chatId:'',sourceUrl:'',messages:[]})
            return
        ch = d.get('ch', 500)
        self._source_url = d.get('su', '')
        self._step_size = int(ch * self.SCROLL_STEP_FACTOR)
        self._log(f"  Размер окна={ch}px шаг={self._step_size}px", 'info')
        QTimer.singleShot(400, self._do_step)

    def _do_step(self):
        if self._cancelled: return
        self._page.runJavaScript(EXTRACT_JS, self._on_chunk)

    def _on_chunk(self, raw):
        if self._cancelled: return
        QApplication.processEvents()
        try: data = json.loads(raw)
        except: data = {}
        if not data or not data.get('messages'):
            QTimer.singleShot(600, self._do_step)
            return

        # Сохраняем title/chatId из первого чанка
        if not self._title:
            self._title = data.get('title', '')
        if not self._chat_id:
            self._chat_id = data.get('chatId', '')

        dom_count = len(data['messages'])
        new_count = 0
        for msg in data['messages']:
            key = hashlib.sha1(f"{msg.get('role','')}|{msg.get('content','')}".encode('utf-8')).hexdigest()
            if key not in self._seen:
                self._seen.add(key)
                self._merged.append(msg)
                new_count += 1

        self._step += 1
        if new_count == 0:
            self._no_growth += 1
        else:
            self._no_growth = 0

        self._log(f"  Чанк {self._step}: DOM={dom_count} +{new_count} новых = {len(self._merged)} всего", 'info')

        if self._pending_catchup_log:
            self._log(
                f"⬆ Catchup дал +{new_count} сообщений ({len(self._merged)} всего)",
                'info')
            self._pending_catchup_log = False

        if self._step >= self.MAX_STEPS:
            self._finish()
            return

        if self._no_growth >= self.MAX_NO_GROWTH:
            if self._catchup_attempts >= self.MAX_CATCHUP:
                self._finish()
                return
            self._catchup_attempts += 1
            js = """(function(){
var c=document.querySelector('.ds-virtual-list._2bd7b35');
if(!c)return JSON.stringify({error:'no_container'});
c.scrollTop=c.scrollHeight;
return JSON.stringify({st:c.scrollTop,sh:c.scrollHeight});
})()"""
            self._page.runJavaScript(js, self._on_catchup)
            return

        # Проверяем позицию
        js = """(function(){
var c=document.querySelector('.ds-virtual-list._2bd7b35');
if(!c)return JSON.stringify({error:'no_container'});
return JSON.stringify({st:c.scrollTop,sh:c.scrollHeight,ch:c.clientHeight});
})()"""
        self._page.runJavaScript(js, self._on_position)

    def _on_position(self, raw):
        if self._cancelled: return
        QApplication.processEvents()
        try: d = json.loads(raw)
        except: d = {}
        if d.get('error'):
            self._finish()
            return
        st = d.get('st', 0)
        sh = d.get('sh', 0)
        ch = d.get('ch', 500)
        at_bottom = st >= sh - ch - 2

        if self._after_catchup:
            at_bottom = False
            self._after_catchup = False

        if at_bottom:
            self._log("  Достигнут низ, завершаю...", 'info')
            self._finish()
            return

        next_top = min(st + self._step_size, sh - ch)
        js = """(function(){
var c=document.querySelector('.ds-virtual-list._2bd7b35');
if(!c)return;
c.scrollTop=""" + str(next_top) + """;
})()"""
        self._page.runJavaScript(js)
        QTimer.singleShot(self.CHUNK_DELAY, self._do_step)

    def _on_catchup(self, raw):
        if self._cancelled: return
        try: d = json.loads(raw)
        except: d = {}
        if d.get('error'):
            self._finish(); return
        self._log(
            f"⚠ Нет роста {self.MAX_NO_GROWTH} чанков подряд", 'warn')
        self._log(
            f"⚠ Catchup #{self._catchup_attempts}/{self.MAX_CATCHUP}", 'warn')
        self._log(
            f"⬇ Hard scroll: top={d.get('st',0)} height={d.get('sh',0)}",
            'info')
        self._no_growth = 0
        self._after_catchup = True
        self._pending_catchup_log = True
        QTimer.singleShot(1500, self._do_step)

    def _finish(self):
        cb = self._callback
        self._callback = None
        self._log(f"✅ Экстракция завершена: {len(self._merged)} сообщений, {self._step} чанков", 'ok')
        if cb:
            cb({
                'title': self._title,
                'chatId': self._chat_id,
                'sourceUrl': self._source_url,
                'messages': self._merged,
            })


# ═══════════════════════════════════════════════════════════════════════════════
#  EXPORT WORKER  (последовательный сбор чатов)
# ═══════════════════════════════════════════════════════════════════════════════

class ExportWorker(QObject):
    log_sig      = pyqtSignal(str, str)
    progress_sig = pyqtSignal(int, int, str)
    done_sig     = pyqtSignal()

    def __init__(self, page, urls):
        super().__init__()
        self.page = page
        self.urls = urls
        self.idx = 0
        self.hold = False
        self._extractor = None
        self._load_timer = None

    def start(self):
        self.hold = False; self.idx = 0
        self.log_sig.emit(f"Начинаю сбор {len(self.urls)} чатов", 'info')
        self._next()

    def cancel(self):
        self.hold = True
        if self._extractor:
            self._extractor.cancel()
            self._extractor = None
        if self._load_timer:
            self._load_timer.stop()
        self.log_sig.emit("Экспорт остановлен", 'warn')
        self.done_sig.emit()

    def _next(self):
        if self.hold: return
        if self.idx >= len(self.urls):
            self.log_sig.emit(f"Готово: {len(self.urls)} чатов", 'ok')
            self.done_sig.emit(); return
        info = self.urls[self.idx]
        self.progress_sig.emit(self.idx, len(self.urls), info.get('title','?'))
        self.log_sig.emit(f"→ {info.get('title','?')}", 'job')
        self.page.loadFinished.connect(self._on_loaded)
        if self._load_timer:
            self._load_timer.stop()
        self._load_timer = QTimer()
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._on_load_timeout)
        self._load_timer.start(20000)
        self.page.load(QUrl(info['url']))

    def _on_load_timeout(self):
        try: self.page.loadFinished.disconnect(self._on_loaded)
        except TypeError: pass
        self.log_sig.emit(f"Таймаут загрузки: {self.urls[self.idx].get('title','?')}", 'err')
        self.idx += 1; QTimer.singleShot(500, self._next)

    def _on_loaded(self, ok):
        try: self.page.loadFinished.disconnect(self._on_loaded)
        except TypeError: pass
        if self._load_timer:
            self._load_timer.stop()
            self._load_timer = None
        if self.hold: return
        if not ok:
            self.log_sig.emit(f"Ошибка загрузки: {self.urls[self.idx].get('title','?')}", 'err')
            self.idx += 1; QTimer.singleShot(500, self._next); return
        self._retry_count = 0
        self._extractor = ScrollExtractor(self.page, lambda m, l: self.log_sig.emit(m, l))
        self._extractor.start(self._on_extracted)

    def _on_extracted(self, data):
        if self.hold: return
        if not data or not data.get('messages'):
            self._retry_count += 1
            if self._retry_count <= 3:
                self.log_sig.emit(f"Нет сообщений ({self.urls[self.idx].get('title','?')}), попытка {self._retry_count}/3...", 'warn')
                self._extractor = ScrollExtractor(self.page, lambda m, l: self.log_sig.emit(m, l))
                self._extractor.start(self._on_extracted)
                return
            else:
                self._retry_count = 0
                self.log_sig.emit(f"Нет сообщений: {self.urls[self.idx].get('title','?')}", 'warn')
                self.idx += 1; QTimer.singleShot(500, self._next)
                return
        self._retry_count = 0
        self.log_sig.emit(f"  🏷 '{data.get('title','')}' | 📨 {len(data['messages'])} сообщений", 'info')
        self._save(data)
        self.idx += 1; QTimer.singleShot(1200, self._next)
        QApplication.processEvents()

    def _save(self, data):
        title = data.get('title','untitled')
        cid   = data.get('chatId','') or str(hash(data.get('sourceUrl','')))[-6:]
        safe  = re.sub(r'[<>:"/\\|?*]', '', title)
        safe  = re.sub(r'\s+', '-', safe).strip('-')[:30].lower() or 'untitled'
        now   = datetime.now().strftime('%Y-%m-%d')
        fname = f"{safe}_deepseek_{now}_{cid}.md"
        md    = f"<!-- SOURCE_URL: {data.get('sourceUrl','')} -->\n<!-- CHAT_ID: deepseek_{cid} -->\n\n"
        md   += f"# deepseek: {title}\n\n"
        for msg in data['messages']:
            p = '#### 👤 Вы' if msg['role']=='user' else '#### 🤖 AI'
            md += f"{p}\n\n{msg['content']}\n\n"
        try:
            QApplication.processEvents()
            payload = json.dumps({"filename": fname, "content": md}).encode()
            req = urllib.request.Request(f"{SERVER_URL}/save", data=payload,
                headers={"Content-Type":"application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10)
            self.log_sig.emit(f"OK  {fname}", 'ok')
        except Exception as e:
            self.log_sig.emit(f"FAIL {fname} — {e}", 'err')


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN WINDOW
# ═══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Chat Exporter")
        self.resize(1300, 760)

        if os.path.exists(ICON_BIG):
            self.setWindowIcon(QIcon(ICON_BIG))

        # Cookie persistence (ДО создания browser)
        profile = QWebEngineProfile.defaultProfile()
        profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        profile.setPersistentStoragePath(COOKIE_DIR)

        self.worker = None
        self._zoom = 100
        self._theme_mode = 'dark'
        self._outbox_path = OBSIDIAN_DIR
        self._theme_sync_in_progress = False
        self._cfg_save_timer = QTimer()
        self._cfg_save_timer.setSingleShot(True)
        self._cfg_save_timer.timeout.connect(self._cfg_save)
        self._build_ui()
        self._cfg_load()
        idx = self.theme_combo.findData(self._theme_mode)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        self._setup_tray()
        self._start_pipeline()
        self._apply_style()
        self._setup_scripts()

        self.browser.loadFinished.connect(lambda ok: self._apply_browser_theme())
        QTimer.singleShot(100, self._set_titlebar_dark)
        QTimer.singleShot(2000, self._refresh_models)
        self.browser.load(QUrl("https://chat.deepseek.com/"))

    # ─── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        c = QWidget(); self.setCentralWidget(c)
        lo = QHBoxLayout(c); lo.setContentsMargins(0,0,0,0); lo.setSpacing(0)

        sp = QSplitter(Qt.Horizontal)
        lo.addWidget(sp)

        # Browser
        self.browser = QWebEngineView()
        self._setup_scripts()
        sp.addWidget(self.browser)

        # Right panel
        self.rp = rp = QWidget(); rp.setObjectName("control_panel"); rl = QVBoxLayout(rp); rl.setContentsMargins(6,6,6,6); rl.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.tabBar().setFont(QFont("Segoe UI", 13))
        self.tabs.addTab(QLabel("Встроенный браузер"), "DeepSeek")
        self.tabs.addTab(QLabel("—"), "Gemini")
        self.tabs.addTab(QLabel("—"), "Qwen")
        self.tabs.addTab(QLabel("—"), "GLM5")
        rl.addWidget(self.tabs)

        self.log = LogTerminal()
        rl.addWidget(self.log, 1)

        # Progress
        px = QHBoxLayout()
        self.pbar = QProgressBar()
        self.pbar.setRange(0,100); self.pbar.setValue(0); self.pbar.setTextVisible(True)
        self.plab = QLabel("0/0")
        px.addWidget(self.pbar,1); px.addWidget(self.plab)
        rl.addLayout(px)

        # Server status
        self.srv = QLabel("● Сервер: запуск…")
        rl.addWidget(self.srv)

        # Zoom control
        zx = QHBoxLayout()
        zx.addWidget(QLabel("🔍"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(50, 200)
        self.zoom_slider.setValue(self._zoom)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.setTickInterval(25)
        zx.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel(f"{self._zoom}%")
        self.zoom_label.setFixedWidth(40)
        zx.addWidget(self.zoom_label)
        self.btn_log_dec = QPushButton("A−")
        self.btn_log_dec.setFixedWidth(32)
        self.btn_log_dec.setToolTip("Уменьшить шрифт лога")
        self.btn_log_dec.clicked.connect(lambda: self._adjust_log_fs(-1))
        zx.addWidget(self.btn_log_dec)
        self.btn_log_inc = QPushButton("A+")
        self.btn_log_inc.setFixedWidth(32)
        self.btn_log_inc.setToolTip("Увеличить шрифт лога")
        self.btn_log_inc.clicked.connect(lambda: self._adjust_log_fs(1))
        zx.addWidget(self.btn_log_inc)
        rl.addLayout(zx)
        self.zoom_slider.valueChanged.connect(self._on_zoom)

        # Theme selection
        tx = QHBoxLayout()
        tx.addWidget(QLabel("🎨 Тема:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("🌙 Тёмная", "dark")
        self.theme_combo.addItem("☀️ Светлая", "light")
        self.theme_combo.addItem("💻 Системная", "system")
        self.theme_combo.setMinimumWidth(120)
        tx.addWidget(self.theme_combo, 1)
        rl.addLayout(tx)
        self.theme_combo.currentIndexChanged.connect(self._on_theme_change)

        # Model selection
        mx = QHBoxLayout()
        mx.addWidget(QLabel("🧠 Модель:"))
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(160)
        mx.addWidget(self.model_combo, 1)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("btn_refresh_models")
        self.refresh_btn.setToolTip("Обновить список моделей из Ollama")
        self.refresh_btn.clicked.connect(self._refresh_models)
        mx.addWidget(self.refresh_btn)
        rl.addLayout(mx)
        self.model_combo.currentTextChanged.connect(self._on_model_change)

        # Buttons
        bx = QHBoxLayout()
        self.b_collect = QPushButton("📥 Собрать все чаты")
        self.b_collect.setObjectName("btn_collect_all")
        self.b_collect.clicked.connect(self._on_collect)
        self.b_collect.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bx.addWidget(self.b_collect)

        self.b_current = QPushButton("📄 Текущий чат")
        self.b_current.clicked.connect(self._on_export_current)
        self.b_current.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bx.addWidget(self.b_current)

        self.b_stop = QPushButton("⏹ Остановить")
        self.b_stop.setObjectName("btn_stop")
        self.b_stop.setEnabled(False)
        self.b_stop.clicked.connect(self._on_stop)
        self.b_stop.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bx.addWidget(self.b_stop)

        ob = QPushButton("📂 Папка сохранения")
        ob.setToolTip(str(self._outbox_path))
        ob.clicked.connect(self._on_pick_folder)
        ob.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bx.addWidget(ob)

        rl.addLayout(bx)

        # Settings + log export
        sx = QHBoxLayout()
        self.b_settings = QPushButton("⚙ Настройки AI")
        self.b_settings.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.b_settings.clicked.connect(self._open_settings)
        sx.addWidget(self.b_settings)
        self.btn_export_log = QPushButton("📋 Экспорт лога")
        self.btn_export_log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.btn_export_log.clicked.connect(self._on_export_log)
        sx.addWidget(self.btn_export_log)
        rl.addLayout(sx)

        sp.addWidget(rp)
        sp.setCollapsible(0, False)
        sp.setCollapsible(1, False)
        rp.setMinimumWidth(380)
        sp.setSizes([850, 450])

    def _apply_style(self):
        t = THEMES[self._resolve_theme()]
        z = self._zoom / 100
        fs  = max(8, int(13 * z))
        tab_pad_v = max(3, int(6 * z))
        tab_pad_h = max(8, int(14 * z))
        btn_pad_v = max(4, int(7 * z))
        btn_pad_h = max(10, int(14 * z))
        br = max(4, int(8 * z))
        pr_h = max(14, int(20 * z))
        srv_fs = max(8, int(12 * z))
        log_fs = max(8, int(11 * z))
        sp_w = max(1, int(2 * z))

        theme = self._resolve_theme()

        if theme == 'dark':
            self.browser.page().setBackgroundColor(QColor("#0b0d14"))
            qss = f"""
QMainWindow{{background:#0b0d14}}
QSplitter::handle{{background:#1f2335;width:{sp_w}px}}
QWidget#control_panel{{background:#11131f;color:#f3f4f6;font-family:"Segoe UI","Inter",sans-serif;font-size:{fs}px;border-left:1px solid #1f2335}}
QLabel{{color:#8a94a6;font-family:"Segoe UI","Inter",sans-serif;font-size:{fs}px;font-weight:500}}
QPushButton{{background:#171a29;color:#f3f4f6;border:1px solid #262b45;border-radius:10px;padding:{btn_pad_v}px {btn_pad_h}px;font-family:"Segoe UI",sans-serif;font-size:{fs}px;font-weight:500;min-height:{max(14, int(22*z))}px}}
QPushButton:hover{{background:#1f2336;border-color:#3b82f6;color:#ffffff}}
QPushButton:pressed{{background:#0d0f18}}
QPushButton:disabled{{background:#0d0f18;color:#4b526d;border-color:#181b28}}
QPushButton#btn_collect_all{{background:#2563eb;color:#ffffff;font-weight:600;border:none}}
QPushButton#btn_collect_all:hover{{background:#3b82f6;border:none}}
QPushButton#btn_collect_all:pressed{{background:#1d4ed8}}
QPushButton#btn_collect_all:disabled{{background:#171a29;color:#4b526d;border:1px solid #262b45}}
QPushButton#btn_stop{{background:#2e151b;color:#fda4af;border:1px solid #4c1d24}}
QPushButton#btn_stop:hover{{background:#4c1d24;border-color:#f43f5e}}
QPushButton#btn_stop:disabled{{background:#0d0f18;color:#4b526d;border-color:#181b28}}
QPushButton#btn_refresh_models{{font-size:14px;font-weight:bold;min-width:28px;max-width:28px;min-height:20px;padding:0px}}
QTextEdit,QPlainTextEdit{{background:#07080d;color:#cbd5e1;border:1px solid #1f2335;border-radius:12px;font-family:"Consolas","Fira Code",monospace;font-size:{log_fs}px;padding:8px}}
QComboBox{{background:#171a29;color:#f3f4f6;border:1px solid #262b45;border-radius:8px;padding:6px 12px;min-height:{max(12, int(20*z))}px;font-size:{max(8, int(12*z))}px}}
QComboBox:hover{{border-color:#3b82f6}}
QComboBox::drop-down{{subcontrol-origin:padding;subcontrol-position:top right;width:24px;border-left:none}}
QComboBox QAbstractItemView{{background:#11131f;color:#f3f4f6;border:1px solid #1f2335;border-radius:8px;selection-background-color:#2563eb;selection-color:#ffffff;padding:4px}}
QProgressBar{{border:1px solid #1f2335;border-radius:8px;background:#07080d;text-align:center;color:#ffffff;font-size:{max(8, int(11*z))}px;font-weight:600;height:{pr_h}px}}
QProgressBar::chunk{{background:#2563eb;border-radius:6px}}
QTabWidget::pane{{background:#11131f;border:1px solid #1f2335;border-radius:12px;top:-1px}}
QTabBar::tab{{background:#0b0d14;color:#8a94a6;padding:{tab_pad_v}px {tab_pad_h}px;border-top-left-radius:8px;border-top-right-radius:8px;margin-right:4px;border:1px solid #1f2335;border-bottom:none;font-size:{fs}px;font-weight:500}}
QTabBar::tab:selected{{background:#11131f;color:#ffffff;font-weight:600;border-bottom:2px solid #2563eb}}
QTabBar::tab:hover:!selected{{background:#171a29;color:#f3f4f6}}
QSlider:horizontal{{min-height:20px;max-height:20px;background:transparent}}
QSlider::groove:horizontal{{border:none;height:4px;background:#1f2335;border-radius:2px;}}
QSlider::sub-page:horizontal{{background:#2563eb;border-radius:2px;}}
QSlider::handle:horizontal{{background:#ffffff;border:1px solid #2563eb;width:14px;height:14px;margin-top:-5px;margin-bottom:-5px;border-radius:7px;}}
QSlider::handle:horizontal:hover{{background:#3b82f6;border-color:#3b82f6}}
            """
        else:
            self.browser.page().setBackgroundColor(QColor("#ffffff"))
            qss = f"""
QWidget#control_panel{{background:#eff1f5;color:#4c4f69;font-family:"Segoe UI",sans-serif;font-size:{fs}px}}
QLabel{{color:#4c4f69;font-size:{fs}px;font-weight:500}}
QPushButton{{background:#e6e9ef;color:#4c4f69;border:1px solid #ccd0da;border-radius:{br}px;padding:{btn_pad_v}px {btn_pad_h}px;font-size:{fs}px;min-height:{max(14, int(22*z))}px}}
QPushButton:hover{{background:#ccd0da}}
QPushButton:pressed{{background:#dce0e8}}
QPushButton:disabled{{background:#f2f2f2;color:#a6a6a6;border-color:#d9d9d9}}
QPushButton#btn_collect_all{{background:#1e66f5;color:#ffffff;font-weight:bold}}
QPushButton#btn_collect_all:hover{{background:#04a5e5;border-color:#4dbeff}}
QPushButton#btn_collect_all:pressed{{background:#005994}}
QPushButton#btn_collect_all:disabled{{background:#e6e9ef;color:#bcc0cc}}
QPushButton#btn_stop{{background:#e64553;color:#ffffff;border:1px solid #d20f39}}
QPushButton#btn_stop:hover{{background:#d20f39;border-color:#ff4d4d}}
QPushButton#btn_stop:disabled{{background:#f2f2f2;color:#a6a6a6;border-color:#d9d9d9}}
QPushButton#btn_refresh_models{{font-size:14px;font-weight:bold;min-width:28px;max-width:28px;min-height:20px;padding:0px}}
QTextEdit,QPlainTextEdit{{background:{t['log_bg']};color:{t['log_fg']};border:1px solid {t['border']};border-radius:12px;font-family:"Consolas","Fira Code",monospace;font-size:{log_fs}px;padding:8px}}
QComboBox{{background:#e6e9ef;color:#4c4f69;border:1px solid #ccd0da;border-radius:6px;padding:4px 8px;min-height:{max(12, int(20*z))}px;font-size:{max(8, int(12*z))}px}}
QComboBox:hover{{border-color:#1e66f5}}
QComboBox::drop-down{{subcontrol-origin:padding;subcontrol-position:top right;width:{max(12, int(20*z))}px;border-left-width:0px}}
QProgressBar{{border:1px solid #ccd0da;border-radius:6px;background:#dce0e8;text-align:center;color:#4c4f69;font-size:{max(8, int(11*z))}px;font-weight:bold;height:{pr_h}px}}
QProgressBar::chunk{{background:#1e66f5;border-radius:5px}}
QTabWidget::pane{{background:#e6e9ef;border:1px solid #ccd0da;border-radius:{br}px}}
QTabBar::tab{{background:#e6e9ef;color:#5c5f77;font-size:{fs}px;padding:{tab_pad_v}px {tab_pad_h}px;border:1px solid #ccd0da;border-bottom:none;border-radius:{br}px {br}px 0 0}}
QTabBar::tab:selected{{background:#eff1f5;color:#1e66f5}}
QSlider::groove:horizontal{{background:#ccd0da;height:4px;border-radius:2px;border:none;}}
QSlider::sub-page:horizontal{{background:#1e66f5;border-radius:2px;}}
QSlider::handle:horizontal{{background:#4c4f69;border:1px solid #1e66f5;width:14px;height:14px;margin-top:-5px;margin-bottom:-5px;border-radius:7px;}}
QSlider::handle:horizontal:hover{{background:#1e66f5;}}
QSplitter::handle{{background:#e6e9ef;width:{sp_w}px}}
            """

        self.setStyleSheet(qss)
        self.srv.setStyleSheet(f"color:{t['warn']};font-size:{srv_fs}px")
        if not self.log._user_scaled:
            self.log._log_fs = max(8, int(11 * z))
            self.log.setFont(QFont("Fira Code", self.log._log_fs))
        self.log.retheme(t)
        self.tabs.tabBar().setFont(QFont("Segoe UI", fs))
        self._apply_widget_font(self.rp, fs)
        self._set_titlebar_dark()
        self._apply_browser_theme()

    # ─── Tray ────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(self)
        if os.path.exists(ICON_SMALL): self.tray.setIcon(QIcon(ICON_SMALL))
        else: self.tray.setIcon(self.style().standardIcon(4))
        m = QMenu()
        m.addAction("Показать").triggered.connect(self.showNormal)
        m.addSeparator()
        a = m.addAction("Выход"); a.triggered.connect(self.close)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.showNormal() if r==QSystemTrayIcon.DoubleClick else None)
        self.tray.show()

    def _setup_scripts(self):
        theme = self._resolve_theme()
        profile = QWebEngineProfile.defaultProfile()
        col = profile.scripts()

        for s in col.findScripts('copilot-dark'):
            col.remove(s)

        if theme == 'dark':
            js = """
(function() {
    var s = document.getElementById('copilot-custom-inject');
    if (!s) { s = document.createElement('style'); s.id = 'copilot-custom-inject'; }
    s.textContent = '__CSS__';
    (document.head || document.documentElement).appendChild(s);
})()""".replace('__CSS__', COPILOT_DARK_CSS)

            script = QWebEngineScript()
            script.setName('copilot-dark')
            script.setInjectionPoint(QWebEngineScript.DocumentReady)
            script.setWorldId(QWebEngineScript.MainWorld)
            script.setSourceCode(js)
            col.insert(script)

            if self.browser.page():
                self.browser.page().runJavaScript(js)
        else:
            clean_js = """
(function() {
    var s = document.getElementById('copilot-custom-inject');
    if (s) s.remove();
})()"""
            if self.browser.page():
                self.browser.page().runJavaScript(clean_js)

    # ─── Pipeline subprocess ─────────────────────────────────────────────────

    def _start_pipeline(self):
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(PROJECT_DIR))
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._pipe_out)
        self.proc.started.connect(lambda: self.srv.setText("● Сервер: запущен"))
        self.proc.finished.connect(lambda: self.srv.setText("● Сервер: остановлен"))
        self.proc.start("python", [str(PIPELINE_SCRIPT), "--serve"])

    def _pipe_out(self):
        raw = self.proc.readAllStandardOutput().data().decode('utf-8','replace')
        for line in raw.strip().split('\n'):
            if line.strip():
                lvl = 'info'
                if 'ERROR' in line: lvl = 'err'
                elif 'HTTP получен' in line: lvl = 'ok'
                elif 'Ollama' in line: lvl = 'job'
                self.log.log(line.strip(), lvl)

    # ─── Model selection ─────────────────────────────────────────────────────

    def _refresh_models(self):
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5) as resp:
                data = json.loads(resp.read())
            models = data.get("models", [])
            current = self.model_combo.currentText()
            self.model_combo.clear()
            for m in models:
                self.model_combo.addItem(m["name"])
            idx = self.model_combo.findText(current)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            # Load current model from pipeline
            try:
                with urllib.request.urlopen(f"{SERVER_URL}/model", timeout=3) as r:
                    cur = json.loads(r.read()).get("model", "")
                if cur:
                    idx = self.model_combo.findText(cur)
                    if idx >= 0:
                        self.model_combo.setCurrentIndex(idx)
            except Exception:
                pass
        except Exception as e:
            self.log.log(f"Не удалось получить список моделей: {e}", "err")

    def _on_model_change(self, model):
        if not model:
            return
        try:
            payload = json.dumps({"model": model}).encode()
            req = urllib.request.Request(f"{SERVER_URL}/model", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3)
            self.log.log(f"🧠 Модель сменена на: {model}", "info")
        except Exception as e:
            self.log.log(f"Не удалось сменить модель: {e}", "warn")

    def _adjust_log_fs(self, delta):
        self.log._log_fs = max(6, min(24, self.log._log_fs + delta))
        self.log._user_scaled = True
        self.log.setFont(QFont("Fira Code", self.log._log_fs))
        self.log.log(f"🔤 Шрифт лога: {self.log._log_fs}px", "info")
        self._cfg_save_timer.start(500)

    def _on_zoom(self, pct):
        self._zoom = pct
        self.zoom_label.setText(f"{pct}%")
        self.browser.setZoomFactor(pct / 100)
        self._apply_style()
        self.log.log(f"🔍 Масштаб: {pct}%", "info")
        self._cfg_save_timer.start(500)

    # ─── Theme ───────────────────────────────────────────────────────────────

    @staticmethod
    def _detect_system_theme():
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return 'light' if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 1 else 'dark'
        except Exception:
            return 'dark'

    def _resolve_theme(self):
        if self._theme_mode == 'system':
            return self._detect_system_theme()
        return self._theme_mode

    def _on_theme_change(self, idx):
        self._theme_mode = self.theme_combo.itemData(idx)
        self._apply_style()
        self._apply_browser_theme()
        label = self.theme_combo.currentText()
        self.log.log(f"🎨 Тема: {label}", "info")
        self._cfg_save_timer.start(500)

    def _set_titlebar_dark(self):
        try:
            import ctypes
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            dark = ctypes.c_int(1 if self._resolve_theme() == 'dark' else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()), DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(dark), ctypes.sizeof(dark))
        except Exception:
            pass

    @staticmethod
    def _apply_widget_font(widget, size):
        for child in widget.findChildren((QLabel, QPushButton)):
            child.setFont(QFont("Segoe UI", size))

    def _apply_browser_theme(self):
        self._setup_scripts()
        url = self.browser.url().toString()
        if "chat.deepseek.com" in url:
            self.browser.page().runJavaScript(DETECT_THEME_JS, self._on_detected_site_theme)

    def _on_detected_site_theme(self, raw):
        if self._theme_sync_in_progress or not raw or raw == 'null' or raw == 'unknown':
            return
        site_theme = raw.strip().strip('"')
        app_theme = self._resolve_theme()
        target_theme = 'dark' if app_theme == 'dark' else 'light'
        if site_theme != target_theme:
            self._sync_theme_to_site(target_theme)

    def _sync_theme_to_site(self, target):
        self._theme_sync_in_progress = True
        js = SET_THEME_JS.replace('__THEME__', target)
        self.browser.page().runJavaScript(js, self._on_theme_sync_result)

    def _on_theme_sync_result(self, result):
        self.log.log(f"🌗 Синхр. темы DeepSeek: {result}", "info")
        QTimer.singleShot(500, self._clear_theme_sync_flag)

    def _clear_theme_sync_flag(self):
        self._theme_sync_in_progress = False

    def _inject_browser_styles(self, *args):
        self._setup_scripts()

    def _open_settings(self):
        dlg = PromptDialog(self)
        dlg.exec_()

    # ─── Collect ─────────────────────────────────────────────────────────────

    def _set_progress_busy(self, busy=True):
        if busy:
            self.pbar.setRange(0, 0)
            self.pbar.setFormat("")
            self.plab.setText("⏳ сбор...")
        else:
            self.pbar.setRange(0, 100)
            self.pbar.setFormat("%v/%m")
            self.plab.setText("—")

    def _on_collect(self):
        self._set_progress_busy(True)
        js = """(function(){
var r=[];var s=new Set();
document.querySelectorAll('a[href*="/chat/"]').forEach(function(a){
var u=a.href;if(!u||s.has(u))return;
var m=u.match(/\\/a\\/chat\\/s\\/([a-f0-9-]+)/);if(!m)return;
s.add(u);var t='untitled';var x=a.querySelector('.c08e6e93');if(x)t=x.textContent.trim();
r.push({url:u,id:m[1],title:t});
});return JSON.stringify(r);
})()"""
        self.browser.page().runJavaScript(js, self._on_urls)

    def _on_urls(self, raw):
        if not raw:
            self.log.log("Нет чатов. Вы залогинены в DeepSeek?", 'err')
            self._set_progress_busy(False); return
        try: urls = json.loads(raw)
        except: self.log.log("Ошибка JSON списка чатов", 'err'); self._set_progress_busy(False); return
        if not urls: self.log.log("Нет чатов для экспорта", 'warn'); self._set_progress_busy(False); return

        self._set_progress_busy(False)
        self.worker = ExportWorker(self.browser.page(), urls)
        self.worker.log_sig.connect(self.log.log)
        self.worker.progress_sig.connect(self._on_progress)
        self.worker.done_sig.connect(self._on_done)
        self.b_collect.setEnabled(False); self.b_stop.setEnabled(True)
        self.worker.start()

    def _on_progress(self, cur, total, title):
        self.pbar.setValue(int(cur*100/total) if total else 0)
        self.pbar.setFormat(f"{cur}/{total}")
        self.plab.setText(f"{cur}/{total}")

    def _on_done(self):
        self.b_collect.setEnabled(True); self.b_stop.setEnabled(False); self.worker = None
        self.pbar.setValue(0); self.pbar.setFormat("%v/%m"); self.plab.setText("0/0")

    def _on_stop(self):
        if self.worker: self.worker.cancel()
        self.pbar.setValue(0); self.pbar.setFormat("%v/%m"); self.plab.setText("⏹")

    def _on_export_current(self):
        url = self.browser.url().toString()
        if not url or "chat.deepseek.com" not in url:
            self.log.log("Сначала откройте чат DeepSeek", 'err')
            return
        self.log.log(f"→ Экспорт текущего чата", 'job')
        self._current_extractor = ScrollExtractor(self.browser.page(), self.log.log)
        self._current_extractor.start(self._on_current_extracted)

    def _on_current_extracted(self, data):
        self._current_extractor = None
        if not data or not data.get('messages'):
            self.log.log("Нет сообщений в текущем чате", 'warn'); return
        self._save_current(data)

    def _on_export_log(self):
        log_dir = PROJECT_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = log_dir / f"{ts}.txt"
        path.write_text(self.log.toPlainText(), encoding="utf-8")
        self.log.log(f"📋 Лог сохранён: {path.name}", 'ok')

    def _save_current(self, data):
        title = data.get('title', 'untitled')
        cid = data.get('chatId', '') or str(hash(data.get('sourceUrl', '')))[-6:]
        safe = re.sub(r'[<>:"/\\|?*]', '', title)
        safe = re.sub(r'\s+', '-', safe).strip('-')[:30].lower() or 'untitled'
        now = datetime.now().strftime('%Y-%m-%d')
        fname = f"{safe}_deepseek_{now}_{cid}.md"
        md = f"<!-- SOURCE_URL: {data.get('sourceUrl','')} -->\n<!-- CHAT_ID: deepseek_{cid} -->\n\n"
        md += f"# deepseek: {title}\n\n"
        for msg in data['messages']:
            p = '#### 👤 Вы' if msg['role'] == 'user' else '#### 🤖 AI'
            md += f"{p}\n\n{msg['content']}\n\n"
        try:
            QApplication.processEvents()
            payload = json.dumps({"filename": fname, "content": md}).encode()
            req = urllib.request.Request(f"{SERVER_URL}/save", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=10)
            self.log.log(f"OK  {fname}", 'ok')
        except Exception as e:
            self.log.log(f"FAIL {fname} — {e}", 'err')

    def _on_pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения", str(self._outbox_path))
        if not folder:
            return
        self._outbox_path = Path(folder)
        self._outbox_path.mkdir(parents=True, exist_ok=True)
        btn = self.sender()
        if btn:
            btn.setToolTip(str(self._outbox_path))
        self.log.log(f"📂 Папка сохранения: {self._outbox_path}", "info")
        try:
            payload = json.dumps({"outbox": str(self._outbox_path)}).encode()
            req = urllib.request.Request(f"{SERVER_URL}/settings", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            self.log.log(f"Не удалось передать путь pipeline: {e}", "warn")
        self._cfg_save()

    # ─── Config save/load ────────────────────────────────────────────────────

    def _cfg_save(self):
        try:
            payload = json.dumps({
                "ui": {"zoom": self._zoom, "log_font_size": self.log._log_fs},
                "outbox": str(self._outbox_path),
            }).encode()
            req = urllib.request.Request(f"{SERVER_URL}/settings", data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:
            # fallback — прямой проход в config.yml
            try:
                cfg_path = PROJECT_DIR / "config.yml"
                import yaml
                if cfg_path.exists():
                    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
                else:
                    cfg = {}
                cfg.setdefault("ui", {})
                cfg["ui"]["zoom"] = self._zoom
                cfg["ui"]["log_font_size"] = self.log._log_fs
                cfg["outbox"] = str(self._outbox_path)
                cfg_path.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            except Exception as e2:
                self.log.log(f"Не удалось сохранить config.yml: {e2}", "warn")

    def _cfg_load(self):
        try:
            cfg_path = PROJECT_DIR / "config.yml"
            if not cfg_path.exists():
                return
            import yaml
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if not cfg:
                return
            ui = cfg.get("ui", {})
            zoom = ui.get("zoom")
            if zoom and 50 <= zoom <= 200:
                self._zoom = zoom
                self.zoom_slider.setValue(zoom)
                self.browser.setZoomFactor(zoom / 100)
            lfs = ui.get("log_font_size")
            if lfs and 6 <= lfs <= 24:
                self.log._log_fs = lfs
                self.log._user_scaled = True
                self.log.setFont(QFont("Fira Code", lfs))
            outbox = cfg.get("outbox")
            if outbox:
                self._outbox_path = Path(outbox)
                self._outbox_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.log.log(f"Не удалось загрузить config.yml: {e}", "warn")

    def closeEvent(self, ev):
        self._cfg_save()
        if self.proc and self.proc.state()==QProcess.Running:
            self.proc.terminate(); self.proc.waitForFinished(3000)
        ev.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("AI Chat Exporter")
    w = MainWindow(); w.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
