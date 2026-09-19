"""Simple in-memory per-IP rate limiting for POST /api/assistant — the one
EDFlow endpoint that costs real money (an LLM call) and is reachable by
anyone on the public internet.

In-memory only, like every other stateful module in this demo — fine for
a single-process deployment. Deliberately NOT tied to app/reset_registry:
this limits the real visitor's IP address, not simulated dashboard state,
so it must not reset just because someone clicks "Reset Demo".
"""

import time
from collections import defaultdict, deque

PER_MINUTE_LIMIT = 10
PER_DAY_LIMIT = 200

_minute_hits = defaultdict(deque)  # ip -> timestamps within the last 60s
_day_hits = defaultdict(deque)  # ip -> timestamps within the last 24h


def check_rate_limit(ip):
    """Records this request and returns (allowed, reason). `reason` is a
    short, user-facing explanation, only set when allowed is False."""
    now = time.time()

    minute_q = _minute_hits[ip]
    while minute_q and now - minute_q[0] > 60:
        minute_q.popleft()
    if len(minute_q) >= PER_MINUTE_LIMIT:
        return False, "You're sending messages a bit too quickly — please wait a minute and try again."

    day_q = _day_hits[ip]
    while day_q and now - day_q[0] > 86400:
        day_q.popleft()
    if len(day_q) >= PER_DAY_LIMIT:
        return False, "This demo assistant has hit its daily limit for your connection — please try again tomorrow."

    minute_q.append(now)
    day_q.append(now)
    return True, None
