# Run issuekit implement in its own shell command

**Applies to:** orchestrating `issuekit implement <id> --agent codex`

Launch `issuekit implement` as a standalone shell command. Do not chain it
after `git add`/`git commit` or `issuekit approve` in the same command line
(for example `approve ... && git commit ... ; issuekit implement N`).

The implementer agent runs unsandboxed and can list processes. During
issuekit#431 a codex implementer saw its parent shell's command line, which
still contained the earlier `git commit`, decided that an "unrelated live
shell" was violating its no-commit instruction, and signalled it. That killed
the `issuekit implement` process itself: the run ended with
`not_submitted ... reason=interrupted` and `cli_exit=130`, leaving partial
edits and the claim at `stage=implementing`.

To recover, rerun `issuekit implement <id> --no-sync` on top of the partial
edits. The claim is still held, but a plain rerun fails at the claim-sync
guard ("has a dirty working tree") because the interrupted run's own edits
are uncommitted; `--no-sync` is the deliberate skip for exactly this case.
Commit the previous issue in a separate command before starting the next
implement run.
