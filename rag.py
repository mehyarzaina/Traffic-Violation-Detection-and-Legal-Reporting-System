"""
Traffic-law retrieval for the chat assistant (lightweight RAG).

The knowledge base is plain text in knowledge/*.txt (one entry per block,
separated by "==="). Entries are ranked with BM25 — no embedding model, no
vector database, nothing to download — so it loads in milliseconds and runs
on any laptop. Arabic questions are mapped to English search terms with a
small glossary, because the knowledge base is written in English.

    from rag import search
    hits = search("what is the fine for driving the wrong way?")
    # → [Entry(id="QA-001", title=..., text=..., penalty=...), ...]
"""
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
TOP_K = 4
MIN_SCORE = 2.0          # below this the match is too weak to be useful
K1, B = 1.4, 0.75        # BM25 parameters

# Field weights: a word in the title or keywords counts more than in the body.
WEIGHTS = {"title": 3, "keywords": 3, "category": 2, "penalty": 1, "text": 1}

STOPWORDS = set("""
a an the and or of to in on at for by with from is are was were be been being it its this that these those
what which who whom how when where why do does did can could should would will shall may might must i me my
you your we our they them their he she his her there here if then than so as about into over under up down
not no yes any all some more most other such only own same too very just also get got have has had
""".split())

# Arabic word (or stem) → English search terms in the knowledge base.
AR_GLOSSARY = {
    "مخالف": "violation", "غرام": "fine penalty", "عقوب": "penalty", "دينار": "fine JOD",
    "عكس": "wrong-way opposite direction", "السير": "traffic", "اتجاه": "direction wrong-way",
    "معاكس": "wrong-way opposite direction", "اصطفاف": "parking", "وقوف": "parking stopping",
    "توقف": "stopping parking", "اصطف": "parking", "رصيف": "sidewalk pavement parking",
    "سرع": "speed speeding", "حزام": "seatbelt", "هاتف": "mobile phone", "خلوي": "mobile phone",
    "جوال": "mobile phone", "موبايل": "mobile phone", "اشار": "traffic signal light",
    "إشار": "traffic signal light", "ضوئي": "traffic light signal", "أحمر": "red light", "احمر": "red light",
    "نقاط": "points", "نقط": "points", "رخص": "license", "ترخيص": "licensing registration",
    "حجز": "impoundment seizure", "سجن": "imprisonment", "حبس": "imprisonment",
    "كحول": "alcohol drunk", "سكر": "alcohol drunk", "مخدر": "drugs", "مشا": "pedestrian",
    "مشاة": "pedestrian crosswalk", "ممر": "crosswalk", "تجاوز": "overtaking", "دوران": "u-turn turning",
    "رجوع": "reversing", "حادث": "accident", "تأمين": "insurance", "لوح": "plate number plates",
    "نمر": "number plate", "أطفال": "child children", "اطفال": "child children", "تدخين": "smoking",
    "إسعاف": "emergency ambulance", "اسعاف": "emergency ambulance", "سباق": "racing", "تفحيط": "racing drifting",
    "حمول": "cargo load", "شاحن": "freight truck", "باص": "bus public transport", "تكسي": "taxi public transport",
    "تاكسي": "taxi public transport", "مسرب": "lane", "مسار": "lane", "تكرار": "repeat doubled",
    "مضاعف": "doubled", "قانون": "law", "ماد": "article", "خصوصي": "privacy", "بيان": "data",
    "كوكيز": "cookies", "بلاغ": "report", "تقرير": "report", "صور": "photo report", "حساب": "account",
    "كلمة السر": "password", "شرطي": "officer", "ضابط": "officer", "مراجع": "review",
}

_AR = re.compile(r"[؀-ۿ]")
_WORD = re.compile(r"[a-z0-9]+")


def _stem(w: str) -> str:
    # Tiny suffix stripper: "violations" → "violat", "parking" → "park".
    for suf in ("ations", "ation", "ings", "ing", "ies", "ers", "ed", "es", "er", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokenize(text: str) -> list[str]:
    text = text.lower().replace("u-turn", "uturn")
    return [_stem(w) for w in _WORD.findall(text) if w not in STOPWORDS and len(w) > 1]


def _arabic_to_english(text: str) -> str:
    if not _AR.search(text):
        return ""
    return " ".join(en for ar, en in AR_GLOSSARY.items() if ar in text)


@dataclass
class Entry:
    id: str
    category: str
    title: str
    source: str
    text: str
    penalty: str
    keywords: str
    tf: Counter = field(default_factory=Counter, repr=False)
    length: int = 0

    def as_context(self) -> str:
        # Always the whole entry: cutting it could silently drop a fine or an exception.
        body = self.text
        pen = f"\nPenalty: {self.penalty}" if self.penalty and self.penalty != "Not applicable" else ""
        return f"[{self.id}] {self.title} ({self.category}; source: {self.source})\n{body}{pen}"


def _parse(path: Path) -> list[Entry]:
    raw = "\n".join(l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#"))
    entries = []
    for block in re.split(r"^===\s*$", raw, flags=re.M):
        def f(name):
            m = re.search(rf"^{name}:[ \t]*(.*)$", block, re.M)
            return m.group(1).strip() if m else ""
        eid = f("ID")
        if not eid:
            continue
        body = re.search(r"^TEXT:\s*\n(.*?)(?=^PENALTY:|\Z)", block, re.S | re.M)
        entries.append(Entry(eid, f("CATEGORY"), f("TITLE"), f("SOURCE") or path.stem,
                             (body.group(1).strip() if body else ""), f("PENALTY"), f("KEYWORDS")))
    return entries


class _Index:
    def __init__(self):
        self.entries: list[Entry] = []
        for path in sorted(KNOWLEDGE_DIR.glob("*.txt")):
            self.entries.extend(_parse(path))
        df = Counter()
        for e in self.entries:
            tf = Counter()
            for name, weight in WEIGHTS.items():
                for tok in tokenize(getattr(e, name)):
                    tf[tok] += weight
            e.tf, e.length = tf, sum(tf.values())
            df.update(tf.keys())
        n = len(self.entries) or 1
        self.avg_len = sum(e.length for e in self.entries) / n
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def search(self, query: str, k=TOP_K, min_score=MIN_SCORE) -> list[Entry]:
        terms = set(tokenize(query + " " + _arabic_to_english(query)))
        scored = []
        for e in self.entries:
            s = 0.0
            for t in terms:
                f = e.tf.get(t)
                if f:
                    s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * e.length / self.avg_len))
            if s >= min_score:
                scored.append((s, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:k]]


_index = None
_lock = threading.Lock()


def get_index() -> _Index:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _Index()
    return _index


def search(query: str, k=TOP_K) -> list[Entry]:
    return get_index().search(query, k)


def reload():
    """Re-read knowledge/*.txt (after editing the files)."""
    global _index
    with _lock:
        _index = None
    return get_index()
