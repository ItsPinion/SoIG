import collections
import contextlib
import datetime
import getpass
import json
import os
import random
import re as regex
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from . import __version__

try:  # optional: makes requests look like a real Chrome build at the TLS level
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - exercised only without the extra
    curl_requests = None

collections.Callable = collections.abc.Callable


class _Ansi:
    """A terminal colour that disappears when colour is switched off."""

    enabled = True

    def __init__(self, code):
        self._code = code

    def __str__(self):
        return self._code if _Ansi.enabled else ""

    def __format__(self, spec):
        return format(str(self), spec)


nu = _Ansi("\033[0m")
re = _Ansi("\033[1;31m")
gr = _Ansi("\033[1;32m")
cy = _Ansi("\033[1;36m")
yl = _Ansi("\033[1;33m")
bd = _Ansi("\033[1m")
dim = _Ansi("\033[2m")
_quiet = False


def set_color(enabled):
    """Turn ANSI colours on or off (the CLI disables them when piped)."""
    _Ansi.enabled = bool(enabled)


def set_quiet(enabled):
    """Silence progress lines and the banner; warnings and results still print."""
    global _quiet
    _quiet = bool(enabled)


def _utf8(stream):
    """True when ``stream`` can take box-drawing glyphs and block bars."""
    encoding = (getattr(stream, "encoding", "") or "").lower()
    return "utf" in encoding


class _Mark:
    """A one-character status mark that degrades to ASCII off UTF-8."""

    def __init__(self, pretty, plain, color):
        self.pretty = pretty
        self.plain = plain
        self.color = color

    def __str__(self):
        glyph = self.pretty if _utf8(sys.stdout) else self.plain
        return f"{self.color}{glyph}{nu}"


# The three marks every human-facing line starts with.
OK = _Mark("✓", "+", gr)
WARN = _Mark("!", "!", yl)
BAD = _Mark("✗", "x", re)


