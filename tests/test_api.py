"""Regression tests for the Instagram profile scraper.

Run from the repository root with::

    python -m unittest discover -s tests
"""

import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.api as api


USER = {
    "username": "prottoy.raha.r",
    "full_name": "Prottoy Raha",
    "biography": "line one\nline two",
    "external_url": "https://example.com",
    "is_private": False,
    "is_verified": True,
    "is_business_account": True,
    "business_category_name": "Creator",
    "connected_fb_page": None,
    "is_joined_recently": False,
    "profile_pic_url_hd": "https://cdn.example/pic.jpg",
    "edge_followed_by": {"count": 120},
    "edge_follow": {"count": 42},
    "edge_owner_to_timeline_media": {
        "count": 2,
        "edges": [
            {
                "node": {
                    "edge_media_to_caption": {
                        "edges": [
                            {"node": {"text": "#python #osint post one #python"}}
                        ]
                    },
                    "thumbnail_resources": [{"src": "https://cdn.example/thumb1.jpg"}],
                    "edge_media_to_comment": {"count": 3},
                    "comments_disabled": False,
                    "taken_at_timestamp": 1700000000,
                    "edge_liked_by": {"count": 9},
                    "location": None,
                    "accessibility_caption": "a photo",
                }
            },
            {"node": {"edge_media_to_caption": {"edges": []}}},
        ],
    },
}


