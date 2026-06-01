# issuekit

`docs/issues/` ローカル issue トラッカー規約を、複数リポジトリで共有するための言語非依存 CLI です。

mine-js-monorepo の Node 実装 (`scripts/issues-*.mjs`, `scripts/check-encoding.mjs`)
を Python に集約し、`uv tool install` で各リポジトリから同じツールを呼べるようにします。

対象リポジトリ (2026-05-28 時点):

- mine-js-monorepo (JS)
- py_cr_wrapper / infra-toolkit / mine-py / fast-domain / repom (Python)

設計の経緯は mine-js-monorepo の `docs/proposals/022_issuekit_shared_issue_cli.md` を参照してください。

## インストール

```powershell
uv tool install --from git+https://github.com/jjjun/issuekit.git issuekit
```

ローカル開発時:

```powershell
uv sync
uv run issuekit --help
```

## MCP server

Install the MCP server once as a global tool:

```powershell
uv tool install "issuekit[mcp] @ git+https://github.com/jjjun/issuekit.git"
```

Then register `issuekit-mcp` with your MCP client for each repo that uses
issuekit. The server resolves `docs/issues/` from the client's working
directory, so the same global binary works across repos.

For local development, install the optional MCP group and start the stdio server
from a checkout with:

```powershell
uv run --group mcp issuekit-mcp
```

The server exposes the same handoff workflow as the CLI: codex claims and
submits tasks for review, and claude reviews, requests changes, or approves.
See `AGENTS.md` for the codex protocol and `CLAUDE.md` for the claude protocol.

## コマンド

| コマンド | 役割 |
|----------|------|
| `issuekit info [--json]` | 状態サマリと次の issue id (read-only) |
| `issuekit validate` | ファイル名 / id 重複 / frontmatter / index 整合 / mojibake / ASCII を検査 |
| `issuekit generate-indexes` | `docs/issues/indexes/*` を再生成 |
| `issuekit complete <id> --summary "..." --verification "..."` | active -> completed 移動 + index 再生成 + validate |
| `issuekit check-encoding [--json]` | tracked source の先頭 BOM / mojibake を検査 |
| `issuekit init` | 規約ディレクトリ / README / `.gitattributes` / pre-commit を未導入 repo に配る |

規約 (issue ファイル形式、status / priority、ASCII ルール) は `docs/issues/README.md` を正とします。

## 開発状況

CLI 本体は未実装です。実装タスクは `docs/issues/active/` に codex-ready issue として用意しています。
このリポジトリは issuekit 自身の issue トラッカーをドッグフードします。
