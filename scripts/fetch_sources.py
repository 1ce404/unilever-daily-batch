#!/usr/bin/env python3
"""Pull today's sources from Feishu into a run directory.

    python3 scripts/fetch_sources.py --config qingyang --run runs/20261030

Writes sources/{schedule,recent_scripts,qc}.txt (history_scripts only with --history, it is ~1.7MB).
"""
import argparse
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import lark_fetch, load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--history", action="store_true", help="also fetch the historical script collection")
    args = ap.parse_args()

    cfg = load_config(args.config)
    out = Path(args.run) / "sources"
    out.mkdir(parents=True, exist_ok=True)
    keys = ["schedule", "recent_scripts", "qc"] + (["history_scripts"] if args.history else [])
    for key in keys:
        text = lark_fetch(cfg["sources"][key])
        (out / f"{key}.txt").write_text(text)
        print(f"{key}: {len(text)} chars -> {out / (key + '.txt')}")


if __name__ == "__main__":
    main()
