# issuekit - Agent Guidelines

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
- ファイルは UTF-8 (BOM なし) / LF で書く。BOM (`EF BB BF`) を絶対に入れない。
- BOM は ripgrep からは不可視なので、バイトレベルで確認する (`head -c 3 <file> | xxd`)。
- `docs/issues/` 配下は英語 ASCII のみで書く (規約は `docs/issues/README.md` を参照)。

## Build & Test

```powershell
uv sync
uv run pytest
uv run issuekit --help
```