class FakeResponse:
    def __init__(
        self, status_code=200, payload=None, text="", url="https://x/", headers=None
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.url = url
        self.headers = headers or {}
        self.content = b"fake image bytes"

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class BaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.tmp.name)
        self.session_path = os.path.join(self.tmp.name, "session.json")
        for name, value in (
            ("tiny_url", mock.Mock(side_effect=lambda url: url)),
            ("write", mock.Mock()),
        ):
            patcher = mock.patch.object(api.extra, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def new_main(self, **kwargs):
        kwargs.setdefault("session_file", self.session_path)
        return api.main(**kwargs)

    def make(self, user=USER, feed=None, **kwargs):
        kwargs.setdefault("interactive", False)
        kwargs.setdefault("session_file", self.session_path)
        with mock.patch.object(
            api.main, "_fetch_profile", return_value=dict(user)
        ), mock.patch.object(api.main, "_fetch_feed", return_value=feed or []):
            return self.new_main(user=kwargs.pop("username", "prottoy.raha.r"), **kwargs)


class ProfileTests(BaseTestCase):
    def test_parses_posts_and_tags(self):
        ig = self.make(username="@prottoy.raha.r/")
        self.assertEqual(ig.user, "prottoy.raha.r")
        self.assertEqual(ig.output["followers"], "120")
        self.assertEqual(ig.output["bio"], "line one, line two")
        self.assertEqual(ig.tags, {"python": 2, "osint": 1})
        self.assertEqual(ig.output["business category"], "Creator")

    def test_missing_optional_fields_do_not_crash(self):
        ig = self.make({"username": "someone", "is_private": False}, username="someone")
        self.assertEqual(ig.output["followers"], "0")
        self.assertEqual(ig.output["business category"], "")
        self.assertEqual(ig.tags, {})

    def test_private_profile(self):
        ig = self.make(dict(USER, is_private=True), username="secret")
        self.assertTrue(ig.is_private)
        self.assertEqual(ig.output["private"], "True")
        self.assertEqual(ig.tags, {})
        self.assertEqual(ig.scrap_uploads(), 1)

    def test_save_data(self):
        ig = self.make()
        with mock.patch.object(ig, "_get", return_value=FakeResponse()):
            ig.save_data()

        target = os.path.join(self.tmp.name, "prottoy.raha.r")
        self.assertEqual(
            sorted(os.listdir(target)),
            ["posts_data.txt", "profile_data.txt", "profile_pic.jpg", "tags.txt"],
        )
        with open(os.path.join(target, "posts_data.txt")) as f:
            posts = json.load(f)
        self.assertEqual(posts["0"]["likes"], "9")
        self.assertEqual(posts["0"]["caption"], "#python #osint post one #python")
        self.assertNotIn("caption", posts["1"])
        with open(os.path.join(target, "tags.txt")) as f:
            self.assertEqual(json.load(f), ig.tags)


MODERN_USER = {
    "username": "prottoy.raha.r",
    "full_name": "Prottoy Raha",
    "biography": "modern bio",
    "bio_links": [{"url": "https://example.com"}],
    "follower_count": 321,
    "following_count": 12,
    "media_count": 4,
    "is_private": False,
    "is_verified": True,
    "is_professional_account": True,
    "category_name": "Creator",
    "profile_pic_url": "https://cdn.example/modern.jpg",
}

MODERN_FEED = [
    {
        "node": {
            "code": "abc123",
            "caption": {"text": "#modern #osint #modern"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example/modern.jpg"}]},
            "like_count": 11,
            "comment_count": 2,
            "taken_at": 1700000001,
            "comments_disabled": True,
            "accessibility_caption": "modern photo",
        }
    },
    {"node": {"caption": None}},
]


def modern_html(user=MODERN_USER):
    payload = {
        "require": [
            [
                "ScheduledServerJS",
                "handle",
                None,
                [
                    {
                        "__bbox": {
                            "result": {
                                "data": {"xig_user_by_username": user}
                            }
                        }
                    }
                ],
            ]
        ]
    }
    return (
        '<html><script type="application/json" data-sjs>'
        + json.dumps(payload)
        + "</script></html>"
    )


class ModernProfileTests(BaseTestCase):
    def test_html_data_sjs_payload(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=modern_html())
        ):
            user = self.make()._fetch_from_html()
        self.assertEqual(user["username"], MODERN_USER["username"])
        self.assertEqual(user["follower_count"], 321)

    def test_profile_fields_from_modern_payload(self):
        ig = self.make(MODERN_USER)
        self.assertEqual(ig.output["followers"], "321")
        self.assertEqual(ig.output["following"], "12")
        self.assertEqual(ig.output["posts"], "4")
        self.assertEqual(ig.output["external url"], "https://example.com")
        self.assertEqual(ig.output["business category"], "Creator")
        self.assertEqual(ig.output["business account"], "True")

    def test_feed_posts_and_tags(self):
        ig = self.make(MODERN_USER, feed=MODERN_FEED)
        self.assertEqual(len(ig.posts), 2)
        self.assertEqual(ig.tags, {"modern": 2, "osint": 1})
        self.assertEqual(ig.posts[0]["picture"], "https://cdn.example/modern.jpg")
        self.assertEqual(ig.posts[0]["likes"], "11")
        self.assertEqual(ig.posts[0]["comments disabled"], "True")
        self.assertEqual(ig.posts[1]["caption"], "")

    def test_feed_failure_is_reported_but_profile_survives(self):
        with mock.patch.object(
            api.main, "_fetch_profile", return_value=dict(MODERN_USER)
        ), mock.patch.object(
            api.main, "_fetch_feed", side_effect=api.InstagramError("feed blocked")
        ):
            ig = self.new_main(user="prottoy.raha.r")
        self.assertEqual(ig.output["followers"], "321")
        self.assertEqual(ig.posts, [])
        self.assertEqual(ig.posts_error, "feed blocked")

    def test_fetch_feed_parses_response(self):
        response = FakeResponse(
            payload={
                "data": {
                    "xdt_api__v1__feed__user_timeline_graphql_connection": {
                        "edges": MODERN_FEED
                    }
                }
            }
        )
        with mock.patch.object(api.main, "_get", return_value=response):
            edges = self.make()._fetch_feed()
        self.assertEqual(edges, MODERN_FEED)

    def test_save_data_with_modern_posts(self):
        ig = self.make(MODERN_USER, feed=MODERN_FEED)
        with mock.patch.object(ig, "_get", return_value=FakeResponse()):
            ig.save_data()
        target = os.path.join(self.tmp.name, "prottoy.raha.r")
        with open(os.path.join(target, "posts_data.txt")) as f:
            posts = json.load(f)
        self.assertEqual(posts["0"]["likes"], "11")
        self.assertEqual(posts["0"]["caption"], "#modern #osint #modern")
        self.assertNotIn("caption", posts["1"])


class FetchTests(BaseTestCase):
    def test_api_success(self):
        response = FakeResponse(payload={"data": {"user": USER}})
        with mock.patch.object(api.main, "_get", return_value=response):
            self.assertEqual(
                self.make()._fetch_from_api()["username"], USER["username"]
            )

    def test_api_404(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(status_code=404)
        ):
            with self.assertRaisesRegex(api.InstagramError, "404"):
                self.make()._fetch_from_api()

    def test_api_400_reports_instagram_message(self):
        response = FakeResponse(
            status_code=400, payload={"message": "invalid parameters"}
        )
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaisesRegex(api.InstagramError, "invalid parameters"):
                self.make()._fetch_from_api()

    def test_api_login_wall(self):
        response = FakeResponse(
            url="https://www.instagram.com/accounts/login/?next=/prottoy.raha.r/"
        )
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaisesRegex(api.InstagramError, "logged-in session"):
                self.make()._fetch_from_api()

    def test_api_non_json(self):
        response = FakeResponse(payload=ValueError("no json"), text="<html>nope</html>")
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaisesRegex(api.InstagramError, "non-JSON"):
                self.make()._fetch_from_api()

    def test_html_shared_data(self):
        html = (
            "<html><script type='text/javascript'>"
            "window._sharedData = "
            + json.dumps(
                {"entry_data": {"ProfilePage": [{"graphql": {"user": USER}}]}}
            )
            + ";</script></html>"
        )
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            self.assertEqual(
                self.make()._fetch_from_html()["username"], USER["username"]
            )

    def test_html_prefers_requested_user(self):
        other = dict(USER, username="someone_else")
        html = (
            "<script type='text/javascript'>"
            '{"require":[["Scheduler",{"x":'
            + json.dumps({"users": [other, USER]})
            + "}]]};</script>"
        )
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            self.assertEqual(
                self.make()._fetch_from_html()["username"], USER["username"]
            )

    def test_html_without_data(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text="<html>login</html>")
        ):
            with self.assertRaises(api.InstagramError):
                self.make()._fetch_from_html()

    def test_falls_back_to_html(self):
        html = (
            "<script>window._sharedData = "
            + json.dumps(
                {"entry_data": {"ProfilePage": [{"graphql": {"user": USER}}]}}
            )
            + ";</script>"
        )

        def fake_get(url, **kwargs):
            if url == api.WEB_PROFILE_INFO_URL:
                return FakeResponse(
                    status_code=401,
                    url="https://www.instagram.com/accounts/login/",
                )
            return FakeResponse(text=html)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            self.assertEqual(self.make().output["followers"], "120")

    def test_reports_both_failures(self):
        with mock.patch.object(
            api.main, "_get", side_effect=api.InstagramError("connection refused")
        ):
            with self.assertRaisesRegex(api.InstagramError, "could not retrieve"):
                self.new_main(user="prottoy.raha.r")

    def test_explicit_sessionid_is_used(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ):
            ig = self.make(sessionid="abc123")
        cookies = ig.session.cookies.get_dict()
        self.assertEqual(cookies.get("sessionid"), "abc123")


