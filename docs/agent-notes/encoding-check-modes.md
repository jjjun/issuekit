# Encoding check modes

**Applies to:** `issuekit check-encoding` and the implementer submit gate

Run `issuekit check-encoding --gate` before submission. It matches the submit
gate by scanning every changed readable implementation file, including files
outside the source-extension list, and limits tracked files to changed lines.
Untracked files and files whose changed-line diff fails are scanned completely.
Both confirmed and unconfirmed mojibake fail, invalid UTF-8 fails, and configured
`check_encoding_exclude` patterns suppress unconfirmed hits only. Confirmed
corruption still fails in every path.

The default `issuekit check-encoding` remains a whole-file check of tracked
source extensions. It fails on confirmed mojibake and invalid UTF-8 by default,
reports unconfirmed hits only when requested, and additionally checks BOM, CRLF,
and stray carriage returns.
