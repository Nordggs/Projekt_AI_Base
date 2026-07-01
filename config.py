import os
from pathlib import Path

CHATGPT_DIAGNOSE = os.environ.get("CHATGPT_DIAGNOSE", "0") == "1"
DIAGNOSE_DIR = Path("raw/debug/chatgpt")