class LoginPromptTests(BaseTestCase):
    def test_401_raises_login_required(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(status_code=401)
        ):
            with self.assertRaises(api.LoginRequiredError):
                self.make()._fetch_from_api()

    def test_400_logged_in_message_raises_login_required(self):
        response = FakeResponse(
            status_code=400,
            payload={"message": "Make sure you are logged in", "status": "fail"},
        )
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaises(api.LoginRequiredError):
                self.make()._fetch_from_api()

    def test_html_login_wall_raises_login_required(self):
        html = "<html><title>Login - Instagram</title></html>"
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            with self.assertRaises(api.LoginRequiredError):
                self.make()._fetch_from_html()

    def test_prompts_and_retries_until_session_works(self):
        fetches = []

        def fetch():
            fetches.append(1)
            if len(fetches) == 1:
                raise api.LoginRequiredError("login")
            return dict(USER)

        statuses = iter(
            [("invalid", "HTTP 401"), ("valid", " (logged in as @demo)")]
        )
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=fetch
        ), mock.patch.object(
            api.main, "_session_status", side_effect=lambda: next(statuses)
        ), mock.patch(
            "api.api._read_prompt", side_effect=["wrong", "right", ""]
        ):
            ig = self.new_main(user="prottoy.raha.r", interactive=True)

        self.assertEqual(ig.output["followers"], "120")
        self.assertEqual(ig.sessionid, "right")
        self.assertEqual(len(fetches), 2)

    def test_prompt_is_not_used_when_not_interactive(self):
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=api.LoginRequiredError("login")
        ), mock.patch("api.api._read_prompt") as getpass_mock:
            with self.assertRaises(api.LoginRequiredError):
                self.new_main(user="prottoy.raha.r", interactive=False)
        getpass_mock.assert_not_called()

    def test_empty_input_cancels(self):
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=api.LoginRequiredError("login")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["   "]
        ), mock.patch.object(
            api.main, "_session_status"
        ) as status:
            with self.assertRaises(api.LoginRequiredError):
                self.new_main(user="prottoy.raha.r", interactive=True)
        status.assert_not_called()

    def test_ctrl_c_cancels(self):
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=api.LoginRequiredError("login")
        ), mock.patch(
            "api.api._read_prompt", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(api.LoginRequiredError):
                self.new_main(user="prottoy.raha.r", interactive=True)

    def test_unverifiable_session_is_retried_anyway(self):
        fetches = []

        def fetch():
            fetches.append(1)
            if len(fetches) == 1:
                raise api.LoginRequiredError("login")
            return dict(USER)

        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=fetch
        ), mock.patch.object(
            api.main, "_session_status", return_value=("unknown", "timed out")
        ), mock.patch("api.api._read_prompt", side_effect=["maybe", ""]):
            ig = self.new_main(user="prottoy.raha.r", interactive=True)
        self.assertEqual(ig.output["followers"], "120")
        self.assertEqual(len(fetches), 2)

    def test_invalid_provided_session_is_dropped(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("invalid", "HTTP 401")
        ):
            ig = self.make(sessionid="stale")
        self.assertEqual(ig.sessionid, "")
        self.assertEqual(ig.session.cookies.get_dict(), {})

    def test_cookie_header_paste_is_understood(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ):
            ig = self.make(sessionid="csrftoken=x; sessionid=abc123; ds_user_id=9")
        self.assertEqual(ig.sessionid, "abc123")

    def test_valid_provided_session_is_kept(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (logged in as @me)")
        ):
            ig = self.make(sessionid='"sessionid=fresh"')
        self.assertEqual(ig.sessionid, "fresh")
        cookies = ig.session.cookies.get_dict()
        self.assertEqual(cookies.get("sessionid"), "fresh")

    def test_session_status_valid_from_web_form_data(self):
        response = FakeResponse(payload={"form_data": {"username": "me"}})
        with mock.patch.object(api.main, "_get", return_value=response):
            self.assertEqual(
                self.make()._session_status(), ("valid", " (logged in as @me)")
            )

    def test_session_status_invalid_on_401(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(status_code=401)
        ):
            self.assertEqual(self.make()._session_status()[0], "invalid")

    def test_session_status_invalid_on_login_page(self):
        response = FakeResponse(text="<html><title>Login - Instagram</title></html>")
        with mock.patch.object(api.main, "_get", return_value=response):
            self.assertEqual(self.make()._session_status()[0], "invalid")

    def test_session_status_unknown_when_unreachable(self):
        with mock.patch.object(
            api.main, "_get", side_effect=api.InstagramError("no network")
        ):
            self.assertEqual(
                self.make()._session_status(), ("unknown", "no network")
            )

    def test_session_status_falls_back_to_profile_check(self):
        def fake_get(url, **kwargs):
            if url == api.SESSION_CHECK_URL:
                return FakeResponse(status_code=404, text="not found")
            return FakeResponse(payload={"data": {"user": USER}})

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            self.assertEqual(self.make()._session_status(), ("valid", ""))


META_HTML = (
    '<html><head>'
    '<meta property="og:title" '
    'content="Thess B (@_thessb17_) • Instagram photos" />'
    '<meta property="og:description" content="1,234 Followers, 56 Following, '
    '78 Posts - See Instagram photos and videos from Thess B (@_thessb17_)" />'
    '<meta property="og:image" content="https://cdn.example/avatar.jpg" />'
    "</head><body></body></html>"
)


