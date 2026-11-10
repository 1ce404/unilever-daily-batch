#!/usr/bin/env python3
"""Measure how much each cut's voice drifts between segments (user 11-10: some videos sound like two speakers).

    python3 scripts/voice_check.py <skill dir> <brand> <state.json> shared|all     # needs numpy + ffmpeg; run WITHOUT -I

The platform voices each segment separately. For every cut, the median pitch of each segment's TTS mp3 is
measured; spread = highest / lowest. ≥ 1.35 sounds like a different person. Prints one line per cut.
"""
import json, sys, subprocess, urllib.request, numpy as np
sys.path.insert(0, sys.argv[1] + "/scripts")
import clipo_batch as cb
from common import load_config

def f0(url):
    """Median voiced pitch (Hz) of one segment's TTS mp3, via autocorrelation on 40 ms frames."""
    pcm = subprocess.run(["ffmpeg", "-v", "quiet", "-i", url, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True).stdout
    x = np.frombuffer(pcm, dtype=np.float32)
    fr, hop, out = 640, 320, []
    for i in range(0, len(x) - fr, hop):
        w = x[i:i + fr]
        if np.sqrt((w ** 2).mean()) < 0.02: continue
        w = w - w.mean(); ac = np.correlate(w, w, "full")[fr - 1:]
        lo, hi = 16000 // 400, 16000 // 70
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.3 * ac[0]: out.append(16000 / k)
    return float(np.median(out)) if out else None

if __name__ == '__main__':
    brand, state, which = sys.argv[2], sys.argv[3], sys.argv[4]
    cfg = load_config(brand); cb.BASE = cfg["clipo"]["base_url"].rstrip("/") + "/api/v1"
    st = json.load(open(state))
    vids = [k for b, sh in st["shares"].items() for k in sh["videos"]] if which == "shared" else sorted(st["videos"])
    res = {}
    for vid in vids:
        v = st["videos"][vid]; c = cb.api("GET", f"/video-cuts/{v['cut_id']}")
        voc = sorted([x for x in c["draft"]["clips"] if x["trackId"] == "voiceover"], key=lambda x: x["from"])
        ps = [f0(x["props"]["src"]) for x in voc]
        good = [p for p in ps if p]
        spread = (max(good) / min(good)) if len(good) > 1 else 1
        res[vid] = {"speaker": c["random_result"].get("tts_speaker_id"), "f0": [round(p) if p else None for p in ps], "spread": round(spread, 2)}
        print(vid, res[vid]["speaker"][:34], res[vid]["f0"], res[vid]["spread"], flush=True)
    json.dump(res, open(f"/tmp/udb-audio/{brand}-{which}.json", "w"), ensure_ascii=False, indent=1)
