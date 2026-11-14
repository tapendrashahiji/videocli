"""Text on screen: a backend reads one RGB frame and returns text boxes.

``rapidocr`` (PaddleOCR PP-OCR models on onnxruntime, the default) ships its detection and
English/Chinese recognition models inside the wheel; other scripts (``layout.ocr_lang``,
e.g. ``devanagari``) are fetched by RapidOCR on first use.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from videocli.layout.geometry import quad_to_box


class TextReader(Protocol):
    def read(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        """``[{"box": [x, y, w, h] fractions, "text": str, "score": 0-1}]``."""
        ...


LANGS = ("en", "ch", "devanagari", "latin", "arabic", "cyrillic", "japan", "korean", "ta", "te", "ka")


class RapidOCRReader:
    def __init__(self, lang: str = "en", min_score: float = 0.5):
        from rapidocr import RapidOCR

        params: dict[str, Any] = {"Global.log_level": "error", "Global.text_score": min_score}
        if lang and lang not in ("en", "ch"):
            from rapidocr import LangRec

            try:
                params["Rec.lang_type"] = LangRec(lang)
            except ValueError:
                params["Rec.lang_type"] = getattr(LangRec, lang.upper())
        self.engine = RapidOCR(params=params)
        self.min_score = min_score

    def read(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        H, W = rgb.shape[:2]
        res = self.engine(np.ascontiguousarray(rgb[:, :, ::-1]))  # BGR, as OpenCV loads images
        boxes = getattr(res, "boxes", None)
        if boxes is None:
            return []
        out = []
        for quad, text, score in zip(boxes, res.txts or (), res.scores or (), strict=False):
            if float(score) < self.min_score or not str(text).strip():
                continue
            out.append({"box": quad_to_box(quad, W, H), "text": str(text).strip(), "score": round(float(score), 3)})
        return out


#: Factory for the text reader; tests replace it.
def make_reader(backend: str, lang: str) -> TextReader:
    if backend == "florence":
        from videocli.layout.florence import FlorenceReader

        return FlorenceReader()
    return RapidOCRReader(lang)


def text_similarity(a: str, b: str) -> float:
    """0-1 similarity of two strings, ignoring case, spaces and punctuation."""
    import difflib
    import re

    def norm(s: str) -> str:
        return re.sub(r"[\W_]+", "", s.lower())

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return min(len(na), len(nb)) / max(len(na), len(nb)) if min(len(na), len(nb)) < 3 else 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()
