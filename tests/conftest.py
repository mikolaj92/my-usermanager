import os
from pathlib import Path

_ = os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path(__file__).resolve().parents[1] / ".venv/playwright"),
)
