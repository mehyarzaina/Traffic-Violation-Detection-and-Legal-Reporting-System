# SmartPath Jordan

A web application for reporting possible traffic violations in Jordan. Citizens send a photo, AI models check it, and an authorised officer makes the final decision.

Built as a university capstone prototype at Al Hussein Technical University (HTU), Amman.

> **Academic prototype.** SmartPath Jordan is not an official police service and does not issue fines. The law content is an unofficial English summary and is not legal advice.

## How it works

```text
Citizen sends a photo and location
            |
            v
  YOLO: possible violation?   +   Gemini: plate, colour, type   +   OpenStreetMap: street, area, city
            |
            v
  Officer review queue:  confirm  /  not a violation
            |
            v
  Confirmed only:  records, PDF reports, analytics, hotspot map
```

## Features

**Reporters (citizens)**
- Upload a photo with location and consent, then see "Report received"
- View or withdraw their own reports, download their data, delete their account
- Never see plates, vehicle details, AI results or review outcomes

**Officers (admins)**
- Review queue with the AI result marked on the photo
- Confirm or reject reports, and change a decision later
- Violation records with PDF export, analytics charts and a hotspot map
- User management: add, edit, deactivate or remove accounts

**Assistant (English and Arabic)**
- Answers traffic-law questions from a local knowledge base and shows its sources
- Answers questions about the site's data, limited to what each role may see
- Says when the knowledge base does not cover a question

**Privacy, security and accessibility**
- Hashed passwords, CSRF protection, role checks and security headers
- Photo metadata (EXIF) is removed and uploads get random file names
- Privacy policy, terms and cookie policy in English and Arabic
- Responsive layout, keyboard support and dark/light themes

## Technology

| Part | Used |
|---|---|
| Backend | Python, Flask |
| Database | PostgreSQL, SQLModel / SQLAlchemy |
| Violation detection | Ultralytics YOLO, OpenCV, Pillow |
| Vehicle details | Google Gemini |
| Assistant | Groq-hosted LLM with BM25 retrieval ([rag.py](rag.py)) |
| Location and maps | OpenStreetMap Nominatim, Leaflet |
| Charts and PDFs | Chart.js, ReportLab |
| Frontend | HTML, CSS, JavaScript, Jinja2 |

## Models

Two fine-tuned YOLO models run on each photo. These are the validation scores saved in the model files:

| Model | Base | Violation class | Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|
| [wrong_way_driving.pt](models/wrong_way_driving.pt) | yolo26s | `wrong-side` | 0.929 | 0.939 | 0.978 | 0.770 |
| [wrong_parking.pt](models/wrong_parking.pt) | yolo11s | `improper` | 0.870 | 0.922 | 0.941 | 0.752 |

The scores are averages over both classes of each model, measured during training. The models have not been tested on an independent set of labelled photos from Jordan, so real-world accuracy is unknown.

A detection counts as a possible violation only for the class above, at 75% confidence or more.

## Setup

Developed with Python 3.12. You need a PostgreSQL server.

**1. Clone and install**

```bash
git clone https://github.com/mehyarzaina/Traffic-Violation-Detection-and-Legal-Reporting-System.git
cd Traffic-Violation-Detection-and-Legal-Reporting-System
python -m venv .venv
```

Activate the environment with `.venv\Scripts\activate` on Windows, or `source .venv/bin/activate` on macOS and Linux. Then:

```bash
pip install -r requirements.txt
```

**2. Create an empty PostgreSQL database**, for example `smartpath`. The tables are created on first start.

**3. Create a `.env` file** in the project folder:

```env
DB_USER=postgres
DB_PASSWORD=your_database_password
DB_HOST=localhost
DB_NAME=smartpath

FLASK_SECRET_KEY=a_long_random_value

# First officer account, created if no admin exists yet
ADMIN_USERNAME=your_admin_name
ADMIN_PASSWORD=your_admin_password

GEMINI_API_KEY=your_key
GROQ_API_KEY=your_key

# Optional
CONTACT_EMAIL=
GROQ_MODEL=
HOST=127.0.0.1
PORT=5000
```

If PostgreSQL does not use port 5432, put the port in `DB_HOST`, for example `localhost:5433`.

To generate a secret key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**4. Run**

```bash
python app.py
```

Open http://localhost:5000. New sign-ups are always reporters; an admin can change roles on the Users page.

Without a Groq key the assistant gives only basic answers. Without Gemini, vehicle details are not read; violation detection still runs.

## Project structure

```text
app.py              Routes and application setup
auth.py             Sign-in, roles, CSRF
crud.py             Database operations
detector.py         YOLO violation detection
chatbot.py          Assistant prompts, privacy rules, citations
rag.py              BM25 search over the knowledge base
reports.py          Geocoding and PDF reports
site_info.py        Site name, operator and contact details
icons.py, times.py  Small helpers
database/           Models, connection, migrations
gemini/             Vehicle detail extraction
knowledge/          Traffic-law and site-guide text
models/             YOLO weights and training notebooks
templates/, static/ Pages and assets
```

Uploaded photos are stored in `images/`, which is created on first start and ignored by Git.

## Data sent to other services

| Service | What it receives |
|---|---|
| Google Gemini | The uploaded photo, to read vehicle details |
| Groq | Assistant questions, recent chat, and the site data the signed-in role may see |
| OpenStreetMap Nominatim | Report coordinates, to find the street and area |
| OpenStreetMap tiles, Google Fonts, jsDelivr, unpkg | Normal browser requests for maps, fonts and scripts |

Do not use real personal data in demos.

## Known limitations

- Detects two violation types only: wrong-way driving and illegal parking
- A single photo cannot always show the direction of traffic, so officer review is required
- Law entries and fines have not been checked against the official Arabic text
- Fines stored in the site (250 JD and 30 JD) are demo values
- Arabic questions are matched to the English knowledge base with a small word list
- No automated tests and no limit on sign-in attempts yet
- Runs on Flask's development server; use a production server and HTTPS before deploying

## Licence

No licence has been chosen for this code yet. It uses [Ultralytics YOLO](https://github.com/ultralytics/ultralytics), which is available under AGPL-3.0 or a commercial licence.

Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.
