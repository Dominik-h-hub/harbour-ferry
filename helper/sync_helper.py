#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Ferry background sync helper.
# Started by the systemd user timer; runs all sync pairs through the same
# engine as the app (network rule and skip behavior included)
# and exits afterwards. Full runs also write the diagnostics marker file.
#
# Can be called from the command line as well:
#   python3 /usr/share/harbour-ferry/helper/sync_helper.py [--pair ID|NAME]
# Prints one result line per pair and reports the outcome as exit code
# (see EXIT_* below). There is deliberately no --force: confirming a
# safety abort stays a decision made in the app.
#
# SPDX-License-Identifier: Apache-2.0

import argparse
import datetime
import json
import os
import sys

LOG_PREFIX = "[ferry-helper]"

# Exit codes for scripts. The systemd unit lists 1-3 as SuccessExitStatus:
# a failed pair is not a failed service, and the diagnostics page would
# otherwise start the unit a second time via D-Bus.
EXIT_OK = 0             # every pair that ran was synced
EXIT_FAILED = 1         # at least one pair failed (or the helper itself)
EXIT_SKIPPED = 2        # nothing ran: network rule, run in progress, paused
EXIT_NOT_FOUND = 3      # --pair matches no pair, or more than one
EXIT_USAGE = 64         # bad arguments (EX_USAGE; argparse would use 2)

# Make the app's Python modules importable.
_INSTALL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_INSTALL_ROOT, "qml", "utilities"))

import common
import sync_engine
import sync_pairs


def log(msg):
    line = "%s %s" % (LOG_PREFIX, msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


class _ArgumentParser(argparse.ArgumentParser):
    """argparse exits with 2 on bad arguments - taken by EXIT_SKIPPED."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, "%s: error: %s\n" % (self.prog, message))


def parse_args(argv):
    parser = _ArgumentParser(
        prog="sync_helper.py",
        description="Run Ferry's sync pairs (all of them, or one).",
        epilog="Exit codes: 0 synced, 1 a pair failed, 2 skipped (network "
               "rule, run in progress, pair paused), 3 pair not found or "
               "ambiguous, 64 bad arguments.")
    parser.add_argument("--pair", metavar="ID|NAME",
                        help="sync only this pair - its id, or its name as"
                             " shown in the app")
    return parser.parse_args(argv)


def write_marker():
    """Diagnostics marker proving the timer chain works."""
    payload = {
        "written_at": datetime.datetime.now().isoformat(),
        "pid": os.getpid(),
        "home": os.path.expanduser("~"),
    }
    try:
        os.makedirs(common.data_dir(), exist_ok=True)
        path = os.path.join(common.data_dir(), "sync-marker.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        log("marker written: %s" % path)
    except OSError as e:
        log("cannot write marker: %s" % e)


def find_pair(key):
    """Resolve --pair: an id first, then the shown name.

    Returns (pair, error). A name that several pairs share is refused rather
    than guessed - syncing the wrong pair is worse than syncing none.
    """
    pairs = sync_pairs.list_pairs()
    for pair in pairs:
        if pair.get("id") == key:
            return pair, ""
    named = [p for p in pairs if sync_pairs.display_name(p) == key]
    if len(named) == 1:
        return named[0], ""
    if named:
        return None, ("%d pairs are named %r - use the id instead: %s"
                      % (len(named), key, ", ".join(p["id"] for p in named)))
    return None, "no sync pair with id or name %r" % key


def report(results, single=False):
    """One line per pair; returns the exit code for the whole run."""
    for result in results:
        log("result: %-7s %r (%s)%s"
            % (result["status"], result["name"], result["id"],
               ": " + result["message"] if result["message"] else ""))
    statuses = [r["status"] for r in results]
    if "failed" in statuses:
        return EXIT_FAILED
    if "ok" in statuses:
        return EXIT_OK
    if single:
        # The one requested pair did not run (paused, network rule, busy).
        return EXIT_SKIPPED
    # A full run where only paused pairs exist, or none at all: the pause
    # is the user's choice and no reason to report a problem.
    return EXIT_OK


def run_one(key):
    pair, error = find_pair(key)
    if pair is None:
        log("helper finished: %s" % error)
        return EXIT_NOT_FOUND
    if pair.get("paused"):
        # A paused pair stays paused - resuming is done in the app.
        code = report([sync_engine.pair_result(pair, {"paused": True})],
                      single=True)
    else:
        log("syncing pair %s %r"
            % (pair["id"], sync_pairs.display_name(pair)))
        outcome = sync_engine.run_pair(pair["id"])
        code = report([sync_engine.pair_result(pair, outcome)], single=True)
    log("helper finished: exit %d" % code)
    return code


def run_all():
    result = sync_engine.run_all_now()
    if result.get("skipped"):
        log("helper finished: skipped - %s" % result.get("reason", ""))
        return EXIT_SKIPPED
    code = report(result.get("results", []))
    log("helper finished: exit %d" % code)
    return code


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    log("helper started, pid=%d" % os.getpid())
    # No app is listening; the run start/end and result lines say it all.
    sync_engine.LOG_EVENTS = False
    try:
        if args.pair:
            return run_one(args.pair)
        # The marker proves the timer chain (service -> helper -> full run)
        # works - the diagnostics page starts the unit exactly like this.
        # A --pair call from a terminal proves nothing about it.
        write_marker()
        return run_all()
    except Exception as e:
        log("helper FAILED: %s" % e)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
