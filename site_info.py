"""
Operator details shown in the footer and the Privacy / Terms / Cookie pages.
Edit these in ONE place (or set CONTACT_EMAIL in .env) — please check them
with your supervisor before the site is shown to anyone outside the team.
"""
import os
from dotenv import load_dotenv

load_dotenv()

SITE = {
    "name": "SmartPath Jordan",
    "name_ar": "سمارت باث الأردن",
    "operator": "the SmartPath Jordan capstone project team",
    "operator_ar": "فريق مشروع التخرج SmartPath Jordan",
    "university": "Al Hussein Technical University (HTU), Amman, Jordan",
    "university_ar": "جامعة الحسين التقنية (HTU)، عمّان، الأردن",
    "contact_email": os.getenv("CONTACT_EMAIL", "").strip(),
    # Date the current legal texts take effect — change it when you edit them.
    "policies_updated": "2026-11-08",
}
