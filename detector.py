"""
Violation detector — runs both YOLO models on a PIL image.
Returns detected violation names AND an annotated PIL image with bounding boxes.
"""

import io
from PIL import Image, ImageDraw, ImageFont
from typing import List, Tuple, Optional

WRONG_WAY_MODEL_PATH     = "models/wrong_way_driving.pt"
WRONG_PARKING_MODEL_PATH = "models/wrong_parking.pt"

CONFIDENCE_THRESHOLD = 0.75

# Colours for each violation type (BGR-style hex for consistency with web)
VIOLATION_COLORS = {
    "Wrong Way Driving": "#ef4444",   # red
    "Wrong Parking":     "#eab308",   # amber
}
DEFAULT_COLOR = "#3b82f6"

_wrong_way_model     = None
_wrong_parking_model = None


def _load_model(path: str):
    try:
        from ultralytics import YOLO
        return YOLO(path)
    except Exception as e:
        print(f"[Detector] Could not load '{path}': {e}")
        return None


def _get_wrong_way_model():
    global _wrong_way_model
    if _wrong_way_model is None:
        _wrong_way_model = _load_model(WRONG_WAY_MODEL_PATH)
    return _wrong_way_model


def _get_wrong_parking_model():
    global _wrong_parking_model
    if _wrong_parking_model is None:
        _wrong_parking_model = _load_model(WRONG_PARKING_MODEL_PATH)
    return _wrong_parking_model


def _run_model(model, image: Image.Image, label: str) -> Tuple[bool, list]:
    """
    Returns (detected: bool, boxes: list of dicts with x1,y1,x2,y2,conf,label).
    """
    if model is None:
        print(f"[Detector] {label} model unavailable — skipping.")
        return False, []
    try:
        results = model(image, verbose=False)
        boxes = []
        detected = False
        for result in results:
            if result.boxes is not None:
                for box in result.boxes:
                    conf = float(box.conf[0])
                    if conf >= CONFIDENCE_THRESHOLD:
                        detected = True
                        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
                        boxes.append({
                            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                            "conf": conf,
                            "label": label,
                        })
        return detected, boxes
    except Exception as e:
        print(f"[Detector] Error running {label}: {e}")
        return False, []


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def annotate_image(image: Image.Image, all_boxes: list) -> Image.Image:
    """
    Draw bounding boxes with labels onto a copy of the image.
    Returns the annotated PIL Image.
    """
    annotated = image.copy().convert("RGBA")
    overlay   = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw_ov   = ImageDraw.Draw(overlay)
    draw_img  = ImageDraw.Draw(annotated)

    # Try to load a decent font; fall back to default
    try:
        font       = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
    except Exception:
        font       = ImageFont.load_default()
        font_small = font

    for box in all_boxes:
        x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
        label = box["label"]
        conf  = box["conf"]
        color_hex = VIOLATION_COLORS.get(label, DEFAULT_COLOR)
        r, g, b   = _hex_to_rgb(color_hex)

        # Semi-transparent fill
        draw_ov.rectangle([x1, y1, x2, y2], fill=(r, g, b, 40))

        # Solid border (4px)
        for offset in range(3):
            draw_img.rectangle(
                [x1 - offset, y1 - offset, x2 + offset, y2 + offset],
                outline=(r, g, b),
            )

        # Label pill background
        text       = f"{label}  {conf:.0%}"
        bbox_text  = draw_img.textbbox((0, 0), text, font=font)
        tw         = bbox_text[2] - bbox_text[0]
        th         = bbox_text[3] - bbox_text[1]
        pad        = 6
        pill_x1    = x1
        pill_y1    = max(0, y1 - th - pad * 2 - 2)
        pill_x2    = x1 + tw + pad * 2
        pill_y2    = max(0, y1 - 2)

        draw_img.rectangle([pill_x1, pill_y1, pill_x2, pill_y2], fill=(r, g, b))
        draw_img.text((pill_x1 + pad, pill_y1 + pad // 2), text, fill=(255, 255, 255), font=font)

        # Corner tick marks for a "targeting" look
        tick = 18
        corners = [
            (x1, y1, x1 + tick, y1, x1, y1 + tick),
            (x2, y1, x2 - tick, y1, x2, y1 + tick),
            (x1, y2, x1 + tick, y2, x1, y2 - tick),
            (x2, y2, x2 - tick, y2, x2, y2 - tick),
        ]
        for cx, cy, ex1, ey1, ex2, ey2 in corners:
            draw_img.line([(cx, cy), (ex1, ey1)], fill=(255, 255, 255), width=3)
            draw_img.line([(cx, cy), (ex2, ey2)], fill=(255, 255, 255), width=3)

    # Merge overlay
    annotated = Image.alpha_composite(annotated, overlay).convert("RGB")
    return annotated


def detect_violations(image: Image.Image) -> Tuple[List[str], Optional[Image.Image]]:
    """
    Returns:
      (violation_names, annotated_image)

    - violation_names: list of detected violation strings, e.g. ["Wrong Way Driving"]
    - annotated_image: PIL Image with bounding boxes drawn, or None if no violations
    """
    detected   = []
    all_boxes  = []

    ww_detected, ww_boxes = _run_model(_get_wrong_way_model(), image, "Wrong Way Driving")
    if ww_detected:
        detected.append("Wrong Way Driving")
        all_boxes.extend(ww_boxes)

    wp_detected, wp_boxes = _run_model(_get_wrong_parking_model(), image, "Wrong Parking")
    if wp_detected:
        detected.append("Wrong Parking")
        all_boxes.extend(wp_boxes)

    annotated_img = annotate_image(image, all_boxes) if all_boxes else None
    return detected, annotated_img