class SavedSessionTests(BaseTestCase):
    def path(self):
        return self.session_path

    def test_saved_session_is_reused_without_prompting(self):
        api.save_session({"sessionid": "saved", "csrftoken": "c"}, self.path())
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (as @me)")
        ), mock.patch("api.api._read_prompt") as prompter:
            ig = self.make()
        prompter.assert_not_called()
        self.assertEqual(ig.sessionid, "saved")
        self.assertTrue(ig._session_verified)

    def test_expired_saved_session_prompts_and_saves_again(self):
        api.save_session({"sessionid": "old", "csrftoken": "c"}, self.path())
        statuses = iter([("invalid", "HTTP 401"), ("valid", "")])
        with mock.patch.object(
            api.main, "_session_status", side_effect=lambda: next(statuses)
        ), mock.patch(
            "api.api._read_prompt", side_effect=["sessionid=fresh", "newcsrf"]
        ) as prompter:
            ig = self.make(interactive=True)
        self.assertEqual(ig.sessionid, "fresh")
        stored = api.load_session(self.path())
        self.assertEqual(stored["sessionid"], "fresh")
        self.assertEqual(stored["csrftoken"], "newcsrf")
        self.assertEqual(prompter.call_count, 2)

    def test_prompt_saves_sessionid_and_csrftoken(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch("api.api._read_prompt", side_effect=["sid-value", "csrf-value"]):
            self.make(interactive=True, login=True)
        stored = api.load_session(self.path())
        self.assertEqual(stored["sessionid"], "sid-value")
        self.assertEqual(stored["csrftoken"], "csrf-value")
        mode = os.stat(self.path()).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_no_save_flag_keeps_disk_clean(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch("api.api._read_prompt", side_effect=["sid", ""]):
            self.make(interactive=True, save_cookies=False)
        self.assertEqual(api.load_session(self.path()), {})

    def test_explicit_cookies_override_the_saved_session(self):
        api.save_session({"sessionid": "stored"}, self.path())
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ):
            ig = self.make(sessionid="sessionid=explicit")
        self.assertEqual(ig.sessionid, "explicit")
        self.assertEqual(api.load_session(self.path())["sessionid"], "explicit")

    def test_corrupt_file_is_ignored(self):
        with open(self.path(), "w") as handle:
            handle.write("{not json")
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch("api.api._read_prompt", side_effect=["fresh", ""]):
            ig = self.make(interactive=True, login=True)
        self.assertEqual(ig.sessionid, "fresh")

    def test_forget_session_removes_the_file(self):
        api.save_session({"sessionid": "saved"}, self.path())
        removed = api.forget_session(self.path())
        self.assertEqual(removed, pathlib.Path(self.path()))
        self.assertFalse(os.path.exists(self.path()))
        self.assertIsNone(api.forget_session(self.path()))

    def test_session_file_path_defaults_under_home(self):
        with mock.patch.object(api.Path, "home", return_value=api.Path("/tmp/home")):
            self.assertEqual(
                str(api.session_file_path()), "/tmp/home/.config/SoIG/session.json"
            )

    def test_custom_session_file_is_used(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch("api.api._read_prompt", side_effect=["sid", ""]):
            self.make(interactive=True, login=True, session_file="/tmp/other.json")
        # nothing written to the default location
        self.assertEqual(api.load_session(self.path()), {})


class SessionNoticeTests(BaseTestCase):
    def test_warns_when_no_session_is_set(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.make()
        text = out.getvalue()
        self.assertIn("no sessionid is set", text)
        self.assertIn("--login", text)

    def test_no_warning_when_sessionid_supplied(self):
        out = io.StringIO()
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), contextlib.redirect_stdout(out):
            self.make(sessionid="good")
        self.assertNotIn("no sessionid is set", out.getvalue())

    def test_debug_lists_sources(self):
        def fake_get(url, **kwargs):
            return FakeResponse(status_code=429)

        out = io.StringIO()
        with mock.patch.object(
            api.main, "_get", side_effect=fake_get
        ), contextlib.redirect_stdout(out):
            with self.assertRaises(api.InstagramError):
                self.new_main(user="prottoy.raha.r", interactive=False, debug=True)
        text = out.getvalue()
        self.assertIn("[debug]", text)
        self.assertIn("profile page", text)


class UpgradeTests(BaseTestCase):

    def meta_only(self, **kwargs):
        """A run whose only working source is the OpenGraph metadata."""
        def fake_get(url, **kwargs):
            if url == api.PROFILE_URL.format(username="_thessb17_"):
                return FakeResponse(text=META_HTML)
            return FakeResponse(status_code=429)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            return self.new_main(user="_thessb17_", **kwargs)

    def test_offers_login_when_only_metadata_is_available(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (as @me)")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["sessionid=good; csrftoken=x"]
        ) as prompter:
            ig = self.meta_only(interactive=True)
        prompter.assert_called_once()
        self.assertTrue(ig._session_verified)
        self.assertEqual(ig.sessionid, "good")
        self.assertEqual(ig.session.cookies.get_dict().get("csrftoken"), "x")

    def test_declining_upgrade_keeps_metadata(self):
        with mock.patch("api.api._read_prompt", side_effect=[""]):
            ig = self.meta_only(interactive=True)
        self.assertEqual(ig.output["followers"], "1234")
        self.assertTrue(ig.limited_data)

    def test_upgrade_replaces_partial_profile(self):
        def fake_get(url, **kwargs):
            if url == api.PROFILE_URL.format(username="_thessb17_"):
                return FakeResponse(text=META_HTML)
            if url == api.WEB_PROFILE_INFO_URL:
                if "sessionid=good" in str(getattr(api.main, "_cookies", "")):
                    return FakeResponse(payload={"data": {"user": dict(USER, username="_thessb17_")}})
                return FakeResponse(status_code=429)
            return FakeResponse(status_code=429)

        real_get = api.main._get
        state = {"logged_in": False}
        original_set_cookies = api.main._set_cookies

        def set_cookies(instance, cookies):
            original_set_cookies(instance, cookies)
            state["logged_in"] = bool(cookies.get("sessionid"))

        def fake_get_bound(instance, url, **kwargs):
            if url == api.WEB_PROFILE_INFO_URL and state["logged_in"]:
                return FakeResponse(
                    payload={"data": {"user": dict(USER, username="_thessb17_")}}
                )
            return fake_get(url, **kwargs)

        with mock.patch.object(api.main, "_get", fake_get_bound), mock.patch.object(
            api.main, "_set_cookies", set_cookies
        ), mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["sessionid=good", ""]
        ):
            ig = self.new_main(user="_thessb17_", interactive=True)
        self.assertFalse(ig.limited_data)
        self.assertEqual(ig.output["followers"], "120")
        self.assertEqual(ig.output["bio"], "line one, line two")

    def test_no_offer_when_not_interactive(self):
        with mock.patch("api.api._read_prompt") as prompter:
            ig = self.meta_only(interactive=False)
        prompter.assert_not_called()
        self.assertTrue(ig.limited_data)

    def test_force_login_prompts_up_front(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["sessionid=good", "csrf"]
        ) as prompter:
            ig = self.make(login=True, interactive=True)
        self.assertEqual(prompter.call_count, 2)  # sessionid + csrftoken
        self.assertTrue(ig._session_verified)
        self.assertEqual(ig.session.cookies.get_dict().get("csrftoken"), "csrf")


ANSI = __import__("re").compile(r"\x1b\[[0-9;]*m")


class UserInfoSourceTests(BaseTestCase):
    """The app's /users/<id>/info/ endpoint as a last-resort full profile."""

    def test_user_id_falls_back_to_the_feed(self):
        feed = [{"node": {"user": {"pk": 999, "username": "prottoy.raha.r"}}}]
        ig = self.make()
        with mock.patch.object(
            api.main, "_fetch_feed", return_value=feed
        ), mock.patch.object(
            api.main, "_fetch_from_username_info",
            side_effect=api.InstagramError("400"),
        ), mock.patch.object(
            api.main, "_fetch_from_mobile_username_info",
            side_effect=api.InstagramError("400"),
        ):
            self.assertEqual(ig._resolve_user_id(), "999")

    def test_user_info_uses_the_resolved_id(self):
        def fake_get(url, **kwargs):
            if "/users/12345/info/" in url:
                return FakeResponse(
                    payload={"user": dict(USER, biography="from info")}
                )
            return FakeResponse(status_code=400, payload={"message": "nope"})

        ig = self.make()
        with mock.patch.object(
            api.main, "_resolve_user_id", return_value="12345"
        ), mock.patch.object(api.main, "_get", side_effect=fake_get):
            user = ig._fetch_from_user_info()
        self.assertEqual(user["biography"], "from info")

    def test_user_info_is_tried_after_the_profile_apis(self):
        labels = [label for label, _ in self.make()._profile_sources()]
        self.assertIn("user info API", labels)
        self.assertLess(labels.index("profile API"), labels.index("user info API"))


class PageDumpTests(BaseTestCase):
    def test_dump_page_writes_the_html(self):
        path = os.path.join(self.tmp.name, "page.html")
        html = MetadataFallbackTests.META_HTML
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=html)
        ):
            self.new_main(user="_thessb17_", interactive=False, dump_page=path)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), html)

    def test_without_the_flag_nothing_is_written(self):
        html = MetadataFallbackTests.META_HTML
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=html)
        ):
            self.new_main(user="_thessb17_", interactive=False)
        self.assertEqual(os.listdir(self.tmp.name), [])


