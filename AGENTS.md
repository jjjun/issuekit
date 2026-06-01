# issuekit - Agent Guidelines

## Handoff protocol (codex)

Codex implements issuekit tasks from `docs/issues/active/`.

When the user asks codex to work on an issue in open-ended terms (for example
"issue の対応をお願いします", "handle the next issue", "take the queue"), do not
wait for explicit commands. Run this protocol end to end:

1. Call the issuekit MCP tool `claim_next_task(assignee="codex")`. The returned
   payload includes the issue body, which is the spec to implement. If it
   returns no issue, report that the queue is empty and stop.
2. Read the claimed issue (Problem, Implementation Plan, Test Plan) and lay out
   a short plan: the files to change and the order of steps. Confirm the plan
   matches the issue scope before writing code; do not expand beyond it.
3. Implement the claimed issue on the current branch and make focused commits.
   Do not create or switch branches. The local workflow commits directly to
   `main` for speed; only create a branch when the user explicitly asks for one.
4. Run the relevant tests and `uv run issuekit check-encoding`.
5. Call `submit_for_review(id, summary, branch, commit)` with an ASCII summary,
   the current branch name (usually `main`), and the implementation commit.
6. If Claude returns the issue with `stage=changes_requested`, call
   `claim_next_task(assignee="codex")` again, read the "## Review Feedback"
   note, re-plan for just that feedback, address it, commit, and submit for
   review again.

Codex owns implementation. Claude owns proposals, codex-ready issues, and
review.

## 概要

`issuekit` は `docs/issues/` ローカル issue トラッカー規約を複数リポジトリで共有するための
言語非依存 CLI です。mine-js-monorepo の Node 実装を Python に集約します。

## アーキテクチャ (目標)

```text
issuekit/
  __init__.py
  cli.py        # argparse ディスパッチャ / コンソールエントリ (issuekit = issuekit.cli:main)
  core.py       # frontmatter parse/format, issue モデル, id 採番, index 生成 (issues-lib.mjs 相当)
  commands/     # 各サブコマンド実装 (info / validate / generate_indexes / complete / check_encoding / init)
  config.py     # [tool.issuekit] 読み込みと既定値
docs/
  issues/       # issuekit 自身の issue トラッカー (ドッグフード)
tests/          # pytest
```

## エージェントのロール分担

- Claude (Opus): proposal、codex-ready issue、レビューを担当する。実装は原則しない。
- codex (gpt5.5): `docs/issues/active/` の issue に沿って CLI を実装する。

## 移植元 (mine-js-monorepo)

各コマンドは以下を移植元とする。挙動を勝手に変えず、まず等価移植する。

- `scripts/issues-lib.mjs` -> `core.py`
- `scripts/issues-info.mjs` -> `commands/info`
- `scripts/issues-validate.mjs` -> `commands/validate`
- `scripts/issues-generate-indexes.mjs` -> `commands/generate_indexes`
- `scripts/issues-complete.mjs` -> `commands/complete`
- `scripts/check-encoding.mjs` -> `commands/check_encoding`
- `.gitattributes` -> `issuekit init` が配布するテンプレート

## 開発方針

- `uv sync` / `uv run pytest` で開発する。
- 純標準ライブラリで実装する (PyYAML 等の追加依存は避け、frontmatter は移植元と同じ簡易パーサで処理する)。

## Encoding rules (required)

文字化けには2種類ある。両方を防ぐこと。

A. 形式破損: BOM 付与 / CRLF / 誤エンコーディング保存。
B. 転記ミス: 既存の非ASCII文字を読んで別の文字に化けさせて書き出す
   (実例: `core.py` の MOJIBAKE_PATTERN を移植した際、元の CJK 文字が
   別コードページ経由で取り込まれ、全く別のコードポイントに化けた)。
   B はファイルが妥当な ASCII/UTF-8 のままなので BOM 検査をすり抜ける。

ルール:

- 全ファイルを UTF-8 (BOM なし) / LF で書く。BOM (`EF BB BF`) を絶対に入れない。
  BOM は ripgrep から不可視なので、バイトレベルで確認する (`head -c 3 <file> | xxd`)。
- **ソース内の非ASCII文字列リテラルは `\uXXXX` エスケープで書く** (生の CJK を置かない)。
  これが A と B の両方を根本的に防ぐ最重要ルール。
- **既存の非ASCII文字を手で打ち直さない**。元のコードポイントをそのまま使う
  (例: 移植時は元ファイルのコードポイントを確認してからエスケープに変換する)。
- 非ASCIIを含むロジック (mojibake 検出など) は、本物の文字を assert する単体テストを必ず書く。
  化けた値で緑になるテストは無意味。
- `docs/issues/` 配下は英語 ASCII のみで書く (規約は `docs/issues/README.md` を参照)。
- 作業完了前に `uv run issuekit check-encoding` を通す (pre-commit でも強制される)。

## Build & Test

```powershell
uv sync
uv run pytest
uv run issuekit --help
```
