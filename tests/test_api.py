"""Regression tests for the Instagram profile scraper.

Run from the repository root with::

    python -m unittest discover -s tests
"""

import json
import os
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
    def __init__(self, status_code=200, payload=None, text="", url="https://x/"):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.url = url
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
        for name, value in (
            ("tiny_url", mock.Mock(side_effect=lambda url: url)),
            ("write", mock.Mock()),
        ):
            patcher = mock.patch.object(api.extra, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make(self, user=USER, feed=None, **kwargs):
        with mock.patch.object(
            api.main, "_fetch_profile", return_value=dict(user)
        ), mock.patch.object(api.main, "_fetch_feed", return_value=feed or []):
            return api.main(user=kwargs.pop("username", "prottoy.raha.r"), **kwargs)


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
            ig = api.main(user="prottoy.raha.r")
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
            self.assertEqual(self.make()._fetch_from_api()["username"], USER["username"])

    def test_api_404(self):
        with mock.patch.object(
            api.main, "_get", return_value=FakeResponse(status_code=404)
        ):
            with self.assertRaisesRegex(api.InstagramError, "404"):
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
                api.main(user="prottoy.raha.r")

    def test_session_id_from_environment(self):
        with mock.patch.dict(os.environ, {"IG_SESSIONID": "abc123"}):
            ig = self.make()
        cookies = {c.name: c.value for c in ig.session.cookies}
        self.assertEqual(cookies.get("sessionid"), "abc123")


if __name__ == "__main__":
    unittest.main()
