#!/usr/bin/env python3
"""整備後のVTTが原本の構造を保っているかを機械検証する"""

import re

TIMESTAMP_RE = re.compile(r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})\.(\d{3})$")
SPEAKER_TAG_RE = re.compile(r"^<v ([^>]+)>\s*(.*)$", re.DOTALL)


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
