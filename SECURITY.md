# Security

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private
vulnerability reporting (Security -> Report a vulnerability) or contact the
maintainer through the repository.

Include the version (`soig --version`), the command you ran and, if possible, a
minimal reproduction. Never include your Instagram cookies or `session.json`.

## What SoIG stores

- `~/.config/SoIG/session.json` holds the Instagram cookies you paste at the
  login prompt. It is written with `0600` (folder `0700`) and replaced
  atomically.
- Nothing is uploaded anywhere: the file is only read on the next run.
- `--no-save-session` keeps everything in memory for one run, and
  `--forget-session` deletes the file.

## Scope

SoIG only reads public pages and your own authenticated session. It does not
implement account takeover, credential handling, CAPTCHA bypasses or anything
else that would attack Instagram. Vulnerabilities in that boundary - for example
path traversal in `--dump-page`/`--inspect-page`, or cookies leaking into logs -
are in scope.
