![SoIG](.github/header.svg)

# SoIG

The Instagram OSINT Tool gets a range of information from an Instagram account that you normally wouldn't be able to get from just looking at their profile.

## Getting Started

**The information includes**:

- **profile** : Username, Profile Name, URL, Followers, Following, Number of Posts, Bio, Profile Picture URL, Is Business Account , Connected to a FB account , External URL, Joined Recently , Business Category Name, Is private , Is Verified.

- **tags** : only with `-t`, which lists every tag and its count.

- **posts** : accessability caption, location, timestamp, comments disabled, Caption, picture url.

### Prerequisites

- Python 3.9 or higher.
- Linux, macOS or Windows (a terminal is recommended for the login prompt).

### Install

Install it as a command (recommended - `soig` works from any directory):

```sh
# from a clone
$ pipx install .          # or: pip install --user .
$ soig -u <username>

# or, for development
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e ".[dev]"
```

`python3 main.py ...` still works exactly like `soig ...`, so the rest of this
file uses the longer form.

```sh
# clone the repo
$ git clone https://github.com/ItsPinion/SoIG
$ cd SoIG
```

Or keep using a virtualenv and `requirements.txt`:

```shell
$ virtualenv venv
$ source venv/bin/activate
$ pip install -r requirements.txt
```

### Running the Application

```sh
# Running the Script
$ python3 main.py --username <username>

# also scrape every post, save the results and list all used tags
$ python3 main.py --username <username> --postscrap --savedata --tagscrap
```

#### If Instagram asks for a login

Instagram only returns profile data to a subset of logged-out requests and often
answers with a login page instead. When that happens SoIG asks for a `sessionid`
and keeps asking until it gets one that works:

```text
  ! Instagram is asking for a logged-in session.
  1. Log in to https://www.instagram.com in your browser.
  2. Open the developer tools (F12) -> Application -> Storage -> Cookies ->
     instagram.com and copy the value of the 'sessionid' cookie.
  3. Paste it below (the value is hidden while you type).
Press Enter without a value to cancel.

sessionid:
```

Paste either the `sessionid` value or, preferably, the **whole `Cookie` request
header** from that request (`sessionid=...; csrftoken=...; ds_user_id=...`). All
cookies are applied, which makes the requests look like your browser's and
avoids the 403/429 responses Instagram sends when only `sessionid` is present.

Each value is checked against Instagram before it is used:

- **`✓ sessionid works (logged in as @you)`** - the cookie is valid and the
  scrape continues.
- **`! that sessionid does not work (...)`** - the cookie was rejected, so it
  is discarded and you are asked again. Press Enter to give up.

Values are only ever typed at the prompt (or stored in the JSON file described
below). Use your own account, keep the value private and run
`--forget-session` when you are done. Scraping is still subject to Instagram's
rate limits.

#### If Instagram rate limits you (HTTP 429)

Even with a valid session, Instagram throttles per IP address. SoIG tells the two
apart:

- **`sessionid works`** followed by a 429 message means the cookie is fine -
  Instagram is throttling the connection, so pasting another sessionid will not
  help. Wait for the time shown (Instagram often asks for 10-60 minutes) and run
  the command again.
- **`! Instagram is asking for a logged-in session.`** means the cookie really
  is missing or rejected, and SoIG prompts for one.

To keep working when `web_profile_info` is throttled, SoIG automatically tries
other sources for the same profile:

1. `users/web_profile_info/?username=`
2. the profile page's embedded JSON (`data-sjs` / `xig_user_by_username`),
   fetched again as a **logged-out browser navigation**, because Instagram
   only embeds the payload for those requests. Missing counts are filled from
   the page's OpenGraph metadata
3. the profile page's OpenGraph metadata (works even when Instagram strips the
   JSON payload; gives name, followers, following, posts and avatar)
4. `graphql/query/` with `PolarisProfilePageContentQuery`, the query the web
   client itself uses (see `PROFILE_DOC_IDS`; the id comes from the post feed)
5. `?__a=1&__d=dis` JSON
6. `users/<username>/usernameinfo/` and `feed/user/<username>/username/`
   (falling back to `feed/user/<id>/`)
7. the same endpoints on `i.instagram.com` with the mobile app's headers
8. `i.instagram.com/users/<id>/info/`, with the id taken from the post feed
   when the lookup endpoints are refused

It also loads `instagram.com` once per session so requests carry a matching
`csrftoken`.

#### Getting the full profile (bio, posts, tags)

Instagram only ships the full profile payload to logged-in sessions. Without a
sessionid SoIG prints a notice up front and falls back to the public metadata
(name, followers, following, posts, avatar). To get everything:

