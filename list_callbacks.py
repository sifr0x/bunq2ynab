import json
import sys

from lib import bunq
from lib import bunq_api
from lib.config import config


config.load()


def print_notification_filter(nfs):
    if not nfs:
        print("  No callbacks")
        return
    for nfi in nfs:
        nf = nfi["NotificationFilterUrl"]
        print('  {} -> {}'.format(
            nf["category"],
            nf.get("notification_target", "-")))


def process_account(u, ac):
    if ac["status"] != "ACTIVE":
        return
    print("Callbacks for account {} ({}):".format(
                                          ac["id"], ac["description"]))
    method = ("v1/user/{}/monetary-account/{}/" +
        "notification-filter-url").format(u["id"], ac["id"])
    nfs = bunq.get(method)
    print_notification_filter(nfs)


def process_user(k, u):
    if k == "UserApiKey":
        #x = print(next(iter(v["requested_by_user"].values())))
        #print(x)
        #name = x["display_name"]
        name = next(iter(v["requested_by_user"].values()))["display_name"]
    else:
        name = u["display_name"]
    print("Callbacks for user {}:".format(name))
    method = "v1/user/{}/notification-filter-url".format(u["id"])
    nfs = bunq.get(method)
    print_notification_filter(nfs)

    method = "v1/user/{}/monetary-account".format(u["id"])
    for acs in bunq.get(method):
        for ac in acs.values():
            process_account(u, ac)


method = "v1/user"
users = bunq.get(method)
for u in users:
    for k, v in u.items():
        process_user(k, v)
