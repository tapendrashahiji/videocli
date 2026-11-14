"""Florence-2 (Microsoft, MIT) on onnxruntime: text with regions and objects, no PyTorch.

Uses the ONNX export ``onnx-community/Florence-2-base-ft`` (vision encoder, token embedding,
text encoder and a merged decoder with key/value cache), the BART tokenizer through
``tokenizers`` (installed with faster-whisper) and greedy decoding. Two tasks per keyframe:

* ``<OCR_WITH_REGION>`` → text lines with quadrilateral boxes;
* ``<OD>`` → object labels with boxes.

Coordinates come back as ``<loc_0>``–``<loc_999>`` tokens: bins of 1/1000 of the image.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from videocli.analyze.base import Availability
from videocli.core.errors import DependencyError

REPO = "onnx-community/Florence-2-base-ft"
NAME = "florence-2-base"
VARIANTS = {"fp32": "", "quantized": "_quantized", "fp16": "_fp16"}
PARTS = ("vision_encoder", "embed_tokens", "encoder_model", "decoder_model_merged")
PROMPTS = {
    "<OCR_WITH_REGION>": "What is the text in the image, with regions?",
    "<OD>": "Locate the objects with category name in the image.",
}
MAX_NEW_TOKENS = 512
SIZE_MB = {"quantized": 290, "fp16": 560, "fp32": 1100}


def model_dir() -> Path:
    from videocli.setup.profiles import models_dir

    return models_dir() / NAME


def variant() -> str:
    try:
        from videocli.cli.state import state

        v = str(state.settings.layout__florence_variant)
    except Exception:  # noqa: BLE001 - settings unavailable: use the default
        v = "quantized"
    return v if v in VARIANTS else "quantized"


def files(var: str | None = None) -> list[str]:
    var = var or variant()
    return [f"onnx/{p}{VARIANTS[var]}.onnx" for p in PARTS] + ["tokenizer.json", "config.json", "preprocessor_config.json"]


def present(var: str | None = None) -> bool:
    d = model_dir()
    return all((d / f).is_file() for f in files(var))


def availability() -> Availability:
    if not present():
        return Availability(False, DependencyError(
            "MODEL_NOT_FOUND", f"The Florence-2 model ({variant()}) is not downloaded.",
            hint="Run: videocli setup --with objects", details={"model": NAME}))
    return Availability.yes()


def download(var: str | None = None) -> Path:
    """Fetch the ONNX files with live progress (``videocli setup --with objects``)."""
    from videocli.core.errors import RuntimeFailure
    from videocli.setup import live

    var = var or variant()
    target = model_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        import warnings

        from huggingface_hub import snapshot_download

        def fetch() -> None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                snapshot_download(REPO, local_dir=str(target), allow_patterns=files(var))

        live.watch_download(fetch, target, SIZE_MB[var] * 1024**2, extra=live.hf_cache_dirs())
    except Exception as exc:  # network, proxy, disk
        raise RuntimeFailure("DOWNLOAD_FAILED", f"Could not download Florence-2 ({var}): {exc}",
                             hint="Check the network (huggingface.co must be reachable), then re-run: videocli setup --with objects",
                             retryable=True) from exc
    return target


# --------------------------------------------------------------------------- parsing

_LOC = re.compile(r"<loc_(\d+)>")


def _strip(text: str) -> str:
    text = text.replace("</s>", "").replace("<s>", "").replace("<pad>", "")
    return re.sub(r">\s+<loc_", "><loc_", text)  # some tokenizers put spaces between added tokens


def _coord(v: int, size: int) -> float:
    return (v + 0.5) / 1000 * size


def parse_ocr(text: str, W: int, H: int) -> list[dict[str, Any]]:
    """``label<loc_x1><loc_y1>…<loc_y4>`` repeated → text lines with boxes (fractions)."""
    from videocli.layout.geometry import quad_to_box

    out = []
    for m in re.finditer(r"([^<]*)((?:<loc_\d+>){8})", _strip(text)):
        label = m.group(1).strip()
        locs = [int(v) for v in _LOC.findall(m.group(2))]
        if not label:
            continue
        quad = [(_coord(locs[i], W), _coord(locs[i + 1], H)) for i in range(0, 8, 2)]
        out.append({"kind": "text", "box": quad_to_box(quad, W, H), "text": label, "score": 1.0})
    return out


def parse_od(text: str, W: int, H: int) -> list[dict[str, Any]]:
    """``label<loc_x1><loc_y1><loc_x2><loc_y2>`` (several boxes may follow one label) → objects."""
    from videocli.layout.geometry import clamp_box

    out = []
    last = ""
    for m in re.finditer(r"([^<]*)((?:<loc_\d+>){4,})", _strip(text)):
        label = m.group(1).strip() or last
        last = label
        locs = [int(v) for v in _LOC.findall(m.group(2))]
        for i in range(0, len(locs) - 3, 4):
            x1, y1, x2, y2 = (_coord(locs[i], W), _coord(locs[i + 1], H), _coord(locs[i + 2], W), _coord(locs[i + 3], H))
            if label:
                out.append({"kind": "object", "label": label, "box": clamp_box([x1 / W, y1 / H, (x2 - x1) / W, (y2 - y1) / H])})
    return out


# --------------------------------------------------------------------------- model


def _ort_type(t: str) -> Any:
    return np.float16 if "float16" in t else np.float32


class Florence2:
    """The four ONNX sessions plus tokenizer; ``generate(image, task)`` returns the raw text."""

    def __init__(self, path: Path | None = None, var: str | None = None, sessions: dict[str, Any] | None = None,
                 tokenizer: Any = None, config: dict[str, Any] | None = None, preprocessor: dict[str, Any] | None = None):
        path = path or model_dir()
        var = var or variant()
        if sessions is None:
            import onnxruntime as ort

            providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in ort.get_available_providers()]
            sessions = {p: ort.InferenceSession(str(path / "onnx" / f"{p}{VARIANTS[var]}.onnx"), providers=providers) for p in PARTS}
        if tokenizer is None:
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(path / "tokenizer.json"))
        self.s = sessions
        self.tok = tokenizer
        self.config = config if config is not None else json.loads((path / "config.json").read_text())
        self.pre = preprocessor if preprocessor is not None else json.loads((path / "preprocessor_config.json").read_text())
        tc = self.config.get("text_config", self.config)
        self.start_id = int(self.config.get("decoder_start_token_id", tc.get("decoder_start_token_id", 2)))
        self.eos_id = int(self.config.get("eos_token_id", tc.get("eos_token_id", 2)))
        dec = self.s["decoder_model_merged"]
        self.dec_inputs = {i.name: i for i in dec.get_inputs()}
        self.past_names = [n for n in self.dec_inputs if n.startswith("past_key_values")]
        heads = int(tc.get("decoder_attention_heads", 12))
        dim = int(tc.get("d_model", 768)) // heads
        self.kv_shape = (heads, dim)
        for n in self.past_names:  # prefer the export's own numbers when they are fixed
            shp = self.dec_inputs[n].shape
            if len(shp) == 4 and isinstance(shp[1], int) and isinstance(shp[3], int):
                self.kv_shape = (shp[1], shp[3])
                break

    def pixels(self, rgb: np.ndarray) -> np.ndarray:
        from PIL import Image

        size = self.pre.get("size", {"height": 768, "width": 768})
        h, w = int(size.get("height", 768)), int(size.get("width", 768))
        img = Image.fromarray(rgb).convert("RGB").resize((w, h), Image.BICUBIC)
        x = np.asarray(img, dtype=np.float32) * float(self.pre.get("rescale_factor", 1 / 255))
        mean = np.asarray(self.pre.get("image_mean", [0.485, 0.456, 0.406]), dtype=np.float32)
        std = np.asarray(self.pre.get("image_std", [0.229, 0.224, 0.225]), dtype=np.float32)
        x = (x - mean) / std
        return x.transpose(2, 0, 1)[None]

    def _embed(self, ids: list[int]) -> np.ndarray:
        sess = self.s["embed_tokens"]
        return sess.run(None, {sess.get_inputs()[0].name: np.asarray([ids], dtype=np.int64)})[0]

    def generate(self, rgb: np.ndarray, task: str, max_new_tokens: int = MAX_NEW_TOKENS) -> str:
        vis = self.s["vision_encoder"]
        vin = vis.get_inputs()[0]
        image_features = vis.run(None, {vin.name: self.pixels(rgb).astype(_ort_type(vin.type))})[0]
        prompt_ids = self.tok.encode(PROMPTS.get(task, task)).ids
        text_embeds = self._embed(prompt_ids)
        embeds = np.concatenate([image_features.astype(text_embeds.dtype), text_embeds], axis=1)
        mask = np.ones(embeds.shape[:2], dtype=np.int64)
        enc = self.s["encoder_model"]
        enc_names = {i.name for i in enc.get_inputs()}
        feed = {"inputs_embeds": embeds} if "inputs_embeds" in enc_names else {}
        if "attention_mask" in enc_names:
            feed["attention_mask"] = mask
        hidden = enc.run(None, feed)[0]

        heads, dim = self.kv_shape
        dtype = embeds.dtype
        past = {n: np.zeros((1, heads, 0, dim), dtype=_ort_type(self.dec_inputs[n].type)) for n in self.past_names}
        dec = self.s["decoder_model_merged"]
        out_names = [o.name for o in dec.get_outputs()]
        ids = [self.start_id]
        for step in range(max_new_tokens):
            feed = {"inputs_embeds": self._embed(ids[-1:]).astype(dtype), "encoder_hidden_states": hidden,
                    "encoder_attention_mask": mask, **past}
            if "use_cache_branch" in self.dec_inputs:
                feed["use_cache_branch"] = np.asarray([step > 0], dtype=bool)
            feed = {k: v for k, v in feed.items() if k in self.dec_inputs}
            outs = dict(zip(out_names, dec.run(None, feed), strict=True))
            nxt = int(np.argmax(outs["logits"][0, -1]))
            ids.append(nxt)
            if nxt == self.eos_id:
                break
            for n in self.past_names:
                pres = n.replace("past_key_values", "present")
                if pres not in outs:
                    continue
                if ".encoder." in n and step > 0:
                    continue  # the encoder's keys and values are computed once, on the first step
                past[n] = outs[pres]
        return self.tok.decode(ids[1:], skip_special_tokens=False)


class FlorenceReader:
    """Layout reader: text lines (OCR with regions) and objects (detection) per keyframe."""

    provenance = "florence-2-base:onnx"

    def __init__(self, model: Florence2 | None = None):
        self.model = model or Florence2()

    def read(self, rgb: np.ndarray) -> list[dict[str, Any]]:
        H, W = rgb.shape[:2]
        text = parse_ocr(self.model.generate(rgb, "<OCR_WITH_REGION>"), W, H)
        objects = parse_od(self.model.generate(rgb, "<OD>"), W, H)
        return text + objects