class _Status:
    """A single-line progress bar for interactive terminals.

    Two forms share one line: a sweeping bar while an operation has no known
    size (fetching the profile), and a determinate bar with counts, percentage
    and ETA while a timeline is walked. The label always names what is
    happening right now. It stays completely silent when stderr is not a
    terminal (pipes, CI logs, cron mail), when --quiet is used, and when
    --debug asks for the full step trace instead. Nothing here ever touches
    stdout, so reports and JSON stay clean.
    """

    BAR_WIDTH = 32
    LABEL_WIDTH = 26
    TICK = 0.1
    FULL = "█"
    EMPTY = "░"

    def __init__(self):
        self.allowed = True
        self.active = False
        self.label = ""
        self.done = 0
        self.total = 0
        self.stream = sys.stderr
        self._frame = 0
        self._width = 0
        self._started = 0.0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = None

    def enabled(self):
        if not self.allowed or _quiet:
            return False
        try:
            return bool(self.stream.isatty())
        except (AttributeError, ValueError):
            return False

    def start(self, label, done=None, total=None):
        """Start or update the line; ``done``/``total`` make it determinate."""
        if not self.enabled():
            return
        with self._lock:
            self.label = str(label)
            self.done = int(done or 0)
            self.total = int(total or 0)
            if self.active:
                self._draw(*self._compose())
                return
            self.active = True
            self._started = time.monotonic()
            self._frame = 0
            self._stop.clear()
            self._draw(*self._compose())
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def bar(self, done, total, label="Loading posts"):
        """Show a determinate bar, or the sweeping form when total is unknown."""
        if not total:
            self.start(label)
            return
        self.start(label, done=done, total=total)

    def stop(self):
        """Erase the line; safe to call at any time."""
        with self._lock:
            if not self.active:
                return
            self.active = False
            thread = self._thread
        self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        with self._lock:
            try:
                self.stream.write("\r" + " " * self._width + "\r")
                self.stream.flush()
            except (OSError, ValueError):
                pass
            self._width = 0
            self.label = ""
            self.done = 0
            self.total = 0

    # -- rendering -------------------------------------------------------
    def _spin(self):
        while not self._stop.wait(self.TICK):
            with self._lock:
                if not self.active:
                    return
                self._frame += 1
                self._draw(*self._compose())

    def _unicode(self):
        return _utf8(self.stream)

    def _bar_width(self, suffix=0):
        # Leave room for "  label   bar   suffix" so the line never wraps.
        try:
            columns = shutil.get_terminal_size((80, 24)).columns
        except OSError:
            columns = 80
        room = columns - self.LABEL_WIDTH - len(suffix) - 6
        return max(8, min(self.BAR_WIDTH, room))

    @staticmethod
    def _clock(seconds):
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes, rest = divmod(int(seconds), 60)
        if minutes < 60:
            return f"{minutes}m{rest:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"

    def _suffix(self, elapsed):
        if not self.total:
            return self._clock(elapsed)
        percent = min(100, int(self.done * 100 / self.total))
        text = f"{self.done}/{self.total} · {percent}% · {self._clock(elapsed)}"
        if self.done and percent < 100:
            remaining = elapsed * (self.total - self.done) / self.done
            if remaining >= 1:
                text += f" · ETA {self._clock(remaining)}"
        return text

    def _compose(self):
        """Return ``(rendered line, visible length)``; called with the lock held."""
        elapsed = max(0.0, time.monotonic() - self._started)
        suffix = self._suffix(elapsed)
        width = self._bar_width(suffix)
        unicode_ok = self._unicode()
        full = self.FULL if unicode_ok else "#"
        empty = self.EMPTY if unicode_ok else "-"

        if self.total:
            filled = min(width, int(width * self.done / self.total))
            cells = full * filled + empty * (width - filled)
        else:
            # A highlight sweeps left to right to show work is happening.
            block = max(4, width // 3)
            offset = (self._frame % (width + block)) - block
            cells = "".join(
                full if offset <= cell < offset + block else empty for cell in range(width)
            )

        if _Ansi.enabled:
            bar = "".join(
                f"{gr}{cell}{nu}" if cell == full else f"{dim}{cell}{nu}" for cell in cells
            )
        else:
            bar = cells

        label = self.label
        if len(label) > self.LABEL_WIDTH:
            ellipsis = "…" if unicode_ok else "..."
            label = label[: self.LABEL_WIDTH - len(ellipsis)] + ellipsis
        line = f"  {bd}{label:<{self.LABEL_WIDTH}}{nu}  {bar}  {suffix}"
        visible = 2 + self.LABEL_WIDTH + 2 + width + 2 + len(suffix)
        return line, visible

    def bar_text(self, done, total, label="Loading posts", elapsed=0.0):
        """The plain determinate line (no colours), handy for tests."""
        width = self.BAR_WIDTH
        filled = min(width, int(width * done / max(total, 1)))
        percent = min(100, int(done * 100 / max(total, 1)))
        bar = self.FULL * filled + self.EMPTY * (width - filled)
        return (
            f"  {label:<{self.LABEL_WIDTH}}  {bar}  "
            f"{done}/{total} · {percent}% · {self._clock(elapsed)}"
        )

    def _draw(self, line, visible):
        # Called with the lock held; pad over the previous, longer line.
        pad = max(0, self._width - visible)
        try:
            self.stream.write("\r" + line + " " * pad)
            self.stream.flush()
        except (OSError, ValueError):
            return
        self._width = visible


_status = _Status()


def set_progress(enabled):
    """Turn the interactive progress line on or off (--debug turns it off)."""
    _status.allowed = bool(enabled)
    if not enabled:
        _status.stop()


class ui:
    """The human-facing report: sectioned, aligned and width-aware.

    Everything degrades gracefully: ASCII rules and marks when the terminal is
    not UTF-8, no colour with ``-c`` or a pipe, and lines that never run past
    the terminal width.
    """

    MAX_WIDTH = 78
    BAR = 20

    @staticmethod
    def width():
        try:
            columns = shutil.get_terminal_size((80, 24)).columns
        except OSError:
            columns = 80
        return max(48, min(ui.MAX_WIDTH, columns - 1))

    @staticmethod
    def _glyphs():
        if _utf8(sys.stdout):
            return {
                "accent": "▌",
                "rule": "─",
                "arrow": "→",
                "dot": "·",
                "bar": "█",
                "track": "░",
            }
        return {"accent": "|", "rule": "-", "arrow": "->", "dot": "-", "bar": "#", "track": "-"}

    @staticmethod
    def _clip(text, limit):
        """One line, collapsed and shortened with an ellipsis."""
        flat = " ".join(str(text or "").split())
        if len(flat) <= limit:
            return flat
        if _utf8(sys.stdout):
            return flat[: max(1, limit - 1)].rstrip() + "…"
        return flat[: max(1, limit - 3)].rstrip() + "..."

    @staticmethod
    def count(value):
        """'74877' -> '74,877'; 1.2K-style shorthand is kept as-is."""
        text = str(value if value is not None else "").strip()
        if not text:
            return "0"
        match = regex.fullmatch(r"([0-9][0-9,.]*)\s*([kKmMbB])?", text)
        if not match:
            return text
        number, suffix = match.group(1), match.group(2)
        if suffix:
            return f"{number}{suffix.upper()}"
        digits = number.replace(",", "")
        if not digits.isdigit():
            return text
        return f"{int(digits):,}"

    @staticmethod
    def when(value):
        """An ISO timestamp as '02 Mar 2026'; anything else is left alone."""
        text = str(value or "").strip()
        if not text or text.lower() in {"none", "nat"}:
            return ""
        try:
            moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ui._clip(text, 19)
        return moment.strftime("%d %b %Y")

    @staticmethod
    def section(title, note=""):
        """'▌ PROFILE' with an optional right-aligned note and a full rule."""
        glyph = ui._glyphs()
        head = f"  {cy}{glyph['accent']}{nu} {bd}{title.upper()}{nu}"
        if note:
            gap = ui.width() - (4 + len(title)) - len(note)
            if gap > 1:
                head += " " * gap + f"{dim}{note}{nu}"
        return f"\n{head}\n{dim}{glyph['rule'] * ui.width()}{nu}\n"

    @staticmethod
    def banner():
        """The framed wordmark printed once at the top of a run."""
        if _quiet:
            return
        glyph = ui._glyphs()
        width = ui.width()
        inner = width - 2
        tagline = "Instagram profile scraper"
        version = f"v{__version__}"
        plain = f"  SoIG  {glyph['dot']}  {tagline}"
        shown = (
            f"  {cy}{bd}SoIG{nu}{dim}  {glyph['dot']}  {nu}{tagline}"
            + " " * max(1, inner - len(plain) - len(version) - 2)
            + f"{dim}{version}{nu}  "
        )
        if inner - len(plain) - len(version) < 3:
            # Too narrow for the tagline: keep just the wordmark and version.
            plain = "  SoIG"
            shown = (
                f"  {cy}{bd}SoIG{nu}"
                + " " * max(1, inner - len(plain) - len(version) - 2)
                + f"{dim}{version}{nu}  "
            )
        if _utf8(sys.stdout):
            tl, tr, bl, br, line, side = "╭", "╮", "╰", "╯", "─", "│"
        else:
            tl = tr = bl = br = "+"
            line, side = "-", "|"
        print(f"\n{dim}{tl}{line * inner}{tr}{nu}")
        print(f"{dim}{side}{nu}{shown}{dim}{side}{nu}")
        print(f"{dim}{bl}{line * inner}{br}{nu}")

    @staticmethod
    def _wrap(text, width):
        """Wrap prose on spaces and long URLs on slashes."""
        text = str(text)
        if len(text) <= width:
            return [text]
        if " " in text.strip():
            return textwrap.wrap(text, width) or [text]
        lines, rest = [], text
        while len(rest) > width:
            cut = rest.rfind("/", 0, width)
            lines.append(rest[: cut if cut > 0 else width])
            rest = rest[cut if cut > 0 else width :]
        lines.append(rest)
        return lines

    @staticmethod
    def _field(label, value, label_width):
        room = ui.width() - 2 - label_width - 2
        lines = ui._wrap(str(value), max(16, room)) or [""]
        text = f"  {dim}{label:<{label_width}}{nu}  {lines[0]}"
        pad = " " * (2 + label_width + 2)
        return "\n".join([text] + [pad + extra for extra in lines[1:]])

    @staticmethod
    def profile(data, user="", source=""):
        """The profile card: who it is, the three counts, then the details."""
        data = data or {}
        username = str(data.get("username") or user or "")
        name = str(data.get("name") or "").strip()
        if name:
            name = ui._clip(name, max(12, ui.width() - len(username) - 24))
        glyph = ui._glyphs()

        note = f"via {source}" if source else ""
        print(ui.section("Profile", note))

        plain = f"@{username}"
        shown = f"{bd}@{username}{nu}"
        if name and name.casefold() != username.casefold():
            plain += f"  {glyph['dot']}  {name}"
            shown += f"{dim}  {glyph['dot']}  {nu}{bd}{name}{nu}"

        badge_mark = "✓ " if _utf8(sys.stdout) else "+ "
        badges = []  # (plain text, rendered text)
        if str(data.get("verified") or "").strip().lower() == "true":
            badges.append((f"{badge_mark}verified", f"{OK} {gr}verified{nu}"))
        if str(data.get("private") or "").strip().lower() == "true":
            badges.append(("private", f"{yl}private{nu}"))
        plain_badges = "  ".join(plain_badge for plain_badge, _ in badges)
        gap = ui.width() - 2 - len(plain) - len(plain_badges)
        head = f"  {shown}"
        if badges and gap > 0:
            head += " " * gap + "  ".join(rendered for _, rendered in badges)
        print(head)

        url = str(data.get("url") or "").strip()
        if url:
            print(f"  {dim}{url}{nu}")

        print()
        sep = "│" if _utf8(sys.stdout) else "|"
        bar = f" {dim}{sep}{nu} "
        print(
            f"  {dim}Followers{nu} {bd}{ui.count(data.get('followers')):>9}{nu}"
            f"{bar}{dim}Following{nu} {bd}{ui.count(data.get('following')):>7}{nu}"
            f"{bar}{dim}Posts{nu} {bd}{ui.count(data.get('posts')):>8}{nu}"
        )

        details = [
            ("Bio", " ".join(str(data.get("bio") or "").split())),
            ("Website", str(data.get("external url") or "").strip()),
            ("Category", str(data.get("business category") or "").strip()),
            ("Facebook", str(data.get("connected to fb") or "").strip()),
            ("Business", "yes" if str(data.get("business account")).lower() == "true" else ""),
            ("New account", "yes" if str(data.get("joined recently")).lower() == "true" else ""),
        ]
        rows = [(label, value) for label, value in details if value]
        if rows:
            print()
            label_width = min(14, max(len(label) for label, _ in rows) + 2)
            for label, value in rows:
                print(ui._field(label, value, label_width))

    @staticmethod
    def posts(posts):
        """A numbered post list with one compact metadata line per post."""
        glyph = ui._glyphs()
        total = len(posts)
        print(ui.section("Posts", f"{total} loaded" if total else "nothing loaded"))
        for index, post in enumerate(posts, start=1):
            caption = ui._clip(post.get("caption"), ui.width() - 8)
            if caption:
                print(f"  {dim}{index:>3}{nu}  {caption}")
            else:
                print(f"  {dim}{index:>3}  (no caption){nu}")
            meta = []
            for key, label in (("likes", "likes"), ("comments", "comments")):
                value = str(post.get(key) or "").strip()
                if value:
                    meta.append(f"{ui.count(value)} {label}")
            if str(post.get("comments disabled") or "").lower() == "true":
                meta.append("comments off")
            when = ui.when(post.get("timestamp"))
            if when:
                meta.append(when)
            location = str(post.get("location") or "").strip()
            if location and location.lower() != "none":
                meta.append(ui._clip(location, 24))
            if meta:
                joined = ui._clip(f" {glyph['dot']} ".join(meta), ui.width() - 7)
                print(f"       {dim}{joined}{nu}")
            url = str(post.get("url") or "").strip()
            if url:
                print(f"       {dim}{url}{nu}")
            if index < total:
                print()

    @staticmethod
    def hashtags(tags):
        """A ranked tag list with inline bars; returns False when empty."""
        glyph = ui._glyphs()
        total = sum(tags.values())
        note = f"{total} mentions {glyph['dot']} {len(tags)} unique" if tags else "none found"
        print(ui.section("Hashtags", note))
        if not tags:
            return False
        top = max(tags.values())
        names = max(len(tag) for tag in tags)
        name_width = min(18, names + 1)
        counts = max(len(str(value)) for value in tags.values())
        for tag, value in sorted(tags.items(), key=lambda item: (-item[1], item[0])):
            filled = max(1, round(ui.BAR * value / top))
            bar = f"{gr}{glyph['bar'] * filled}"
            if filled < ui.BAR:
                bar += f"{dim}{glyph['track'] * (ui.BAR - filled)}"
            bar += f"{nu}"
            label = ui._clip(tag, name_width - 1)
            print(f"  {cy}#{label:<{name_width - 1}}{nu}  {bar}  {bd}{value:>{counts}}{nu}")
        return True

    @staticmethod
    def saved(kind, path):
        arrow = ui._glyphs()["arrow"]
        label = f"saved {kind:<12}"
        plain = f"{label}  {arrow} "
        lines = ui._wrap(str(path), ui.width() - 2 - len(plain))
        print(f"  {OK} {label}  {dim}{arrow}{nu} {lines[0]}")
        pad = " " * (2 + len(plain))
        for extra in lines[1:]:
            print(pad + extra)


useragent = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

REQUEST_TIMEOUT = 15
HOME_URL = "https://www.instagram.com/"
PROFILE_URL = "https://www.instagram.com/{username}/"
WEB_PROFILE_INFO_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"
GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
IG_APP_ID = "936619743392459"
FEED_DOC_ID = "9310670392322965"
# PolarisProfilePageContentQuery: the query Instagram's own web client uses
# for profile pages since web_profile_info started answering 400/429.
PROFILE_QUERIES = (
    (
        "25980296051578533",
        {
            "id": None,
            "render_surface": "PROFILE",
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
        },
    ),
    (
        "28036671149327607",
        {
            "id": None,
            "render_surface": "PROFILE",
            "enable_integrity_filters": True,
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
        },
    ),
    (
        "27937681195819736",
        {
            "id": None,
            "render_surface": "PROFILE",
            "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
        },
    ),
)
PROFILE_DOC_IDS = tuple(doc_id for doc_id, _ in PROFILE_QUERIES)
IPHONE_USER_AGENT = (
    "Instagram 275.0.0.27.98 (iPhone14,5; iOS 17_4; en_US; en; scale=3.00; 1170x2532; 458229237)"
)
FEED_PAGE_SIZE = 12
FEED_PAGE_DELAY = 1.0  # seconds between feed pages when walking the timeline
FEED_MAX_PAGES = 2000  # hard stop, so a bad cursor cannot loop forever
SESSION_CHECK_URL = "https://www.instagram.com/api/v1/accounts/edit/web_form_data/"
SESSION_CHECK_USERNAME = "instagram"
WWW_API = "https://www.instagram.com/api/v1"
WWW_USERNAME_INFO_URL = WWW_API + "/users/{username}/usernameinfo/"
WWW_FEED_BY_USERNAME_URL = WWW_API + "/feed/user/{username}/username/"
WWW_FEED_BY_ID_URL = WWW_API + "/feed/user/{user_id}/"
MOBILE_API = "https://i.instagram.com/api/v1"
MOBILE_APP_ID = "567067343352427"
MOBILE_USER_AGENT = (
    "Instagram 275.0.0.27.98 Android (33/13; 480dpi; 1080x2400; samsung; "
    "SM-G991B; o1s; exynos2100; en_US; 458229237)"
)
MOBILE_PROFILE_INFO_URL = MOBILE_API + "/users/web_profile_info/"
USERNAME_INFO_URL = MOBILE_API + "/users/{username}/usernameinfo/"
USER_INFO_URL = MOBILE_API + "/users/{user_id}/info/"
MOBILE_FEED_URL = MOBILE_API + "/feed/user/{username}/username/"
IG_ASBD_ID = "129477"

COOKIE_NAMES = (
    "sessionid",
    "csrftoken",
    "ds_user_id",
    "mid",
    "ig_did",
    "ig_nrcb",
    "datr",
    "rur",
)

LOGIN_HINT = (
    "Instagram requires a logged-in session for this request. Log in to "
    "instagram.com in your browser and paste the value of the 'sessionid' cookie "
    "when prompted, then run the tool again."
)

LOGIN_HELP = (
    "Instagram is asking for a logged-in session.\n"
    "  1. Log in to https://www.instagram.com in your browser.\n"
    "  2. Open the developer tools (F12) -> Network, reload the page and click the\n"
    "     first request to instagram.com. Copy the whole 'Cookie' request header\n"
    "     (or at least the 'sessionid' cookie value from the Application tab).\n"
    "  3. Paste it below (the value is hidden while you type).\n"
    "Pasting the full cookie header works best: 'sessionid=...; csrftoken=...;\n"
    "ds_user_id=...'. Press Enter without a value to cancel."
)


class InstagramError(Exception):
    """Raised when Instagram does not return usable profile data."""


class LoginRequiredError(InstagramError):
    """Raised when Instagram asks for an authenticated session."""


class RateLimitedError(InstagramError):
    """Raised when Instagram throttles the current IP (HTTP 429)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _dig(data, *keys, default=None):
    """Safely walk nested dictionaries/lists."""
    for key in keys:
        try:
            data = data[key]
        except (KeyError, IndexError, TypeError):
            return default
    return data


def _first_value(data, *keys, default=None):
    """Return the first non-None value among ``keys``."""
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return default


def _as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _stdin_available():
    """True when a prompt can read from stdin without blocking forever."""
    stream = getattr(sys, "stdin", None)
    if stream is None:
        return False
    return not getattr(stream, "closed", False)


def _read_prompt(prompt):
    """Read one line, hidden on a real terminal and visible elsewhere."""
    _status.stop()
    stream = sys.stdin
    if stream is not None and getattr(stream, "isatty", lambda: False)():
        return getpass.getpass(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = stream.readline() if stream is not None else ""
    if not line:
        raise EOFError
    return line.rstrip("\r\n")


def _parse_cookie_input(value):
    """Turn pasted text into a ``{cookie name: value}`` mapping.

    Accepts a bare sessionid, ``sessionid=...``, a full ``Cookie`` header such
    as ``sessionid=abc; csrftoken=def; ds_user_id=123`` or a JSON object of
    cookies. Unknown cookies are kept, so pasting the browser header in full is
    the most reliable option.
    """
    value = (value or "").strip()
    if len(value) >= 7 and value[:7].lower() == "cookie:":
        value = value[7:].strip()
    value = value.strip().strip('"').strip("'")
    if not value:
        return {}

    if value.startswith("{"):
        try:
            payload = json.loads(value)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            return {
                str(name).strip(): str(cookie).strip()
                for name, cookie in payload.items()
                if str(name).strip() and str(cookie).strip()
            }

    if ";" in value:
        cookies = {}
        for part in value.split(";"):
            name, separator, cookie = part.strip().partition("=")
            if separator and name.strip():
                cookies[name.strip()] = cookie.strip()
        if cookies:
            return cookies

    name, separator, cookie = value.partition("=")
    if separator and name.strip().lower() in COOKIE_NAMES:
        return {name.strip(): cookie.strip()}

    return {"sessionid": value}


def session_file_path():
    """Default location for the saved cookies (see --session-file)."""
    return Path.home() / ".config" / "SoIG" / "session.json"


def load_session(path=None):
    """Read saved cookies, ignoring missing or corrupt files."""
    path = Path(path).expanduser() if path else session_file_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in data.items()
        if name in COOKIE_NAMES and str(value).strip()
    }


def save_session(cookies, path=None, username=None):
    """Write cookies as JSON (0600) so later runs can reuse them."""
    cookies = {
        name: value
        for name, value in (cookies or {}).items()
        if name in COOKIE_NAMES and str(value).strip()
    }
    if not cookies.get("sessionid"):
        return None

    path = Path(path).expanduser() if path else session_file_path()
    payload = dict(cookies)
    payload["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if username:
        payload["username"] = username

    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(path.parent, 0o700)
        # Write to a sibling temp file and rename, so a crash mid-write cannot
        # leave a truncated session (which would force a new login).
        temp = path.with_name(f".{path.name}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        return None
    return path


def forget_session(path=None):
    """Delete the saved cookies; returns the path when something was removed."""
    path = Path(path).expanduser() if path else session_file_path()
    try:
        path.unlink()
    except OSError:
        return None
    return path


def _clean_sessionid(value):
    """Return just the sessionid from anything ``_parse_cookie_input`` accepts."""
    return _parse_cookie_input(value).get("sessionid", "")


def _retry_after(response):
    """Read the Retry-After header (seconds) from a throttled response."""
    value = response.headers.get("Retry-After") if response.headers else None
    try:
        seconds = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _response_message(response):
    """Best-effort extraction of Instagram's error message from a response."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload.get("message")
    return None


def _looks_like_login(text):
    """True when the response looks like Instagram's login wall."""
    compact = "".join((text or "")[:500_000].lower().split())
    return (
        '"require_login":true' in compact
        or "<title>login" in compact
        or 'id="loginform"' in compact
    )


def _looks_like_rate_limit(text):
    """True when Instagram serves its 'please wait a few minutes' page."""
    compact = "".join((text or "")[:500_000].lower().split())
    return (
        "pleasewaitafewminutesbeforeyoutryagain" in compact
        or "waitafewminutesbeforeyoutryagain" in compact
    )


_COUNT_RE = r"([\d.,]+\s*[KMB]?)"


def _parse_count(value):
    """Turn '12,345', '1.2M' or '3K' into an int."""
    text = str(value or "").strip().replace(",", "").replace(" ", "")
    if not text:
        return 0
    multiplier = 1
    suffix = text[-1].upper()
    if suffix in "KMB":
        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return 0
    return int(number * multiplier)


def _meta_content(soup, **attrs):
    tag = soup.find("meta", attrs=attrs)
    return (tag.get("content") or "").strip() if tag else ""


def _profile_from_html_meta(html, username):
    """Read name/counts/picture from the page's OpenGraph metadata.

    These tags are rendered for every public profile, including pages where
    Instagram strips the JSON payload for clients it does not trust.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    title = _meta_content(soup, property="og:title")
    description = _meta_content(soup, property="og:description") or _meta_content(
        soup, attrs={"name": "description"}
    )
    image = _meta_content(soup, property="og:image")
    if not description and not title:
        return None

    match = regex.search(r"\(@([^)\s]+)\)", f"{title} {description}")
    if not match or match.group(1).casefold() != str(username).casefold():
        return None

    counts = regex.search(
        rf"{_COUNT_RE}\s*Followers?,\s*{_COUNT_RE}\s*Following,\s*"
        rf"{_COUNT_RE}\s*Posts?",
        description,
        regex.IGNORECASE,
    )
    if not counts:
        return None

    name = title.split("(@")[0].strip()
    if not name:
        name = regex.search(r"from\s+(.+?)\s*\(@", description) or ""
        name = name.group(1).strip() if hasattr(name, "group") else ""

    return {
        "username": match.group(1),
        "full_name": name,
        "biography": "",
        "follower_count": _parse_count(counts.group(1)),
        "following_count": _parse_count(counts.group(2)),
        "media_count": _parse_count(counts.group(3)),
        "profile_pic_url": image,
        "is_private": False,
        "is_verified": False,
    }


def _json_after_marker(text, marker):
    """Return the first JSON object that follows ``marker`` in ``text``."""
    start = 0
    while True:
        index = text.find(marker, start)
        if index == -1:
            return None
        brace = text.find("{", index + len(marker))
        if brace == -1:
            return None
        try:
            return json.JSONDecoder().raw_decode(text[brace:])[0]
        except ValueError:
            start = index + max(len(marker), 1)


def _iter_embedded_data(obj):
    """Yield the data of every GraphQL result embedded in a page's JSON."""
    if isinstance(obj, dict):
        bbox = obj.get("__bbox")
        result = bbox.get("result") if isinstance(bbox, dict) else None
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            yield result["data"]
        for value in obj.values():
            yield from _iter_embedded_data(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            yield from _iter_embedded_data(value)


def _embedded_profile(html, username):
    """Find the profile Instagram embeds in a logged-out page's JSON scripts."""
    wanted = str(username).casefold()
    for match in regex.finditer(
        r'<script type="application/json"[^>]*>(.*?)</script>', html, regex.DOTALL
    ):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        for data in _iter_embedded_data(payload):
            user = data.get("xig_user_by_username")
            if not isinstance(user, dict):
                continue
            if str(user.get("username", "")).casefold() != wanted:
                continue
            user = dict(user)
            user.pop("polaris_ordered_timeline_connection", None)
            if user.get("pk") and not user.get("id"):
                user["id"] = user["pk"]
            return user
    return None


def _is_user_payload(data):
    """True when a JSON object looks like an Instagram profile."""
    if not isinstance(data, dict) or "username" not in data:
        return False
    return any(
        key in data
        for key in (
            "follower_count",
            "edge_followed_by",
            "profile_pic_url",
            "profile_pic_url_hd",
            "media_count",
            "edge_owner_to_timeline_media",
        )
    )


def _iter_user_payloads(payload, depth=0):
    """Yield every user object found inside an arbitrary JSON payload."""
    if depth > 50:
        return
    if isinstance(payload, dict):
        if _is_user_payload(payload):
            yield payload
        for value in payload.values():
            yield from _iter_user_payloads(value, depth + 1)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            yield from _iter_user_payloads(value, depth + 1)


def _find_user_payload(payload, username=None):
    """Find the user object, preferring one for ``username``."""
    wanted = str(username).casefold() if username is not None else None
    fallback = None
    for user in _iter_user_payloads(payload):
        if wanted is not None and str(user.get("username", "")).casefold() == wanted:
            return user
        if fallback is None:
            fallback = user
    return fallback if wanted is None else None


def inspect_page(path):
    """Describe a saved HTML page so a stripped payload can be diagnosed.

    Prints how often each payload marker occurs, then shows the largest
    scripts and a short snippet around the first interesting key. Returns
    True when the file could be read.
    """
    try:
        html = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"  {WARN} could not read {path}: {exc}")
        return False

    print(f"  {OK} {path}: {len(html)} characters")
    markers = (
        "xig_user_by_username",
        "follower_count",
        "biography",
        "edge_owner_to_timeline_media",
        "profile_pic_url",
        "data-sjs",
        "xdt_api",
        "__bbox",
        "web_profile_info",
        "window._sharedData",
        "require_login",
        "consent",
        "checkpoint",
    )
    for name in markers:
        print(f"    {name:<30} {html.count(name)}")

    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script")
    json_scripts = [
        script
        for script in scripts
        if script.get("type") in ("application/json", "application/ld+json")
    ]
    print(f"    script tags                    {len(scripts)}")
    print(f"    JSON script tags               {len(json_scripts)}")
    largest = sorted(scripts, key=lambda s: len(s.get_text() or ""), reverse=True)
    for index, script in enumerate(largest[:5], 1):
        text = script.get_text() or ""
        head = " ".join(text[:110].split())
        print(f"    script {index}: {len(text)} chars, type={script.get('type')}, starts: {head}")

    print("    contexts:")
    seen = set()
    for key in ('"biography"', '"profile_pic_url"', "xdt_api", "xig_user", "__bbox"):
        index = html.find(key)
        if index == -1 or key in seen:
            continue
        seen.add(key)
        snippet = " ".join(html[max(0, index - 60) : index + 140].split())
        print(f"      {key} @ {index}: ...{snippet}...")
    return True


class extra:
    @staticmethod
    def write(in_text):
        """Write text immediately.

        The old typing animation slept 0.1s per character, which cost several
        seconds on every run and only made logs harder to read.
        """
        if _quiet:
            return
        sys.stdout.write(in_text)
        sys.stdout.flush()

    @staticmethod
    def extract_hash_tags(stri):
        """Hashtags in a caption, without trailing punctuation, lower-cased."""
        return [tag.casefold() for tag in regex.findall(r"#(\w+)", stri or "")]

    @staticmethod
    def banner():
        ui.banner()


class main:
    def __init__(
        self,
        user=None,
        sessionid=None,
        interactive=None,
        proxy=None,
        impersonate=None,
        retry_wait=0,
        retries=1,
        login=False,
        debug=False,
        save_cookies=True,
        session_file=None,
        dump_page=None,
        all_posts=False,
        max_posts=0,
        login_only=False,
    ):
        ui.banner()
        self.user = (user or "").strip().lstrip("@").rstrip("/")
        self.base_dir = os.getcwd()
        self.tags = {}
        self.posts = []
        self.posts_error = None
        self._warned_posts_error = False
        self.p_data = {}
        self.profile_pic_url = ""
        self.is_private = False
        self.sessionid = ""
        self._session_verified = False
        self._prompts = 0
        self._feed_items = {}
        self._feed_edges = None
        self._feed_page_info = {}
        self._user_id = None
        self.limited_data = False
        self._warned_limited = False
        self.data_source = ""
        self.all_posts = bool(all_posts)
        self.max_posts = max(0, int(max_posts or 0))
        self.retry_wait = retry_wait
        self.retries = retries
        self.proxy = (proxy or "").strip()
        self.session = self._new_session(impersonate=impersonate, proxy=proxy)
        headers = {"Accept-Language": "en-US,en;q=0.9"}
        if self.impersonating is False:
            # With impersonation curl_cffi already sends a matching UA.
            headers["User-Agent"] = random.choice(useragent)
        self.session.headers.update(headers)
        # Prompting is allowed unless it was explicitly disabled, or stdin is
        # gone (e.g. nohup/cron). It works in IDEs whose run pane is not a TTY
        # by reading a visible line instead of using getpass.
        if interactive is None:
            interactive = _stdin_available()
        self.interactive = bool(interactive)
        self._provided_cookies = _parse_cookie_input(sessionid or "")
        self.force_login = login
        self.debug = debug
        self.save_cookies = save_cookies
        self.session_file = Path(session_file).expanduser() if session_file else session_file_path()
        self._stored_session = {} if self._provided_cookies else load_session(self.session_file)
        self.dump_page = Path(dump_page).expanduser() if dump_page else None
        self._last_status = None
        self._last_url = ""
        self._last_bytes = 0
        if login_only:
            self._login_only_flow()
            return
        if not self.user:
            raise InstagramError(
                "no username was given (pass -u/--username, or -l/--login "
                "on its own to only store a session)"
            )
        self.get_profile()

    def _debug(self, message):
        if self.debug:
            print(f"{cy}[debug]{nu} {message}")

    def _progress(self, text):
        """Start or update the progress line; --debug shows the trace instead."""
        if not self.debug:
            _status.start(text)

    def _progress_bar(self, done, total):
        if not self.debug:
            _status.bar(done, total)

    def _step(self, message):
        """Print a trace line; only with --debug (see -d)."""
        if not self.debug or _quiet:
            return
        _status.stop()
        print(f"{cy}[step]{nu} {message}")

    def _status_text(self):
        return f"HTTP {self._last_status}" if self._last_status is not None else "no response"

    def _new_session(self, impersonate=None, proxy=None):
        """Build the HTTP session, impersonating Chrome when curl_cffi exists.

        Instagram strips the embedded profile payload and rate limits harder for
        clients whose TLS fingerprint does not look like a browser, so the
        impersonating transport is used whenever it is installed.
        """
        want_impersonation = True if impersonate is None else bool(impersonate)
        self.impersonation_missing = want_impersonation and curl_requests is None
        if want_impersonation and curl_requests is not None:
            self.impersonating = True
            session = curl_requests.Session(impersonate="chrome")
        else:
            self.impersonating = False
            session = requests.Session()
        proxy = (proxy or "").strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        return session

    def _anonymous_session(self):
        """A cookie-less session used to read the logged-out profile page.

        Instagram only embeds the profile payload when the page is requested
        like a browser navigation by a logged-out visitor; a request carrying
        the session cookie gets a client-rendered shell instead.
        """
        if curl_requests is not None:
            session = curl_requests.Session(impersonate="chrome")
        else:
            session = requests.Session()
            session.headers["User-Agent"] = random.choice(useragent)
        if self.proxy:
            session.proxies.update({"http": self.proxy, "https": self.proxy})
        return session

    def _get(self, url, method="GET", **kwargs):
        # ``_session`` lets a caller (the logged-out page) reuse this method's
        # error handling and status bookkeeping with a throwaway session.
        session = kwargs.pop("_session", None) or self.session
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        self._last_url = url
        try:
            response = session.request(method, url, **kwargs)
        except Exception as exc:  # transport errors differ per backend
            self._last_status = None
            self._debug(f"GET {url} failed: {exc}")
            raise InstagramError(f"could not reach {url}: {exc}") from exc
        self._last_status = getattr(response, "status_code", None)
        try:
            self._last_bytes = len(getattr(response, "content", b"") or b"")
        except TypeError:
            self._last_bytes = 0
        self._debug(f"GET {url} -> {self._status_text()} ({self._last_bytes} bytes)")
        return response

    def _set_cookies(self, cookies):
        """Attach every cookie from a parsed mapping (or clear the jar)."""
        if not cookies:
            self._set_sessionid(None)
            return
        self.session.cookies.clear()
        for name, value in cookies.items():
            if name != "sessionid":
                self.session.cookies.set(name, value, domain=".instagram.com")
        self._set_sessionid(cookies.get("sessionid", ""))

    def _set_sessionid(self, sessionid):
        """Attach (or clear) the sessionid cookie used for every request."""
        self.sessionid = sessionid or ""
        self._session_verified = False
        if not self.sessionid:
            self.session.cookies.clear()
            return
        self.session.cookies.set("sessionid", self.sessionid, domain=".instagram.com")
        self._warm_up_session()

    def _warm_up_session(self):
        """Load instagram.com once so the session gets its csrftoken/mid cookies.

        The website expects a csrftoken alongside the sessionid on its private
        endpoints; sending only sessionid makes even valid sessions look
        suspicious and is a common cause of HTTP 429/403 responses.
        """
        if self.session.cookies.get("csrftoken"):
            return
        with contextlib.suppress(InstagramError):
            self._get(
                HOME_URL,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    )
                },
            )

    def _api_headers(self, reference_username=None, mobile=False):
        """Headers for API calls, matching the web or mobile Instagram client."""
        headers = self._session_headers(reference_username)
        if mobile:
            headers["User-Agent"] = MOBILE_USER_AGENT
            headers["X-IG-App-ID"] = MOBILE_APP_ID
            headers.pop("X-ASBD-ID", None)
            headers.pop("X-Instagram-AJAX", None)
            headers.pop("Origin", None)
        return headers

    def _session_headers(self, reference_username=None):
        username = reference_username or self.user
        headers = {
            "Accept": "*/*",
            "Referer": PROFILE_URL.format(username=username),
            "Origin": "https://www.instagram.com",
            "X-IG-App-ID": IG_APP_ID,
            "X-IG-WWW-Claim": "0",
            "X-Instagram-AJAX": "1",
            "X-ASBD-ID": IG_ASBD_ID,
            "X-Requested-With": "XMLHttpRequest",
        }
        csrf = self.session.cookies.get("csrftoken", domain=".instagram.com")
        if not csrf:
            csrf = self.session.cookies.get("csrftoken")
        if csrf:
            headers["X-CSRFToken"] = csrf
        return headers

    def _session_status(self):
        """Check whether the current sessionid is accepted by Instagram.

        Returns a ``(status, detail)`` tuple where status is ``"valid"``,
        ``"invalid"`` or ``"unknown"`` (could not be determined).
        """
        try:
            response = self._get(SESSION_CHECK_URL, headers=self._session_headers())
        except InstagramError as exc:
            return "unknown", str(exc)

        if response.status_code in (401, 403) or "/accounts/login" in response.url:
            return "invalid", f"HTTP {response.status_code}"
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                form = payload.get("form_data")
                if isinstance(form, dict):
                    username = form.get("username")
                    if username:
                        return "valid", f" (logged in as @{username})"
                    return "valid", ""
                if payload.get("require_login"):
                    return "invalid", payload.get("message") or "login required"
                if payload.get("status") == "fail":
                    return "invalid", payload.get("message") or "session rejected"
            if _looks_like_login(response.text):
                return "invalid", "Instagram served a login page"
        return self._session_status_from_profile()

    def _session_status_from_profile(self):
        """Second opinion: can we read a known public profile with this session?"""
        try:
            response = self._get(
                WEB_PROFILE_INFO_URL,
                params={"username": SESSION_CHECK_USERNAME},
                headers=self._api_headers(SESSION_CHECK_USERNAME),
            )
        except InstagramError as exc:
            return "unknown", str(exc)

        if response.status_code in (401, 403) or "/accounts/login" in response.url:
            return "invalid", f"HTTP {response.status_code}"
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                return "unknown", "Instagram returned an unexpected response"
            if _dig(payload, "data", "user"):
                return "valid", ""
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and payload.get("require_login"):
                return "invalid", message or "login required"
            return "unknown", message or "unexpected response"
        return "unknown", f"HTTP {response.status_code}"

    def _use_provided_sessionid(self):
        """Validate cookies passed in explicitly."""
        if not self._provided_cookies:
            return
        self._set_cookies(self._provided_cookies)
        if not self.sessionid:
            print(f"\n  {WARN} the pasted cookies did not include a 'sessionid'; ignoring them.")
            self._set_cookies({})
            return
        self._step("checking the sessionid with Instagram ...")
        status, detail = self._session_status()
        self._step(f"sessionid check: {status} ({self._status_text()})")
        if status == "valid":
            self._session_verified = True
            print(f"\n  {OK} sessionid works{detail}")
            if len(self._provided_cookies) > 1:
                print(f"  {OK} using {len(self._provided_cookies)} cookies")
            self._save_session(self._provided_cookies)
            return
        print()
        if status == "invalid":
            print(f"  {WARN} the provided sessionid does not work ({detail}); ignoring it.")
            self._set_sessionid(None)
        else:
            print(f"  {WARN} could not verify the provided sessionid ({detail}); trying it anyway.")
        print()

    def _use_stored_session(self):
        """Reuse the cookies saved by an earlier run when they still work."""
        if not self._stored_session:
            return
        self._step(
            f"saved session: {self.session_file} ({', '.join(sorted(self._stored_session))})"
        )
        self._set_cookies(self._stored_session)
        self._step("checking the saved session with Instagram ...")
        status, detail = self._session_status()
        self._step(f"saved session check: {status} ({self._status_text()})")
        if status == "valid":
            self._session_verified = True
            print(f"\n  {OK} using the saved session{detail} ({self.session_file})")
            return
        if status == "invalid":
            print(f"\n  {WARN} the saved session in {self.session_file} has expired ({detail}).")
            self._set_cookies({})
            self._stored_session = {}
            if self.interactive:
                print("    Please log in again.\n")
                print(f"  {WARN} {LOGIN_HELP}\n")
                if not self._prompt_for_cookies():
                    self._explain_offline_login()
            else:
                self._explain_offline_login()
            return
        print(f"\n  {WARN} could not verify the saved session ({detail}); using it anyway.")

    def _save_session(self, cookies):
        if not self.save_cookies:
            return
        # Instagram sets csrftoken and mid itself when instagram.com is
        # loaded; keep them so later runs do not have to fetch them again.
        merged = dict(cookies)
        for name in COOKIE_NAMES:
            value = self.session.cookies.get(name)
            if value and not merged.get(name):
                merged[name] = value
        cookies = merged
        path = save_session(cookies, self.session_file)
        if path:
            print(f"  {OK} saved session to {path}")
        elif cookies.get("sessionid"):
            print(f"  {WARN} could not save the session to {self.session_file}")

    def _fetch_profile_with_login(self):
        """Fetch the profile, asking for a sessionid when Instagram demands one."""
        attempt = 0
        while True:
            try:
                return self._fetch_profile()
            except LoginRequiredError as exc:
                if self._session_verified or self._prompts >= 3:
                    # Retrying with another cookie will not help: Instagram is
                    # throttling or refusing this IP, not the session.
                    self._step(
                        "login wall with a verified session -> not asking for another sessionid"
                    )
                    raise InstagramError(str(exc)) from None
                self._step("login required -> asking for a sessionid")
                self._prompt_for_sessionid(exc)
            except RateLimitedError as exc:
                if not self.sessionid and self.interactive and self._prompts == 0 and attempt == 0:
                    # No session yet and Instagram is refusing the requests:
                    # let the user try an authenticated one before giving up.
                    _status.stop()
                    self._step(
                        "every source was rate limited and no session is set "
                        "-> asking for a sessionid"
                    )
                    print(
                        f"\n  {WARN} Instagram refused every request and no "
                        "session is set. Paste a sessionid to retry with an "
                        "authenticated request, or press Enter to stop."
                    )
                    print(f"  {WARN} {LOGIN_HELP}\n")
                    if self._prompt_for_cookies():
                        attempt += 1
                        continue
                    raise
                if not self.retry_wait or attempt >= self.retries:
                    raise
                attempt += 1
                wait = exc.retry_after or self.retry_wait
                _status.stop()
                self._step(
                    f"rate limited -> waiting {wait}s and retrying ({attempt}/{self.retries})"
                )
                print(
                    f"\n  {WARN} rate limited; retrying in {wait}s ({attempt}/{self.retries}) ..."
                )
                time.sleep(wait)

    def _ask_csrftoken(self, cookies):
        """Ask for the csrftoken when the pasted value did not include one."""
        if cookies.get("csrftoken"):
            return cookies
        prompt = f"{gr}csrftoken{nu} (optional, press Enter to fetch it automatically): "
        try:
            value = _read_prompt(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return cookies
        value = (value or "").strip().strip('"').strip("'")
        if value.lower().startswith("csrftoken="):
            value = value.split("=", 1)[1].strip()
        if value:
            cookies = dict(cookies, csrftoken=value)
        return cookies

    def _prompt_for_cookies(self):
        """Ask for sessionid and csrftoken until they work; False if cancelled."""
        if not self.interactive:
            return False

        while True:
            try:
                value = _read_prompt(f"{gr}sessionid{nu} (or the whole Cookie header): ")
            except (EOFError, KeyboardInterrupt):
                print()
                return False

            if not value.strip():
                return False

            cookies = _parse_cookie_input(value)
            if not cookies.get("sessionid"):
                print(
                    f"  {WARN} that value did not include a 'sessionid' cookie. "
                    "Please try again, or press Enter to cancel.\n"
                )
                continue

            self._set_cookies(cookies)
            status, detail = self._session_status()
            if status == "invalid":
                print(
                    f"  {WARN} that sessionid does not work ({detail}). "
                    "Please try again, or press Enter to cancel.\n"
                )
                self._set_cookies({})
                continue

            self._prompts += 1
            cookies = self._ask_csrftoken(cookies)
            self._set_cookies(cookies)

            if status == "valid":
                self._session_verified = True
                print(f"  {OK} sessionid works{detail}\n")
            else:
                print(f"  {WARN} could not verify the sessionid ({detail}); trying it anyway.\n")
            self._save_session(cookies)
            return True

    def _prompt_for_sessionid(self, error):
        """Ask for a sessionid until it is accepted; raise ``error`` to cancel."""
        if not self.interactive:
            raise error

        print()
        print(f"  {WARN} {LOGIN_HELP}\n")
        if not self._prompt_for_cookies():
            raise error from None

    def offer_login(self):
        """Let the user log in before scraping (--login)."""
        if self.sessionid:
            return
        print(f"\n  {WARN} {LOGIN_HELP}\n")
        self._prompt_for_cookies()

    def _login_only_flow(self):
        """Check or replace the stored session; used by ``--login`` alone.

        There is no profile to scrape in this mode, so the whole job is to end
        up with a session Instagram accepts. A saved session that still works
        is reported as such instead of asking for the same sessionid again.
        """
        if self._stored_session:
            self._set_cookies(self._stored_session)
            self._step("checking the saved session with Instagram ...")
            status, detail = self._session_status()
            self._step(f"saved session check: {status} ({self._status_text()})")
            if status != "invalid":
                if status == "valid":
                    self._session_verified = True
                    print(f"  {OK} already logged in{detail} ({self.session_file})")
                else:
                    print(
                        f"  {WARN} could not verify the saved session "
                        f"({detail}); keeping it anyway."
                    )
                print("    To replace it, run --forget-session and then --login again.")
                return
            print(f"\n  {WARN} the saved session in {self.session_file} has expired ({detail}).\n")
            self._set_cookies({})
            self._stored_session = {}

        print(f"  {WARN} {LOGIN_HELP}\n")
        if not self._prompt_for_cookies():
            raise InstagramError("no session was provided")
        if not self.save_cookies:
            print(
                f"  {WARN} --no-save-session is set, so the session was checked but not stored.\n"
            )

    def _check_status(self, response, source):
        self._last_status = getattr(response, "status_code", None)
        try:
            self._last_bytes = len(getattr(response, "content", b"") or b"")
        except TypeError:
            self._last_bytes = 0
        if response.status_code == 404:
            raise InstagramError(f"the {source} returned HTTP 404 (account not found)")
        if response.status_code == 429:
            raise RateLimitedError(
                f"the {source} is rate limiting this IP (HTTP 429)",
                retry_after=_retry_after(response),
            )
        if response.status_code in (401, 403) or "/accounts/login" in response.url:
            raise LoginRequiredError(
                f"the {source} requires a logged-in session (HTTP {response.status_code})"
            )
        if response.status_code == 400:
            message = _response_message(response) or ""
            if "log" in message.lower():
                raise LoginRequiredError(f"the {source} says: {message}")
            if message:
                raise InstagramError(f"the {source} returned HTTP 400: {message}")
        if response.status_code >= 400:
            raise InstagramError(f"the {source} returned HTTP {response.status_code}")

    def _json_user(
        self,
        url,
        source,
        params=None,
        mobile=False,
        headers=None,
        method="GET",
        data=None,
    ):
        """Request a JSON endpoint and pull the user object out of the response."""
        response = self._get(
            url,
            method=method,
            params=params,
            data=data,
            headers=(headers if headers is not None else self._api_headers(mobile=mobile)),
        )
        try:
            self._check_status(response, source)
        except InstagramError:
            text = (response.text or "")[:300].replace("\n", " ")
            self._debug(f"{source} body: {text}")
            raise
        try:
            payload = response.json()
        except ValueError:
            if _looks_like_login(response.text):
                raise LoginRequiredError(
                    f"the {source} served a login wall instead of JSON"
                ) from None
            if _looks_like_rate_limit(response.text):
                raise RateLimitedError(f"the {source} is rate limiting this IP") from None
            raise InstagramError(f"the {source} returned non-JSON data") from None

        user = _dig(payload, "data", "user")
        if not isinstance(user, dict):
            user = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user, dict):
            user = _find_user_payload(payload, self.user)
        if isinstance(user, dict):
            self.data_source = source
            return user

        message = payload.get("message") if isinstance(payload, dict) else None
        text = (response.text or "")[:300].replace("\n", " ")
        self._debug(f"{source} body: {text}")
        if isinstance(payload, dict) and (
            payload.get("require_login") or "log" in str(message or "").lower()
        ):
            raise LoginRequiredError(message or f"the {source} requires a login")
        raise InstagramError(message or f"the {source} returned no user object")

    def _fetch_from_api(self):
        """web_profile_info on the website host."""
        return self._json_user(WEB_PROFILE_INFO_URL, "profile API", params={"username": self.user})

    def _fetch_from_mobile_api(self):
        """web_profile_info on the mobile host, with mobile app headers."""
        return self._json_user(
            MOBILE_PROFILE_INFO_URL,
            "mobile profile API",
            params={"username": self.user},
            mobile=True,
        )

    def _fetch_from_page_json(self):
        """Ask the profile URL for JSON directly (works for some sessions)."""
        response = self._get(
            PROFILE_URL.format(username=self.user),
            params={"__a": "1", "__d": "dis"},
            headers=self._api_headers(),
        )
        self._check_status(response, "profile JSON")
        try:
            payload = response.json()
        except ValueError:
            raise InstagramError("the profile JSON endpoint returned no JSON") from None
        user = _find_user_payload(payload, self.user)
        if not isinstance(user, dict):
            message = payload.get("message") if isinstance(payload, dict) else None
            raise InstagramError(message or "the profile JSON had no profile")
        self.data_source = "profile JSON endpoint"
        return user

    def _fetch_from_username_info(self):
        return self._json_user(
            WWW_USERNAME_INFO_URL.format(username=self.user), "username lookup API"
        )

    def _fetch_from_mobile_username_info(self):
        return self._json_user(
            USERNAME_INFO_URL.format(username=self.user),
            "mobile username lookup API",
            mobile=True,
        )

    def _fetch_from_profile_graphql(self):
        """Ask the GraphQL profile query (PolarisProfilePageContentQuery)."""
        user_id = self._resolve_user_id()
        if not user_id:
            raise InstagramError("could not resolve the numeric user id")
        headers = {
            **self._session_headers(),
            "User-Agent": IPHONE_USER_AGENT,
            "X-IG-App-ID": IG_APP_ID,
            "Referer": "https://www.instagram.com/",
            "Accept-Language": "en-US,en;q=0.8",
        }
        problems = []
        for doc_id, template in PROFILE_QUERIES:
            variables = dict(template, id=str(user_id))
            try:
                return self._json_user(
                    GRAPHQL_URL,
                    "profile GraphQL",
                    method="POST",
                    params={
                        "doc_id": doc_id,
                        "variables": json.dumps(variables, separators=(",", ":")),
                        "server_timestamps": "true",
                    },
                    headers=headers,
                    data="",
                )
            except InstagramError as exc:
                problems.append(f"{doc_id}: {exc}")
                continue
        raise InstagramError("; ".join(problems) or "no profile doc_id answered")

    def _fetch_from_user_info(self):
        """The app's user info endpoint, reached via the numeric id.

        Instagram's web profile endpoints answer 400 for many web sessions
        while the GraphQL post feed keeps working, so the id can still be
        resolved from the feed and used here.
        """
        user_id = self._resolve_user_id()
        if not user_id:
            raise InstagramError("could not resolve the numeric user id")
        return self._json_user(
            USER_INFO_URL.format(user_id=user_id),
            "user info API",
            mobile=True,
        )

    def _resolve_user_id(self):
        """Find the numeric id of the target account, if Instagram exposes it."""
        if self._user_id:
            return self._user_id
        for value in (self.p_data.get("pk"), self.p_data.get("id")):
            if value:
                self._user_id = str(value)
                return self._user_id
        lookups = (
            self._fetch_from_username_info,
            self._fetch_from_mobile_username_info,
        )
        for fetch in lookups:
            try:
                user = fetch()
            except InstagramError:
                continue
            value = user.get("pk") or user.get("id")
            if value:
                self._user_id = str(value)
                return self._user_id
        # Last resort: the post feed embeds the author, and the feed often
        # keeps working when the profile endpoints are refused.
        try:
            edges = self._fetch_feed()
        except InstagramError:
            return None
        for edge in edges:
            node = edge.get("node", edge) if isinstance(edge, dict) else {}
            value = _dig(node, "user", "pk") or _dig(node, "user", "id")
            if value:
                self._user_id = str(value)
                return self._user_id
        return None

    def _profile_from_feed(self, items, source):
        """Rebuild a profile object from feed items (each item embeds its user)."""
        if not items:
            raise InstagramError(f"the {source} returned no posts")
        first = items[0] if isinstance(items[0], dict) else {}
        user = first.get("user")
        if not isinstance(user, dict) or not user.get("username"):
            user = _find_user_payload(first, self.user)
        if not isinstance(user, dict):
            raise InstagramError(f"the {source} did not include the profile")
        if user.get("follower_count") is None and user.get("pk"):
            for url, mobile in (
                (USER_INFO_URL.format(user_id=user["pk"]), True),
                (WWW_API + "/users/{user_id}/info/".format(user_id=user["pk"]), False),
            ):
                try:
                    return self._json_user(url, "user info API", mobile=mobile)
                except InstagramError:
                    continue
        return user

    def _fetch_from_feed(self):
        """Rebuild the profile from the website's user feed."""
        profile = self._profile_from_feed(self._www_feed_items(), "user feed")
        self.data_source = "user feed"
        return profile

    def _fetch_from_mobile_feed(self):
        """Rebuild the profile from the mobile app's user feed."""
        profile = self._profile_from_feed(self._mobile_feed_items(), "mobile user feed")
        self.data_source = "mobile user feed"
        return profile

    def _request_feed_items(self, url, source, mobile):
        response = self._get(
            url,
            params={"count": FEED_PAGE_SIZE},
            headers=self._api_headers(mobile=mobile),
        )
        self._check_status(response, source)
        try:
            payload = response.json()
        except ValueError:
            if _looks_like_rate_limit(response.text):
                raise RateLimitedError(f"the {source} is rate limiting this IP") from None
            raise InstagramError(f"the {source} returned non-JSON data") from None
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            message = payload.get("message") if isinstance(payload, dict) else None
            raise InstagramError(message or f"the {source} returned no posts")
        return items

    def _www_feed_items(self):
        if "www" in self._feed_items:
            return self._feed_items["www"]
        errors = []
        urls = [WWW_FEED_BY_USERNAME_URL.format(username=self.user)]
        user_id = self._resolve_user_id()
        if user_id:
            urls.append(WWW_FEED_BY_ID_URL.format(user_id=user_id))
        for url in urls:
            try:
                items = self._request_feed_items(url, "user feed", mobile=False)
            except InstagramError as exc:
                errors.append(exc)
                continue
            self._feed_items["www"] = items
            return items
        rate_limited = [exc for exc in errors if isinstance(exc, RateLimitedError)]
        raise (rate_limited or errors)[-1]

    def _mobile_feed_items(self):
        if "mobile" in self._feed_items:
            return self._feed_items["mobile"]
        errors = []
        urls = [MOBILE_FEED_URL.format(username=self.user)]
        user_id = self._resolve_user_id()
        if user_id:
            urls.append(f"{MOBILE_API}/feed/user/{user_id}/")
        for url in urls:
            try:
                items = self._request_feed_items(url, "mobile user feed", mobile=True)
            except InstagramError as exc:
                errors.append(exc)
                continue
            self._feed_items["mobile"] = items
            return items
        rate_limited = [exc for exc in errors if isinstance(exc, RateLimitedError)]
        raise (rate_limited or errors)[-1]

    def _find_user_in_html(self, html):
        user = _embedded_profile(html, self.user)
        if user is not None:
            return user
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            if not text:
                continue
            for marker in ("window._sharedData", "window.__additionalDataLoaded"):
                payload = _json_after_marker(text, marker)
                user = _find_user_payload(payload, self.user) if payload is not None else None
                if user is not None:
                    return user
            if script.get("type") in ("application/json", "application/ld+json"):
                try:
                    payload = json.loads(text)
                except ValueError:
                    continue
                user = _find_user_payload(payload, self.user)
                if user is not None:
                    return user
            # Modern pages expose the profile under the xig_user_by_username key.
            if "xig_user_by_username" in text:
                payload = _json_after_marker(text, '"xig_user_by_username"')
                user = _find_user_payload(payload, self.user) if payload is not None else None
                if user is not None and (
                    str(user.get("username", "")).casefold() == self.user.casefold()
                ):
                    return user
            # Some pages embed the bootstrap payload as a bare JS object literal
            # instead of assigning it to a well known global.
            if '"username"' in text and (
                '"edge_followed_by"' in text
                or '"follower_count"' in text
                or '"profile_pic_url"' in text
            ):
                brace = text.find("{")
                if brace != -1:
                    try:
                        payload = json.JSONDecoder().raw_decode(text[brace:])[0]
                    except ValueError:
                        payload = None
                    user = _find_user_payload(payload, self.user) if payload is not None else None
                    if user is not None:
                        return user
        return None

    def _debug_markers(self, html):
        """Say what an HTML page does and does not contain (for --debug)."""
        if not self.debug:
            return
        markers = (
            "xig_user_by_username",
            '"follower_count"',
            '"biography"',
            "edge_owner_to_timeline_media",
            "window._sharedData",
            "data-sjs",
        )
        found = ", ".join(f"{name}={'yes' if name in html else 'no'}" for name in markers)
        self._debug(f"page has {html.count('<script')} script tags; markers: {found}")

    def _fetch_from_html(self):
        response = self._get(
            PROFILE_URL.format(username=self.user),
            headers={
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Referer": "https://www.instagram.com/",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        self._check_status(response, "profile page")
        self._step(f"profile page: {self._status_text()}, {len(response.text)} characters")
        self._debug_markers(response.text)
        if self.dump_page:
            try:
                self.dump_page.parent.mkdir(parents=True, exist_ok=True)
                self.dump_page.write_text(response.text, encoding="utf-8")
                self._step(f"saved the profile page HTML to {self.dump_page}")
            except OSError as exc:
                print(f"  {WARN} could not save the page HTML: {exc}")
        user = self._find_user_in_html(response.text)
        if user is not None:
            self.data_source = "profile page"
            return user

        user = _profile_from_html_meta(response.text, self.user)
        if user is not None:
            # Instagram stripped the JSON payload, but the public metadata is
            # enough for the headline numbers.
            self.limited_data = True
            self.data_source = "profile page metadata"
            self._step(
                "profile page: no embedded profile JSON, only OpenGraph "
                "metadata (bio and posts are stripped for this request)"
            )
            return user

        if _looks_like_rate_limit(response.text):
            raise RateLimitedError(
                "the profile page is rate limiting this IP (HTTP 200 with a "
                "'wait a few minutes' page)"
            )
        if _looks_like_login(response.text):
            raise LoginRequiredError("the profile page served a login wall instead of profile data")
        raise InstagramError("the profile page did not contain embedded profile data")

    def _fetch_from_anonymous_page(self):
        """Read the profile from a logged-out page load, as a browser would."""
        session = self._anonymous_session()
        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
        try:
            response = self._get(
                PROFILE_URL.format(username=self.user),
                headers=headers,
                _session=session,
            )
        except InstagramError as exc:
            raise InstagramError(f"could not reach the logged-out profile page: {exc}") from exc
        self._last_status = getattr(response, "status_code", None)
        self._check_status(response, "logged-out profile page")
        self._step(
            f"logged-out profile page: {self._status_text()}, {len(response.text)} characters"
        )
        if self.dump_page:
            target = self.dump_page.with_name(
                f"{self.dump_page.stem}.anonymous{self.dump_page.suffix}"
            )
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(response.text, encoding="utf-8")
                self._step(f"saved the logged-out page HTML to {target}")
            except OSError as exc:
                print(f"  {WARN} could not save the page HTML: {exc}")
        user = _embedded_profile(response.text, self.user)
        if user is None:
            raise InstagramError("the logged-out profile page did not embed the profile")
        self.data_source = "logged-out profile page"
        return user

    def _profile_sources(self):
        return (
            ("profile API", self._fetch_from_api),
            ("profile page", self._fetch_from_html),
            ("logged-out profile page", self._fetch_from_anonymous_page),
            ("profile JSON", self._fetch_from_page_json),
            ("profile GraphQL", self._fetch_from_profile_graphql),
            ("username lookup API", self._fetch_from_username_info),
            ("user feed", self._fetch_from_feed),
            ("mobile profile API", self._fetch_from_mobile_api),
            ("mobile username lookup API", self._fetch_from_mobile_username_info),
            ("user info API", self._fetch_from_user_info),
            ("mobile user feed", self._fetch_from_mobile_feed),
        )

    @staticmethod
    def _fill_missing(user, extra_data):
        """Fill gaps in a full profile with values from the metadata fallback."""
        merged = dict(user)
        for key, value in (extra_data or {}).items():
            if value in (None, "", 0, [], {}):
                continue
            if merged.get(key) in (None, "", 0, [], {}):
                merged[key] = value
        return merged

    def _fetch_profile(self):
        problems = []
        login_required = False
        rate_limit = None
        fallback = None
        sources = self._profile_sources()
        total = len(sources)
        self._step(f"trying {total} profile sources ...")
        for number, (label, fetch) in enumerate(sources, 1):
            started = time.monotonic()
            self._step(f"{number}/{total} {label} ...")
            try:
                user = fetch()
            except RateLimitedError as exc:
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                self._step(
                    f"{number}/{total} {label}: rate limited "
                    f"({self._status_text()}, {time.monotonic() - started:.1f}s)"
                )
                if rate_limit is None or (exc.retry_after or 0) > (rate_limit.retry_after or 0):
                    rate_limit = exc
                continue
            except LoginRequiredError as exc:
                login_required = True
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                self._step(
                    f"{number}/{total} {label}: login required "
                    f"({self._status_text()}, {time.monotonic() - started:.1f}s)"
                )
                continue
            except InstagramError as exc:
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                self._step(f"{number}/{total} {label}: failed ({self._status_text()}) -> {exc}")
                continue
            if user:
                self._debug(f"{label}: ok")
                self._step(
                    f"{number}/{total} {label}: ok ({self._status_text()}, "
                    f"{time.monotonic() - started:.1f}s)"
                )
                if not self.limited_data:
                    if fallback is not None:
                        user = self._fill_missing(user, fallback[0])
                    return user
                # Only public metadata so far: keep it and try the remaining
                # endpoints for a richer payload.
                if fallback is None:
                    fallback = (user, self.data_source, self.limited_data)
                self._step(
                    f"{number}/{total} {label}: partial profile, looking for a fuller one ..."
                )
                self.limited_data = False
                self.data_source = ""
                continue
            problems.append(f"{label}: no profile data returned")
            self._debug(f"{label}: no profile data returned")
            self._step(f"{number}/{total} {label}: no profile data returned")

        if fallback is not None:
            user, source, limited = fallback
            self.data_source = source
            self.limited_data = limited
            self._step(f"using the best available profile: {source}")
            return user

        details = "\n    ".join(problems)
        message = f"could not retrieve profile data for '{self.user}'.\n    {details}"
        if login_required and not self._session_verified:
            extra = ""
            if rate_limit is not None:
                extra = "\n    " + self._rate_limit_hint(rate_limit.retry_after)
            raise LoginRequiredError(message + f"\n    {LOGIN_HINT}{extra}")
        if rate_limit is not None:
            raise RateLimitedError(
                message + "\n    " + self._rate_limit_hint(rate_limit.retry_after),
                retry_after=rate_limit.retry_after,
            )
        if login_required:
            raise InstagramError(
                message + "\n    The sessionid was accepted, so this is not a login problem: "
                "Instagram is refusing these endpoints for this IP/account. "
                "Wait a few minutes and try again."
            )
        raise InstagramError(
            message + "\n    Instagram may be blocking this IP; wait a few minutes and try again."
        )

    def _rate_limit_hint(self, retry_after):
        wait = (
            f"Instagram asked to wait {retry_after} seconds"
            if retry_after
            else "Wait a few minutes (Instagram usually clears this in 10-60 minutes)"
        )
        hint = f"{wait} and run the command again."
        if self._session_verified:
            hint += (
                " Your sessionid was accepted, so this is throttling, not a login "
                "problem - pasting another sessionid will not help."
            )
        else:
            hint += " Logging in with a valid sessionid can also raise the limit."
        hint += " If it keeps happening, use another network or pass --proxy http://host:port."
        return hint

    def _announce_transport(self):
        backend = "curl_cffi (Chrome impersonation)" if self.impersonating else "requests"
        self._step(f"transport: {backend}")
        if self.impersonation_missing:
            print(
                f"\n  {WARN} curl_cffi is not installed, so requests are not "
                "browser-impersonated.\n"
                "    Instagram answers such clients with stripped pages, "
                "'feedback_required' and 429s.\n"
                "    Fix it with: pip install curl_cffi\n"
            )
        if not self._provided_cookies and not self._stored_session and not self.force_login:
            print(
                f"\n  {WARN} no sessionid is set: Instagram will only expose the "
                "public profile metadata (name, followers, following, posts).\n"
                f"    For bio, posts and tags run: python3 main.py -u {self.user} "
                "--login\n"
                "    The sessionid and csrftoken you enter are saved for later "
                f"runs in {self.session_file}\n"
            )

    def get_profile(self):
        started = time.monotonic()
        self._step(
            f"target: {self.user} | login: {self.force_login} | save session: "
            f"{self.save_cookies} | proxy: {'yes' if self.session.proxies else 'no'}"
        )
        self._announce_transport()
        self._use_provided_sessionid()
        if not self.sessionid and not self.force_login:
            self._use_stored_session()
        if self.force_login:
            self.offer_login()
        self._step("session: " + ("set" if self.sessionid else "none (public data only)"))
        self._progress(f"Fetching @{self.user}")
        try:
            self.p_data = self._fetch_profile_with_login()
        finally:
            _status.stop()
        self._step(
            f"profile source: {self.data_source or 'unknown'} "
            f"({'full' if not self.limited_data else 'metadata only'})"
        )
        if self.limited_data:
            self._upgrade_limited_profile()
        result = self._apply_profile()
        self._step(f"done in {time.monotonic() - started:.1f}s")
        return result

    def _apply_profile(self):
        """Derive the output, posts and tags from ``self.p_data``."""
        self.is_private = _as_bool(self.p_data.get("is_private"))
        self.profile_pic_url = (
            self.p_data.get("profile_pic_url_hd") or self.p_data.get("profile_pic_url") or ""
        )
        self.posts = self._load_posts()
        if self.is_private and not self.posts:
            self.output = self._profile_details()
            self._explain_private_profile()
            return {}

        if self.is_private:
            # The page we could read was the logged-out one, which always
            # looks private. If the session follows the account, the feed
            # still answers with its posts and the profile is readable.
            self._step(
                f"posts: private account, but this session reads "
                f"{len(self.posts)} posts, so it follows it"
            )
        self.output = self._profile_details()

        tags = []
        for post in self.posts:
            if post["caption"]:
                tags.extend(extra.extract_hash_tags(post["caption"]))
        self.tags = dict(collections.Counter(tags))
        if self.debug and self.posts:
            first = next((post for post in self.posts if post["caption"]), None)
            if first is not None:
                self._debug(f"first caption: {first['caption'][:300]}")
                self._debug(f"hashtags in it: {extra.extract_hash_tags(first['caption'])}")
        captions = sum(1 for post in self.posts if post["caption"])
        if self.tags:
            self._step(
                f"tags: {sum(self.tags.values())} hashtags in {captions} "
                f"captions ({len(self.tags)} unique)"
            )
        else:
            if self.max_posts:
                hint = (
                    f" (only the first {self.max_posts} posts; "
                    "raise -m/--max-posts or add -a/--all-posts)"
                )
            elif self.all_posts:
                hint = ""
            else:
                hint = " (first page only; try --all-posts)"
            self._step(f"tags: none of the {captions} captions contain a hashtag{hint}")
        return self.tags

    def to_dict(self):
        """A JSON-serialisable snapshot of this run (used by ``--json``)."""
        profile = dict(getattr(self, "output", None) or {})
        for key in ("followers", "following", "posts"):
            if key in profile:
                profile[key] = _parse_count(profile[key])
        return {
            "scraped_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "username": self.user,
            "profile": profile,
            "posts": [dict(post) for post in self.posts],
            "tags": dict(self.tags),
            "posts_error": self.posts_error,
            "limited_data": bool(self.limited_data),
        }

    def _profile_details(self):
        p_data = self.p_data
        username = str(p_data.get("username") or self.user)

        followers = _first_value(p_data, "follower_count")
        if followers is None:
            followers = _dig(p_data, "edge_followed_by", "count", default=0)

        following = _first_value(p_data, "following_count")
        if following is None:
            following = _dig(p_data, "edge_follow", "count", default=0)

        posts = _first_value(p_data, "media_count")
        if posts is None:
            posts = _dig(p_data, "edge_owner_to_timeline_media", "count")
        if posts is None:
            posts = len(self.posts)

        external_url = p_data.get("external_url") or _dig(p_data, "bio_links", 0, "url", default="")

        return {
            "username": username,
            "name": str(p_data.get("full_name") or ""),
            "url": f"instagram.com/{username}",
            "followers": str(followers),
            "following": str(following),
            "posts": str(posts),
            "bio": str(p_data.get("biography") or "").replace("\n", ", "),
            "external url": str(external_url or ""),
            "private": str(self.is_private),
            "verified": str(_as_bool(p_data.get("is_verified"))),
            "profile pic url": str(self.profile_pic_url or ""),
            "business account": str(
                _as_bool(p_data.get("is_business_account"))
                or _as_bool(p_data.get("is_professional_account"))
                or _as_bool(p_data.get("is_business"))
            ),
            "connected to fb": str(p_data.get("connected_fb_page") or ""),
            "joined recently": str(_as_bool(p_data.get("is_joined_recently"))),
            "business category": str(
                p_data.get("business_category_name")
                or p_data.get("category_name")
                or p_data.get("category")
                or ""
            ),
        }

    def _wants_more_posts(self, have):
        """True when -a/--all-posts or -m/--max-posts asks for more than ``have``."""
        if self.max_posts:
            return have < self.max_posts
        return self.all_posts

    def _cap_posts(self, posts):
        """Stop at -m/--max-posts however the posts were loaded."""
        if self.max_posts and len(posts) > self.max_posts:
            self._step(f"posts: keeping the first {self.max_posts} of {len(posts)}")
            return posts[: self.max_posts]
        return posts

    def _load_posts(self):
        """Normalise the posts from the profile payload or the GraphQL feed."""
        self._step("posts: loading the first page ...")
        edges = _dig(self.p_data, "edge_owner_to_timeline_media", "edges", default=[])
        if isinstance(edges, list) and edges:
            posts = [
                self._normalize_post(edge["node"])
                for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
            ]
            self._step(f"posts: {len(posts)} from the profile payload")
            if not self._wants_more_posts(len(posts)):
                return self._cap_posts(posts)
            # The payload only carries the newest posts. Walk the feed for the
            # rest; if it does not answer, the payload posts are still ours.
            try:
                feed_edges = self._fetch_all_feed_edges()
            except InstagramError as exc:
                self._step(f"posts: keeping the payload posts ({exc})")
                return self._cap_posts(posts)
            if not feed_edges:
                return self._cap_posts(posts)
            edges = feed_edges

        else:
            try:
                edges = self._fetch_all_feed_edges()
            except InstagramError as exc:
                for getter in (self._www_feed_items, self._mobile_feed_items):
                    try:
                        items = getter()
                    except InstagramError:
                        continue
                    return self._cap_posts(
                        [self._normalize_post(item) for item in items if isinstance(item, dict)]
                    )
                self.posts_error = str(exc)
                self._step(f"posts: none available ({exc})")
                return []

        posts = [
            self._normalize_post(edge["node"])
            for edge in edges
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
        ]
        self._step(f"posts: {len(posts)} from the GraphQL feed")
        return self._cap_posts(posts)

    def _fetch_feed(self):
        """Fetch the first page of the user's posts from Instagram's feed query."""
        if self._feed_edges is not None:
            return self._feed_edges
        edges, page_info = self._fetch_feed_page()
        self._feed_edges = edges
        self._feed_page_info = page_info
        return edges

    def _fetch_feed_page(self, after=None):
        """Request one page of the feed and return ``(edges, page_info)``."""
        variables = {
            "after": after,
            "before": None,
            "data": {
                "count": FEED_PAGE_SIZE,
                "include_reel_media_seen_timestamp": True,
                "include_relationship_info": True,
                "latest_besties_reel_media": True,
                "latest_reel_media": True,
            },
            "first": FEED_PAGE_SIZE,
            "last": None,
            "username": self.user,
            "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
            "__relay_internal__pv__PolarisShareSheetV3relayprovider": True,
        }
        response = self._get(
            GRAPHQL_URL,
            params={
                "doc_id": FEED_DOC_ID,
                "variables": json.dumps(variables, separators=(",", ":")),
            },
            headers={
                **self._session_headers(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        self._check_status(response, "post feed")
        try:
            payload = response.json()
        except ValueError:
            raise InstagramError("the post feed returned non-JSON data") from None

        connection = _dig(
            payload,
            "data",
            "xdt_api__v1__feed__user_timeline_graphql_connection",
            default={},
        )
        edges = connection.get("edges") if isinstance(connection, dict) else None
        if not isinstance(edges, list):
            message = payload.get("message") if isinstance(payload, dict) else None
            raise InstagramError(message or "the post feed returned no posts")
        page_info = connection.get("page_info") if isinstance(connection, dict) else {}
        if self.debug and edges:
            first = edges[0].get("node", edges[0]) if isinstance(edges[0], dict) else {}
            self._debug(f"feed first node keys: {sorted(first)[:25]}")
            self._debug(f"feed caption: {str(first.get('caption'))[:160]}")
            self._debug(f"feed page_info: {page_info}")
        return edges, page_info if isinstance(page_info, dict) else {}

    def _fetch_all_feed_edges(self):
        """Fetch the first page, then follow the end cursor as far as asked.

        One page is always requested. Later pages are walked when -a asks for
        the whole timeline, or when -m asks for more than the first page holds.
        The two flags are independent: -m alone pages up to N posts, and with -a
        it caps the walk at N. Trimming happens in ``_cap_posts``.
        """
        _status.start("Loading posts")
        try:
            return self._walk_feed_edges()
        finally:
            _status.stop()

    def _walk_feed_edges(self):
        """The paging loop behind ``_fetch_all_feed_edges``."""
        edges = list(self._fetch_feed())
        target = self.max_posts
        if not self.all_posts and not target:
            return edges
        if target and len(edges) >= target:
            return edges
        expected = _first_value(self.p_data, "media_count") or _dig(
            self.p_data, "edge_owner_to_timeline_media", "count"
        )
        total = target or expected or 0
        if total > FEED_PAGE_SIZE:
            self._progress_bar(len(edges), total)
        if target:
            self._step(
                f"posts: collecting {target} posts, one request every {FEED_PAGE_DELAY:g}s ..."
            )
        else:
            self._step(
                "posts: walking the whole timeline"
                + (f" ({expected} posts)" if expected else "")
                + f", one request every {FEED_PAGE_DELAY:g}s ..."
            )
        page_info = getattr(self, "_feed_page_info", {}) or {}
        page = 1
        while page_info.get("has_next_page") and page < FEED_MAX_PAGES:
            if target and len(edges) >= target:
                break
            cursor = page_info.get("end_cursor")
            if not cursor:
                self._step("posts: Instagram reported more pages but no cursor")
                break
            page += 1
            time.sleep(FEED_PAGE_DELAY)
            try:
                new_edges, page_info = self._fetch_feed_page(after=cursor)
            except InstagramError as exc:
                _status.stop()
                self._step(f"posts: stopped after page {page - 1} ({exc})")
                self.posts_error = str(exc)
                break
            if not new_edges:
                break
            edges.extend(new_edges)
            if total:
                self._progress_bar(len(edges), total)
            self._step(
                f"posts: page {page} -> {len(edges)} posts"
                + ("" if page_info.get("has_next_page") else " (last page)")
            )
        _status.stop()
        if page > 1:
            self._step(f"posts: collected {len(edges)} posts in {page} pages")
        return edges

    @staticmethod
    def _normalize_post(node):
        """Support both the legacy timeline payload and the current feed shape."""
        picture = _dig(node, "thumbnail_resources", 0, "src", default="")
        if not picture:
            picture = _dig(node, "image_versions2", "candidates", 0, "url", default="")
        if not picture:
            picture = node.get("display_url") or node.get("thumbnail_src") or ""

        caption = _dig(node, "edge_media_to_caption", "edges", 0, "node", "text", default="")
        if not caption:
            raw_caption = node.get("caption")
            if isinstance(raw_caption, dict):
                caption = raw_caption.get("text") or ""
            elif isinstance(raw_caption, str):
                caption = raw_caption

        comments = _dig(node, "edge_media_to_comment", "count")
        if comments is None:
            comments = node.get("comment_count")
        if comments is None:
            comments = 0

        timestamp = _first_value(node, "taken_at_timestamp", "taken_at", default="")

        likes = _dig(node, "edge_liked_by", "count")
        if likes is None:
            likes = _dig(node, "edge_media_preview_like", "count")
        if likes is None:
            likes = node.get("like_count")
        if likes is None:
            likes = 0

        code = str(node.get("code") or "")
        return {
            "picture": picture,
            "url": f"instagram.com/p/{code}" if code else "",
            "caption": str(caption or ""),
            "comments": str(comments),
            "comments disabled": str(_as_bool(node.get("comments_disabled", False))),
            "timestamp": str(timestamp),
            "likes": str(likes),
            "location": str(node.get("location")),
            "accessability caption": str(node.get("accessibility_caption") or ""),
        }

    def print_data_(self):
        """The default profile output; tags are only shown with -t."""
        _status.stop()
        self._extracted_from_print_data_2()
        self._warn_limited_data()
        self._warn_posts_error()

    def print_data(self):
        _status.stop()
        self._extracted_from_print_data_2()
        self._warn_limited_data()

    def _upgrade_limited_profile(self):
        """Offer a login when Instagram only exposed the public metadata."""
        if self._session_verified:
            self._step(
                "not asking for another sessionid: the current one is valid, "
                "so Instagram is refusing the full profile for this "
                "account/IP"
            )
            return
        if not self.interactive:
            self._explain_offline_login()
            return

        print(
            f"\n  {WARN} Instagram only returned the public profile metadata "
            "because this run is not logged in (bio, posts and tags are hidden)."
        )
        print(
            "Paste a sessionid / Cookie header to fetch the full profile, "
            "or press Enter to keep the public numbers."
        )
        if not self._prompt_for_cookies():
            return

        try:
            profile = self._fetch_profile()
        except InstagramError as exc:
            print(f"  {WARN} could not fetch the full profile: {exc}\n")
            return

        self.p_data = profile
        self.limited_data = False
        self._feed_items = {}
        self._user_id = None
        self.posts_error = None
        print(f"  {OK} full profile available\n")

    def _explain_offline_login(self):
        print(
            f"  {WARN} no terminal input is available, so you cannot be asked "
            "for a sessionid here.\n"
            f"    Run this in a terminal instead: python3 main.py -u {self.user} "
            "--login\n"
            f"    or store the cookies with --session-file {self.session_file} "
            "yourself.\n"
        )

    def _warn_limited_data(self):
        if self.limited_data and not self._warned_limited:
            print()
            if self._session_verified:
                advice = (
                    "The session itself is valid, so logging in again will not "
                    "help: Instagram is refusing the full profile for this "
                    "account/IP. Wait a few minutes and retry, or try another "
                    "network with --proxy."
                )
            elif self.impersonating:
                advice = (
                    "Instagram stripped the full payload even with browser "
                    "impersonation; try another network or --proxy."
                )
            else:
                advice = (
                    "Install curl_cffi (pip install curl_cffi) for browser "
                    "impersonation, or --proxy, to get the full profile."
                )
            print(
                f"  {WARN} only the public profile metadata was available "
                f"({self.data_source}): followers/following/posts are shown, but "
                f"bio, posts and tags are unavailable.\n"
                f"    {advice}\n"
            )
            self._warned_limited = True

    def _explain_private_profile(self):
        print()
        print(
            f"  {WARN} @{self.user} is private and Instagram returned no "
            "posts for it.\n"
            "    A private profile is readable only when the logged-in account "
            "follows it and the follow request was accepted.\n"
        )
        if not self.sessionid:
            print(f"    This run has no session: python3 main.py -u {self.user} --login\n")
        elif not (self._stored_session or {}).get("csrftoken"):
            print(
                "    Your saved session has no csrftoken. Run --login (or paste "
                "the whole Cookie header)\n"
                "    so Instagram sees a complete browser session.\n"
            )
        else:
            print(
                "    If you do follow it, Instagram is refusing the feed for "
                "this account/IP; retry later or use --proxy.\n"
            )

    def _warn_posts_error(self):
        if self.posts_error and not self._warned_posts_error:
            print()
            print(f"  {WARN} could not load posts: {self.posts_error}\n")
            self._warned_posts_error = True

    def _extracted_from_print_data_2(self):
        ui.profile(self.output, self.user, self.data_source)

    def make_dir(self):
        path = os.path.join(self.base_dir, self.user)
        os.makedirs(path, exist_ok=True)
        os.chdir(path)

    def scrap_uploads(self):
        _status.stop()
        if self.is_private and not self.posts:
            self._explain_private_profile()
            return 1

        if not self.posts:
            if self.posts_error:
                self._warn_posts_error()
            else:
                print(f"  {WARN} no posts found\n")
            return 1

        ui.posts(self.posts)
        print()

    def most_common_tags(self):
        _status.stop()
        self._warn_posts_error()
        captions = sum(1 for post in self.posts if post["caption"])
        if ui.hashtags(self.tags):
            return
        print(
            f"  {WARN} no hashtags were found in the {captions} captions "
            f"loaded from {len(self.posts)} posts.\n"
            "    The tag list is built from post captions, so an account "
            "that does not use #hashtags has none to count.\n"
        )
        if self.max_posts:
            print(
                f"    Only the first {self.max_posts} posts were checked; "
                "raise --max-posts or add --all-posts "
                "to scan more.\n"
            )
        elif not self.all_posts:
            print(
                "    Only the first page of posts was checked; add "
                "--all-posts to scan the whole timeline.\n"
            )

    def save_data(self):
        self.make_dir()
        self._warn_posts_error()
        print()

        # DOWNLOAD PROFILE PICTURE
        if self.profile_pic_url:
            time.sleep(1)
            self._progress("Downloading avatar")
            try:
                r = self._get(
                    self.profile_pic_url,
                    headers={"User-Agent": random.choice(useragent)},
                )
            except InstagramError as exc:
                _status.stop()
                print(f"  {BAD} could not download profile picture: {exc}")
            else:
                _status.stop()
                if r.status_code == 200:
                    with open("profile_pic.jpg", "wb") as f:
                        f.write(r.content)
                    ui.saved("avatar", os.path.join(os.getcwd(), "profile_pic.jpg"))
                else:
                    print(f"  {WARN} could not download profile picture: HTTP {r.status_code}")

        # SAVES PROFILE DATA TO TEXT FILE
        self.output_data = self._profile_details()
        with open("profile_data.txt", "w") as f:
            f.write(json.dumps(self.output_data))
        ui.saved("profile data", os.path.join(os.getcwd(), "profile_data.txt"))

        # SAVES INFORMATION
        posts = {}
        for index, post in enumerate(self.posts):
            entry = {key: value for key, value in post.items() if key not in ("picture", "caption")}
            entry["picture"] = post["picture"]
            if post["caption"]:
                entry["caption"] = post["caption"]
            posts[index] = entry

        with open("posts_data.txt", "w") as f:
            f.write(json.dumps(posts))
        ui.saved("post info", os.path.join(os.getcwd(), "posts_data.txt"))

        # SAVES TAGS
        with open("tags.txt", "w") as f:
            f.write(json.dumps(self.tags))
        ui.saved("tags", os.path.join(os.getcwd(), "tags.txt"))
        print()
