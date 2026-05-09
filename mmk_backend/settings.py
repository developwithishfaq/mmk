"""Environment-backed settings (paths, CORS — not live login credentials)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# All log files go into this sub-directory so they are easy to find after a
# trading session.  Created on first import if it doesn't already exist.
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


@dataclass(frozen=True)
class Settings:
    csv_file: str
    ui_dir: str
    api_log: str
    session_state_file: str
    cors_origins_raw: str


def load_settings() -> Settings:
    return Settings(
        csv_file="watchlist_prices.csv",
        ui_dir="ui",
        api_log=os.path.join(LOG_DIR, "api_hits.jsonl"),
        session_state_file="web_session_state.json",
        cors_origins_raw=os.getenv("MMK_CORS_ORIGINS", "*").strip(),
    )
