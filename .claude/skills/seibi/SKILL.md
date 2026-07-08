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
