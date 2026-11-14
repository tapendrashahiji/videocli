"""Florence-2 layout backend: decoding loop (fake ONNX sessions), output parsing, setup wiring."""

from __future__ import annotations

from types import SimpleNamespace as NS

import numpy as np
import pytest

from videocli.layout import florence


def test_parse_ocr_and_od():
    txt = "</s>Helpful keymaps<loc_100><loc_200><loc_500><loc_200><loc_500><loc_260><loc_100><loc_260>r to reload<loc_110><loc_300><loc_300><loc_300><loc_300><loc_340><loc_110><loc_340></s>"
    lines = florence.parse_ocr(txt, 1000, 1000)
    assert [ln["text"] for ln in lines] == ["Helpful keymaps", "r to reload"]
    assert lines[0]["box"] == pytest.approx([0.1005, 0.2005, 0.4, 0.06], abs=1e-3)
    od = florence.parse_od("<s>person<loc_10><loc_20><loc_300><loc_900>laptop<loc_400><loc_500><loc_900><loc_950><loc_50><loc_60><loc_70><loc_80></s>", 640, 360)
    assert [o["label"] for o in od] == ["person", "laptop", "laptop"]
    assert od[0]["box"][0] == pytest.approx(0.0105, abs=1e-3) and od[0]["kind"] == "object"
    # spaces between location tokens are tolerated
    assert florence.parse_od("cat <loc_1> <loc_2> <loc_3> <loc_4>", 100, 100)[0]["label"] == "cat"


class Sess:
    def __init__(self, ins, outs, fn):
        self._ins = [NS(name=n, type="tensor(float)", shape=s) for n, s in ins]
        self._outs = [NS(name=n) for n in outs]
        self.fn = fn
        self.feeds = []

    def get_inputs(self):
        return self._ins

    def get_outputs(self):
        return self._outs

    def run(self, _names, feed):
        self.feeds.append(feed)
        return self.fn(feed)


VOCAB = {2: "</s>", 7: "Hi", 8: "<loc_100>", 9: "<loc_500>"}


class Tok:
    def encode(self, text):
        return NS(ids=[0, 5, 6, 2])

    def decode(self, ids, skip_special_tokens=False):
        return "".join(VOCAB.get(i, "") for i in ids)


def fake_model():
    D, H, Dh = 8, 2, 4
    script = [7] + [8, 8, 9, 8, 9, 9, 8, 9] + [2]  # "Hi" + 8 locations + eos
    state = {"step": 0}

    def dec(feed):
        k = state["step"]
        logits = np.zeros((1, 1, 16), np.float32)
        logits[0, 0, script[k]] = 1.0
        state["step"] += 1
        enc_len = feed["encoder_hidden_states"].shape[1]
        dec_len = feed["past_key_values.0.decoder.key"].shape[2] + 1
        # a merged decoder returns placeholder encoder values after the first step
        enc_kv = np.ones((1, H, enc_len if k == 0 else 0, Dh), np.float32)
        dkv = np.zeros((1, H, dec_len, Dh), np.float32)
        return [logits, dkv, dkv, enc_kv, enc_kv]

    past = [f"past_key_values.0.{w}.{kv}" for w in ("decoder", "encoder") for kv in ("key", "value")]
    sessions = {
        "vision_encoder": Sess([("pixel_values", [1, 3, 768, 768])], ["image_features"], lambda f: [np.zeros((1, 4, D), np.float32)]),
        "embed_tokens": Sess([("input_ids", ["b", "n"])], ["inputs_embeds"], lambda f: [np.zeros((1, f["input_ids"].shape[1], D), np.float32)]),
        "encoder_model": Sess([("inputs_embeds", None), ("attention_mask", None)], ["last_hidden_state"], lambda f: [np.zeros((1, f["inputs_embeds"].shape[1], D), np.float32)]),
        "decoder_model_merged": Sess(
            [("inputs_embeds", None), ("encoder_hidden_states", None), ("encoder_attention_mask", None), ("use_cache_branch", [1])]
            + [(n, ["b", H, "s", Dh]) for n in past],
            ["logits", "present.0.decoder.key", "present.0.decoder.value", "present.0.encoder.key", "present.0.encoder.value"], dec),
    }
    cfg = {"decoder_start_token_id": 2, "eos_token_id": 2, "text_config": {"decoder_attention_heads": H, "d_model": D}}
    pre = {"size": {"height": 768, "width": 768}, "rescale_factor": 1 / 255, "image_mean": [0.5] * 3, "image_std": [0.5] * 3}
    return florence.Florence2(sessions=sessions, tokenizer=Tok(), config=cfg, preprocessor=pre), sessions


def test_generate_greedy_with_cache():
    m, sessions = fake_model()
    out = m.generate(np.zeros((90, 160, 3), np.uint8), "<OCR_WITH_REGION>")
    assert out.startswith("Hi<loc_100>") and out.endswith("</s>")
    feeds = sessions["decoder_model_merged"].feeds
    assert len(feeds) == 10  # 9 tokens + eos
    assert feeds[0]["use_cache_branch"].tolist() == [False] and feeds[1]["use_cache_branch"].tolist() == [True]
    assert feeds[0]["past_key_values.0.decoder.key"].shape[2] == 0
    assert feeds[3]["past_key_values.0.decoder.key"].shape[2] == 3  # grows by one per step
    enc_len = feeds[0]["encoder_hidden_states"].shape[1]
    assert all(f["past_key_values.0.encoder.key"].shape[2] == enc_len for f in feeds[1:])  # kept from step 0
    assert feeds[0]["inputs_embeds"].shape[1] == 1
    px = sessions["vision_encoder"].feeds[0]["pixel_values"]
    assert px.shape == (1, 3, 768, 768) and px.min() == pytest.approx(-1.0)
    lines = florence.parse_ocr(out, 160, 90)
    assert lines and lines[0]["text"] == "Hi"


def test_reader_combines_text_and_objects(monkeypatch):
    class M:
        def generate(self, rgb, task):
            return "Hi<loc_1><loc_1><loc_9><loc_1><loc_9><loc_9><loc_1><loc_9>" if task == "<OCR_WITH_REGION>" else "dog<loc_1><loc_2><loc_3><loc_4>"

    got = florence.FlorenceReader(M()).read(np.zeros((10, 10, 3), np.uint8))
    assert {g["kind"] for g in got} == {"text", "object"}


def test_florence_backend_needs_model(cli, media_dir, monkeypatch):
    from conftest import ff

    p = media_dir / "flo.mp4"
    if not p.exists():
        ff("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
    cli.ok("config", "set", "layout.backend", "florence")
    code, env = cli("analyze", str(p), "--only", "layout")
    assert code != 0 and env["error"]["code"] == "MODEL_NOT_FOUND" and "--with objects" in env["error"]["hint"]


def test_setup_objects_plans_model_download(cli):
    d = cli.ok("setup", "--with", "objects", "--dry-run")
    assert any(a["kind"] == "model" and a["name"] == "florence-2-base" for a in d["actions"])
