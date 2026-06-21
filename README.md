# 🚦 SmartPath: Automated Traffic Violation Detection and Legal Reporting System

SmartPath is an AI-powered traffic monitoring platform designed to assist traffic authorities in detecting road violations, generating legal reports, and providing traffic law explanations using Retrieval-Augmented Generation (RAG).

The system combines Computer Vision, Google Gemini, Geographic Information Systems (GIS), and Large Language Models to automate the traffic violation reporting process.

---

## 🎯 Project Overview

Traditional traffic monitoring relies heavily on cameras and manual review by traffic officers. SmartPath automates this process by analyzing uploaded traffic images, identifying violations, extracting vehicle information, and generating comprehensive reports linked to Jordanian traffic laws.

The platform provides:

* Automatic traffic violation detection
* Vehicle information extraction
* Location-based violation tracking
* Legal article retrieval using RAG
* PDF report generation
* Violation analytics dashboard
* AI-powered traffic law chatbot

---

## ✨ Features

### 🚗 Vehicle Information Extraction

Using Google Gemini Vision, the system extracts:

* License plate number
* Vehicle type
* Vehicle color

### 🚨 Traffic Violation Detection

Detects violations from uploaded traffic images using Computer Vision models.

Examples include:

* Illegal parking
* Lane violations
* Traffic rule infractions

### 📍 GPS & Location Intelligence

* Reverse geocoding using OpenStreetMap Nominatim
* City identification
* Area identification
* Street identification

### ⚖️ Legal Report Generation

Retrieval-Augmented Generation (RAG) is used to retrieve relevant Jordanian traffic laws and generate detailed legal explanations for detected violations.

### 📄 Automated PDF Reports

Generate professional reports containing:

* Vehicle information
* Violation details
* Fine calculations
* Legal references
* Evidence image
* Location information

### 📊 Analytics Dashboard

Visualize:

* Violation trends
* Violations by city
* Violations by type
* Time-based analytics
* Fine statistics

### 🤖 AI Chatbot

Ask questions about:

* Traffic laws
* Violation penalties
* Legal regulations
* Traffic enforcement procedures

---

## 🏗️ System Architecture

```text
Traffic Image
      │
      ▼
Computer Vision Detection
      │
      ├── Violation Detection
      │
      └── Gemini Vehicle Analysis
              │
              ▼
      Vehicle Information
              │
              ▼
      RAG Legal Engine
              │
              ▼
      Violation Report
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
 Database  Dashboard  PDF Report
```

---

## 🛠️ Technology Stack

### Backend

* Python
* Flask

### Database

* PostgreSQL
* SQLAlchemy

### Artificial Intelligence

* Google Gemini Vision
* Retrieval-Augmented Generation (RAG)

### Computer Vision

* YOLO-based violation detection

### GIS & Mapping

* OpenStreetMap Nominatim

### Reporting

* ReportLab PDF Generation

### Frontend

* HTML
* CSS
* JavaScript

---

## 📂 Project Structure

```text
SmartPath/
│
├── app.py
├── detector.py
├── crud.py
├── times.py
│
├── database/
├── gemini/
├── rag/
├── templates/
├── static/
├── images/
└── models/
```

---

## 🚀 Installation

### Clone the Repository

```bash
git clone https://github.com/yourusername/smartpath.git
cd smartpath
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure Environment Variables

Create a `.env` file:

```env
GEMINI_API_KEY=your_api_key
DB_USER=postgres
DB_PASSWORD=password
DB_HOST=localhost
DB_PORT=5432
DB_NAME=smartpath
```

### Run the Application

```bash
python app.py
```

---

## 📈 Future Enhancements

* Real-time camera integration
* Mobile application support
* Live traffic monitoring
* Multi-language chatbot
* Violation prediction using machine learning
* Government system integration


