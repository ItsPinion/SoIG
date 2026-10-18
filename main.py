#!/usr/bin/env python3

"""Command line entry point for SoIG.

``python3 main.py ...`` and the installed ``soig`` command run the same code
(``cli``), so scripts can rely on one set of flags and exit codes.
"""

import argparse
import contextlib
import json
import os
import signal
import sys
from pathlib import Path

from api import __version__
from api.api import (
    InstagramError,
    LoginRequiredError,
    RateLimitedError,
    forget_session,
    inspect_page,
    main,
    nu,
    re,
    session_file_path,
    set_color,
    set_progress,
    set_quiet,
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_LOGIN_REQUIRED = 3
EXIT_RATE_LIMITED = 4
EXIT_INTERRUPTED = 130

# These only make sense when there is a profile to scrape.
USER_ONLY_FLAGS = {
    "postscrap": "-p/--postscrap",
    "savedata": "-s/--savedata",
    "tagscrap": "-t/--tagscrap",
    "all_posts": "-a/--all-posts",
    "max_posts": "-m/--max-posts",
    "dump_page": "-D/--dump-page",
}


def build_parser():
    ap = argparse.ArgumentParser(
        description="Scrape an Instagram profile, its posts and its hashtags.",
        epilog=(
            "exit codes: 0 ok, 1 error, 2 usage, 3 login required, 4 rate limited, 130 interrupted"
        ),
    )
    ap.add_argument(
        "-u",
        "--username",
        default=None,
        help="username of account to scan (not needed with -l, -F or -I)",
    )
    ap.add_argument(
        "-p", "--postscrap", action="store_true", help="scrape all uploaded images info "
    )
    ap.add_argument("-s", "--savedata", action="store_true", help="save data to file")
    ap.add_argument("-t", "--tagscrap", action="store_true", help="list often used tags")
    ap.add_argument("-x", "--proxy", default=None, help="HTTP(S) proxy to use")
    ap.add_argument(
        "-i",
        "--no-impersonate",
        action="store_true",
        help="disable browser TLS impersonation (curl_cffi)",
    )
    ap.add_argument(
        "-w",
        "--retry-wait",
        type=int,
        default=0,
        metavar="SECONDS",
        help="wait and retry once when Instagram rate limits you",
    )
    ap.add_argument(
        "-l",
        "--login",
        action="store_true",
        help="ask for an Instagram sessionid before scraping; "
        "on its own, just log in and store the session",
    )
    ap.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="show which profile sources were tried and why they failed",
    )
    ap.add_argument(
        "-f",
        "--session-file",
        default=None,
        metavar="PATH",
        help="where to save the session cookies (default: ~/.config/SoIG/session.json)",
    )
    ap.add_argument(
        "-n",
        "--no-save-session",
        action="store_true",
        help="do not save the sessionid/csrftoken to disk",
    )
    ap.add_argument(
        "-a",
        "--all-posts",
        action="store_true",
        help="page through the whole timeline instead of the first 12 posts",
    )
    ap.add_argument(
        "-m",
        "--max-posts",
        type=int,
        default=0,
        metavar="N",
        help="stop after N posts (pages as needed; also caps -a/--all-posts)",
    )
    ap.add_argument(
        "-D",
        "--dump-page",
        default=None,
        metavar="PATH",
        help="save the fetched profile page HTML for debugging",
    )
    ap.add_argument(
        "-I",
        "--inspect-page",
        default=None,
        metavar="PATH",
        help="describe a saved profile page HTML and exit",
    )
    ap.add_argument(
        "-F",
        "--forget-session",
        action="store_true",
        help="delete the saved sessionid/csrftoken and exit",
    )
    ap.add_argument("-q", "--quiet", action="store_true", help="hide progress lines and the banner")
    ap.add_argument(
        "-c",
        "--no-color",
        action="store_true",
        help="never use ANSI colours (automatic when output is piped)",
    )
    ap.add_argument(
        "-j",
        "--json",
        default=None,
        metavar="PATH",
        help="also write the result as JSON to PATH ('-' = stdout)",
    )
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    # Not listed in --help: a small token for whoever looks closely.
    ap.add_argument("--credits", action="store_true", help=argparse.SUPPRESS)
    return ap


def _document(ig=None, error=None, exit_code=EXIT_OK):
    """Build the JSON document written by ``--json``."""
    document = {
        "tool": "SoIG",
        "version": __version__,
        "exit_code": exit_code,
        "credit": "ItsPinion",
    }
    if ig is not None:
        document.update(ig.to_dict())
    if error:
        document["error"] = error
    return document