```sh
$ python3 main.py --username <username> --login
```

`--login` also works **on its own**, without a username: it only checks or
replaces the stored session and exits, so you can log in once and scrape any
account afterwards:

```sh
$ python3 main.py --login          # log in and save the session
$ python3 main.py -u someone -p    # later runs reuse it
```

If a saved session still works, that command just says so instead of asking for
the same cookies again (`--forget-session` removes it first when you want to
switch accounts). The same is true of the other actions that do not scrape
anything: `--forget-session` and `--inspect-page` never ask for `--username`, and
`--help` does not either. Every option that needs a profile (`-p`, `-s`, `-t`,
`-a`, `-m`, `-D`) still requires `-u`.

#### Short flags

Every option has a one-letter form, so a typical debug run is just:

```sh
$ python3 main.py -u <username> -l -d          # log in, then trace every step
$ python3 main.py -u <username> -d -a -m 500   # trace, 500 posts
```

`-h` lists them all. The table at the end of this file has the full mapping.

#### Output modes

| Mode | What you get |
| ---- | ------------ |
| default | profile report, plus posts with `-p` and tags with `-t`, plus a live progress line |
| `-q`, `--quiet` | no progress line and no banner, only warnings and the report (for scripts) |
| `-c`, `--no-color` | no ANSI colours; also automatic when stdout is not a terminal |
| `-d`, `--debug` | the full `[step]` trace instead of the progress line, with URLs and statuses |
| `-j PATH`, `--json PATH` | the same result as JSON (`-j -` prints only JSON on stdout) |

A report looks like this (add `-p` for the post list, `-t` for hashtags):

```text
╭────────────────────────────────────────────────────────────────────────────╮
│  SoIG  ·  Instagram profile scraper                                v1.0.0  │
╰────────────────────────────────────────────────────────────────────────────╯

  ▌ PROFILE                                                   via profile page
──────────────────────────────────────────────────────────────────────────────

  @prottoy.raha.r  ·  Prottoy Raha                                  ✓ verified
  instagram.com/prottoy.raha.r

  Followers    12,345 │ Following     321 │ Posts       87

  Bio         Building things, mostly on the internet.
  Website     https://prottoy.example
  Category    Creator
```

Numbers are grouped, empty fields are left out, and the posts/hashtags sections
look like this:

```text
  ▌ POSTS                                                            2 loaded
──────────────────────────────────────────────────────────────────────────────

    1  A new project is out today — read the thread.
       1,234 likes · 56 comments · 02 Mar 2026 · Kolkata, India
       instagram.com/p/abc123

    2  (no caption)
       12 likes · 0 comments · comments off
       instagram.com/p/def456

  ▌ HASHTAGS                                           23 mentions · 4 unique
──────────────────────────────────────────────────────────────────────────────

  #buildinpublic  ████████████████████  12
  #python         ████████████░░░░░░░░   7
  #osint          █████░░░░░░░░░░░░░░░   3
  #ai             ██░░░░░░░░░░░░░░░░░░   1
```

The framed wordmark, block bars and box-drawing rules fall back to ASCII
(`+---+`, `#`, `-`) when the terminal is not UTF-8, and `-q` hides the wordmark.

On success the JSON document has `tool`, `version`, `exit_code`, `scraped_at`,
`username`, `profile`, `posts`, `tags`, `posts_error` and `limited_data`; a
failed run keeps the metadata and adds an `error` string. It can be fed straight
to `jq`:

```sh
$ python3 main.py -u <username> -p -j - | jq '.posts[].url'
```

#### Exit codes

| Code | Meaning |
| ---- | ------- |
| `0` | success |
| `1` | runtime error (network, unexpected page, unreadable profile) |
| `2` | bad usage (missing `-u`, unknown flag, negative `-m`) |
| `3` | a login is required and none was available |
| `4` | Instagram is rate limiting this IP (retry with `-w/--retry-wait`) |
| `130` | interrupted with Ctrl+C |

#### Progress and debugging

A normal run keeps the terminal quiet: one status line names the current
request and, while a timeline is walked, turns into a real progress bar with
counts, percentage, elapsed time and an ETA. It is only drawn on a terminal, so
pipes and log files stay clean:

```text
  Fetching @headlines.ban     ░░░░██████████░░░░░░░░░░░░░░░░░░  1.4s
  Loading posts               ████████████████░░░░░░░░░░░░░░░░  12/24 · 50% · 3.2s · ETA 3.2s
```

While the size is unknown the highlight sweeps across the bar; terminals without
UTF-8 get `#`/`-` instead of the block characters.

Add `-d/--debug` to replace that with a `[step]` line for every stage, so a bad
result can be traced to the request that caused it:

