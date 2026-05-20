"""
SmartPath Jordan — RAG Engine
Integrates ChromaDB + SentenceTransformer + Groq LLM into the Flask app.

Setup (one-time):
    1. Place your smartpath_jordan_complete_rag.txt in the rag/ folder.
    2. Run:  python rag/rag_engine.py  — this builds the ChromaDB index.
    3. The Flask app will then load the pre-built index automatically.
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────
RAG_TXT_PATH     = os.path.join(os.path.dirname(__file__), "smartpath_jordan_complete_rag.txt")
CHROMA_DIR       = os.path.join(os.path.dirname(__file__), "chroma_store")
COLLECTION_NAME  = "smartpath_jordan_laws"
EMBEDDING_MODEL  = "intfloat/multilingual-e5-large"
GROQ_MODEL       = "llama-3.3-70b-versatile"
GROQ_API_KEY     = os.getenv("GROQ_API_KEY", "")

# Violation type → natural language query mapping (from your notebook)
VIOLATION_QUERY_MAP = {
    "Wrong Way Driving" : "vehicle driving in opposite direction on divided road wrong way",
    "Wrong Parking"     : "vehicle parked in prohibited location wrong parking violation",
    "wrong_way_driving" : "vehicle driving in opposite direction on divided road wrong way",
    "wrong_parking"     : "vehicle parked in prohibited location wrong parking violation",
    "speeding"          : "exceeded speed limit by 50 km/h",
    "red_light"         : "vehicle passed through red traffic light",
    "drunk_driving"     : "driver was drunk and lost control of the vehicle",
    "no_seatbelt"       : "driver not wearing seatbelt",
    "mobile_phone"      : "driver using handheld mobile phone while driving",
    "no_helmet"         : "motorcycle rider not wearing helmet",
    "reckless_driving"  : "driver driving recklessly and dangerously on road",
    "hit_and_run"       : "driver fled the scene of the accident",
}

# ── Lazy-loaded singletons ─────────────────────────────────────────────────────
_embedding_model = None
_chroma_collection = None
_groq_client = None


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        print("[RAG] Loading embedding model...")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        print("[RAG] Embedding model ready.")
    return _embedding_model


def _get_collection():
    global _chroma_collection
    if _chroma_collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=CHROMA_DIR)
        try:
            _chroma_collection = client.get_collection(name=COLLECTION_NAME)
            print(f"[RAG] Loaded existing ChromaDB collection ({_chroma_collection.count()} articles).")
        except Exception:
            print("[RAG] No existing collection found — building index now...")
            _chroma_collection = _build_index(client)
    return _chroma_collection


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq
        _groq_client = Groq(api_key=GROQ_API_KEY)
        print("[RAG] Groq client ready.")
    return _groq_client


# ── Index Builder ──────────────────────────────────────────────────────────────

def _parse_chunk(chunk: str) -> dict:
    result = {}
    lines = chunk.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("ARTICLE_ID:"):
            result["article_id"] = line.replace("ARTICLE_ID:", "").strip()
        elif line.startswith("CATEGORY:"):
            result["category"] = line.replace("CATEGORY:", "").strip()
        elif line.startswith("TITLE:"):
            result["title"] = line.replace("TITLE:", "").strip()
        elif line.startswith("PENALTY:"):
            result["penalty"] = line.replace("PENALTY:", "").strip()
        elif line.startswith("KEYWORDS:"):
            result["keywords"] = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("LEGAL_TEXT:"):
            text_lines = []
            for j in range(i + 1, len(lines)):
                if lines[j].startswith(("PENALTY:", "KEYWORDS:")):
                    break
                text_lines.append(lines[j])
            result["legal_text"] = "\n".join(text_lines).strip()
    return result


def _build_index(chroma_client):
    """Parse the RAG text file and build the ChromaDB index."""
    if not os.path.exists(RAG_TXT_PATH):
        raise FileNotFoundError(
            f"[RAG] RAG knowledge base not found at: {RAG_TXT_PATH}\n"
            "Please place smartpath_jordan_complete_rag.txt in the rag/ folder."
        )

    with open(RAG_TXT_PATH, "r", encoding="utf-8") as f:
        raw_text = f.read()

    raw_chunks = raw_text.split("===")
    parsed_chunks = []
    for chunk in raw_chunks:
        chunk = chunk.strip()
        if chunk and "ARTICLE_ID" in chunk:
            parsed = _parse_chunk(chunk)
            if parsed:
                parsed_chunks.append(parsed)

    print(f"[RAG] Parsed {len(parsed_chunks)} articles.")

    model = _get_embedding_model()
    collection = chroma_client.create_collection(name=COLLECTION_NAME)

    for chunk in parsed_chunks:
        text_to_embed = chunk.get("legal_text", "") + " " + chunk.get("keywords", "")
        vector = model.encode(text_to_embed).tolist()
        collection.add(
            ids=[chunk["article_id"]],
            embeddings=[vector],
            documents=[chunk.get("legal_text", "")],
            metadatas=[{
                "article_id": chunk.get("article_id", ""),
                "category":   chunk.get("category", ""),
                "title":      chunk.get("title", ""),
                "penalty":    chunk.get("penalty", ""),
                "keywords":   chunk.get("keywords", ""),
            }]
        )

    # Fix ART-014 known parsing issue
    try:
        collection.update(
            ids=["ART-014"],
            metadatas=[{
                "article_id": "ART-014",
                "category":   "Traffic Violations — Traffic Signals",
                "title":      "Traffic Signal and Traffic Light Violations",
                "penalty":    "Red light: 200-300 JOD or imprisonment 1-2 months (2023). Doubled if repeated within 1 year. Mandatory signs: 20 JOD. Officer signals: 30 JOD.",
                "keywords":   "red light, traffic light, traffic signal, stop sign, STOP, right of way, priority, mandatory signs, traffic officer signal, signal violation, bypass red light, running red light"
            }]
        )
    except Exception:
        pass

    print(f"[RAG] Index built and stored at: {CHROMA_DIR}")
    return collection


# ── Core Functions ─────────────────────────────────────────────────────────────

def retrieve(violation_type: str, n_results: int = 2) -> list:
    """Retrieve relevant law articles for a violation type."""
    query = VIOLATION_QUERY_MAP.get(violation_type, violation_type)
    model = _get_embedding_model()
    collection = _get_collection()

    vector = model.encode(query).tolist()
    results = collection.query(query_embeddings=[vector], n_results=n_results)

    retrieved = []
    for i in range(len(results["ids"][0])):
        retrieved.append({
            "article_id" : results["metadatas"][0][i]["article_id"],
            "title"      : results["metadatas"][0][i]["title"],
            "penalty"    : results["metadatas"][0][i]["penalty"],
            "legal_text" : results["documents"][0][i],
        })
    return retrieved


def _ask_llm(prompt: str) -> str:
    """Send a prompt to the Groq LLM and return the response."""
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    return response.choices[0].message.content


def generate_violation_report(
    violation_type: str,
    plate_number: str,
    timestamp: str,
    location: str,
) -> dict:
    """
    Full RAG pipeline for a single violation.
    Returns dict with: article_id, title, penalty, report (LLM text)
    """
    retrieved = retrieve(violation_type, n_results=2)
    if not retrieved:
        return {
            "article_id": "N/A",
            "title":      "Unknown",
            "penalty":    "N/A",
            "report":     "Could not retrieve relevant law articles for this violation.",
        }

    articles_text = ""
    for i, article in enumerate(retrieved):
        articles_text += f"""
