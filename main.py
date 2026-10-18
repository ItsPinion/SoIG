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
args = vars(ap.parse_args())

os.system('clear') if os.name == 'posix' else os.system('cls')
try:
    ig = main(user=args["username"])
except InstagramError as exc:
    print(f"\n{re}[!]{nu} {exc}\n")
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
