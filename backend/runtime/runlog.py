"""Per-run stats collection at death, before Retry.

The GAME STATS dialog offers MORE STATS (whose Round Stats panel has a COPY
button -> full tab-separated stats land on the Windows clipboard via MuMu
clipboard sync - exact values, no OCR) and PERKS (captured as frames and
OCR'd offline). Everything is written to runs/<account>/<stamp>/run.md.

Blocking is fine here: the tower is dead, nothing else needs monitoring.
"""
import datetime
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from settings import CONFIG, ROOT, adb_args, input_args, run_hidden
from device import capture
from device import act
from vision import detect
from runtime import logger

MORE_STATS = (360, 1352)
PERKS = (719, 1352)
COPY_BTN = (906, 1951)      # copy icon on the Round Stats panel
STATS_X = (905, 583)        # close X on Round Stats
PERKS_X = (934, 478)        # close X on Perks


def _clipboard() -> str:
    p = run_hidden(["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard"],
                   capture_output=True, text=True, timeout=30)
    return p.stdout or ""


def _tap(x, y, reason):
    try:
        act.tap(x, y, reason=reason, instant=True)
    except act.TapRefused:
        raise RuntimeError(f"tap refused during runlog ({reason})")


def _write_md(out: Path, account: str, stats_tsv: str):
    md = [f"# Run log - {account} - {out.name}", "",
          f"- Account: {account}", "- Source: Round Stats copy button", ""]
    if stats_tsv.strip():
        md.append("## Round Stats")
        for line in stats_tsv.splitlines():
            line = line.rstrip()
            if not line.strip():
                continue
            if "\t" in line:
                k, _, v = line.partition("\t")
                md.append(f"| {k.strip()} | {v.strip()} |")
            else:
                md += ["", f"### {line.strip()}", "", "| Stat | Value |",
                       "|---|---|"]
    md += ["", "## Perks", "",
           "(see frame_*_perks.png - OCR/transcribe during analysis)", ""]
    (out / "run.md").write_text("\n".join(md), encoding="utf-8")


def collect(account: str) -> Path | None:
    """Run the collection. Call ONLY when the GAME STATS dialog is visible.
    Returns the run directory, or None on failure (never raises)."""
    try:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = ROOT / "runs" / account / stamp
        out.mkdir(parents=True, exist_ok=True)
        frame = capture.grab()
        cv2.imwrite(str(out / "frame_001_gamestats.png"), frame)

        # ---- Round Stats via the copy button (exact values)
        _tap(*MORE_STATS, "runlog_more_stats")
        time.sleep(1.4)
        frame = capture.grab()
        cv2.imwrite(str(out / "frame_002_morestats.png"), frame)
        _tap(*COPY_BTN, "runlog_copy")
        time.sleep(0.9)
        stats = _clipboard()
        (out / "round_stats.txt").write_text(stats, encoding="utf-8")
        _tap(*STATS_X, "runlog_close_stats")
        time.sleep(1.0)

        # ---- Perks: capture + drag-scroll until the list stops moving
        _tap(*PERKS, "runlog_perks")
        time.sleep(1.4)
        prev = None
        for i in range(3, 8):
            frame = capture.grab()
            g = cv2.cvtColor(frame[420:1630, 60:980], cv2.COLOR_BGR2GRAY)
            if prev is not None and float(np.mean(cv2.absdiff(prev, g))) < 2.0:
                break
            prev = g
            cv2.imwrite(str(out / f"frame_{i:03d}_perks.png"), frame)
            act.swipe(540, 1500, 540, 800, 350, reason="perk scroll")
            time.sleep(1.0)
        _tap(*PERKS_X, "runlog_close_perks")
        time.sleep(0.8)

        _write_md(out, account, stats)
        logger.event("runlog", dir=str(out),
                     stats_lines=len(stats.splitlines()))
        return out
    except Exception as e:
        logger.event("runlog_error", error=str(e))
        return None
