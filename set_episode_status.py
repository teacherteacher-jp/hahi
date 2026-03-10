#!/usr/bin/env python3
"""スプレッドシートからエピソードのはひステータスを取得し、各ディレクトリに空ファイルとして配置する"""

import csv
import io
import sys
from pathlib import Path
from urllib.request import urlopen, Request

TSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vTJk0pMmtDzhq-VzkIjcZ94ApBKAJ4VO6S-FBe6xHPkhjoD8W9k97wqOFg4CTfbhulB0djWbYK7fj7g"
    "/pub?gid=468602790&single=true&output=tsv"
)
EPISODES_DIR = Path(__file__).parent / "episodes"


def fetch_tsv():
    req = Request(TSV_URL, headers={"User-Agent": "hahi/0.1"})
    with urlopen(req) as resp:
        return resp.read().decode("utf-8")


def parse_tsv(text):
    """TSVをパースして {episode_id: status} の辞書を返す"""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    entries = {}
    for row in reader:
        link = row.get("リンク", "").strip()
        status = row.get("はひステータス", "").strip()
        if not link or not status:
            continue
        episode_id = link.rstrip("/").split("/")[-1]
        entries[episode_id] = status
    return entries


def find_episode_dir(episode_id):
    """episode_idに対応するディレクトリを探す"""
    for d in EPISODES_DIR.iterdir():
        if d.is_dir() and d.name.endswith(f"-{episode_id}"):
            return d
    return None


def all_statuses(entries):
    """TSVに出現する全ステータス値の集合"""
    return set(entries.values())


def main():
    print(f"Fetching TSV: {TSV_URL[:60]}...")
    text = fetch_tsv()

    entries = parse_tsv(text)
    print(f"Found {len(entries)} episodes in spreadsheet")

    statuses = all_statuses(entries)
    print(f"Statuses: {statuses}")

    updated = 0
    not_found = 0

    for episode_id, status in entries.items():
        ep_dir = find_episode_dir(episode_id)
        if not ep_dir:
            print(f"  dir not found: {episode_id}", file=sys.stderr)
            not_found += 1
            continue

        # 既存のステータスファイルを削除
        for s in statuses:
            old = ep_dir / s
            if old.exists():
                old.unlink()

        # 新しいステータスファイルを配置
        (ep_dir / status).touch()
        updated += 1

    print(f"\nDone: {updated} updated, {not_found} not found")


if __name__ == "__main__":
    main()
