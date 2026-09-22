# Follow heartbeat git status

**Applies to:** default exec agent runs with `--follow` or a TTY stderr.

The run watcher polls `git status --short` when `--follow` is passed or stderr
is a TTY. It uses `--no-optional-locks`, so it does not refresh the index or
acquire `index.lock` and is safe while an issue rewrites the checkout. Redirect
stderr to silence the heartbeat.