class ProfileGraphQLTests(BaseTestCase):
    def test_profile_graphql_returns_the_user(self):
        payload = {"data": {"user": dict(USER, biography="gql bio")}}
        seen = []

        def fake_get(url, **kwargs):
            seen.append(kwargs.get("params", {}).get("doc_id"))
            if kwargs.get("params", {}).get("doc_id") == api.PROFILE_DOC_IDS[1]:
                return FakeResponse(payload=payload)
            return FakeResponse(status_code=400, payload={"message": "bad doc"})

        ig = self.make()
        with mock.patch.object(
            api.main, "_resolve_user_id", return_value="123"
        ), mock.patch.object(api.main, "_get", side_effect=fake_get):
            user = ig._fetch_from_profile_graphql()
        self.assertEqual(user["biography"], "gql bio")
        self.assertEqual(seen[0], api.PROFILE_DOC_IDS[0])
        self.assertEqual(seen[1], api.PROFILE_DOC_IDS[1])

    def test_profile_graphql_needs_an_id(self):
        ig = self.make()
        with mock.patch.object(api.main, "_resolve_user_id", return_value=None):
            with self.assertRaisesRegex(api.InstagramError, "numeric user id"):
                ig._fetch_from_profile_graphql()

    def test_profile_graphql_is_a_profile_source(self):
        labels = [label for label, _ in self.make()._profile_sources()]
        self.assertIn("profile GraphQL", labels)


class PageInspectTests(BaseTestCase):
    def test_reports_markers_and_script_sizes(self):
        path = os.path.join(self.tmp.name, "page.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                "<html><script type='application/json'>"
                '{"xig_user_by_username": {"username": "x", "biography": ""}}'
                "</script></html>"
            )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertTrue(api.inspect_page(path))
        text = out.getvalue()
        self.assertIn("xig_user_by_username", text)
        self.assertIn("JSON script tags", text)
        self.assertIn('"biography"', text)

    def test_missing_file_returns_false(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(api.inspect_page(os.path.join(self.tmp.name, "no.html")))
        self.assertIn("could not read", out.getvalue())


class EnvironmentWarningTests(BaseTestCase):
    def test_missing_curl_cffi_is_warned_about(self):
        out = io.StringIO()
        with mock.patch.object(
            api, "curl_requests", None
        ), contextlib.redirect_stdout(out):
            self.make()
        text = out.getvalue()
        self.assertIn("curl_cffi is not installed", text)
        self.assertIn("pip install curl_cffi", text)

    def test_incomplete_saved_session_is_flagged(self):
        api.save_session({"sessionid": "only"}, self.session_path)
        out = io.StringIO()
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (logged in as @x)")
        ), contextlib.redirect_stdout(out):
            self.make()
        text = out.getvalue()
        self.assertIn("only the sessionid is saved", text)
        self.assertIn("whole Cookie header", text)

    def test_complete_saved_session_is_not_flagged(self):
        api.save_session(
            {"sessionid": "full", "ds_user_id": "1", "mid": "m"}, self.session_path
        )
        out = io.StringIO()
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (logged in as @x)")
        ), contextlib.redirect_stdout(out):
            self.make()
        self.assertNotIn("only the sessionid is saved", out.getvalue())


