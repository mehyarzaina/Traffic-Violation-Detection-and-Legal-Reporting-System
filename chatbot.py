"""
Website assistant chatbot.

Answers questions about the data on the site (how many violations, where,
when, which plates, fines…), about how to use the site, and about Jordan's
traffic law. Each question is sent to an LLM (Groq) together with:

  * the traffic-law / site-guide entries that best match the question
    (retrieved by rag.py from knowledge/*.txt), and
  * a fresh summary of the database, scoped to what the signed-in user is
    allowed to see:

  * admin → every violation record, uploads and user counts
  * user  → only their own uploads
"""
import os
import re
from collections import Counter
from datetime import datetime, timedelta

from crud import get_violations_only, get_all_fines, get_uploads_for_user, user_counts, report_counts
from times import get_time_label
import rag

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

ADMIN_GUIDE = """This person is an ADMIN (authorised officer). Pages available:
- Home: overview with stats.
- Reports: the review queue of citizen reports. Each shows the photo with AI boxes, AI result, plate, vehicle, place and reporter. Tick the violation type(s) and press "Confirm violation", or press "Not a violation". Only confirmed reports become official violation records (History, Analytics, stats). Decisions can be changed later.
- Detect: an officer's own upload is recorded immediately. Upload a traffic camera image; YOLO detects Wrong Way Driving / Wrong Parking, Gemini reads the plate, colour and car type, and the GPS location is reverse-geocoded. A PDF report can be downloaded.
- History: table of every violation with image, plate, location, fine and PDF download; the whole table can be exported as PDF.
- Analytics: charts by hour, city, type and day, plus a map of all violations.
- Users: click "+ Add user" (top right) or open "+ Add a new user", fill in username, password, optional name/email, pick the role (User/Admin) and press "Create user". Click "Edit" on a row to change name, email, role, active status or set a new password, then "Save changes"; "Delete user" removes the account but keeps its uploads and violation records. Search filters by name, username or email. People can also sign themselves up from the login page; those accounts are always normal users.
- The chat assistant (this chat) is on every page."""

USER_GUIDE = """This person is a REPORTER (a member of the public). How the site works for them:
- Report: choose or take a photo of a possible violation (car driving the wrong way, or parked illegally), tick the photo-rules box, and press "Send report". Their location is attached so reviewers know where it happened.
- After sending they see "Report received". An authorised reviewer (officer) checks every report.
- My reports: the list of photos they sent and when. "Remove report" deletes a report that hasn't been reviewed yet; reviewed reports stay with the reviewers but disappear from their list.
- Account: change password, download their data, or delete their account.
- Privacy Policy, Terms and Cookie Policy are linked in the footer.
- Safety rules: never use the phone while driving; only photograph public roads; no false reports. This is a university prototype, not an official police service; in an emergency call 911.
STRICT PRIVACY RULE: reporters are NOT told the outcome of reports. Never say whether a report was a violation, confirmed, rejected or pending, and never reveal plates, car details, fines or AI results — not even for their own reports. If asked, explain kindly that results are only visible to authorised reviewers.
Reporters MAY ask general questions about Jordan's traffic law (fines, points, rules); answer those from the LEGAL REFERENCE, in general terms, never about a specific report or vehicle."""


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
    rc = report_counts()
    recent_days = sorted(by_day.items())[-30:]

    return f"""DATABASE SUMMARY (as of {now:%Y-%m-%d %H:%M}):
Official (confirmed) violations: {len(records)} | total reference fines: {total_fine} JD
Citizen reports: {rc['pending']} waiting for review, {rc['confirmed']} confirmed, {rc['rejected']} not a violation
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
    # Deliberately ONLY when/where they reported — no AI result, review
    # status, plates or fines ever reach the model for a reporter.
    uploads = get_uploads_for_user(user.id)
    rows = "\n".join(
        f"- report sent {u.uploaded_at:%Y-%m-%d %H:%M} near {u.area}, {u.city}"
        for u in uploads[:MAX_ROWS]
    ) or "- none yet"
    return f"""THIS REPORTER'S DATA (as of {datetime.now():%Y-%m-%d %H:%M}):
Reports sent: {len(uploads)}
First report: {uploads[-1].uploaded_at:%Y-%m-%d} | latest: {uploads[0].uploaded_at:%Y-%m-%d}
Violation types the site can detect: Wrong Way Driving, Wrong Parking

THEIR REPORTS (newest first):
{rows}""" if uploads else f"""THIS REPORTER'S DATA: no reports sent yet.
Violation types the site can detect: Wrong Way Driving, Wrong Parking"""


def _legal_context(hits) -> str:
    if not hits:
        return "LEGAL REFERENCE: no matching entry for this question."
    header = ("LEGAL REFERENCE (unofficial English summary of Traffic Law No. 49 of 2008 "
              "as amended in 2023, plus the site guide):")
    return header + "\n\n" + "\n\n---\n\n".join(h.as_context() for h in hits)


def _system_prompt(user, hits) -> str:
    role_ctx = _admin_context() if user.is_admin else _user_context(user)
    guide = ADMIN_GUIDE if user.is_admin else USER_GUIDE
    return f"""You are the SmartPath Jordan website assistant, a traffic-violation detection system for Jordan.
