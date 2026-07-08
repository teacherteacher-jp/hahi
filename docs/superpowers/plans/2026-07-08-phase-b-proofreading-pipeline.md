# フェーズB: LLM文字起こし整備パイプライン 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude Codeセッションで1エピソードずつ「再取得→LLM校正→機械検証→レポート→Discord通知→記録」を回せる対話型パイプラインを作る。

**Architecture:** リポジトリ既存のスタイル (依存ゼロ・stdlibのみ・フラットな単機能Pythonスクリプト) に合わせる。決定的な処理 (検証・投稿) はPythonスクリプト、判断を伴う処理 (校正・レポート執筆) はスキル `/seibi` がサブエージェントを指揮して行う。

**Tech Stack:** Python 3.13 (stdlibのみ、テストはunittest)、Claude Codeスキル、gws CLI (スプレッドシート)、Discord Webhook。

**Spec:** `docs/superpowers/specs/2026-07-08-llm-proofreading-workflow-design.md`

## Global Constraints

- 外部パッケージを追加しない。Python標準ライブラリのみ (既存スクリプトと同様)
- タイムスタンプの比較はミリ秒にパースして行う (原本が `MM:SS.mmm`、整備後が `HH:MM:SS.mmm` でも同時刻なら一致とみなす)
- 整備後VTTのタイムスタンプ表記は `HH:MM:SS.mmm` 形式であること
- 話者名はallowlist方式 (レギュラー: `はるか` `ひとし` + エピソードごとの登録話者)
- エラーメッセージ・レポート・コミットメッセージは日本語
- 半角記号を使う (全角括弧は音声イベント表記等のデータ内のみ)

## File Structure

| ファイル | 責務 |
|---|---|
| `validate_vtt.py` | VTTパース + 原本との構造比較検証 (ライブラリ関数 + CLI) |
| `tests/test_validate_vtt.py` | 上記の単体テスト (unittest) |
| `post_report.py` | テキストをDiscord WebhookにPOST (分割送信対応) |
| `tests/test_post_report.py` | メッセージ分割ロジックの単体テスト |
| `glossary.md` | 番組用語集 (校正サブエージェントが参照する固有名詞辞書) |
| `.claude/skills/seibi/SKILL.md` | `/seibi` スキル本体 (パイプラインの指揮手順書) |

---

### Task 1: VTTパーサ (`parse_vtt`)

**Files:**
- Create: `validate_vtt.py`
- Test: `tests/test_validate_vtt.py`

**Interfaces:**
- Produces:
  - `parse_timestamp(ts: str) -> int` — `"MM:SS.mmm"` または `"HH:MM:SS.mmm"` をミリ秒intに変換。不正なら `ValueError`
  - `parse_vtt(text: str) -> list[dict]` — 各cueは `{"start_raw": str, "end_raw": str, "start_ms": int, "end_ms": int, "speaker": str | None, "text": str}`。不正なら `ValueError`

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_validate_vtt.py` を作成:

```python
import unittest

from validate_vtt import parse_timestamp, parse_vtt


RAW_VTT = """WEBVTT

00:01.140 --> 00:06.940
聞こえてない?今。

00:06.940 --> 00:07.940
部屋の外。
"""

TAGGED_VTT = """WEBVTT

00:00:04.300 --> 00:00:15.440
<v はるか>こんにちは。はるかです。

00:00:16.700 --> 00:00:20.617
<v ひとし>ひとしです。
"""


class TestParseTimestamp(unittest.TestCase):
    def test_full_format(self):
        self.assertEqual(parse_timestamp("00:01:02.345"), 62345)

    def test_short_format_without_hours(self):
        self.assertEqual(parse_timestamp("01:02.345"), 62345)

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            parse_timestamp("1.234")