class StepTraceTests(BaseTestCase):
    """Every stage must announce itself so a failure can be located."""

    def test_successful_run_prints_the_steps(self):
        out = io.StringIO()
        with mock.patch.object(
            api.main,
            "_get",
            return_value=FakeResponse(payload={"data": {"user": dict(USER)}}),
        ), contextlib.redirect_stdout(out):
            self.new_main(user="prottoy.raha.r", interactive=False)
        text = ANSI.sub("", out.getvalue())
        for expected in (
            "[step] target:",
            "[step] transport:",
            "[step] trying",
            "/10 profile API ...",
            ": ok (HTTP",
            "[step] done in",
        ):
            self.assertIn(expected, text)

    def test_every_failed_source_is_named_with_its_status(self):
        def fake_get(url, **kwargs):
            return FakeResponse(status_code=429)

        out = io.StringIO()
        with mock.patch.object(
            api.main, "_get", side_effect=fake_get
        ), contextlib.redirect_stdout(out):
            with self.assertRaises(api.InstagramError):
                self.new_main(user="prottoy.raha.r", interactive=False)
        text = ANSI.sub("", out.getvalue())
        self.assertIn("1/10 profile API", text)
        self.assertIn("2/10 profile page", text)
        self.assertIn("rate limited (HTTP 429", text)

    def test_metadata_only_run_names_the_stripped_payload(self):
        html = MetadataFallbackTests.META_HTML
        out = io.StringIO()
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=html)
        ), contextlib.redirect_stdout(out):
            self.new_main(user="_thessb17_", interactive=False)
        text = ANSI.sub("", out.getvalue())
        self.assertIn("no embedded profile JSON", text)
        self.assertIn("profile source: profile page metadata", text)

    def test_verified_session_is_not_offered_again(self):
        out = io.StringIO()
        meta = FakeResponse(text=MetadataFallbackTests.META_HTML)
        with mock.patch.object(
            api.main, "_get", return_value=meta
        ), mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (logged in as @x)")
        ), mock.patch("api.api._read_prompt") as prompt:
            with contextlib.redirect_stdout(out):
                ig = self.new_main(
                    user="_thessb17_", sessionid="good", interactive=False
                )
                ig.print_data_()
        prompt.assert_not_called()
        text = ANSI.sub("", out.getvalue())
        self.assertIn("not asking for another sessionid", text)
        self.assertIn("logging in again will not help", text)


class MetadataFallbackTests(BaseTestCase):
    META_HTML = (
        '<html><head>'
        '<meta property="og:title" '
        'content="Thess B (@_thessb17_) • Instagram photos" />'
        '<meta property="og:description" content="1,234 Followers, 56 Following, '
        '78 Posts - See Instagram photos and videos from Thess B (@_thessb17_)" />'
        '<meta property="og:image" content="https://cdn.example/avatar.jpg" />'
        "</head><body></body></html>"
    )

    def test_profile_page_metadata_is_used_when_payload_is_stripped(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=META_HTML)
        ):
            ig = self.new_main(user="_thessb17_", interactive=False)
        self.assertEqual(ig.output["followers"], "1234")
        self.assertEqual(ig.output["following"], "56")
        self.assertEqual(ig.output["posts"], "78")
        self.assertEqual(ig.output["name"], "Thess B")
        self.assertEqual(ig.output["profile pic url"], "https://cdn.example/avatar.jpg")
        self.assertTrue(ig.limited_data)
        self.assertEqual(ig.data_source, "profile page metadata")

    def test_metadata_for_another_account_is_rejected(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(text=META_HTML)
        ):
            with self.assertRaises(api.InstagramError):
                self.make(username="someone_else")._fetch_from_html()

    def test_abbreviated_counts(self):
        html = META_HTML.replace(
            "1,234 Followers, 56 Following, 78 Posts",
            "1.2M Followers, 3K Following, 9.5B Posts",
        ).replace("@_thessb17_", "@prottoy.raha.r")
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            user = self.make()._fetch_from_html()
        self.assertEqual(user["follower_count"], 1_200_000)
        self.assertEqual(user["following_count"], 3_000)
        self.assertEqual(user["media_count"], 9_500_000_000)

    def test_login_page_metadata_is_not_mistaken_for_a_profile(self):
        html = (
            "<html><head><meta property='og:title' content='Login • Instagram' />"
            "<meta property='og:description' content='See everyday moments.' />"
            "</head></html>"
        )
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            with self.assertRaises(api.InstagramError):
                self.make()._fetch_from_html()

    def test_page_json_endpoint(self):
        response = FakeResponse(payload={"data": {"user": USER}})
        with mock.patch.object(api.main, "_get", return_value=response):
            user = self.make()._fetch_from_page_json()
        self.assertEqual(user["username"], "prottoy.raha.r")

    def test_session_uses_impersonation_when_available(self):
        ig = self.make(impersonate=True)
        if api.curl_requests is None:
            self.assertFalse(ig.impersonating)
        else:
            self.assertTrue(ig.impersonating)

    def test_impersonation_can_be_disabled(self):
        self.assertFalse(self.make(impersonate=False).impersonating)

    def test_works_without_curl_cffi(self):
        with mock.patch.object(api, "curl_requests", None):
            ig = self.make()
            self.assertFalse(ig.impersonating)
            self.assertEqual(ig.output["followers"], "120")

    def test_retry_wait_sleeps_and_retries(self):
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                raise api.RateLimitedError("limited", retry_after=0)
            return dict(USER)

        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=fetch
        ), mock.patch("api.api.time.sleep") as sleeper:
            ig = self.new_main(
                user="prottoy.raha.r",
                interactive=False,
                retry_wait=5,
                retries=1,
            )
        self.assertEqual(ig.output["followers"], "120")
        sleeper.assert_called_once_with(5)
        self.assertEqual(len(calls), 2)

    def test_retry_wait_honours_retry_after(self):
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                raise api.RateLimitedError("limited", retry_after=120)
            return dict(USER)

        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=fetch
        ), mock.patch("api.api.time.sleep") as sleeper:
            self.new_main(
                user="prottoy.raha.r",
                interactive=False,
                retry_wait=5,
                retries=1,
            )
        sleeper.assert_called_once_with(120)

    def test_429_without_session_offers_a_prompt(self):
        calls = []

        def fetch():
            calls.append(1)
            if len(calls) == 1:
                raise api.RateLimitedError("limited")
            return dict(USER)

        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=fetch
        ), mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["sessionid=good", ""]
        ) as prompter:
            ig = self.new_main(user="prottoy.raha.r", interactive=True)
        self.assertEqual(prompter.call_count, 2)
        self.assertEqual(ig.sessionid, "good")

    def test_no_retry_by_default(self):
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=api.RateLimitedError("limited")
        ), mock.patch("api.api.time.sleep") as sleeper:
            with self.assertRaises(api.RateLimitedError):
                self.new_main(user="prottoy.raha.r", interactive=False)
        sleeper.assert_not_called()


