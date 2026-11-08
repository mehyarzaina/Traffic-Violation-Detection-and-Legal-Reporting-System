"""
Website assistant chatbot.

Answers questions about the data on the site (how many violations, where,
when, which plates, fines…) and about how to use the site. Each question is
sent to an LLM (Groq) together with a fresh summary of the database, scoped
to what the signed-in user is allowed to see:

  * admin → every violation record, uploads and user counts
  * user  → only their own uploads
"""
import os
from collections import Counter
from datetime import datetime, timedelta

from crud import get_violations_only, get_all_fines, get_uploads_for_user, user_counts
from times import get_time_label

# Tried in order; a model Groq has retired is skipped. Override with GROQ_MODEL in .env.
GROQ_MODELS = [m for m in (os.getenv("GROQ_MODEL", "").strip(),
                           "openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b") if m]
MAX_HISTORY = 8          # previous chat turns sent for follow-up questions
MAX_ROWS = 40            # individual records included in the context

_groq_client = None


def _get_groq():
    global _groq_client
    if _groq_client is None:
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            return None
        from groq import Groq
        _groq_client = Groq(api_key=key)
    return _groq_client


# ── Site guide ───────────────────────────────────────────────────────────────

ADMIN_GUIDE = """Pages available to this admin:
- Home: overview with live stats.
- Detect: upload a traffic camera image; YOLO detects Wrong Way Driving / Wrong Parking, Gemini reads the plate, colour and car type, and the GPS location is reverse-geocoded. A PDF report can be downloaded.
- History: table of every violation with image, plate, location, fine and PDF download; the whole table can be exported as PDF.
- Analytics: charts by hour, city, type and day, plus a map of all violations.
- Users: click "+ Add user" (top right) or open "+ Add a new user", fill in username, password, optional name/email, pick the role (User/Admin) and press "Create user". Click "Edit" on a row to change name, email, role, active status or set a new password, then "Save changes"; "Delete user" removes the account but keeps its uploads and violation records. Search filters by name, username or email. People can also sign themselves up from the login page; those accounts are always normal users.
- The chat assistant (this chat) is on every page."""

USER_GUIDE = """Pages available to this user:
- Detect: upload a traffic image (or take a photo on a phone). The system detects Wrong Way Driving / Wrong Parking, reads the plate, colour and car type, and saves the result.
- My Uploads: every image this user uploaded, with results, a PDF report for violations, and a delete button.
- The chat assistant (this chat) is on every page.
Users cannot see other people's uploads, the full violation history, analytics or user management — those are admin-only."""


# ── Context builders ─────────────────────────────────────────────────────────

def _top(counter: Counter, n=12) -> str:
    return ", ".join(f"{k} ({v})" for k, v in counter.most_common(n)) or "none"


def _fines_text() -> str:
    return ", ".join(f"{f.violation_name} = {f.fine_amount} JD" for f in get_all_fines())


def _admin_context() -> str:
    records = get_violations_only()
    now = datetime.now()
    by_type, by_city, by_area, by_street = Counter(), Counter(), Counter(), Counter()
    by_period, by_hour, by_day, by_plate = Counter(), Counter(), Counter(), Counter()
    by_color, by_cartype = Counter(), Counter()
    total_fine = 0
    last7 = last30 = today = 0

    for rec, fine in records:
        by_type[fine.violation_name] += 1
        by_city[rec.city or "Unknown"] += 1
        by_area[f"{rec.area}, {rec.city}"] += 1
        by_street[f"{rec.street}, {rec.area}, {rec.city}"] += 1
        by_period[get_time_label(rec.timestamp.hour)] += 1
        by_hour[f"{rec.timestamp.hour:02d}:00"] += 1
        by_day[rec.timestamp.strftime("%Y-%m-%d")] += 1
        if rec.plate_number and rec.plate_number != "Unreadable":
            by_plate[rec.plate_number] += 1
        by_color[rec.car_color or "Unknown"] += 1
        by_cartype[rec.car_type or "Unknown"] += 1
        total_fine += fine.fine_amount
        age = now - rec.timestamp
        if rec.timestamp.date() == now.date():
            today += 1
        if age <= timedelta(days=7):
            last7 += 1
        if age <= timedelta(days=30):
            last30 += 1

    repeat = Counter({p: c for p, c in by_plate.items() if c > 1})
    rows = "\n".join(
        f"- #{rec.id} | {rec.timestamp:%Y-%m-%d %H:%M} | {fine.violation_name} | {fine.fine_amount} JD | "
        f"plate {rec.plate_number} | {rec.car_color or '?'} {rec.car_type or '?'} | "
        f"{rec.street}, {rec.area}, {rec.city} ({rec.latitude}, {rec.longitude})"
        for rec, fine in records[:MAX_ROWS]
    ) or "- none"
    users = user_counts()
    recent_days = sorted(by_day.items())[-30:]

    return f"""DATABASE SUMMARY (as of {now:%Y-%m-%d %H:%M}):
Total violations: {len(records)} | total fines: {total_fine} JD
Today: {today} | last 7 days: {last7} | last 30 days: {last30}
Fines table: {_fines_text()}
By violation type: {_top(by_type)}
By city: {_top(by_city)}
By area: {_top(by_area, 15)}
By street: {_top(by_street, 15)}
By time period: {_top(by_period)}
By hour: {_top(by_hour, 24)}
By day (last 30 active days): {", ".join(f"{d} ({c})" for d, c in recent_days) or "none"}
Car colours: {_top(by_color)} | car types: {_top(by_cartype)}
Plates with more than one violation: {_top(repeat, 15)}
Accounts: {users['total']} total ({users['admins']} admins, {users['users']} users, {users['active']} active)

MOST RECENT {min(MAX_ROWS, len(records))} VIOLATIONS (newest first):
{rows}"""


