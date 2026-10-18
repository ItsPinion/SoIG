#!/bin/env python3

import collections
import getpass
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request

import requests
from bs4 import BeautifulSoup

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

LOGIN_HINT = (
    "Instagram requires a logged-in session for this request. Log in to "
    "instagram.com in your browser, copy the value of the 'sessionid' cookie and "
    "paste it when prompted (or export it as IG_SESSIONID), then run the tool again."
)

LOGIN_HELP = (
    "Instagram is asking for a logged-in session.\n"
    "  1. Log in to https://www.instagram.com in your browser.\n"
    "  2. Open the developer tools (F12) -> Application -> Storage -> Cookies ->\n"
    "     instagram.com and copy the value of the 'sessionid' cookie.\n"
    "  3. Paste it below (the value is hidden while you type).\n"
    "Press Enter without a value to cancel."
)


class InstagramError(Exception):
    """Raised when Instagram does not return usable profile data."""


class LoginRequiredError(InstagramError):
    """Raised when Instagram asks for an authenticated session."""


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


def _clean_sessionid(value):
    """Normalise a pasted sessionid.

    Accepts a bare value, ``sessionid=...`` or a whole pasted cookie header
    such as ``sessionid=abc; ds_user_id=123``.
    """
    value = (value or "").strip().strip('"').strip("'")
    if ";" in value:
        for part in value.split(";"):
            name, separator, cookie_value = part.strip().partition("=")
            if separator and name.strip().lower() == "sessionid":
                value = cookie_value
                break
    if value.lower().startswith("sessionid="):
        value = value.split("=", 1)[1]
    return value.strip().strip('"').strip("'")


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


def _iter_user_payloads(payload, depth=0):
    """Yield every user object found inside an arbitrary JSON payload."""
    if depth > 50:
        return
    if isinstance(payload, dict):
        if "username" in payload and (
            "edge_followed_by" in payload or "follower_count" in payload
        ):
            yield payload
        for value in payload.values():
            yield from _iter_user_payloads(value, depth + 1)
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            yield from _iter_user_payloads(value, depth + 1)


