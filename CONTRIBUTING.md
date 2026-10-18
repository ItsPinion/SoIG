# Contributing

Thanks for taking the time to improve SoIG.

## Setup

```sh
git clone https://github.com/ItsPinion/SoIG
cd SoIG
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Checks

Every change must keep these green:

```sh
ruff check .
ruff format --check .
python -m unittest discover -s tests -v
```

The suite covers the parser, the HTTP fallbacks, pagination, the session/login
flow and the CLI. Add a test for every behaviour you change - the project has no
network-dependent tests, so mock `api.main._get` (see `tests/test_api.py`) rather
than calling Instagram.

## Code guidelines

- Keep the scraper free of interactive prompts unless `interactive` is true; CI
  and cron runs have no stdin.
- Print results (profile, posts, tags, JSON) to stdout and progress to stderr.
  Never clear the screen when stdout is not a terminal.
- Any new option needs both a long form and a one-letter form, and a line in the
  README table.
- Do not add environment-variable configuration: everything is a CLI flag or a
  prompt.
- Never commit `session.json` or any cookie. The `.gitignore` already covers the
  default paths.

## Pull requests

- One logical change per pull request, with a short description of the user
  visible effect.
- Mention the commands you ran (`ruff check`, `unittest`) in the description.
- Update `README.md` when flags, output or exit codes change, and add an entry to
  `CHANGELOG.md` under "Unreleased".