class CookieAndHostTests(BaseTestCase):
    def test_parse_cookie_input_formats(self):
        cases = {
            "abc123": {"sessionid": "abc123"},
            "sessionid=abc123": {"sessionid": "abc123"},
            "sessionid=abc; csrftoken=def; ds_user_id=42": {
                "sessionid": "abc",
                "csrftoken": "def",
                "ds_user_id": "42",
            },
            "Cookie: sessionid=abc; mid=xyz": {"sessionid": "abc", "mid": "xyz"},
            '{"sessionid": "abc", "csrftoken": "def"}': {
                "sessionid": "abc",
                "csrftoken": "def",
            },
            "  ": {},
        }
        for raw, expected in cases.items():
            self.assertEqual(api._parse_cookie_input(raw), expected, raw)

    def test_full_cookie_header_is_applied(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", "")
        ):
            ig = self.make(
                sessionid="sessionid=sess1; csrftoken=csrf1; ds_user_id=99"
            )
            cookies = ig.session.cookies.get_dict()
        self.assertEqual(ig.sessionid, "sess1")
        self.assertEqual(cookies["csrftoken"], "csrf1")
        self.assertEqual(cookies["ds_user_id"], "99")

    def test_api_headers_include_csrf_token(self):
        ig = self.make()
        ig.session.cookies.set("csrftoken", "tok", domain=".instagram.com")
        headers = ig._api_headers()
        self.assertEqual(headers["X-CSRFToken"], "tok")
        self.assertEqual(headers["X-IG-App-ID"], api.IG_APP_ID)

    def test_mobile_headers_use_mobile_app_id(self):
        ig = self.make()
        headers = ig._api_headers(mobile=True)
        self.assertEqual(headers["X-IG-App-ID"], api.MOBILE_APP_ID)
        self.assertEqual(headers["User-Agent"], api.MOBILE_USER_AGENT)

    def test_www_fallbacks_use_web_headers(self):
        seen = []

        def fake_get(url, **kwargs):
            seen.append((url, kwargs.get("headers", {})))
            if url == api.WWW_USERNAME_INFO_URL.format(username="prottoy.raha.r"):
                return FakeResponse(payload={"user": USER})
            return FakeResponse(status_code=429)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            ig = self.new_main(user="prottoy.raha.r", interactive=False)
        web_url, web_headers = next(
            (url, headers)
            for url, headers in seen
            if "usernameinfo" in url
        )
        self.assertTrue(web_url.startswith("https://www.instagram.com/"))
        self.assertEqual(web_headers.get("X-IG-App-ID"), api.IG_APP_ID)
        self.assertEqual(ig.output["followers"], "120")

    def test_feed_by_id_after_username_feed_fails(self):
        def fake_get(url, **kwargs):
            if url == api.WWW_FEED_BY_USERNAME_URL.format(username="prottoy.raha.r"):
                return FakeResponse(status_code=404)
            if url == api.WWW_FEED_BY_ID_URL.format(user_id="777"):
                return FakeResponse(payload={"items": ThrottlingTests.ITEMS})
            raise AssertionError(url)

        ig = self.make()
        ig.p_data = {"pk": "777"}
        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            items = ig._www_feed_items()
        self.assertEqual(items, ThrottlingTests.ITEMS)
        profile = ig._profile_from_feed(items, "user feed")
        self.assertEqual(profile["follower_count"], 777)

    def test_xig_user_by_username_is_parsed(self):
        html = (
            '<script type="application/json" data-sjs>{"require":[["x",'
            '{"xig_user_by_username":' + json.dumps(MODERN_USER) + "}]]}</script>"
        )
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            user = self.make()._fetch_from_html()
        self.assertEqual(user["follower_count"], 321)

    def test_unrelated_user_in_payload_is_not_returned(self):
        other = dict(MODERN_USER, username="someone_else")
        html = (
            '<script type="application/json" data-sjs>'
            + json.dumps({"data": {"xig_user_by_username": other}})
            + "</script>"
        )
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            with self.assertRaises(api.InstagramError):
                self.make()._fetch_from_html()

    def test_proxy_option_is_applied(self):
        ig = self.make(proxy="http://127.0.0.1:8888")
        self.assertEqual(ig.session.proxies.get("https"), "http://127.0.0.1:8888")

    def test_no_proxy_by_default(self):
        ig = self.make()
        self.assertFalse(ig.session.proxies.get("https"))


