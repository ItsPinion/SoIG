# Changelog

All notable changes to SoIG are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-19

First packaged, installable release. `python3 main.py ...` keeps working, and
the same code is available as the `soig` command.

### Added

- `pyproject.toml` (PEP 621) with pinned dependencies, a `soig` console script,
  optional `dev` extras, ruff and coverage configuration.
- `-V/--version`.
- `-q/--quiet` for scripted runs (no progress line or banner).
- `-c/--no-color`; colours are also disabled automatically when stdout is not a
  terminal.
- `-j/--json PATH` to write the result as a machine-readable document;
  `-j -` writes only JSON to stdout so it can be piped to `jq`.
- A single status line that names the current request: a sweeping highlight
  while the profile is fetched, then a real progress bar with counts,
  percentage, elapsed time and an ETA while a timeline is walked. Silent off a
  terminal, so CI logs and redirected output stay clean. Terminals without
  UTF-8 get an ASCII bar.
- Distinct exit codes: `0` ok, `1` error, `2` usage, `3` login required,
  `4` rate limited, `130` interrupted.
- Atomic session writes (temp file + `os.replace`) so a crash cannot truncate
  the saved cookies.
- CI workflow running the test suite on Python 3.9-3.13, linting and building
  the wheel.

### Changed

- The report is a sectioned card: a framed `SoIG` wordmark, cyan `▌ PROFILE` /
  `▌ POSTS` / `▌ HASHTAGS` headings over a rule, grouped counts
  (`74,877`), `✓` / `!` / `✗` status marks, a numbered post list with likes,
  comments, date, location and URL, and a ranked hashtag list with inline bars.
  Empty fields are left out, long values wrap at the terminal width, and
  everything falls back to ASCII on non-UTF-8 terminals.
- The `[step]` trace is printed **only** with `-d/--debug`. A normal run shows
  the report plus the progress line.
- The banner is printed once, before the first request, so warnings no longer
  appear above it. The old ASCII logo and its "Coded By" credit are gone; the
  author credit is the hidden `--credits` token.
- `-m/--max-posts` is independent of `-a/--all-posts`: `-m N` alone pages as far
  as needed and keeps exactly N posts, while `-a -m N` caps the walk at N.
- `--login` and `--forget-session` (and `--inspect-page`) no longer require
  `--username`; `--login` on its own verifies or replaces the saved session and
  exits.
- `--login` asks only for the cookies the scraper uses (`sessionid` and
  `csrftoken`); `csrftoken` and `mid` set by Instagram are saved automatically.
- Private profiles are read through the feed instead of being refused up front,
  so accounts the session follows work.
- The 0.1s-per-character animation was removed; runs start immediately.

### Fixed

- Crash when `window._sharedData` was not the fourth script tag (the original
  `IndexError`) - the scraper now supports the current embedded payload, the
  GraphQL profile queries, `web_profile_info` and OpenGraph metadata.
- `-a -m 2` returned a full first page (12 posts) instead of 2.
- Saved sessions without `ds_user_id`/`mid` are no longer reported as incomplete.

[1.0.0]: https://github.com/ItsPinion/SoIG/releases/tag/v1.0.0
