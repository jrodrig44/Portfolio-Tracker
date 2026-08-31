from __future__ import annotations

import threading


MARKET_REFRESH_LOCK = threading.RLock()