def _find_user_payload(payload, username=None):
    """Find the user object, preferring one for ``username``."""
    fallback = None
    wanted = str(username).casefold() if username is not None else None
    for user in _iter_user_payloads(payload):
        if wanted is not None and str(user.get("username", "")).casefold() == wanted:
            return user
        if fallback is None:
            fallback = user
    return fallback


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
    def __init__(self, user, sessionid=None, interactive=None):
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
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": random.choice(useragent),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        # Prompts for a sessionid only when running on a real terminal.
        if interactive is None:
            try:
                interactive = sys.stdin.isatty()
            except (AttributeError, ValueError):
                interactive = False
        self.interactive = bool(interactive)
        self._provided_sessionid = _clean_sessionid(
            sessionid if sessionid is not None else os.environ.get("IG_SESSIONID", "")
        )
        self.get_profile()

    def _get(self, url, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        try:
            return self.session.get(url, **kwargs)
        except requests.RequestException as exc:
            raise InstagramError(f"could not reach {url}: {exc}") from exc

    def _set_sessionid(self, sessionid):
        """Attach (or clear) the sessionid cookie used for every request."""
        self.sessionid = sessionid or ""
        if self.sessionid:
            self.session.cookies.set(
                "sessionid", self.sessionid, domain=".instagram.com"
            )
        else:
            self.session.cookies.clear()

    def _session_headers(self, reference_username=None):
        username = reference_username or self.user
        return {
            "Accept": "*/*",
            "Referer": PROFILE_URL.format(username=username),
            "X-IG-App-ID": IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
        }

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
                headers=self._session_headers(SESSION_CHECK_USERNAME),
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
        """Validate a sessionid passed as an argument or via IG_SESSIONID."""
        if not self._provided_sessionid:
            return
        self._set_sessionid(self._provided_sessionid)
        status, detail = self._session_status()
        if status == "valid":
            print(f"\n{gr}[+]{nu} sessionid works{detail}")
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

    def _fetch_profile_with_login(self):
        """Fetch the profile, asking for a sessionid when Instagram demands one."""
        while True:
            try:
                return self._fetch_profile()
            except LoginRequiredError as exc:
                self._prompt_for_sessionid(exc)

    def _prompt_for_sessionid(self, error):
        """Ask for a sessionid until it is accepted; raise ``error`` to cancel."""
        if not self.interactive:
            raise error

        print()
        print(f"{re}[!]{gr} {LOGIN_HELP}\n")
        while True:
            try:
                value = getpass.getpass(f"{gr}sessionid{nu}: ")
            except (EOFError, KeyboardInterrupt):
                print()
                raise error from None

            value = _clean_sessionid(value)
            if not value:
                raise error from None

            self._set_sessionid(value)
            status, detail = self._session_status()
            if status == "valid":
                print(f"{gr}[+]{nu} sessionid works{detail}\n")
                return
            if status == "unknown":
                print(
                    f"{re}[!]{gr} could not verify the sessionid ({detail}); "
                    "trying it anyway.\n"
                )
                return

            print(
                f"{re}[!]{gr} that sessionid does not work ({detail}). "
                "Please try again, or press Enter to cancel.\n"
            )
            self._set_sessionid(None)

    @staticmethod
    def _check_status(response, source):
        if response.status_code == 404:
            raise InstagramError(f"the {source} returned HTTP 404 (account not found)")
        if response.status_code == 429:
            raise InstagramError(
                f"the {source} is rate limiting this IP (HTTP 429); wait a while and try again"
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

    def _fetch_from_api(self):
        response = self._get(
            WEB_PROFILE_INFO_URL,
            params={"username": self.user},
            headers={
                "Accept": "*/*",
                "Referer": PROFILE_URL.format(username=self.user),
                "X-IG-App-ID": IG_APP_ID,
                "X-IG-WWW-Claim": "0",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self._check_status(response, "profile API")
        try:
            payload = response.json()
        except ValueError:
            if _looks_like_login(response.text):
                raise LoginRequiredError(
                    "the profile API served a login wall instead of JSON"
                ) from None
            raise InstagramError("the profile API returned non-JSON data") from None

        user = _dig(payload, "data", "user")
        if not isinstance(user, dict):
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and (
                payload.get("require_login") or "log" in str(message or "").lower()
            ):
                raise LoginRequiredError(message or "the profile API requires a login")
            raise InstagramError(message or "the profile API returned no user object")
        return user

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
            # Some pages embed the bootstrap payload as a bare JS object literal
            # instead of assigning it to a well known global.
            if '"username"' in text and (
                '"edge_followed_by"' in text or '"follower_count"' in text
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
            },
        )
        self._check_status(response, "profile page")
        user = self._find_user_in_html(response.text)
        if user is None:
            if _looks_like_login(response.text):
                raise LoginRequiredError(
                    "the profile page served a login wall instead of profile data"
                )
            raise InstagramError(
                "the profile page did not contain embedded profile data "
                "(Instagram hides it from logged-out visitors)"
            )
        return user

    def _fetch_profile(self):
        problems = []
        login_required = False
        for label, fetch in (
            ("profile API", self._fetch_from_api),
            ("profile page", self._fetch_from_html),
        ):
            try:
                user = fetch()
            except LoginRequiredError as exc:
                login_required = True
                problems.append(f"{label}: {exc}")
                continue
            except InstagramError as exc:
                problems.append(f"{label}: {exc}")
                continue
            if user:
                return user
            problems.append(f"{label}: no profile data returned")

        details = "\n    ".join(problems)
        message = (
            f"could not retrieve profile data for '{self.user}'.\n    "
            f"{details}\n    {LOGIN_HINT}"
        )
        if login_required:
            raise LoginRequiredError(message)
        raise InstagramError(message)

    def get_profile(self):
        extra.write(f"\n{gr}[+]{nu} getting profile ...")
        self._use_provided_sessionid()
        self.p_data = self._fetch_profile_with_login()
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
                "Accept": "*/*",
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": PROFILE_URL.format(username=self.user),
                "X-IG-App-ID": IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
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
        self._warn_posts_error()
        print(f"{gr}[+]{nu} most used user tags : \n")
        for key, value in collections.Counter(self.tags).most_common(5):
            print(f"{gr}{key:<17} : {nu}{value}")
        print("")

    def print_data(self):
        self._extracted_from_print_data_2()

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