You are talking to {user.full_name or user.username} (role: {user.role}).

Answer questions about the website's data, how to use the website, and Jordan's traffic law, using ONLY the facts below.
Rules:
- Be short and direct. Use numbers from the data; never invent records, plates, places or counts.
- If the answer isn't in the data, say so and suggest the page where they can find it.
- A {user.role} may only see what is listed below; never reveal other users' data.
- Law questions: answer only from the LEGAL REFERENCE. After each fact taken from it, add its ID in square brackets, e.g. [QA-001]. If the reference does not cover the question, say you don't have that rule and suggest checking with the Public Security Directorate. Never guess a fine, article number or penalty.
- The LEGAL REFERENCE is an unofficial summary, not legal advice. The website already prints that disclaimer under every answer that cites it, so do not add your own note or disclaimer.
- The site's reference fines (in the Fines table) are demo values; don't present them as the legal penalty.
- Reply in the language of the user's LATEST message: English question → English answer, Arabic question → Arabic answer (the user's name does not decide the language). The reference is in English; translate it when answering in Arabic and keep the [ID] tags.
- Use plain text with simple "-" bullet lists when listing things. You may use **bold** for key numbers. No headings, no tables, no emojis, no italics or single asterisks.
- Keep a calm, professional tone.
- "Unknown" means the location could not be identified; mention that when it matters.

{guide}

{_legal_context(hits)}

{role_ctx}"""


def _fallback_answer(user, question: str, hits=()) -> str:
    """Used when no LLM is reachable: give the key numbers or the best law entry."""
    law = next((h for h in hits if not h.id.startswith("SITE-")), None)
    if law and law.penalty and law.penalty != "Not applicable":
        return (f"The AI assistant is unavailable right now. The closest traffic-law entry is "
                f"\"{law.title}\": {law.penalty} (unofficial summary, not legal advice).")
    if user.is_admin:
        records = get_violations_only()
        types = Counter(f.violation_name for _, f in records)
        cities = Counter(r.city or "Unknown" for r, _ in records)
        return (f"The AI assistant is unavailable right now, but here are the numbers: "
                f"{len(records)} violations recorded ({_top(types)}). "
                f"Top locations: {_top(cities, 5)}. See History and Analytics for details.")
    uploads = get_uploads_for_user(user.id)
    return (f"The AI assistant is unavailable right now. You have sent {len(uploads)} "
            f"report{'' if len(uploads) == 1 else 's'} — open My reports to see them.")


_ITALIC = re.compile(r"(?<![*\w])\*(?![*\s])([^*\n]+?)(?<![*\s])\*(?![*\w])")
# Asked for [QA-001], but models also write (QA-001), a bare QA-001, or lenticular brackets with a suffix.
_CITE = re.compile(r"\s*[\[(【]([A-Z]{2,5}-\d{3}[^\[\]()【】\n]{0,80})[\])】]")
_ENTRY_ID = re.compile(r"\b[A-Z]{2,5}-\d{3}\b")
_BARE_ID = re.compile(r"\s*[-–—:]?\s*\b(?:ART|PARK|WWD|QA|SITE)-\d{3}\b")


def _split_citations(answer: str, hits):
    """Remove [ID] tags from the text and return the cited entries as sources."""
    # Drop invisible characters and unusual hyphens that some models put inside IDs.
    answer = re.sub(r"[\u200b\u2060\ufeff]", "", answer)
    answer = re.sub(r"\b([A-Z]{2,5})[\u2010-\u2015](\d{3})\b", r"\1-\2", answer)
    known = {h.id: h for h in hits}
    cited = []
    for cid in _ENTRY_ID.findall(answer):
        if cid in known and known[cid] not in cited:
            cited.append(known[cid])
    clean = _BARE_ID.sub("", _CITE.sub("", answer))
    clean = re.sub(r"\s*[(\[]\s*[)\]]", "", clean)   # brackets left empty
    clean = _ITALIC.sub(r"\1", clean).strip()   # the chat only renders **bold**
    return clean, [{"id": h.id, "title": h.title, "source": h.source} for h in cited]


def _retrieval_query(question: str, history) -> str:
    # Include the previous user turn so follow-ups ("and if it's repeated?") still match.
    prev = [str(t.get("content", ""))[:300] for t in (history or []) if t.get("role") == "user"]
    return (prev[-1] + " " if prev else "") + question


def ask_with_sources(user, question: str, history=None):
    """Returns (answer, sources) — sources are the knowledge entries the answer cites."""
    hits = rag.search(_retrieval_query(question, history))
    client = _get_groq()
    if client is None:
        return _fallback_answer(user, question, hits), []

    messages = [{"role": "system", "content": _system_prompt(user, hits)}]
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
                return _split_citations(answer, hits)
        except Exception as exc:
            print(f"[Chatbot] LLM error ({model}): {exc}")
            if "model_not_found" not in str(exc) and "decommissioned" not in str(exc):
                break
    return _fallback_answer(user, question, hits), []


def ask(user, question: str, history=None) -> str:
    return ask_with_sources(user, question, history)[0]
