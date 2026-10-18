#!/bin/env python3

import argparse
import os
import sys

from api.api import (
    InstagramError,
    forget_session,
    main,
    nu,
    re,
    session_file_path,
)

ap = argparse.ArgumentParser()
ap.add_argument("-u", "--username", required=True,
                help="username of account to scan")
ap.add_argument("-p", "--postscrap", action='store_true',
                help="scrape all uploaded images info ")
ap.add_argument("-s", "--savedata", action='store_true',
                help="save data to file")
ap.add_argument("-t", "--tagscrap", action="store_true",
                help="list often used tags")
ap.add_argument("--proxy", default=None,
                help="HTTP(S) proxy to use")
ap.add_argument("--no-impersonate", action="store_true",
                help="disable browser TLS impersonation (curl_cffi)")
ap.add_argument("--retry-wait", type=int, default=0, metavar="SECONDS",
                help="wait and retry once when Instagram rate limits you")
ap.add_argument("--login", action="store_true",
                help="ask for an Instagram sessionid before scraping")
ap.add_argument("--debug", action="store_true",
                help="show which profile sources were tried and why they failed")
ap.add_argument("--session-file", default=None, metavar="PATH",
                help="where to save the session cookies "
                     "(default: ~/.config/SoIG/session.json)")
ap.add_argument("--no-save-session", action="store_true",
                help="do not save the sessionid/csrftoken to disk")
ap.add_argument("--forget-session", action="store_true",
                help="delete the saved sessionid/csrftoken and exit")
args = vars(ap.parse_args())

if args["forget_session"]:
    removed = forget_session(args["session_file"])
    if removed:
        print(f"{re}[!]{nu} removed the saved session: {removed}")
    else:
        print(f"{re}[!]{nu} no saved session at {args['session_file'] or session_file_path()}")
    sys.exit(0)

os.system('clear') if os.name == 'posix' else os.system('cls')
try:
    ig = main(
        user=args["username"],
        proxy=args["proxy"],
        impersonate=False if args["no_impersonate"] else None,
        retry_wait=args["retry_wait"],
        login=args["login"],
        debug=args["debug"],
        save_cookies=not args["no_save_session"],
        session_file=args["session_file"],
    )
except InstagramError as exc:
    print(f"\n{re}[!]{nu} {exc}\n")
    sys.exit(1)
except KeyboardInterrupt:
    print(f"\n{re}[!]{nu} cancelled\n")
    sys.exit(1)

if args['tagscrap']:
    ig.print_data()
    ig.most_common_tags()
else:
    ig.print_data_()

if args['postscrap']:
    ig.scrap_uploads()

if args['savedata']:
    ig.save_data()