```text
[step] target: headlines.ban | login: False | save session: True | proxy: no
[step] transport: curl_cffi (Chrome impersonation)
[step] saved session: /home/you/.config/SoIG/session.json (csrftoken, sessionid)
[step] checking the saved session with Instagram ...
[step] saved session check: valid (HTTP 200)
  ✓ using the saved session (logged in as @you) (/home/you/.config/SoIG/session.json)
[step] session: set
[step] trying 11 profile sources ...
[step] 1/11 profile API ...
[step] 1/11 profile API: rate limited (HTTP 429, 0.1s)
[step] 2/11 profile page ...
[step] profile page: HTTP 200, 318214 characters
[step] profile page: no embedded profile JSON, only OpenGraph metadata (bio and posts are stripped for this request)
[step] 2/11 profile page: ok (HTTP 200, 0.6s)
[step] profile source: profile page metadata (metadata only)
[step] not asking for another sessionid: the current one is valid, so Instagram is refusing the full profile for this account/IP
[step] posts: loading the first page ...
[step] posts: none available (the post feed returned non-JSON data)
[step] done in 3.4s
```

`-d/--debug` also prints the exact URL, HTTP status and response size of every
request. The trace is never shown unless you ask for it.

The prompt works in a normal terminal (input hidden) and also when input is
piped or comes from an IDE run pane (input visible). If no input is available at
all, SoIG tells you to rerun with `--login` from a terminal.

#### Posts and tags

By default the first page of posts (up to 12) is loaded with the profile, which
keeps a run to a couple of requests. To load more:

```sh
$ python3 main.py --username <username> -a        # every post
$ python3 main.py --username <username> -m 200    # exactly 200 posts
$ python3 main.py --username <username> -a -m 200 # same 200, -a caps at -m
$ python3 main.py --username <username> -t -a     # tags from every caption
```

`-m/--max-posts` stands on its own: it pages as far as needed and then keeps
exactly N posts, so `-m 2` prints 2 posts even though Instagram pages in 12s.
With `-a` it caps the walk instead of extending it - the two flags never
contradict each other.

SoIG follows the feed's `end_cursor` one page at a time (12 posts per request,
1 second apart), draws a progress bar, and keeps what it already collected if a
page is throttled. A `-m N` run stops as soon as N posts are in hand and trims
the last page down to N. With `-d` the same walk is logged step by step:

```text
[step] posts: walking the whole timeline (8274 posts), one request every 1s ...
[step] posts: page 2 -> 24 posts
...
[step] posts: collected 8274 posts in 690 pages
```

A capped run looks like this:

```text
[step] posts: collecting 20 posts, one request every 1s ...
[step] posts: page 2 -> 24 posts
[step] posts: collected 24 posts in 2 pages
[step] posts: keeping the first 20 of 24
```

For 8k posts that is roughly 690 requests and 12 minutes - use `-m/--max-posts`
to sample a smaller slice.

Use:

```sh
$ python3 main.py --username <username> -p   # print each post (URL, caption, likes, comments, time)
$ python3 main.py --username <username> -s   # save posts_data.txt, profile_data.txt, tags.txt and the avatar
$ python3 main.py --username <username> -t   # print the full tag list instead of the top five
```

Tags are the hashtags found in post captions. `-t` prints the full list; with
`-d` the trace also reports how many captions were scanned, so an empty list is
explained rather than mysterious:

```text
[step] tags: 14 hashtags in 9 captions (6 unique)
# or
[step] tags: none of the 12 captions contain a hashtag
```

#### Private profiles

A private profile is readable only while the logged-in account follows it and
the follow request has been accepted. SoIG therefore does not refuse a private
account up front: it asks Instagram's feed, and only reports the profile as
unreadable when no posts come back.

With `-d`, a readable private profile says so:

```text
[step] posts: private account, but this session reads 12 posts, so it follows it
```

If nothing comes back, the run explains what to fix (log in, or paste the whole
Cookie header, or retry later).

#### Saved sessions

Instead of pasting one long string, SoIG asks for the values it needs:

```text
sessionid (or the whole Cookie header): …
csrftoken (optional, press Enter to fetch it automatically): …
  ✓ sessionid works (logged in as @you)
  ✓ saved session to /home/you/.config/SoIG/session.json
```

`sessionid` is the cookie that actually identifies you; `csrftoken` is sent back
with every GraphQL request as an `X-CSRFToken` header, so the prompt offers to
fetch it for you (Instagram hands one out on any page load). The scraper never
reads `ds_user_id` or `mid`, so it does not ask for them — it only stores them
when Instagram set them by itself.

