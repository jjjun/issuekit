# Read-only agent sandbox policies

**Applies to:** Codex and Claude Code read-only agent launches

Verified with Codex CLI 0.158.0 and Claude Code 2.1.288. `codex exec --help`
lists `read-only` and `workspace-write` as sandbox modes. The command-executing
smoke test
`codex sandbox -c 'sandbox_mode="read-only"' -- git -C <repo> log -1 --oneline`
exits 1: bwrap fails with `loopback: Failed RTM_NEWADDR: Operation not
permitted`. Codex `exec` can still return a final answer when the requested
shell command was denied. The lightweight
`codex sandbox -c 'sandbox_mode="read-only"' -- true` probe exits 1 with the
same error. The `workspace-write` probe also exits 1 with
`bwrap: setting up uid map: Permission denied`. This occurs on Ubuntu hosts
where AppArmor restricts unprivileged user namespaces and /usr/bin/bwrap has
no AppArmor profile. Enabling
unprivileged user namespaces for bwrap fixes the host, for example with an
AppArmor profile for /usr/bin/bwrap containing `userns,` or by setting
`kernel.apparmor_restrict_unprivileged_userns=0`, then rerunning the command.
The legacy `-c features.use_legacy_landlock=true` option does not help; it
panics because bubblewrap is still required to isolate app-server sockets.
There is no non-bwrap fallback. issuekit now runs the `codex sandbox` probe
before launching a role with a Codex sandbox policy and reports the probe error
without launching the agent. Operators can instead opt out for that role with
`[agents.codex.roles.<role>] approval_argv = [...]`.

On Windows 11 with Codex CLI 0.158.0, `codex --version` reports
`codex-cli 0.158.0`, and `codex sandbox --help` describes running commands
under the Windows restricted-token sandbox. The probes
`codex sandbox -c 'sandbox_mode="read-only"' -- cmd /c exit 0` and
`codex sandbox -c 'sandbox_mode="workspace-write"' -- cmd /c exit 0` both exit
0 with no output. Running
`codex sandbox -c 'sandbox_mode="read-only"' -- cmd /c git log -1 --oneline`
also exits 0 and prints the commit. The POSIX `true` command and direct `git`
or `git.exe` commands fail with Windows error 2 because the restricted-token
runner cannot find those executables; use `cmd /c` for the preflight command.
The command-executing smoke run
`codex exec --sandbox read-only --model <model> -c 'mcp_servers={}' --skip-git-repo-check 'Run git log -1 --oneline and print its output'`
completed and printed the current commit. If `codex exec` fails because the
model in the local Codex config is not available to the account, pass
`--model <model>` explicitly. issuekit's Windows preflight must therefore use
`cmd /c exit 0`, report Windows sandbox failures without Linux AppArmor or
bubblewrap remediation, and keep failing closed if the sandbox is unavailable.

The empty `mcp_servers` TOML override disables configured Codex MCP servers
for that invocation. Codex CLI 0.158.0 accepts this override and the sandbox
mode flags.

Claude Code accepts `--permission-mode dontAsk`, `--allowedTools` with one
comma-separated argument, and `--strict-mcp-config`. A `claude -p` smoke run
from `/tmp` with the read-only Git allowlist and those flags completed
successfully. With strict MCP config and no `--mcp-config`, configured MCP
servers are not loaded.

Claude's Bash sandbox settings use `sandbox.enabled`,
`sandbox.allowUnsandboxedCommands`, and `sandbox.failIfUnavailable`. On Linux,
the installed sandbox needs both `bubblewrap` and `socat`; this environment has
`bubblewrap` but not `socat`, so a sandboxed Claude Bash smoke test could not be
verified. Keep the built-in reviewer allowlist restricted to read-only Git
commands here. Projects that need reviewer tests can configure a complete
`[agents.claude.roles.reviewer] approval_argv` list with narrowly scoped
`Bash(<test command>)` entries and `--strict-mcp-config`.
