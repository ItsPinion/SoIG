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
answers with a login page instead. If SoIG reports that the profile could not be
retrieved:

1. Log in to <https://www.instagram.com> in your browser.
2. Open the developer tools, go to **Application/Storage → Cookies →
   instagram.com** and copy the value of the `sessionid` cookie.
3. Export it for the current shell and run the tool again:

```sh
$ export IG_SESSIONID="<sessionid value>"
$ python3 main.py --username <username>
```

Use your own account, keep the value private and log out / rotate the session
when you are done. Scraping is still subject to Instagram's rate limits.

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
