#!/usr/bin/env python
import getpass
import json
import os
import sys
from uuid import getnode

import gkeepapi
import gpsoauth
import keyring

keep = gkeepapi.Keep()

cache_home = os.environ.get(
    "XDG_CACHE_HOME", os.path.join(os.environ["HOME"], ".cache")
)
nvim_cache = os.path.join(cache_home, "nvim")
os.makedirs(nvim_cache, exist_ok=True)
config_file = os.path.join(nvim_cache, "gkeep.json")
email = None
data = {}
if os.path.exists(config_file):
    with open(config_file, "r") as ifile:
        data = json.load(ifile)
        email = data.get("email")

if email is None:
    email = input("Email:")
    data["email"] = email
    with open(config_file, "w") as ofile:
        json.dump(data, ofile)

token = keyring.get_password("google-keep-token", email)

if token is None:
    password = getpass.getpass("Password:")
    res = gpsoauth.perform_master_login(email, password, str(getnode()))
    token = res.get('Token')
    if token is None:
        url = res.get("Url")
        if url:
            print(url)
            print("Complete login flow in browser")
            sys.exit(0)
        else:
            print("Unknown error logging in", res)
            sys.exit(1)
    keyring.set_password("google-keep-token", email, token)

keep.resume(email, token)
keep.sync()
for note in keep.all():
    print(note.title)
