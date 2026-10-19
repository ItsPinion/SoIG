"""The command-line surface: short flags must stay available."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "main.py")
sys.path.insert(0, ROOT)

import api.api as api  # noqa: E402
import main as cli  # noqa: E402

SHORT_FLAGS = {
    "-l": "--login",
    "-d": "--debug",
    "-a": "--all-posts",
    "-m": "--max-posts",
    "-x": "--proxy",
    "-w": "--retry-wait",
    "-i": "--no-impersonate",
    "-f": "--session-file",
    "-n": "--no-save-session",
    "-D": "--dump-page",
    "-I": "--inspect-page",
    "-F": "--forget-session",
    "-q": "--quiet",
    "-c": "--no-color",
    "-j": "--json",
    "-V": "--version",
}


def run(*args, stdin=None):
    return subprocess.run(
        [sys.executable, MAIN, *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        stdin=stdin,
        timeout=60,
    )


class ShortFlagTests(unittest.TestCase):
    def test_help_lists_every_short_flag(self):
        result = run("--help")
        self.assertEqual(result.returncode, 0)
        for short, long in SHORT_FLAGS.items():
            self.assertIn(f"[{short}", result.stdout, short)
            self.assertIn(long, result.stdout, long)

    def test_short_flags_are_accepted_together(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = os.path.join(tmp, "page.html")
            with open(page, "w", encoding="utf-8") as handle:
                handle.write("<html><script></script></html>")
            result = run(
                "-I",
                page,
                "-l",
                "-d",
                "-a",
                "-m",
                "5",
                "-x",
                "http://h:1",
                "-w",
                "1",
                "-i",
                "-f",
                os.path.join(tmp, "s.json"),
                "-n",
                "-D",
                os.path.join(tmp, "dump.html"),
            )
        self.assertNotEqual(result.returncode, 2, result.stderr)
        self.assertIn("JSON script tags", result.stdout)

    def test_inputs_are_optional_before_the_username_check(self):
        result = run()
        self.assertEqual(result.returncode, 2)
        self.assertIn("-u/--username", result.stderr)

    def test_forget_session_works_without_a_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"sessionid": "x"}')
            result = run("-f", path, "-F")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("-u/--username", result.stderr)
            self.assertFalse(os.path.exists(path))

    def test_login_alone_does_not_ask_for_a_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            result = run("-l", "-f", path, stdin=subprocess.DEVNULL)
        self.assertNotEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("-u/--username", result.stderr)
        self.assertIn("Instagram is asking for a logged-in session", result.stdout)

    def test_scrape_flags_still_need_a_username(self):
        result = run("-p")
        self.assertEqual(result.returncode, 2)
        self.assertIn("-p/--postscrap", result.stderr)
        self.assertIn("-u/--username", result.stderr)

    def test_negative_max_posts_is_rejected(self):
        result = run("-u", "someone", "-m", "-5")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--max-posts", result.stderr)

    def test_short_forget_session_deletes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "session.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"sessionid": "x"}')
            result = run("-u", "someone", "-f", path, "-F")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("removed the saved session", result.stdout)
            self.assertFalse(os.path.exists(path))


class FakeScraper:
    to_dict = api.main.to_dict

    def __init__(self, **kwargs):
        self.user = kwargs.get("user") or ""
        self.output = {"username": self.user, "followers": "1.2K", "following": "3", "posts": "10"}
        self.posts = [{"url": "instagram.com/p/a", "caption": "#x"}]
        self.tags = {"x": 1}
        self.posts_error = None
        self.limited_data = False

    def print_data_(self):
        print("PROFILE REPORT")

    def print_data(self):
        print("PROFILE REPORT")

    def most_common_tags(self):
        print("TAGS REPORT")

    def scrap_uploads(self):
        print("POSTS REPORT")

    def save_data(self):
        print("SAVED")


class CliTests(unittest.TestCase):
    def tearDown(self):
        api.set_color(True)
        api.set_quiet(False)

    def test_hidden_credits_flag(self):
        result = run("--credits")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ItsPinion")
        self.assertNotIn("--credits", run("--help").stdout)

    def test_version_flag(self):
        result = run("-V")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1.0.0", result.stdout)

    def test_json_file_holds_the_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.json")
            with (
                mock.patch.object(cli, "main", FakeScraper),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.cli(["-u", "someone", "-j", path])
            self.assertEqual(code, 0)
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
        self.assertEqual(document["exit_code"], 0)
        self.assertEqual(document["profile"]["followers"], 1200)
        self.assertEqual(document["posts"][0]["url"], "instagram.com/p/a")
        self.assertEqual(document["tags"], {"x": 1})

    def test_json_stdout_is_pure(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(cli, "main", FakeScraper),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.cli(["-u", "someone", "-j", "-"])
        self.assertEqual(code, 0)
        self.assertIn("PROFILE REPORT", err.getvalue())
        document = json.loads(out.getvalue())  # nothing else on stdout
        self.assertEqual(document["username"], "someone")

    def test_login_required_exit_code(self):
        with (
            mock.patch.object(cli, "main", side_effect=api.LoginRequiredError("log in")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.cli(["-u", "someone"]), 3)

    def test_rate_limited_exit_code(self):
        with (
            mock.patch.object(cli, "main", side_effect=api.RateLimitedError("slow down")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.cli(["-u", "someone"]), 4)

    def test_generic_error_exit_code(self):
        with (
            mock.patch.object(cli, "main", side_effect=api.InstagramError("broken")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.cli(["-u", "someone"]), 1)

    def test_interrupted_exit_code(self):
        with (
            mock.patch.object(cli, "main", side_effect=KeyboardInterrupt),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.cli(["-u", "someone"]), 130)

    def test_quiet_flag_sets_quiet_mode(self):
        with (
            mock.patch.object(cli, "main", FakeScraper),
            mock.patch.object(cli, "set_quiet") as quiet,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.cli(["-u", "someone", "-q"])
        quiet.assert_called_once_with(True)

    def test_no_color_flag_sets_color_off(self):
        with (
            mock.patch.object(cli, "main", FakeScraper),
            mock.patch.object(cli, "set_color") as color,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli.cli(["-u", "someone", "-c"])
        color.assert_called_once_with(False)

    def test_negative_retry_wait_is_rejected(self):
        result = run("-u", "someone", "-w", "-1")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--retry-wait", result.stderr)


if __name__ == "__main__":
    unittest.main()
