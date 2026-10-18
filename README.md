![SoIG](.github/header.svg)

# SoIG

The Instagram OSINT Tool gets a range of information from an Instagram account that you normally wouldn't be able to get from just looking at their profile.

## Getting Started

**The information includes**:

- **profile** : Username, Profile Name, URL, Followers, Following, Number of Posts, Bio, Profile Picture URL, Is Business Account , Connected to a FB account , External URL, Joined Recently , Business Category Name, Is private , Is Verified.

- **tags** : most used , and by `-t` all used tags.

- **posts** : accessability caption, location, timestamp, comments disabled, Caption, picture url.

### Prerequisites

- Python 3.8 or higher.
- Linux, macOS or Windows.

### Project setup

```sh
# clone the repo
$ git clone https://github.com/yezz123/SoIG

# move to the project folder
$ cd SoIG
```

### Creating virtual environment

- Create a virtual environment using virtualenv.

```shell
# creating virtual environment
$ virtualenv venv
ls -la venv/bin/
# activate virtual environment
$ source venv/bin/activate

# install all dependencies
$ pip install -r requirements.txt
```

### Running the Application

- You can run the `main.py` file Manually.

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
[!] Instagram is asking for a logged-in session.
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

- **`[+] sessionid works (logged in as @you)`** - the cookie is valid and the
  scrape continues.
- **`[!] that sessionid does not work (...)`** - the cookie was rejected, so it
  is discarded and you are asked again. Press Enter to give up.

You can also supply it up front; it is validated the same way, and a stale value
is ignored with a warning:

```sh
$ export IG_SESSIONID="<sessionid value>"
$ python3 main.py --username <username>
```

The value is read from the `IG_SESSIONID` environment variable or typed at the
prompt; it is never written to disk. Use your own account, keep the value
private and log out / rotate the session when you are done. Scraping is still
subject to Instagram's rate limits.

#### If Instagram rate limits you (HTTP 429)

Even with a valid session, Instagram throttles per IP address. SoIG tells the two
apart:

- **`sessionid works`** followed by a 429 message means the cookie is fine -
  Instagram is throttling the connection, so pasting another sessionid will not
  help. Wait for the time shown (Instagram often asks for 10-60 minutes) and run
  the command again.
- **`[!] Instagram is asking for a logged-in session.`** means the cookie really
  is missing or rejected, and SoIG prompts for one.

To keep working when `web_profile_info` is throttled, SoIG automatically tries
other sources for the same profile, all on `www.instagram.com` where web cookies
are accepted:

1. `users/web_profile_info/?username=`
2. `users/<username>/usernameinfo/`
3. `feed/user/<username>/username/` (falling back to `feed/user/<id>/`)
4. the profile page's embedded JSON
5. the same endpoints on `i.instagram.com` with the mobile app's headers

It also loads `instagram.com` once per session so requests carry a matching
`csrftoken`.

If your IP is throttled for a longer period, route SoIG through another
connection with `IG_PROXY`:

```sh
$ IG_PROXY=http://user:pass@host:port python3 main.py --username <username>
```

## Usage

A simple workflow of command used to run SoIG with a Description.

| Usage                                 | Description                                               |
| ------------------------------------- | --------------------------------------------------------- |
| `-h` , `--help`                       | show this help message and exit.                          |
| `-u` USERNAME , `--username` USERNAME | username of account to scan.                              |
| `-p` , `--postscrap`                  | scrape all uploaded images info.                          |
| `-s` , `--savedata`                   | save data to file ( save profile pic, info , post info ). |
| `-t` , `--tagscrap`                   | list all used tags (the five most used are shown by default). |

## Running the tests

```sh
$ python -m unittest discover -s tests
```

## Contributing

- Join the SoIG Creator and Contribute to the Project if you have any enhancement or add-ons to create a good and Secure Project, Help any User to Use it in a good and simple way.

### Disclaimer

- This project can only be used for educational purposes. Using this software against target systems without prior permission is illegal, and any damages from misuse of this software will not be the responsibility of the author.

## License

This project is licensed under the terms of the [MIT license](LICENSE).
