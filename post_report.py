#!/usr/bin/env python3
"""整備レポートをDiscordのスレッドにWebhookで投稿する

環境変数:
    HAHI_DISCORD_WEBHOOK_URL  Webhook URL (必須)
    HAHI_DISCORD_THREAD_ID    投稿先スレッドID (任意)
"""

import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

LIMIT = 1900  # Discordの2000字制限に対する余裕


def split_message(text, limit=LIMIT):
    """行境界を優先してlimit以内のチャンクに分割する"""
    chunks = []
    current = ""

    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]

        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def post(webhook_url, content):
    req = Request(
        webhook_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "hahi/0.1"},
    )
    with urlopen(req) as resp:
        resp.read()


def main():
    url = os.environ.get("HAHI_DISCORD_WEBHOOK_URL")
    if not url:
        sys.exit("環境変数 HAHI_DISCORD_WEBHOOK_URL が未設定")

    thread_id = os.environ.get("HAHI_DISCORD_THREAD_ID")
    if thread_id:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}thread_id={thread_id}"

    if len(sys.argv) > 1:
        text = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    chunks = split_message(text)
    if not chunks:
        sys.exit("投稿する内容が空")

    for chunk in chunks:
        post(url, chunk)
    print(f"投稿完了 ({len(chunks)}件)")


if __name__ == "__main__":
    main()