class ThrottlingTests(BaseTestCase):
    ITEMS = [
        {
            "user": {
                "pk": "42",
                "username": "prottoy.raha.r",
                "full_name": "Prottoy Raha",
                "is_private": False,
                "follower_count": 777,
                "following_count": 3,
                "media_count": 1,
                "biography": "from the feed",
                "profile_pic_url": "https://cdn.example/feed.jpg",
            },
            "caption": {"text": "#feed"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example/1.jpg"}]},
            "like_count": 4,
            "comment_count": 1,
            "taken_at": 1700000002,
        }
    ]

    def test_429_is_rate_limit_error_with_retry_after(self):
        response = FakeResponse(status_code=429, headers={"Retry-After": "600"})
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaises(api.RateLimitedError) as caught:
                self.make()._fetch_from_api()
        self.assertEqual(caught.exception.retry_after, 600)
        self.assertIn("rate limiting", str(caught.exception))

    def test_429_without_header(self):
        response = FakeResponse(status_code=429)
        with mock.patch.object(api.main, "_get", return_value=response):
            with self.assertRaises(api.RateLimitedError) as caught:
                self.make()._fetch_from_api()
        self.assertIsNone(caught.exception.retry_after)

    def test_verified_session_does_not_prompt_on_429(self):
        response = FakeResponse(status_code=429, headers={"Retry-After": "600"})
        with mock.patch.object(
            api.main, "_session_status", return_value=("valid", " (logged in as @me)")
        ), mock.patch.object(
            api.main, "_get", return_value=response
        ), mock.patch("api.api._read_prompt") as prompter:
            with self.assertRaises(api.RateLimitedError) as caught:
                self.new_main(user="prottoy.raha.r", sessionid="good", interactive=True)
        prompter.assert_not_called()
        message = str(caught.exception)
        self.assertIn("not a login problem", message)
        self.assertIn("600 seconds", message)

    def test_mobile_api_rescues_a_429_from_www(self):
        def fake_get(url, **kwargs):
            if url == api.WEB_PROFILE_INFO_URL:
                return FakeResponse(status_code=429, headers={"Retry-After": "60"})
            if url == api.MOBILE_PROFILE_INFO_URL:
                return FakeResponse(payload={"data": {"user": USER}})
            raise AssertionError(url)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            ig = self.make()
        self.assertEqual(ig.output["followers"], "120")

    def test_username_info_rescues_a_429(self):
        def fake_get(url, **kwargs):
            if url in (api.WEB_PROFILE_INFO_URL, api.MOBILE_PROFILE_INFO_URL):
                return FakeResponse(status_code=429)
            if url == api.USERNAME_INFO_URL.format(username="prottoy.raha.r"):
                return FakeResponse(payload={"user": USER})
            raise AssertionError(url)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            ig = self.make()
        self.assertEqual(ig.output["followers"], "120")

    def test_feed_rebuilds_profile_and_posts(self):
        def fake_get(url, **kwargs):
            if url == api.WWW_FEED_BY_USERNAME_URL.format(username="prottoy.raha.r"):
                return FakeResponse(payload={"items": self.ITEMS})
            if url == api.GRAPHQL_URL:
                return FakeResponse(status_code=429)
            return FakeResponse(status_code=429)

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            ig = self.new_main(user="prottoy.raha.r", interactive=False)
        self.assertEqual(ig.output["followers"], "777")
        self.assertEqual(ig.output["bio"], "from the feed")
        self.assertEqual(ig.tags, {"feed": 1})
        self.assertEqual(ig.posts[0]["likes"], "4")

    def test_rate_limit_page_in_html(self):
        html = "<html>Please wait a few minutes before you try again.</html>"
        with mock.patch.object(api.main, "_get", return_value=FakeResponse(text=html)):
            with self.assertRaises(api.RateLimitedError):
                self.make()._fetch_from_html()

    def test_unverified_session_still_prompts_on_login_wall(self):
        with mock.patch.object(
            api.main, "_session_status", return_value=("invalid", "HTTP 401")
        ), mock.patch.object(
            api.main,
            "_fetch_profile",
            side_effect=api.LoginRequiredError("login"),
        ), mock.patch(
            "api.api._read_prompt", side_effect=[""]
        ) as prompter:
            with self.assertRaises(api.LoginRequiredError):
                self.new_main(user="prottoy.raha.r", interactive=True)
        self.assertEqual(prompter.call_count, 1)

    def test_prompt_reads_visible_line_when_not_a_tty(self):
        with mock.patch.object(sys, "stdin", io.StringIO("sessionid=abc\n")):
            self.assertEqual(api._read_prompt("sessionid: "), "sessionid=abc")

    def test_stdin_availability(self):
        self.assertTrue(api._stdin_available())
        with mock.patch.object(sys, "stdin", None):
            self.assertFalse(api._stdin_available())

    def test_profile_output_is_not_erased(self):
        out = io.StringIO()
        ig = self.make()
        with mock.patch("os.system") as system, contextlib.redirect_stdout(out):
            ig.print_data_()
        system.assert_not_called()
        self.assertIn("followers", out.getvalue())

    def test_eof_on_stdin_cancels(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            with self.assertRaises(EOFError):
                api._read_prompt("sessionid: ")

    def test_no_more_than_three_prompts(self):
        answers = ["a", "b", "c", "d"]
        with mock.patch.object(
            api.main, "_fetch_profile", side_effect=api.LoginRequiredError("login")
        ), mock.patch.object(
            api.main, "_session_status", return_value=("unknown", "no network")
        ), mock.patch(
            "api.api._read_prompt", side_effect=["a", "", "b", "", "c", "", "d", ""]
        ) as prompter:
            with self.assertRaises(api.InstagramError):
                self.new_main(user="prottoy.raha.r", interactive=True)
        # three sessionid attempts (plus the csrftoken asks) at most
        self.assertLessEqual(prompter.call_count, 8)

    def test_warm_up_receives_csrf_and_sends_it(self):
        calls = []

        def fake_get(url, **kwargs):
            calls.append((url, kwargs.get("headers", {})))
            return FakeResponse(
                payload={"data": {"user": USER}},
                headers={"Set-Cookie": "csrftoken=tok123; Domain=.instagram.com"},
            )

        with mock.patch.object(api.main, "_get", side_effect=fake_get):
            ig = self.make(sessionid="good")
            ig.session.cookies.set(
                "csrftoken", "tok123", domain=".instagram.com"
            )
            headers = ig._session_headers()
        self.assertEqual(headers.get("X-CSRFToken"), "tok123")
        self.assertTrue(any(url == "https://www.instagram.com/" for url, _ in calls))

    def test_set_sessionid_warms_up_session(self):
        with mock.patch.object(api.main, "_get", return_value=FakeResponse()) as getter:
            ig = self.make()
            ig._set_sessionid("abc")
        urls = [call.args[0] for call in getter.call_args_list]
        self.assertIn("https://www.instagram.com/", urls)


if __name__ == "__main__":
    unittest.main()