class TestParseVtt(unittest.TestCase):
    def test_raw_vtt_without_speaker_tags(self):
        cues = parse_vtt(RAW_VTT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start_ms"], 1140)
        self.assertEqual(cues[0]["end_ms"], 6940)
        self.assertIsNone(cues[0]["speaker"])
        self.assertEqual(cues[0]["text"], "聞こえてない?今。")

    def test_tagged_vtt_with_speaker(self):
        cues = parse_vtt(TAGGED_VTT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["speaker"], "はるか")
        self.assertEqual(cues[0]["text"], "こんにちは。はるかです。")
        self.assertEqual(cues[0]["start_raw"], "00:00:04.300")

    def test_missing_header_raises(self):
        with self.assertRaises(ValueError):
            parse_vtt("00:01.000 --> 00:02.000\nテキスト\n")

    def test_cue_block_without_timestamp_raises(self):
        with self.assertRaises(ValueError):
            parse_vtt("WEBVTT\n\nタイムスタンプのないブロック\n")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_validate_vtt -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'validate_vtt'`)

`tests/` にはパッケージ化のための `__init__.py` は不要 (unittestのdiscoveryはリポジトリルートから実行する)。

- [ ] **Step 3: 実装**

`validate_vtt.py` を作成:

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_validate_vtt -v`
Expected: PASS (8 tests OK)

- [ ] **Step 5: 実データでの動作確認 (スモーク)**

Run:

```bash
python3 -c "
from pathlib import Path
from validate_vtt import parse_vtt
cues = parse_vtt(Path('episodes/20230523_0600-ikjo6pl5/transcript.vtt').read_text())
print(len(cues), cues[0])
"
```

Expected: cue数と先頭cueのdictが表示され、例外が出ない。

- [ ] **Step 6: コミット**

```bash
git add validate_vtt.py tests/test_validate_vtt.py
git commit -m "VTTパーサを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: VTT検証 (`validate` + CLI)

**Files:**
- Modify: `validate_vtt.py` (Task 1で作成したものに追記)
- Test: `tests/test_validate_vtt.py` (追記)

**Interfaces:**
- Consumes: `parse_vtt`, `parse_timestamp` (Task 1)
- Produces:
  - `validate(original_text: str, edited_text: str, allowed_speakers: list[str], min_ratio: float = 0.5) -> list[str]` — 問題のリストを返す。空リスト = 合格
  - CLI: `python3 validate_vtt.py <original.vtt> <edited.vtt> --speakers はるか,ひとし [--min-ratio 0.5]` — 合格なら `OK` を出力してexit 0、問題があれば列挙してexit 1

検証項目 (specの機械検証要件に対応):
1. 両ファイルがVTTとしてパースできる
2. cue数が原本と一致する
3. 各cueのタイムスタンプがミリ秒単位で原本と一致する
4. 整備後のタイムスタンプ表記が `HH:MM:SS.mmm` 形式である
5. 全cueに話者タグがあり、話者名がallowlist内である
6. cueテキストが空でない
7. cueごとのテキスト変更量が閾値内 (difflib.SequenceMatcher ratio >= min_ratio)

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_validate_vtt.py` に追記:

```python
from validate_vtt import validate

ORIGINAL = """WEBVTT

00:01.140 --> 00:06.940
えーっと、聞こえてない?今。

00:06.940 --> 00:07.940
部屋の外。
"""

GOOD_EDIT = """WEBVTT

00:00:01.140 --> 00:00:06.940
<v はるか>聞こえてない？今。

00:00:06.940 --> 00:00:07.940
<v ひとし>部屋の外。
"""

SPEAKERS = ["はるか", "ひとし"]


class TestValidate(unittest.TestCase):
    def test_good_edit_passes(self):
        self.assertEqual(validate(ORIGINAL, GOOD_EDIT, SPEAKERS), [])

    def test_timestamp_change_detected(self):
        bad = GOOD_EDIT.replace("00:00:06.940 --> 00:00:07.940",
                                "00:00:06.940 --> 00:00:08.000")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("タイムスタンプ" in i for i in issues))

    def test_short_timestamp_format_detected(self):
        bad = GOOD_EDIT.replace("00:00:01.140 --> 00:00:06.940",
                                "00:01.140 --> 00:06.940")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("HH:MM:SS.mmm" in i for i in issues))

    def test_cue_count_mismatch_detected(self):
        bad = GOOD_EDIT.rsplit("\n\n", 1)[0] + "\n"
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("cue数" in i for i in issues))

    def test_unknown_speaker_detected(self):
        bad = GOOD_EDIT.replace("<v ひとし>", "<v ヒトシ>")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("allowlist" in i for i in issues))

    def test_missing_speaker_tag_detected(self):
        bad = GOOD_EDIT.replace("<v ひとし>", "")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("話者タグ" in i for i in issues))

    def test_excessive_change_detected(self):
        bad = GOOD_EDIT.replace("聞こえてない？今。", "全然違う話をここに書く。")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("変更量" in i for i in issues))

    def test_empty_text_detected(self):
        bad = GOOD_EDIT.replace("部屋の外。", "")
        issues = validate(ORIGINAL, bad, SPEAKERS)
        self.assertTrue(any("空" in i for i in issues))

    def test_broken_edited_vtt_reported(self):
        issues = validate(ORIGINAL, "こわれたファイル", SPEAKERS)
        self.assertTrue(any("パース" in i for i in issues))
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_validate_vtt -v`
Expected: FAIL (`ImportError: cannot import name 'validate'`)

- [ ] **Step 3: 実装**

`validate_vtt.py` に追記 (import部に `argparse` `difflib` `sys` `pathlib.Path` を追加):

```python
import argparse
import difflib
import sys
from pathlib import Path

FULL_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_validate_vtt -v`
Expected: PASS (17 tests OK)

- [ ] **Step 5: CLIのスモーク確認**

Run:

```bash
python3 validate_vtt.py episodes/20230523_0600-ikjo6pl5/transcript.vtt \
  episodes/20230523_0600-ikjo6pl5/transcript.vtt --speakers はるか,ひとし; echo "exit=$?"
```

Expected: 原本は話者タグなしなので `NG: ... 話者タグがない` が並び `exit=1` (CLIが正しく失敗を検出できている)。

- [ ] **Step 6: コミット**

```bash
git add validate_vtt.py tests/test_validate_vtt.py
git commit -m "VTT検証 (validate + CLI) を追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Discord投稿 (`post_report.py`)

**Files:**
- Create: `post_report.py`
- Test: `tests/test_post_report.py`

**Interfaces:**
- Produces:
  - `split_message(text: str, limit: int = 1900) -> list[str]` — 行境界を優先して2000字制限内のチャンクに分割
  - CLI: `python3 post_report.py <report.md>` (引数なしならstdin) — 環境変数 `HAHI_DISCORD_WEBHOOK_URL` (必須) と `HAHI_DISCORD_THREAD_ID` (任意、スレッド投稿用) を使う

- [ ] **Step 1: 失敗するテストを書く**

`tests/test_post_report.py` を作成:

```python
import unittest

from post_report import split_message


class TestSplitMessage(unittest.TestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(split_message("こんにちは"), ["こんにちは"])

    def test_splits_on_line_boundary(self):
        text = "a" * 1000 + "\n" + "b" * 1000
        chunks = split_message(text, limit=1500)
        self.assertEqual(chunks, ["a" * 1000, "b" * 1000])

    def test_force_splits_overlong_line(self):
        text = "x" * 4000
        chunks = split_message(text, limit=1900)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(split_message(""), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: テストが失敗することを確認**

Run: `python3 -m unittest tests.test_post_report -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'post_report'`)

- [ ] **Step 3: 実装**

`post_report.py` を作成:

```python
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
```

- [ ] **Step 4: テストが通ることを確認**

Run: `python3 -m unittest tests.test_post_report -v`
Expected: PASS (4 tests OK)

- [ ] **Step 5: CLIのエラーハンドリング確認 (実投稿はしない)**

Run: `env -u HAHI_DISCORD_WEBHOOK_URL python3 post_report.py /dev/null; echo "exit=$?"`
Expected: `環境変数 HAHI_DISCORD_WEBHOOK_URL が未設定` と表示され `exit=1`

- [ ] **Step 6: コミット**

```bash
git add post_report.py tests/test_post_report.py
git commit -m "Discord Webhook投稿スクリプトを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 番組用語集 (`glossary.md`)

**Files:**
- Create: `glossary.md`

**Interfaces:**
- Produces: 校正サブエージェントが誤認識修正の判断に使う固有名詞辞書。`/seibi` スキル (Task 5) がプロンプトに含める。

- [ ] **Step 1: 用語集の下地を作成**

`glossary.md` を作成:

```markdown
# 番組用語集

「子育てのラジオ Teacher Teacher」の固有名詞辞書。
文字起こしの誤認識を修正する際の正式表記リファレンス。

> [!NOTE]
> 整備作業のなかで誤認識されやすい用語が見つかり次第、追記していく。

## 話者 (レギュラー)

| 表記 | 説明 |
|---|---|
| はるか | パーソナリティ。先生 |
| ひとし | パーソナリティ |

## 番組・団体

| 正式表記 | 誤認識されやすい例 |
|---|---|
| 『ティーチャーティーチャー』 | ティーチャー・ティーチャー、Teacher Teacher (会話中の言及) |
| JAPAN PODCAST AWARDS | ジャパンポッドキャストアワード |

## 番組内の用語・概念

| 正式表記 | 説明 |
|---|---|
| コンプリメント | ほめて自信を育てるアプローチ |
| アイメッセージ | 「私」を主語にした伝え方 (Iメッセージ表記のタイトルもある) |
| ヨイ出し | ダメ出しの逆。よいところを指摘する |
| ファンタジーマネジメント | 癇癪対応の手法 |
| 論理的結末 | アドラー心理学の概念 |
| イエナプラン | オランダ発の教育モデル |
```

- [ ] **Step 2: proofreading_guide.md との整合を確認**

Run: `grep -o "『[^』]*』" proofreading_guide.md | sort -u`
Expected: ガイドに登場する固有名詞 (『ティーチャーティーチャー』『イエナプラン』) が用語集に含まれていることを目視確認。

- [ ] **Step 3: コミット**

```bash
git add glossary.md
git commit -m "番組用語集の下地を追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `/seibi` スキル

**Files:**
- Create: `.claude/skills/seibi/SKILL.md`

**Interfaces:**
- Consumes:
  - `python3 validate_vtt.py <original> <edited> --speakers <csv>` (Task 2)
  - `python3 post_report.py <report.md>` (Task 3)
  - `glossary.md` (Task 4)
  - `proofreading_guide.md` (既存)
  - gws CLI (進行管理表の読み書き)
- Produces: `/seibi [episode_id]` で起動するパイプライン手順書。成果物は `episodes/<dir>/transcript.original.vtt`, `transcript.vtt` (整備済み), `report.md`

- [ ] **Step 1: SKILL.mdを作成**

`.claude/skills/seibi/SKILL.md` を作成 (内容は以下の通り。コードブロックの入れ子に注意):

````markdown
---
name: seibi
description: Teacher TeacherエピソードのLISTEN文字起こしをLLMで整備する。ユーザーが /seibi を実行したとき、または「エピソードを整備して」「文字起こしを校正して」と依頼してきたときに使う。引数にエピソードID (例: ikjo6pl5) を取れる。
---

# 文字起こし整備パイプライン

設計: `docs/superpowers/specs/2026-07-08-llm-proofreading-workflow-design.md`

1エピソードを「再取得 → LLM校正 → 機械検証 → レポート → 人間確認 →
アップロード案内 → Discord通知 → 記録」の順で整備する。

## 0. 対象エピソードの決定

- 引数でエピソードIDが指定されていればそれを使う
- 指定がなければ進行管理表から「作業待ち」を新しい順に列挙し、ユーザーに選んでもらう:

```bash
gws sheets spreadsheets values get --params '{"spreadsheetId":"1q0qMbdvjzMS06H_hwEUV3TbOcFUJlwtm1QbA-aIOunQ","range":"Sheet1!A1:F400"}' --format json
```

「はひステータス」列が「作業待ち」の行を抽出し、タイトル・公開日時・リンクを提示する。

## 1. VTTの再取得

エピソードIDから対象ディレクトリを特定する (ディレクトリ名は `YYYYMMDD_HHMM-<id>`):

```bash
ls -d episodes/*-<エピソードID>
```

話者登録済みの最新VTTをダウンロードし、原本として保存する:

```bash
curl -sf "https://listen.style/p/teacherteacher/<エピソードID>/transcript.vtt" \
  -o "episodes/<dir>/transcript.original.vtt"
```

**前提条件チェック**: `transcript.original.vtt` の先頭20行を見て `<v 話者名>` タグが
あることを確認する。タグがなければ話者登録がまだなので、
「LISTENエディタで話者登録 (5分作業) をしてから再実行してください」と伝えて中断する。

登場する話者名の一覧を控えておく (allowlistとして使う):

```bash
grep -o "<v [^>]*>" "episodes/<dir>/transcript.original.vtt" | sort | uniq -c
```

想定外の話者名 (レギュラーの「はるか」「ひとし」以外) があればユーザーに確認する
(ゲスト回なら正しい。表記はガイドの「ゲストは漢字フルネーム」ルールに従っているか見る)。

## 2. LLM校正

`transcript.original.vtt` をcue単位で分割し、**60cueごとのチャンク**に分けて
サブエージェント (model: sonnet) に並列で校正させる。チャンク境界の文脈が
わかるよう、前後2cueずつを「参考 (編集対象外)」として含める。

各サブエージェントへのプロンプトは以下を含めること:

1. `proofreading_guide.md` の全文
2. `glossary.md` の全文
3. 制約 (絶対に守らせる):
   - タイムスタンプは1文字も変更しない (形式変換もしない。すでにHH:MM:SS.mmm形式のため)
   - cueの数・順序を変えない。結合も分割もしない
   - 話者タグは原則維持。文脈上明らかに誤っている場合のみ変更し、変更ログに記録
   - 音声イベントの補記はしない (音声を聞けないため)
   - 迷ったら変更しない。疑わしい話者割り当ては変更せず「話者疑義」として報告
4. 出力形式: 校正済みチャンク全文と、変更ログ (JSON) を分けて返す。変更ログの形式:

```json
[
  {"time": "00:01:23.400", "category": "フィラー削除", "before": "えーっと、それでね", "after": "それでね"},
  {"time": "00:02:10.100", "category": "話者疑義", "before": "<v はるか>", "after": null, "note": "文脈上ひとしの可能性"}
]
```

カテゴリは次の6種類のみ: `フィラー削除` `句読点` `表記統一` `誤認識修正` `話者修正` `話者疑義`

全チャンクの結果を結合して `episodes/<dir>/transcript.vtt` に書き出す
(参考として付けた前後cueは除外し、重複しないように注意する)。

## 3. 機械検証

```bash
python3 validate_vtt.py \
  "episodes/<dir>/transcript.original.vtt" \
  "episodes/<dir>/transcript.vtt" \
  --speakers "はるか,ひとし,<ゲストがいれば追加>"
```

- `OK` が出るまで先に進まない
- NGが出たら該当cueを含むチャンクだけ修正 (再校正 or 手で直す) して再検証する
- 3回やり直してもNGが残る場合はユーザーに報告して指示を仰ぐ

## 4. レポート生成

変更ログを集計して `episodes/<dir>/report.md` を作る:

```markdown
# 整備レポート: <エピソードタイトル>

- エピソード: https://listen.style/p/teacherteacher/<エピソードID>
- 整備日: <YYYY-MM-DD>
- cue数: <N> (うち変更: <M>)

## カテゴリ別修正件数

| カテゴリ | 件数 |
|---|---|
| フィラー削除 | <n> |
| 句読点 | <n> |
| 表記統一 | <n> |
| 誤認識修正 | <n> |
| 話者修正 | <n> |

## 注目の修正 (抜粋)

- <time> 「<before>」→「<after>」 (<ひとこと理由>)
(誤認識修正・話者修正を中心に5件程度)

## 話者疑義 (未修正・人間の確認待ち)

- <time> <v 話者>「<セリフ冒頭>」 — <疑う理由>
(なければ「なし」)
```

## 5. 人間確認

ユーザーにレポートと変更の概況を提示する:

- レポート本文
- `git diff --stat` 相当の変更規模
- 特に見てほしい箇所 (誤認識修正・話者疑義)

フィードバックをもらったら反映し、ステップ3の検証からやり直す。

## 6. アップロード (手動)

ユーザーにこう案内する:
「`episodes/<dir>/transcript.vtt` をLISTENのVTTアップロード機能で反映してください」

アップロード完了の返事を待ってから次へ進む。

## 7. Discord通知

```bash
python3 post_report.py "episodes/<dir>/report.md"
```

環境変数 `HAHI_DISCORD_WEBHOOK_URL` (+ 任意で `HAHI_DISCORD_THREAD_ID`) が必要。
未設定なら投稿をスキップし、レポート本文を提示して手動投稿できるようにする
(致命的エラーにしない)。

## 8. 記録

1. 進行管理表の「はひステータス」を「いったん納品」に更新する。
   対象行は guid またはリンク列で特定する。**書き込みなので実行前にユーザーに確認する**:

```bash
gws sheets spreadsheets values update --params '{"spreadsheetId":"1q0qMbdvjzMS06H_hwEUV3TbOcFUJlwtm1QbA-aIOunQ","range":"Sheet1!B<行番号>","valueInputOption":"USER_ENTERED"}' --json '{"values":[["いったん納品"]]}'
```

   更新に失敗しても致命的エラーにせず、ユーザーに手動更新を依頼して先へ進む。

2. ローカルのステータスファイルを更新する:

```bash
rm -f "episodes/<dir>/作業待ち" && touch "episodes/<dir>/いったん納品"
```

3. コミットする:

```bash
git add "episodes/<dir>/transcript.original.vtt" "episodes/<dir>/transcript.vtt" \
  "episodes/<dir>/report.md" "episodes/<dir>/いったん納品"
git rm --cached "episodes/<dir>/作業待ち" 2>/dev/null || true
git commit -m "<タイトル> の文字起こしを整備

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

## 運用メモ

- フィードバックで繰り返し指摘されたルールは `proofreading_guide.md` へ、
  誤認識されやすい固有名詞は `glossary.md` へ還元することを毎回検討する
- 序盤の10〜20本は品質見極め期間。ユーザーのレビュー結果が評価データになる
````

- [ ] **Step 2: スキルの構文確認**

Run: `head -5 .claude/skills/seibi/SKILL.md`
Expected: frontmatter (`---` / `name: seibi` / `description: ...`) が正しく出力される。

- [ ] **Step 3: コミット**

```bash
git add .claude/skills/seibi/SKILL.md
git commit -m "/seibi スキルを追加

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 全体テストと締め

**Files:**
- なし (確認のみ)

- [ ] **Step 1: 全テストを実行**

Run: `python3 -m unittest discover -s tests -v`
Expected: 全テストPASS (21 tests OK)

- [ ] **Step 2: 実運用の準備確認をユーザーに依頼**

以下をユーザーに確認する:
- Discord WebhookのURL発行と `HAHI_DISCORD_WEBHOOK_URL` / `HAHI_DISCORD_THREAD_ID` の設定方法 (シェル環境変数 or direnv等、ユーザーの好みに合わせる)
- 最初に整備する1本を選んで `/seibi` を試すこと (パイロット運用)