Every cookie is stored as JSON with owner-only permissions (`0600`, folder
`0700`) and reused automatically on later runs:

```text
  ✓ using the saved session (logged in as @you) (/home/you/.config/SoIG/session.json)
```

When Instagram stops accepting them, SoIG says so and asks again, replacing the
file:

```text
  ! the saved session in …/session.json has expired (HTTP 401).
    Please log in again.
```

Manage it with:

```sh
$ python3 main.py --forget-session                     # delete saved cookies
$ python3 main.py --login                              # log in, then exit
$ python3 main.py --username <username> --no-save-session  # do not write them
$ python3 main.py --username <username> --session-file ~/ig.json
```

Pasting a full `Cookie` header at the prompt still works (it is split into the
individual cookies). The file is plain text: keep it private and run
`--forget-session` when you are done.

#### Browser impersonation (recommended)

Instagram strips the embedded JSON payload and rate limits harder for Python
clients whose TLS fingerprint is not a browser. SoIG uses
[`curl_cffi`](https://pypi.org/project/curl-cffi/) to impersonate Chrome whenever
it is installed (it is in `requirements.txt`); disable it with
`--no-impersonate` if needed. If only the OpenGraph metadata could be read, SoIG
says so and offers to log in: paste a `sessionid` / `Cookie` header and it
fetches the full profile; press Enter to continue with just the public numbers.
Use `--login` to be asked for cookies before scraping at all.

```sh
$ pip install curl_cffi        # or: pip install -r requirements.txt
```

#### Proxies and retries

Route SoIG through another connection when your IP is throttled:

```sh
$ python3 main.py --username <username> --proxy http://user:pass@host:port
```

To wait and retry automatically after a 429 (using Instagram's `Retry-After`
when present):

```sh
$ python3 main.py --username <username> --retry-wait 60
```

## Usage

A simple workflow of command used to run SoIG with a Description.

| Usage                                 | Description                                               |
| ------------------------------------- | --------------------------------------------------------- |
| `-h` , `--help`                       | show this help message and exit.                          |
| `-u` USERNAME , `--username` USERNAME | username of account to scan (not needed with `-l`, `-F`, `-I`). |
| `-p` , `--postscrap`                  | scrape all uploaded images info.                          |
| `-s` , `--savedata`                   | save data to file ( save profile pic, info , post info ). |
| `-t` , `--tagscrap`                   | list all used tags (hidden otherwise).                        |
| `-l`, `--login`                       | log in before scraping; alone: log in and exit.              |
| `-x`, `--proxy PROXY`                 | route requests through an HTTP(S) proxy.                     |
| `-w`, `--retry-wait SECONDS`          | wait and retry once after a 429.                             |
| `-i`, `--no-impersonate`              | disable browser TLS impersonation (`curl_cffi`).             |
| `-d`, `--debug`                       | show the full `[step]` trace and every request that was tried. |
| `-D`, `--dump-page PATH`              | save the profile page HTML for inspection.                   |
| `-I`, `--inspect-page PATH`            | describe a saved page HTML and exit (no `-u` needed).        |
| `-a`, `--all-posts`                   | page through the whole timeline.                             |
| `-m`, `--max-posts N`                 | stop after N posts (pages as needed; also caps `--all-posts`). |
| `-f`, `--session-file PATH`           | where saved cookies live.                                    |
| `-n`, `--no-save-session`             | do not save the sessionid/csrftoken to disk.                 |
| `-F`, `--forget-session`              | delete the saved sessionid/csrftoken and exit (no `-u` needed). |
| `-q`, `--quiet`                       | hide the progress line and banner.                           |
| `-c`, `--no-color`                    | no ANSI colours (automatic when piped).                      |
| `-j`, `--json PATH`                   | also write the result as JSON (`-` prints it on stdout).     |
| `-V`, `--version`                     | show the version and exit.                                   |

## Development

```sh
$ pip install -e ".[dev]"
$ ruff check . && ruff format --check .
$ python -m unittest discover -s tests -v
```

The suite is offline (it mocks the HTTP layer) and runs on Python 3.9-3.13 in
CI. See [CONTRIBUTING.md](CONTRIBUTING.md) for the code guidelines and
[SECURITY.md](SECURITY.md) for how session cookies are handled.

## Contributing

- Join the SoIG Creator and Contribute to the Project if you have any enhancement or add-ons to create a good and Secure Project, Help any User to Use it in a good and simple way.

### Disclaimer

- This project can only be used for educational purposes. Using this software against target systems without prior permission is illegal, and any damages from misuse of this software will not be the responsibility of the author.

## License

This project is licensed under the terms of the [MIT license](LICENSE).
