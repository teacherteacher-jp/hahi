#!/usr/bin/env python3
"""LISTENのRSSフィードからTeacher Teacherの全エピソードのVTT書き起こしを取得する"""

import sys
import xml.etree.ElementTree as ET
from datetime import timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.request import urlopen, Request

JST = timezone(timedelta(hours=9))

FEED_URL = "https://rss.listen.style/p/teacherteacher/rss"
EPISODES_DIR = Path(__file__).parent / "episodes"

NAMESPACES = {
    "podcast": "https://podcastindex.org/namespace/1.0",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}


def fetch_feed():
    req = Request(FEED_URL, headers={"User-Agent": "hahi/0.1"})
    with urlopen(req) as resp:
        return resp.read()


def parse_episodes(xml_bytes):
    root = ET.fromstring(xml_bytes)
    episodes = []

    for item in root.findall(".//item"):
        link = item.findtext("link", "")
        episode_id = link.rstrip("/").split("/")[-1] if link else None
        if not episode_id:
            continue

        pub_date_str = item.findtext("pubDate", "")
        try:
            pub_date = parsedate_to_datetime(pub_date_str)
        except Exception:
            print(f"  skip: pubDate parse error: {pub_date_str}", file=sys.stderr)
            continue

        transcript_el = item.find("podcast:transcript", NAMESPACES)
        transcript_url = transcript_el.get("url") if transcript_el is not None else None

        title = item.findtext("title", "")
        description = item.findtext("description", "")
        duration = item.findtext("itunes:duration", "", NAMESPACES)

        episodes.append({
            "id": episode_id,
            "title": title,
            "pub_date": pub_date,
            "transcript_url": transcript_url,
            "description": description,
            "duration": duration,
        })

    return episodes


def episode_dir_name(episode):
    ts = episode["pub_date"].astimezone(JST).strftime("%Y%m%d_%H%M")
    return f"{ts}-{episode['id']}"


def build_metadata_md(ep):
    pub_jst = ep["pub_date"].astimezone(JST)
    link = f"https://listen.style/p/teacherteacher/{ep['id']}"

    fm_lines = [
        "---",
        f"title: \"{ep['title']}\"",
        f"date: \"{pub_jst.strftime('%Y-%m-%d %H:%M')} JST\"",
    ]
    if ep["duration"]:
        fm_lines.append(f"duration: \"{ep['duration']}\"")
    fm_lines += [
        f"listen: \"{link}\"",
        "---",
    ]

    return "\n".join(fm_lines) + "\n\n" + ep["description"] + "\n"


def save_metadata(ep, dest):
    content = build_metadata_md(ep)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")


def download_vtt(url, dest):
    req = Request(url, headers={"User-Agent": "hahi/0.1"})
    with urlopen(req) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def main():
    print(f"Fetching RSS feed: {FEED_URL}")
    xml_bytes = fetch_feed()

    episodes = parse_episodes(xml_bytes)
    print(f"Found {len(episodes)} episodes")

    episodes.sort(key=lambda e: e["pub_date"])

    downloaded = 0
    no_vtt = 0

    for ep in episodes:
        dir_name = episode_dir_name(ep)
        ep_dir = EPISODES_DIR / dir_name
        vtt_path = ep_dir / "transcript.vtt"
        meta_path = ep_dir / "metadata.md"

        save_metadata(ep, meta_path)

        if not ep["transcript_url"]:
            print(f"  no VTT: {ep['title']}")
            no_vtt += 1
            continue

        try:
            size = download_vtt(ep["transcript_url"], vtt_path)
            downloaded += 1
            print(f"  [{downloaded}] {dir_name} ({size:,} bytes)")
        except Exception as e:
            print(f"  ERROR: {dir_name}: {e}", file=sys.stderr)

    print(f"\nDone: {downloaded} downloaded, {no_vtt} no VTT")


if __name__ == "__main__":
    main()
