"""The command-line surface: short flags must stay available."""

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "main.py")

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
                "-I", page, "-l", "-d", "-a", "-m", "5", "-x", "http://h:1",
                "-w", "1", "-i", "-f", os.path.join(tmp, "s.json"), "-n",
                "-D", os.path.join(tmp, "dump.html"),
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


if __name__ == "__main__":
    unittest.main()
