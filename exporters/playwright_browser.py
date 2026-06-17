class BrowserPlaywright:
    def __init__(self, user_data_dir=".cookies/playwright", log=None, headless=True):
        self._pw = None
        self._context = None
        self._page = None
        self.user_data_dir = user_data_dir
        self.log = log
        self.headless = headless

    def start(self):
        import os
        import shutil

        from playwright.sync_api import sync_playwright

        # Cleanup zombie locks from previous crashes
        lock_path = os.path.join(self.user_data_dir, ".lock")
        shutil.rmtree(lock_path, ignore_errors=True)

        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            viewport={"width": 1200, "height": 800},
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        if self.log:
            self.log.add("[INFO] Playwright context ready")

    def new_page(self):
        self._page = self._context.new_page()
        return self._page

    @property
    def page(self):
        return self._page

    def close(self):
        if self._context:
            self._context.close()
        if self._pw:
            self._pw.stop()
        if self.log:
            self.log.add("[INFO] Playwright stopped")
