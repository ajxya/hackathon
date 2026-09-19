"""EDFlow assistant — a small LLM-backed helper that explains the current
simulated ED state and the rules-based recommendations. It never performs
an action itself (the user always clicks Apply) and never gives medical
advice — operational information only, and always clear that the
underlying data is synthetic.

The actual provider call is isolated in `_call_llm()` so the provider and
model can be swapped through environment variables alone, with no other
code changes:
  ASSISTANT_API_KEY   Required. If unset, the assistant replies with a
                       friendly "not configured" message instead of
                       calling out anywhere. Read on the server only —
                       never sent to the browser.
  ASSISTANT_API_BASE  OpenAI-compatible chat-completions base URL.
                       Default: https://api.openai.com/v1
  ASSISTANT_MODEL     Model name. Default: gpt-4o-mini — small, fast, and
                       cheap, which is all short operational Q&A needs.
"""

import json
import os
import urllib.request
from urllib.error import HTTPError, URLError

MAX_HISTORY_MESSAGES = 6
MAX_OUTPUT_TOKENS = 220
REQUEST_TIMEOUT_SECONDS = 8
MAX_EVENT_LOG_ENTRIES_IN_CONTEXT = 10

FALLBACK_REPLY = (
    "I couldn't reach the assistant model just now. The dashboard's status "
    "banner and recommendations panel already have the latest information — "
    "feel free to ask me again in a moment."
)

NOT_CONFIGURED_REPLY = (
    "The assistant isn't connected to an AI model in this deployment (no "
    "ASSISTANT_API_KEY is set), so I can't answer questions right now. "
    "Everything else in the dashboard works normally."
)

SYSTEM_PROMPT = """You are the EDFlow assistant, embedded in a synthetic, \
simulated Emergency Department operations dashboard built for a hackathon \
demo. You help the user understand the CURRENT SIMULATED STATE shown in \
the dashboard and the rules-based recommendations it produces.

Rules you must always follow:
- Answer only using the CONTEXT block provided with this message (a live \
snapshot of the simulated dashboard). If the answer isn't in that context, \
say you don't know rather than guessing.
- Keep answers to 1-3 short sentences, unless the user explicitly asks you \
to summarize recent events or the whole session — a short paragraph is \
fine for that.
- You may suggest what action to take next, but you can never perform one \
yourself — the user always clicks "Apply" in the dashboard to actually do \
anything.
- All data is simulated/synthetic. Never imply this reflects a real \
hospital, real patients, or a real medical situation.
- You give operational information only: capacity, staffing, wait times, \
and the recommendation ladder. You never give medical, diagnostic, or \
treatment advice — if asked for that, politely decline and explain you \
only help with this simulated operations dashboard.
- You can summarize recent events or the session on request, using the \
event log given in the context."""


def _format_breach_table(breach_summary):
    lines = []
    for tier in range(1, 6):
        t = breach_summary["by_tier"][tier]
        lines.append(f"  Tier {tier}: {t['waiting']} waiting, {t['breached']} breached, avg wait {t['avg_wait_minutes']} min")
    return "\n".join(lines)


def _format_recommendations(recommendations):
    if not recommendations:
        return "  (none)"
    return "\n".join(f"  Rung {r['rung']} — {r['action']}: {r['reason']}" for r in recommendations)


def _format_facilities(nearby_facilities):
    if not nearby_facilities:
        return "  (none)"
    return "\n".join(
        f"  {f['name']} ({f['type']}): {f['status']}, avg wait {f['avg_wait_minutes']} min" for f in nearby_facilities
    )


def build_assistant_context(utilization, breach_summary, status, recommendations, session_summary):
    """Compact plain-text snapshot for the LLM prompt — aggregate/
    operational data only. Deliberately excludes the patient list."""
    beds = utilization["beds"]
    rooms = utilization["rooms"]
    nurses = utilization["nurses"]
    physicians = utilization["physicians"]

    recent_events = session_summary["event_log"][:MAX_EVENT_LOG_ENTRIES_IN_CONTEXT]
    events_text = (
        "\n".join(f"  {e['time']}  {e['message']}" for e in recent_events) if recent_events else "  (no events yet)"
    )

    return f"""STATUS: {status['level'].upper()} — {status['reason']}

BREACH SUMMARY (Tier 1-2 breaches: {breach_summary['tier1_2_breaches']}, total breaches: {breach_summary['total_breaches']}):
{_format_breach_table(breach_summary)}

CAPACITY:
  Beds: {beds['occupied']}/{beds['total']} occupied ({beds['pct']}%)
  Rooms: {rooms['in_use']}/{rooms['total']} in use ({rooms['pct']}%)
  Nurses: {nurses['capacity_used']}/{nurses['capacity_total']} slots filled ({nurses['pct']}%)
  Physicians: {physicians['capacity_used']}/{physicians['capacity_total']} slots filled ({physicians['pct']}%)
  Patients waiting: {utilization['patients_waiting']}, avg wait: {utilization['avg_wait_minutes']} min

HOSPITAL FLOAT POOL AVAILABLE: {session_summary['float_pool']}
OVERFLOW BEDS AVAILABLE: {session_summary['overflow_pool']}

ACTIVE RECOMMENDATIONS:
{_format_recommendations(recommendations)}

NEARBY FACILITIES:
{_format_facilities(session_summary['nearby_facilities'])}

RECENT EVENT LOG (most recent {len(recent_events)}):
{events_text}
"""


def _call_llm(messages):
    """Isolated provider call: one OpenAI-compatible chat-completions
    request. Returns (reply, error) — error is None on success, otherwise
    "not_configured" or "call_failed". Never raises."""
    api_key = os.environ.get("ASSISTANT_API_KEY")
    if not api_key:
        return None, "not_configured"

    api_base = os.environ.get("ASSISTANT_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("ASSISTANT_MODEL", "gpt-4o-mini")

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.3,
    }
    request = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
        reply = body["choices"][0]["message"]["content"].strip()
        return (reply, None) if reply else (None, "call_failed")
    except (URLError, HTTPError, TimeoutError, KeyError, IndexError, ValueError):
        return None, "call_failed"


def get_assistant_reply(user_message, history, utilization, breach_summary, status, recommendations, session_summary):
    """Builds context, calls the LLM, and always returns a reply string —
    never raises. Falls back to a friendly message if the assistant isn't
    configured or the call fails for any reason (timeout, network error,
    malformed response, ...)."""
    context = build_assistant_context(utilization, breach_summary, status, recommendations, session_summary)

    trimmed_history = history[-MAX_HISTORY_MESSAGES:] if history else []

    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\nCONTEXT:\n{context}"}]
    for turn in trimmed_history:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    reply, error = _call_llm(messages)
    if error == "not_configured":
        return NOT_CONFIGURED_REPLY
    if error is not None:
        return FALLBACK_REPLY
    return reply
