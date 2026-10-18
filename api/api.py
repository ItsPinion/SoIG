#!/bin/env python3

import collections
import getpass
import json
import os
import random
import re as regex
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:  # optional: makes requests look like a real Chrome build at the TLS level
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - exercised only without the extra
    curl_requests = None

collections.Callable = collections.abc.Callable

nu = "\033[0m"
re = "\033[1;31m"
gr = "\033[1;32m"
cy = "\033[1;36m"

useragent = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

REQUEST_TIMEOUT = 15
PROFILE_URL = "https://www.instagram.com/{username}/"
WEB_PROFILE_INFO_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"
GRAPHQL_URL = "https://www.instagram.com/graphql/query/"
IG_APP_ID = "936619743392459"
FEED_DOC_ID = "9310670392322965"
FEED_PAGE_SIZE = 12
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
    "instagram.com in your browser, copy the value of the 'sessionid' cookie and "
    "paste it when prompted (or export it as IG_SESSIONID), then run the tool again."
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
    if getattr(stream, "closed", False):
        return False
    return True


def _read_prompt(prompt):
    """Read one line, hidden on a real terminal and visible elsewhere."""
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
    """Where the saved cookies live (override with IG_SESSION_FILE)."""
    override = os.environ.get("IG_SESSION_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "SoIG" / "session.json"


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
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.chmod(path, 0o600)
    except OSError:
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


class extra:
    @staticmethod
    def tiny_url(url):
        if not url:
            return ""
        try:
            apiurl = "https://tinyurl.com/api-create.php?url="
            with urllib.request.urlopen(
                apiurl + urllib.parse.quote(url, safe=""), timeout=5
            ) as response:
                return response.read().decode("utf-8").strip()
        except Exception:
            # URL shortening is cosmetic; never let it break a scrape.
            return url

    @staticmethod
    def write(in_text):
        for char in in_text:
            time.sleep(0.1)
            sys.stdout.write(char)
            sys.stdout.flush()

    @staticmethod
    def extract_hash_tags(stri):
        return [part[1:] for part in stri.split() if part.startswith("#")]

    @staticmethod
    def banner():
        print(
            f"""{cy}
  ╔═╗ ╔═╗     ╦  ╔═╗
  ╚═╗ ║ ║     ║  ║ ╦
  ╚═╝ ╚═╝  {gr}o{cy}  ╩  ╚═╝
 
        {gr}Coded By :
  {gr}Yezz123{nu}
\t            """
        )


class main:
    def __init__(
        self,
        user,
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
    ):
        self.user = user.strip().lstrip("@").rstrip("/")
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
        self._user_id = None
        self.limited_data = False
        self._warned_limited = False
        self.data_source = ""
        self.retry_wait = retry_wait
        self.retries = retries
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
        self._provided_cookies = _parse_cookie_input(
            sessionid if sessionid is not None else os.environ.get("IG_SESSIONID", "")
        )
        self.force_login = login
        self.debug = debug
        self.save_cookies = save_cookies
        self.session_file = (
            Path(session_file).expanduser() if session_file else session_file_path()
        )
        self._stored_session = (
            {} if self._provided_cookies else load_session(self.session_file)
        )
        self.get_profile()

    def _debug(self, message):
        if self.debug:
            print(f"{cy}[debug]{nu} {message}")

    def _new_session(self, impersonate=None, proxy=None):
        """Build the HTTP session, impersonating Chrome when curl_cffi exists.

        Instagram strips the embedded profile payload and rate limits harder for
        clients whose TLS fingerprint does not look like a browser, so the
        impersonating transport is used whenever it is installed.
        """
        want_impersonation = True if impersonate is None else bool(impersonate)
        if want_impersonation and curl_requests is not None:
            self.impersonating = True
            session = curl_requests.Session(impersonate="chrome")
        else:
            self.impersonating = False
            session = requests.Session()
        proxy = (proxy if proxy is not None else os.environ.get("IG_PROXY", "")).strip()
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})
        return session

    def _get(self, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        try:
            return self.session.get(url, **kwargs)
        except Exception as exc:  # transport errors differ per backend
            raise InstagramError(f"could not reach {url}: {exc}") from exc

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
        try:
            self._get(
                "https://www.instagram.com/",
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "image/avif,image/webp,*/*;q=0.8"
                    )
                },
            )
        except InstagramError:
            pass

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
        """Validate cookies passed as an argument or via IG_SESSIONID."""
        if not self._provided_cookies:
            return
        self._set_cookies(self._provided_cookies)
        if not self.sessionid:
            print(
                f"\n{re}[!]{gr} the pasted cookies did not include a 'sessionid'; "
                "ignoring them."
            )
            self._set_cookies({})
            return
        status, detail = self._session_status()
        if status == "valid":
            self._session_verified = True
            print(f"\n{gr}[+]{nu} sessionid works{detail}")
            if len(self._provided_cookies) > 1:
                print(f"{gr}[+]{nu} using {len(self._provided_cookies)} cookies")
            self._save_session(self._provided_cookies)
            return
        print()
        if status == "invalid":
            print(
                f"{re}[!]{gr} the provided sessionid does not work ({detail}); "
                "ignoring it."
            )
            self._set_sessionid(None)
        else:
            print(
                f"{re}[!]{gr} could not verify the provided sessionid ({detail}); "
                "trying it anyway."
            )
        print()

    def _use_stored_session(self):
        """Reuse the cookies saved by an earlier run when they still work."""
        if not self._stored_session:
            return
        self._set_cookies(self._stored_session)
        status, detail = self._session_status()
        if status == "valid":
            self._session_verified = True
            print(
                f"\n{gr}[+]{nu} using the saved session{detail} "
                f"({self.session_file})"
            )
            return
        if status == "invalid":
            print(
                f"\n{re}[!]{gr} the saved session in {self.session_file} has expired "
                f"({detail})."
            )
            self._set_cookies({})
            self._stored_session = {}
            if self.interactive:
                print("    Please log in again.\n")
                print(f"{re}[!]{gr} {LOGIN_HELP}\n")
                if not self._prompt_for_cookies():
                    self._explain_offline_login()
            else:
                self._explain_offline_login()
            return
        print(
            f"\n{re}[!]{gr} could not verify the saved session ({detail}); "
            "using it anyway."
        )

    def _save_session(self, cookies):
        if not self.save_cookies:
            return
        path = save_session(cookies, self.session_file)
        if path:
            print(f"{gr}[+]{nu} saved session to {path}")
        elif cookies.get("sessionid"):
            print(
                f"{re}[!]{gr} could not save the session to {self.session_file}"
            )

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
                    raise InstagramError(str(exc)) from None
                self._prompt_for_sessionid(exc)
            except RateLimitedError as exc:
                if (
                    not self.sessionid
                    and self.interactive
                    and self._prompts == 0
                    and attempt == 0
                ):
                    # No session yet and Instagram is refusing the requests:
                    # let the user try an authenticated one before giving up.
                    print(
                        f"\n{re}[!]{gr} Instagram refused every request and no "
                        "session is set. Paste a sessionid to retry with an "
                        "authenticated request, or press Enter to stop."
                    )
                    print(f"{re}[!]{gr} {LOGIN_HELP}\n")
                    if self._prompt_for_cookies():
                        attempt += 1
                        continue
                    raise
                if not self.retry_wait or attempt >= self.retries:
                    raise
                attempt += 1
                wait = exc.retry_after or self.retry_wait
                print(
                    f"\n{re}[!]{nu} rate limited; retrying in {wait}s "
                    f"({attempt}/{self.retries}) ..."
                )
                time.sleep(wait)

    def _ask_csrftoken(self, cookies):
        """Ask for the csrftoken when the pasted value did not include one."""
        if cookies.get("csrftoken"):
            return cookies
        prompt = (
            f"{gr}csrftoken{nu} (optional, press Enter to fetch it "
            "automatically): "
        )
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
                value = _read_prompt(f"{gr}sessionid{nu}: ")
            except (EOFError, KeyboardInterrupt):
                print()
                return False

            if not value.strip():
                return False

            cookies = _parse_cookie_input(value)
            if not cookies.get("sessionid"):
                print(
                    f"{re}[!]{gr} that value did not include a 'sessionid' cookie. "
                    "Please try again, or press Enter to cancel.\n"
                )
                continue

            self._set_cookies(cookies)
            status, detail = self._session_status()
            if status == "invalid":
                print(
                    f"{re}[!]{gr} that sessionid does not work ({detail}). "
                    "Please try again, or press Enter to cancel.\n"
                )
                self._set_cookies({})
                continue

            self._prompts += 1
            cookies = self._ask_csrftoken(cookies)
            self._set_cookies(cookies)

            if status == "valid":
                self._session_verified = True
                print(f"{gr}[+]{nu} sessionid works{detail}\n")
            else:
                print(
                    f"{re}[!]{gr} could not verify the sessionid ({detail}); "
                    "trying it anyway.\n"
                )
            self._save_session(cookies)
            return True

    def _prompt_for_sessionid(self, error):
        """Ask for a sessionid until it is accepted; raise ``error`` to cancel."""
        if not self.interactive:
            raise error

        print()
        print(f"{re}[!]{gr} {LOGIN_HELP}\n")
        if not self._prompt_for_cookies():
            raise error from None

    def offer_login(self):
        """Let the user log in before scraping (--login)."""
        if self.sessionid:
            return
        print(f"\n{re}[!]{gr} {LOGIN_HELP}\n")
        self._prompt_for_cookies()

    @staticmethod
    def _check_status(response, source):
        if response.status_code == 404:
            raise InstagramError(f"the {source} returned HTTP 404 (account not found)")
        if response.status_code == 429:
            raise RateLimitedError(
                f"the {source} is rate limiting this IP (HTTP 429)",
                retry_after=_retry_after(response),
            )
        if response.status_code in (401, 403) or "/accounts/login" in response.url:
            raise LoginRequiredError(
                f"the {source} requires a logged-in session "
                f"(HTTP {response.status_code})"
            )
        if response.status_code == 400:
            message = _response_message(response) or ""
            if "log" in message.lower():
                raise LoginRequiredError(f"the {source} says: {message}")
        if response.status_code >= 400:
            raise InstagramError(f"the {source} returned HTTP {response.status_code}")

    def _json_user(self, url, source, params=None, mobile=False):
        """GET a JSON endpoint and pull the user object out of the response."""
        response = self._get(
            url, params=params, headers=self._api_headers(mobile=mobile)
        )
        self._check_status(response, source)
        try:
            payload = response.json()
        except ValueError:
            if _looks_like_login(response.text):
                raise LoginRequiredError(
                    f"the {source} served a login wall instead of JSON"
                ) from None
            if _looks_like_rate_limit(response.text):
                raise RateLimitedError(f"the {source} is rate limiting this IP")
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
        if isinstance(payload, dict) and (
            payload.get("require_login") or "log" in str(message or "").lower()
        ):
            raise LoginRequiredError(message or f"the {source} requires a login")
        raise InstagramError(message or f"the {source} returned no user object")

    def _fetch_from_api(self):
        """web_profile_info on the website host."""
        return self._json_user(
            WEB_PROFILE_INFO_URL, "profile API", params={"username": self.user}
        )

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
        profile = self._profile_from_feed(
            self._mobile_feed_items(), "mobile user feed"
        )
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
                raise RateLimitedError(
                    f"the {source} is rate limiting this IP"
                ) from None
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
            urls.append(MOBILE_API + "/feed/user/{user_id}/".format(user_id=user_id))
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
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script"):
            text = script.string or script.get_text() or ""
            if not text:
                continue
            for marker in ("window._sharedData", "window.__additionalDataLoaded"):
                payload = _json_after_marker(text, marker)
                user = (
                    _find_user_payload(payload, self.user)
                    if payload is not None
                    else None
                )
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
                user = (
                    _find_user_payload(payload, self.user)
                    if payload is not None
                    else None
                )
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
                    user = (
                        _find_user_payload(payload, self.user)
                        if payload is not None
                        else None
                    )
                    if user is not None:
                        return user
        return None

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
            return user

        if _looks_like_rate_limit(response.text):
            raise RateLimitedError(
                "the profile page is rate limiting this IP (HTTP 200 with a "
                "'wait a few minutes' page)"
            )
        if _looks_like_login(response.text):
            raise LoginRequiredError(
                "the profile page served a login wall instead of profile data"
            )
        raise InstagramError(
            "the profile page did not contain embedded profile data"
        )

    def _profile_sources(self):
        return (
            ("profile API", self._fetch_from_api),
            ("profile page", self._fetch_from_html),
            ("profile JSON", self._fetch_from_page_json),
            ("username lookup API", self._fetch_from_username_info),
            ("user feed", self._fetch_from_feed),
            ("mobile profile API", self._fetch_from_mobile_api),
            ("mobile username lookup API", self._fetch_from_mobile_username_info),
            ("mobile user feed", self._fetch_from_mobile_feed),
        )

    def _fetch_profile(self):
        problems = []
        login_required = False
        rate_limit = None
        for label, fetch in self._profile_sources():
            try:
                user = fetch()
            except RateLimitedError as exc:
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                if rate_limit is None or (
                    exc.retry_after or 0
                ) > (rate_limit.retry_after or 0):
                    rate_limit = exc
                continue
            except LoginRequiredError as exc:
                login_required = True
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                continue
            except InstagramError as exc:
                problems.append(f"{label}: {exc}")
                self._debug(f"{label}: {exc}")
                continue
            if user:
                self._debug(f"{label}: ok")
                return user
            problems.append(f"{label}: no profile data returned")
            self._debug(f"{label}: no profile data returned")

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
                message
                + "\n    The sessionid was accepted, so this is not a login problem: "
                "Instagram is refusing these endpoints for this IP/account. "
                "Wait a few minutes and try again."
            )
        raise InstagramError(
            message + "\n    Instagram may be blocking this IP; wait a few minutes "
            "and try again."
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
        hint += (
            " If it keeps happening, use another network or set "
            "IG_PROXY=http://host:port."
        )
        return hint

    def _announce_transport(self):
        backend = "curl_cffi (Chrome impersonation)" if self.impersonating else "requests"
        self._debug(f"transport: {backend}")
        if (
            not self._provided_cookies
            and not self._stored_session
            and not self.force_login
        ):
            print(
                f"\n{re}[!]{gr} no sessionid is set: Instagram will only expose the "
                "public profile metadata (name, followers, following, posts).\n"
                f"    For bio, posts and tags run: python3 main.py -u {self.user} "
                "--login\n"
                "    The sessionid and csrftoken you enter are saved for later "
                f"runs in {self.session_file}\n"
            )

    def get_profile(self):
        extra.write(f"\n{gr}[+]{nu} getting profile ...")
        self._announce_transport()
        self._use_provided_sessionid()
        if not self.sessionid and not self.force_login:
            self._use_stored_session()
        if self.force_login:
            self.offer_login()
        self.p_data = self._fetch_profile_with_login()
        if self.limited_data:
            self._upgrade_limited_profile()
        return self._apply_profile()

    def _apply_profile(self):
        """Derive the output, posts and tags from ``self.p_data``."""
        self.is_private = _as_bool(self.p_data.get("is_private"))
        self.profile_pic_url = (
            self.p_data.get("profile_pic_url_hd")
            or self.p_data.get("profile_pic_url")
            or ""
        )
        if self.is_private:
            self.output = self._profile_details()
            print(f"{re}[!]{gr} private profile can't scrap data !\n")
            return {}

        self.posts = self._load_posts()
        self.output = self._profile_details()

        tags = []
        for post in self.posts:
            if post["caption"]:
                tags.extend(extra.extract_hash_tags(post["caption"]))
        self.tags = dict(collections.Counter(tags))
        return self.tags

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

        external_url = p_data.get("external_url") or _dig(
            p_data, "bio_links", 0, "url", default=""
        )

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
            "profile pic url": extra.tiny_url(self.profile_pic_url),
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

    def _load_posts(self):
        """Normalise the posts from the profile payload or the GraphQL feed."""
        edges = _dig(
            self.p_data, "edge_owner_to_timeline_media", "edges", default=[]
        )
        if isinstance(edges, list) and edges:
            return [
                self._normalize_post(edge["node"])
                for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
            ]

        try:
            edges = self._fetch_feed()
        except InstagramError as exc:
            for getter in (self._www_feed_items, self._mobile_feed_items):
                try:
                    items = getter()
                except InstagramError:
                    continue
                return [
                    self._normalize_post(item)
                    for item in items
                    if isinstance(item, dict)
                ]
            self.posts_error = str(exc)
            return []

        return [
            self._normalize_post(edge["node"])
            for edge in edges
            if isinstance(edge, dict) and isinstance(edge.get("node"), dict)
        ]

    def _fetch_feed(self):
        """Fetch the first page of the user's posts from Instagram's feed query."""
        variables = {
            "after": None,
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

        edges = _dig(
            payload,
            "data",
            "xdt_api__v1__feed__user_timeline_graphql_connection",
            "edges",
            default=[],
        )
        if not isinstance(edges, list):
            message = payload.get("message") if isinstance(payload, dict) else None
            raise InstagramError(message or "the post feed returned no posts")
        return edges

    @staticmethod
    def _normalize_post(node):
        """Support both the legacy timeline payload and the current feed shape."""
        picture = _dig(node, "thumbnail_resources", 0, "src", default="")
        if not picture:
            picture = _dig(node, "image_versions2", "candidates", 0, "url", default="")
        if not picture:
            picture = node.get("display_url") or node.get("thumbnail_src") or ""

        caption = _dig(
            node, "edge_media_to_caption", "edges", 0, "node", "text", default=""
        )
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

        return {
            "picture": picture,
            "caption": str(caption or ""),
            "comments": str(comments),
            "comments disabled": str(_as_bool(node.get("comments_disabled", False))),
            "timestamp": str(timestamp),
            "likes": str(likes),
            "location": str(node.get("location")),
            "accessability caption": str(node.get("accessibility_caption") or ""),
        }

    def print_data_(self):
        self._extracted_from_print_data_2()
        self._warn_limited_data()
        self._warn_posts_error()
        print(f"{gr}[+]{nu} most used user tags : \n")
        for key, value in collections.Counter(self.tags).most_common(5):
            print(f"{gr}{key:<17} : {nu}{value}")
        print("")

    def print_data(self):
        self._extracted_from_print_data_2()
        self._warn_limited_data()

    def _upgrade_limited_profile(self):
        """Offer a login when Instagram only exposed the public metadata."""
        if self._session_verified:
            return
        if not self.interactive:
            self._explain_offline_login()
            return

        print(
            f"\n{re}[!]{gr} Instagram only returned the public profile metadata "
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
            print(f"{re}[!]{gr} could not fetch the full profile: {exc}\n")
            return

        self.p_data = profile
        self.limited_data = False
        self._feed_items = {}
        self._user_id = None
        self.posts_error = None
        print(f"{gr}[+]{nu} full profile available\n")

    def _explain_offline_login(self):
        print(
            f"{re}[!]{gr} no terminal input is available, so you cannot be asked "
            "for a sessionid here.\n"
            "    Export one and run again:\n"
            f"    IG_SESSIONID='sessionid=...; csrftoken=...' python3 main.py "
            f"-u {self.user}\n"
        )

    def _warn_limited_data(self):
        if self.limited_data and not self._warned_limited:
            if self.impersonating:
                advice = (
                    "Instagram stripped the full payload even with browser "
                    "impersonation; try another network or IG_PROXY."
                )
            else:
                advice = (
                    "Install curl_cffi (pip install curl_cffi) for browser "
                    "impersonation, or use IG_PROXY, to get the full profile."
                )
            print(
                f"{re}[!]{gr} only the public profile metadata was available "
                f"({self.data_source}): followers/following/posts are shown, but "
                f"bio, posts and tags are unavailable.\n"
                f"    Log in with: python3 main.py -u {self.user} --login\n"
                f"    {advice}\n"
            )
            self._warned_limited = True

    def _warn_posts_error(self):
        if self.posts_error and not self._warned_posts_error:
            print(f"{re}[!]{gr} could not load posts: {self.posts_error}\n")
            self._warned_posts_error = True

    def _extracted_from_print_data_2(self):
        os.system("clear" if os.name == "posix" else "cls")
        extra.banner()
        for key, value in self.output.items():
            print(f"{gr}{key:<17} : {nu}{value}")
        print("")

    def make_dir(self):
        path = os.path.join(self.base_dir, self.user)
        os.makedirs(path, exist_ok=True)
        os.chdir(path)

    def scrap_uploads(self):
        if self.is_private:
            print(f"{re}[!]{gr} private profile can't scrap data !\n")
            return 1

        if not self.posts:
            if self.posts_error:
                self._warn_posts_error()
            else:
                print(f"{re}[!]{gr} no posts found\n")
            return 1

        print(f"{gr}[+]{nu} user uploads data : \n")
        for post in self.posts:
            print(f"{gr}picture : {nu}{extra.tiny_url(post['picture'])}")
            if post["caption"]:
                print(f"{gr}Caption : {nu}{post['caption']}")
            for key, value in post.items():
                if key not in ("picture", "caption"):
                    print(f"{gr}{key} : {nu}{value}")
            print("")

    def most_common_tags(self):
        self._warn_posts_error()
        print(f"{gr}[+]{nu} user uploads tags : \n")
        for key, value in collections.Counter(self.tags).most_common():
            print(f"{gr}{key} : {nu}{value}")

    def save_data(self):
        self.make_dir()
        self._warn_posts_error()

        # DOWNLOAD PROFILE PICTURE
        if self.profile_pic_url:
            time.sleep(1)
            try:
                r = self._get(
                    self.profile_pic_url,
                    headers={"User-Agent": random.choice(useragent)},
                )
            except InstagramError as exc:
                print(f"{re}[!]{gr} could not download profile picture: {exc}")
            else:
                if r.status_code == 200:
                    with open("profile_pic.jpg", "wb") as f:
                        f.write(r.content)
                    print(f"{gr}[+]{nu} saved pic to {os.getcwd()}/profile_pic.jpg")
                else:
                    print(
                        f"{re}[!]{gr} could not download profile picture: "
                        f"HTTP {r.status_code}"
                    )

        # SAVES PROFILE DATA TO TEXT FILE
        self.output_data = self._profile_details()
        with open("profile_data.txt", "w") as f:
            f.write(json.dumps(self.output_data))
        print(f"{gr}[+]{nu} saved data to {os.getcwd()}/profile_data.txt")

        # SAVES INFORMATION
        posts = {}
        for index, post in enumerate(self.posts):
            entry = {
                key: value
                for key, value in post.items()
                if key not in ("picture", "caption")
            }
            entry["picture"] = extra.tiny_url(post["picture"])
            if post["caption"]:
                entry["caption"] = post["caption"]
            posts[index] = entry

        with open("posts_data.txt", "w") as f:
            f.write(json.dumps(posts))
        print(f"{gr}[+]{nu} saved post info to {os.getcwd()}/posts_data.txt")

        # SAVES TAGS
        with open("tags.txt", "w") as f:
            f.write(json.dumps(self.tags))
        print(f"{gr}[+]{nu} saved tags to {os.getcwd()}/tags.txt\n")
