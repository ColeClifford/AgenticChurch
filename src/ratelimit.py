"""In-memory rate limits, keyed by client IP.

The church runs as one process beside one SQLite file, so limits live in memory
and reset on restart. Running several workers would give each its own counters.

Behind a reverse proxy, set FORWARDED_ALLOW_IPS (read by uvicorn) to the proxy's
address so the client IP comes from X-Forwarded-For; otherwise every request
appears to come from the proxy and shares one budget.

Set RATE_LIMITS=off to disable (local development, the congregation simulator).
"""

import ipaddress
import math
import os
import time
from collections import deque


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


class SlidingWindow:
    """At most `limit` hits per key in any `window` seconds."""

    def __init__(self, limit: int, window: float, clock=time.monotonic):
        self.limit = limit
        self.window = window
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def hit(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, or seconds until the next hit is allowed."""
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return hits[0] + self.window - now if hits else self.window
        hits.append(now)
        self._calls += 1
        if self._calls % 1000 == 0:
            self._sweep(now)
        return None

    def _sweep(self, now: float) -> None:
        for key in [k for k, h in self._hits.items() if not h or h[-1] <= now - self.window]:
            del self._hits[key]

    def reset(self) -> None:
        self._hits.clear()


# Every HTTP request, REST and MCP alike.
requests = SlidingWindow(_env_int("RATE_LIMIT_REQUESTS_PER_MINUTE", 120), 60)
# New visitor services (members are already limited to one service a week).
visitor_services = SlidingWindow(_env_int("RATE_LIMIT_VISITOR_SERVICES_PER_HOUR", 20), 3600)
# New memberships.
joins = SlidingWindow(_env_int("RATE_LIMIT_JOINS_PER_DAY", 100), 86400)

ALL = [requests, visitor_services, joins]

# Shared intentions waiting for review; beyond this, new ones are held privately.
MODERATION_QUEUE_MAX = _env_int("MODERATION_QUEUE_MAX", 200)
# ...and no more than this many from any one member at a time.
MODERATION_QUEUE_PER_MEMBER = _env_int("MODERATION_QUEUE_PER_MEMBER", 3)


def enabled() -> bool:
    return os.environ.get("RATE_LIMITS", "on").lower() not in ("off", "0", "false")


def address_key(client_ip: str) -> str:
    """What one visitor's budget is keyed by. An IPv6 user typically holds a whole /64,
    so counting single IPv6 addresses would give them an unlimited budget."""
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip
    if address.version == 6:
        if address.ipv4_mapped:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def check(limiter: SlidingWindow, client_ip: str | None) -> int | None:
    """Seconds to wait (rounded up), or None if the hit is allowed or limits don't apply."""
    if client_ip is None or not enabled():
        return None
    wait = limiter.hit(address_key(client_ip))
    return None if wait is None else max(1, math.ceil(wait))


def reset_all() -> None:
    for limiter in ALL:
        limiter.reset()
