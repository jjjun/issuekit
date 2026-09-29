# Checking agent model ids and effort levels

**Applies to:** choosing `model` / `reasoning_effort` for `[agents.codex]` or `[agents.claude]`

issuekit passes model ids and effort values through unchecked, so a bad value
only fails when the agent launches. Check them before editing config:

- Codex keeps its current catalog in `~/.codex/models_cache.json`: each entry's
  `slug` is a valid `--model` value and `supported_reasoning_levels` lists the
  efforts it accepts. The catalog differs by sign-in method. A bare family name
  such as `gpt-5.6` is not a model: under a ChatGPT sign-in it fails with HTTP
  400 "not supported when using Codex with a ChatGPT account". Use the variant
  slugs (`gpt-5.6-sol`, `gpt-6-luna`, ...). Not every model accepts every
  effort; for example `gpt-6-luna` stops at `max` and has no `ultra`.
- `claude --help` lists the accepted `--effort` values; `--model` takes an
  alias or a full id such as `claude-sonnet-5-5`.
- To confirm what actually ran, smoke-test outside the repo:
  `codex exec --skip-git-repo-check -s read-only --model <id> -c model_reasoning_effort=<e> "Reply OK"`
  prints `model:` and `reasoning effort:` in its header, and
  `claude -p "Reply OK" --output-format json --model <id> --effort <e>` names
  the model under `modelUsage`. The Claude result does not echo the effort.
