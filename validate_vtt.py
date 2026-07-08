#!/usr/bin/env python3
"""整備後のVTTが原本の構造を保っているかを機械検証する"""

import argparse
import difflib
import re
import sys
from pathlib import Path

TIMESTAMP_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})$")
SPEAKER_TAG_RE = re.compile(r"^<v ([^>]+)>\s*(.*)$", re.DOTALL)
FULL_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


def parse_timestamp(ts):
    """"MM:SS.mmm" または "HH:MM:SS.mmm" をミリ秒に変換する"""
    m = TIMESTAMP_RE.match(ts)
    if not m:
        raise ValueError(f"不正なタイムスタンプ: {ts!r}")
    hours = int(m.group(1) or 0)
    minutes, seconds, millis = int(m.group(2)), int(m.group(3)), int(m.group(4))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_vtt(text):
    """VTTをcueのリストにパースする"""
    if not text.lstrip().startswith("WEBVTT"):
        raise ValueError("WEBVTTヘッダーがない")

    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []

    for block in blocks[1:]:
        lines = block.strip().split("\n")
        # cue識別子付きの形式にも耐える (LISTENのVTTには通常ないが)
        if "-->" not in lines[0] and len(lines) > 1 and "-->" in lines[1]:
            lines = lines[1:]
        if "-->" not in lines[0]:
            raise ValueError(f"タイムスタンプ行のないcueブロック: {lines[0][:50]!r}")

        start_raw, _, end_raw = (p.strip() for p in lines[0].partition("-->"))
        body = "\n".join(lines[1:]).strip()

        speaker = None
        cue_text = body
        m = SPEAKER_TAG_RE.match(body)
        if m:
            speaker = m.group(1)
            cue_text = m.group(2).strip()

        cues.append({
            "start_raw": start_raw,
            "end_raw": end_raw,
            "start_ms": parse_timestamp(start_raw),
            "end_ms": parse_timestamp(end_raw),
            "speaker": speaker,
            "text": cue_text,
        })

    return cues


def validate(original_text, edited_text, allowed_speakers, min_ratio=0.5):
    """整備後VTTを原本と比較し、問題のリストを返す (空 = 合格)"""
    try:
        original = parse_vtt(original_text)
    except ValueError as e:
        return [f"原本VTTのパースに失敗: {e}"]
    try:
        edited = parse_vtt(edited_text)
    except ValueError as e:
        return [f"整備後VTTのパースに失敗: {e}"]

    if len(original) != len(edited):
        return [f"cue数が変わっている: {len(original)} -> {len(edited)}"]

    allowed = set(allowed_speakers)
    issues = []

    for i, (o, e) in enumerate(zip(original, edited), start=1):
        label = f"cue {i} ({e['start_raw']})"

        if o["start_ms"] != e["start_ms"] or o["end_ms"] != e["end_ms"]:
            issues.append(
                f"{label}: タイムスタンプが変わっている "
                f"{o['start_raw']} --> {o['end_raw']} が "
                f"{e['start_raw']} --> {e['end_raw']} に"
            )
        if not (FULL_TIMESTAMP_RE.match(e["start_raw"])
                and FULL_TIMESTAMP_RE.match(e["end_raw"])):
            issues.append(f"{label}: タイムスタンプがHH:MM:SS.mmm形式でない")
        if e["speaker"] is None:
            issues.append(f"{label}: 話者タグがない")
        elif e["speaker"] not in allowed:
            issues.append(f"{label}: 話者 {e['speaker']!r} がallowlist外")
        if not e["text"]:
            issues.append(f"{label}: テキストが空")
        elif difflib.SequenceMatcher(None, o["text"], e["text"]).ratio() < min_ratio:
            issues.append(
                f"{label}: 変更量が閾値超え (原文: {o['text'][:30]!r})"
            )

    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("edited", type=Path)
    parser.add_argument("--speakers", required=True,
                        help="許可する話者名 (カンマ区切り)")
    parser.add_argument("--min-ratio", type=float, default=0.5,
                        help="cueごとのテキスト類似度の下限 (default: 0.5)")
    args = parser.parse_args()

    issues = validate(
        args.original.read_text(encoding="utf-8"),
        args.edited.read_text(encoding="utf-8"),
        [s.strip() for s in args.speakers.split(",") if s.strip()],
        min_ratio=args.min_ratio,
    )

    if issues:
        for issue in issues:
            print(f"NG: {issue}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