def _user_context(user) -> str:
    uploads = get_uploads_for_user(user.id)
    with_v = [u for u in uploads if u.violation_list]
    by_type = Counter(v for u in with_v for v in u.violation_list)
    by_city = Counter(u.city or "Unknown" for u in with_v)
    rows = "\n".join(
        f"- upload #{u.id} | {u.uploaded_at:%Y-%m-%d %H:%M} | file {u.original_name or u.image_filename} | "
        f"violations: {', '.join(u.violation_list) or 'none'} | fine {u.total_fine} JD | "
        f"plate {u.plate_number or '?'} | {u.car_color or '?'} {u.car_type or '?'} | "
        f"{u.street}, {u.area}, {u.city}"
        for u in uploads[:MAX_ROWS]
    ) or "- none yet"
    return f"""THIS USER'S DATA (as of {datetime.now():%Y-%m-%d %H:%M}):
Uploads: {len(uploads)} | with a violation: {len(with_v)} | clean: {len(uploads) - len(with_v)}
Total fines from their uploads: {sum(u.total_fine for u in uploads)} JD
Fines table: {_fines_text()}
Their violations by type: {_top(by_type)}
Their violations by city: {_top(by_city)}

THEIR MOST RECENT UPLOADS (newest first):
{rows}"""


def _system_prompt(user) -> str:
    role_ctx = _admin_context() if user.is_admin else _user_context(user)
    guide = ADMIN_GUIDE if user.is_admin else USER_GUIDE
    return f"""You are the SmartPath Jordan website assistant, a traffic-violation detection system for Jordan.
You are talking to {user.full_name or user.username} (role: {user.role}).

Answer questions about the website's data and how to use the website, using ONLY the facts below.
Rules:
- Be short and direct. Use numbers from the data; never invent records, plates, places or counts.
- If the answer isn't in the data, say so and suggest the page where they can find it.
- A {user.role} may only see what is listed below; never reveal other users' data.
- Reply in the same language the user writes in (Arabic or English).
- Use plain text with simple "-" bullet lists when listing things. You may use **bold** for key numbers. No headings, no tables.
- "Unknown" means the location could not be identified; mention that when it matters.

{guide}

{role_ctx}"""


def _fallback_answer(user, question: str) -> str:
    """Used when no LLM is reachable: give the key numbers."""
    if user.is_admin:
        records = get_violations_only()
        types = Counter(f.violation_name for _, f in records)
        cities = Counter(r.city or "Unknown" for r, _ in records)
        return (f"The AI assistant is unavailable right now, but here are the numbers: "
                f"{len(records)} violations recorded ({_top(types)}). "
                f"Top locations: {_top(cities, 5)}. See History and Analytics for details.")
    uploads = get_uploads_for_user(user.id)
    n_v = sum(1 for u in uploads if u.violation_list)
    return (f"The AI assistant is unavailable right now. You have {len(uploads)} uploads, "
            f"{n_v} with a violation. Open My Uploads to see them.")


def ask(user, question: str, history=None) -> str:
    client = _get_groq()
    if client is None:
        return _fallback_answer(user, question)

    messages = [{"role": "system", "content": _system_prompt(user)}]
    for turn in (history or [])[-MAX_HISTORY:]:
        role = turn.get("role")
        content = str(turn.get("content", ""))[:2000]
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question[:2000]})

    for model in GROQ_MODELS:
        try:
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0.2, max_tokens=1500,
            )
            answer = (resp.choices[0].message.content or "").strip()
            if answer:
                return answer
        except Exception as exc:
            print(f"[Chatbot] LLM error ({model}): {exc}")
            if "model_not_found" not in str(exc) and "decommissioned" not in str(exc):
                break
    return _fallback_answer(user, question)
