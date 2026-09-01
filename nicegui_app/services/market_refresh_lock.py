from __future__ import annotations

import threading


# One process may receive refresh requests from browser sessions, explicit data
# preparation, and the server market worker at the same time. Keep the entire
# Yahoo -> metrics -> live-snapshot transaction serialized so callers neither
# multiply external requests nor observe a partially refreshed portfolio.
# RLock is intentional because shared preparation paths can re-enter the guard.
MARKET_REFRESH_LOCK = threading.RLock()
