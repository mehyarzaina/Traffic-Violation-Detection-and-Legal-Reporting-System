"""
Gemini API service.
Extracts license plate number, car color, and car type from an image.
"""

import json
import os
from PIL import Image
import google.generativeai as genai

import os
from dotenv import load_dotenv

load_dotenv()



def configure_gemini(api_key: str):
    """Call once at startup with your Gemini API key."""
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))



def extract_vehicle_info(image: Image.Image) -> dict:
    """
    Send the image to Gemini and extract:
      - license_plate
      - car_color
      - car_type

    Returns a dict with those keys (values may be None if not found).
    """
    model = genai.GenerativeModel("gemini-3.1-flash-lite-preview")

    prompt = """        
        You are a traffic violation detection AI. Analyze this image. 

        Tasks:
        1. Extract license plate exactly (if visible), otherwise null.
        2. Identify car color.
        3. Identify car type.
        4. Return ONLY JSON in this format:

        {
        "license_plate": "",
        "car_color": "",
        "car_type": ""
        }
        Do NOT add extra text.
    """

    try:
        response = model.generate_content([prompt, image])
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        result = json.loads(raw)
        return {
            "license_plate": result.get("license_plate"),
            "car_color": result.get("car_color"),
            "car_type": result.get("car_type"),
        }

    except (json.JSONDecodeError, Exception) as e:
        print(f"[Gemini] Error: {e}")
        return {
            "license_plate": None,
            "car_color": None,
            "car_type": None,
        }