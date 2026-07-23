# Claude trust-dialog warning

**Applies to:** `issuekit review --agent claude` in a checkout Claude Code has not trusted

Claude Code can print `Ignoring N permissions.allow entries from
.claude/settings.local.json: this workspace has not been trusted.` when
issuekit launches it. This is cosmetic when the configured launch uses
`--permission-mode bypassPermissions`; the review can still complete. Run
Claude Code interactively once in the checkout and accept its trust dialog to
clear the warning.