def _write_json(path, document):
    """Write ``document`` to ``path`` atomically; ``-`` means stdout."""
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    if path == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, target)


def _clear_screen():
    """Clear the terminal, but never a log file or a pipe."""
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _report(ig, args):
    if args["tagscrap"]:
        ig.print_data()
        ig.most_common_tags()
    else:
        ig.print_data_()
    if args["postscrap"]:
        ig.scrap_uploads()
    if args["savedata"]:
        ig.save_data()


def _run(parser, args):
    """Carry out the requested action; returns ``(exit_code, scraper, error)``."""
    if args["credits"]:
        print("ItsPinion")
        return EXIT_OK, None, None

    if args["inspect_page"]:
        ok = inspect_page(args["inspect_page"])
        return (EXIT_OK if ok else EXIT_ERROR), None, None

    if args["forget_session"]:
        removed = forget_session(args["session_file"])
        if removed:
            print(f"{re}[!]{nu} removed the saved session: {removed}")
        else:
            target = args["session_file"] or session_file_path()
            print(f"{re}[!]{nu} no saved session at {target}")
        return EXIT_OK, None, None

    if args["max_posts"] < 0:
        parser.error("-m/--max-posts expects 0 (no limit) or a positive number")
    if args["retry_wait"] < 0:
        parser.error("-w/--retry-wait expects a positive number of seconds")

    login_only = False
    if not args["username"]:
        requested = [flag for key, flag in USER_ONLY_FLAGS.items() if args[key]]
        if requested:
            parser.error(f"{', '.join(requested)} must be used with -u/--username")
        if not args["login"]:
            parser.error(
                "nothing to do: pass -u/--username to scrape a profile, or use "
                "-l/--login, -F/--forget-session or -I/--inspect-page on their own"
            )
        login_only = True

    if not args["quiet"]:
        _clear_screen()
    try:
        ig = main(
            user=args["username"],
            proxy=args["proxy"],
            impersonate=False if args["no_impersonate"] else None,
            retry_wait=args["retry_wait"],
            login=args["login"],
            debug=args["debug"],
            save_cookies=not args["no_save_session"],
            session_file=args["session_file"],
            dump_page=args["dump_page"],
            all_posts=args["all_posts"],
            max_posts=args["max_posts"],
            login_only=login_only,
        )
        if login_only:
            return EXIT_OK, ig, None
        _report(ig, args)
    except LoginRequiredError as exc:
        print(f"\n{re}[!]{nu} {exc}\n")
        print("    Log in first: python3 main.py --login\n")
        return EXIT_LOGIN_REQUIRED, None, str(exc)
    except RateLimitedError as exc:
        print(f"\n{re}[!]{nu} {exc}\n")
        print("    Wait a few minutes, retry with --retry-wait 60, or use --proxy.\n")
        return EXIT_RATE_LIMITED, None, str(exc)
    except InstagramError as exc:
        print(f"\n{re}[!]{nu} {exc}\n")
        return EXIT_ERROR, None, str(exc)
    except KeyboardInterrupt:
        print(f"\n{re}[!]{nu} cancelled\n")
        return EXIT_INTERRUPTED, None, "cancelled"
    except OSError as exc:
        print(f"\n{re}[!]{nu} {exc}\n")
        return EXIT_ERROR, None, str(exc)
    return EXIT_OK, ig, None


def cli(argv=None):
    """Run SoIG; returns the process exit code."""
    if os.name == "posix":
        # `soig ... | head` should stop quietly instead of printing a traceback.
        with contextlib.suppress(OSError, ValueError):
            # ValueError when cli() is embedded in a non-main thread.
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = build_parser()
    args = vars(parser.parse_args(argv))

    # Colour is a convenience for humans on a terminal, never for pipes/logs.
    set_color(not args["no_color"] and sys.stdout.isatty())
    set_quiet(args["quiet"])
    # --debug shows the full [step] trace, so the spinner would only fight it.
    set_progress(not args["debug"])

    json_path = args["json"]
    piped_json = json_path == "-"
    if piped_json:
        # Keep every human-facing line off stdout so `soig -j - | jq` works.
        real_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            code, ig, error = _run(parser, args)
        finally:
            sys.stdout = real_stdout
    else:
        code, ig, error = _run(parser, args)

    if json_path:
        try:
            _write_json(json_path, _document(ig, error=error, exit_code=code))
        except OSError as exc:
            print(f"{re}[!]{nu} could not write {json_path}: {exc}", file=sys.stderr)
            if code == EXIT_OK:
                code = EXIT_ERROR
    return code


if __name__ == "__main__":
    sys.exit(cli())
