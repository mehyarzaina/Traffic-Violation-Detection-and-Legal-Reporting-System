"""
Time slots weighted by traffic density.
Rush hours (7-9 AM, 4-7 PM) have weight 10 → chosen most frequently.
"""

import random
from datetime import datetime

TIME_SLOTS = [
    {"hour": 0,  "label": "Midnight",        "weight": 1},
    {"hour": 1,  "label": "Late Night",      "weight": 1},
    {"hour": 2,  "label": "Late Night",      "weight": 1},
    {"hour": 3,  "label": "Pre-Dawn",        "weight": 1},
    {"hour": 4,  "label": "Pre-Dawn",        "weight": 2},
    {"hour": 5,  "label": "Early Morning",   "weight": 3},
    {"hour": 6,  "label": "Morning Rush",    "weight": 7},
    {"hour": 7,  "label": "Morning Rush",    "weight": 10},
    {"hour": 8,  "label": "Morning Rush",    "weight": 10},
    {"hour": 9,  "label": "Morning Rush",    "weight": 8},
    {"hour": 10, "label": "Mid-Morning",     "weight": 5},
    {"hour": 11, "label": "Mid-Morning",     "weight": 5},
    {"hour": 12, "label": "Lunch Hour",      "weight": 7},
    {"hour": 13, "label": "Early Afternoon", "weight": 6},
    {"hour": 14, "label": "Afternoon",       "weight": 5},
    {"hour": 15, "label": "Afternoon",       "weight": 5},
    {"hour": 16, "label": "Evening Rush",    "weight": 9},
    {"hour": 17, "label": "Evening Rush",    "weight": 10},
    {"hour": 18, "label": "Evening Rush",    "weight": 10},
    {"hour": 19, "label": "Evening Rush",    "weight": 8},
    {"hour": 20, "label": "Evening",         "weight": 5},
    {"hour": 21, "label": "Evening",         "weight": 4},
    {"hour": 22, "label": "Night",           "weight": 3},
    {"hour": 23, "label": "Late Night",      "weight": 2},
]


def get_weighted_timestamp() -> datetime:
    """Return today's date with a traffic-weighted random hour/minute/second."""
    weights = [s["weight"] for s in TIME_SLOTS]
    slot = random.choices(TIME_SLOTS, weights=weights, k=1)[0]
    now = datetime.now()
    return now.replace(
        hour=slot["hour"],
        minute=random.randint(0, 59),
        second=random.randint(0, 59),
        microsecond=0,
    )


def get_time_label(hour: int) -> str:
    for slot in TIME_SLOTS:
        if slot["hour"] == hour:
            return slot["label"]
    return "Unknown"