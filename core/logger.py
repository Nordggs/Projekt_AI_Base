import threading


class LogBuffer:
    INFO = "INFO"
    WARN = "WARN"
    SUCCESS = "SUCCESS"

    def __init__(self, max_size=5000):
        self.logs = []
        self.max_size = max_size
        self._lock = threading.Lock()

    def add(self, msg):
        with self._lock:
            self.logs.append(msg)
            if len(self.logs) > self.max_size:
                self.logs.pop(0)

    def get(self):
        with self._lock:
            return "\n".join(self.logs)

    def clear(self):
        with self._lock:
            self.logs.clear()