Article {i+1} (ID: {article['article_id']}):
Title: {article['title']}
Legal Text: {article['legal_text']}
Penalty: {article['penalty']}
"""

    prompt = f"""
You are a formal traffic violation report generator for Jordan's Public Security Directorate (SmartPath Jordan system).

Your job is to write a SHORT, FORMAL, and PROFESSIONAL violation report in English.

STRICT RULES:
- Answer ONLY using the law articles provided below.
- Do NOT make up fine amounts or legal rulings.
- Always refer to articles by their Article ID (e.g. ART-016) not by their order number.
- Always state a specific fine amount — pick the most relevant one from the penalty field.
- Keep the report to maximum 4 sentences.
- Do not say "cannot be determined" — always give the best answer from the provided articles.

VIOLATION DETAILS:
- Violation Type: {violation_type}
- License Plate: {plate_number}
- Timestamp: {timestamp}
- Location: {location}

RETRIEVED LAW ARTICLES:
{articles_text}

Write a formal violation report including:
1. What violation was detected and where
2. The specific Article ID and law it violates
3. The exact fine amount in JOD
4. Any additional consequences (imprisonment, towing, license seizure)
"""

    report = _ask_llm(prompt)
    return {
        "article_id": retrieved[0]["article_id"],
        "title":      retrieved[0]["title"],
        "penalty":    retrieved[0]["penalty"],
        "report":     report,
    }


def run_rag_for_violations(
    violation_names: list,
    plate_number: str,
    timestamp: str,
    city: str,
    area: str,
    street: str,
) -> list:
    """
    Called by app.py after detection.
    violation_names: e.g. ["Wrong Way Driving", "Wrong Parking"]
    Returns list of report dicts.
    """
    location = f"{street}, {area}, {city}"
    reports = []
    for v_name in violation_names:
        result = generate_violation_report(
            violation_type=v_name,
            plate_number=plate_number,
            timestamp=timestamp,
            location=location,
        )
        result["violation_type"] = v_name
        reports.append(result)
    return reports


def chatbot(question: str) -> str:
    """
    Answer an officer's question using the RAG knowledge base.
    Called by the /chatbot route in app.py.
    """
    model = _get_embedding_model()
    collection = _get_collection()

    vector = model.encode(question).tolist()
    results = collection.query(query_embeddings=[vector], n_results=3)

    context = ""
    for i in range(len(results["ids"][0])):
        context += f"""
Article {i+1} (ID: {results['metadatas'][0][i]['article_id']}):
Title: {results['metadatas'][0][i]['title']}
Content: {results['documents'][0][i]}
Penalty: {results['metadatas'][0][i]['penalty']}
"""

    prompt = f"""
You are a traffic law assistant for Jordan's Public Security Directorate.
You help traffic officers find answers about Jordan's Traffic Law quickly and accurately.

STRICT RULES:
- Answer ONLY using the law articles provided below.
- If the answer is not in the provided articles, say exactly:
  "I don't have enough information to answer this from the traffic law database."
- Do NOT make up fine amounts, article numbers, or legal rulings.
- Be concise and direct — officers need fast answers.
- Always mention the fine amount in JOD if relevant.
- Always mention the article ID if relevant.

RETRIEVED LAW ARTICLES:
{context}

OFFICER QUESTION:
{question}

Answer:
"""
    return _ask_llm(prompt)


# ── Run as script to pre-build the index ──────────────────────────────────────
if __name__ == "__main__":
    print("Building RAG index...")
    _get_collection()
    print("Done! Index stored at:", CHROMA_DIR)
