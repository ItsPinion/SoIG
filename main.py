#!/bin/env python3

import argparse
import os
import sys

from api.api import InstagramError, main, nu, re

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
                help="HTTP(S) proxy to use (or set IG_PROXY)")
ap.add_argument("--no-impersonate", action="store_true",
                help="disable browser TLS impersonation (curl_cffi)")
ap.add_argument("--retry-wait", type=int, default=0, metavar="SECONDS",
                help="wait and retry once when Instagram rate limits you "
                     "(or set IG_RETRY_WAIT)")
args = vars(ap.parse_args())

os.system('clear') if os.name == 'posix' else os.system('cls')
retry_wait = args["retry_wait"] or int(os.environ.get("IG_RETRY_WAIT", "0") or 0)
try:
    ig = main(
        user=args["username"],
        proxy=args["proxy"],
        impersonate=False if args["no_impersonate"] else None,
        retry_wait=retry_wait,
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
