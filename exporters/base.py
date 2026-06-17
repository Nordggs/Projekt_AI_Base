class Browser:
    def __init__(self, window=None, mode="webview", log=None):
        self.mode = mode
        self.log = log
        self._window = window
        self._playwright_page = None
        self.capabilities = {
            "scroll": mode == "playwright",
            "eval": True,
            "multi_tab": mode == "playwright",
        }

    @property
    def page(self):
        if self.mode == "playwright":
            return self._playwright_page
        return self._window

    def execute(self, js, timeout=30000):
        if self.mode == "webview":
            return self._window.evaluate_js(js)
        elif self.mode == "playwright":
            return self._playwright_page.evaluate(js)

    def eval(self, js, callback=None):
        try:
            result = self.execute(js)
            if callback:
                callback(result)
        except Exception as e:
            if callback:
                callback({"error": str(e)})
            else:
                raise


class Exporter:
    def __init__(self, browser):
        self._browser = browser
        self._on_complete = None

    def start(self, on_complete):
        self._on_complete = on_complete

    def cancel(self):
        pass
