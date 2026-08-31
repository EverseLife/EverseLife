# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Can Russia still reach us? Asked two ways, because one is not enough.

    python tools/subnet_watch.py                    # whatever the site's names resolve to
    python tools/subnet_watch.py 13.140.145.20      # a candidate address, before paying for it
    python tools/subnet_watch.py --no-probe         # lists only, no third-party service

**The lists.** The registry names single addresses, but a good share of
providers filter by the whole /24 around them. That is how the landing went
dark for part of Russia in August 2026 while being listed exactly nowhere: a
neighbour at 144.91.67.143 was listed, the aggregated lists gained
144.91.67.0/24 with it, and everyone filtering by block lost the site.

So the thing to watch is not our address but the block it sits in. Lists come
from antifilter.download, which republishes the registry a few times a day.
`allyouneed.lst` is the aggregated one and answers the only question that
matters; `ip.lst` holds the exact addresses and is fetched only once the first
says we are caught -- to name the neighbour who did it, since the answer
decides what to do next. Ours listed means the site drew attention; a
neighbour listed means the address is simply spoiled and needs replacing.

**The probe.** check-host opens TCP 443 from Moscow and Saint Petersburg. Be
clear about what this does and does not see: its nodes sit in data centres,
and during the August outage every one of them connected to the blocked
address without trouble -- the filtering happened at home and mobile
providers, which nobody here can reach. The probe therefore catches a
different family of trouble (the server gone, the port shut, the route to
Russia broken) and is no substitute for the lists. When an alert fires, which
of the two spoke is the first thing worth knowing.

The two are also trusted differently, and deliberately so. A list that cannot
be read or has gone stale is a failure, because the lists are the detector
here and silence from them reads as good news. The probe leans on somebody
else's free service, so its being unavailable is reported and no more --
paging every hour because check-host is having a day would only teach us to
ignore the channel. For the same reason nothing here treats a node's silence
as evidence: only a node that tried and failed says anything about us.

Exit codes are deliberately not 1 or 2. Those two are what Python itself
returns for an unhandled traceback and what argparse returns for a bad
command line, and a caller cannot tell them from a verdict -- which is how a
malformed HTTP header would have announced itself in Discord as "the address
is blocked, change servers". Anything outside the list below means the
monitor broke, not that the news is bad.

    0  all well
    4  caught by the lists          <- worst, because it is the known killer
    5  no answer from Russia
    6  a name gave no address
    7  the lists could not be trusted
    20 this script fell over

Several can be true at once; they are reported in that order of precedence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import ipaddress
import json
import socket
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

#: The public names. Addresses are deliberately not written down: what is
#: worth watching is whatever DNS points at today, so retiring an old address
#: costs no edit here.
DOMAINS = ("everse.life", "alpha.everse.life", "agents.everse.life")

AGGREGATED = "https://antifilter.download/list/allyouneed.lst"
EXACT = "https://antifilter.download/list/ip.lst"

#: Past this the list is not evidence of anything. The source republishes
#: several times a day; two days of silence means it is broken, and a broken
#: source must not read as good news.
MAX_AGE_HOURS = 48

CHECK_HOST = "https://check-host.net"
#: check-host's own node names, which do change. What actually gets asked is
#: whichever of these the service accepts and places in Russia -- see `probe`.
PROBE_NODES = (
    "ru1.node.check-host.net",
    "ru2.node.check-host.net",
    "ru3.node.check-host.net",
)
PROBE_PORT = 443
#: The service answers asynchronously: ask, then collect, backing off so an
#: hourly job does not hammer a free service for a minute at three-second
#: intervals. Sums to just over a minute, which is long enough for a slow node.
PROBE_WAITS = (3, 3, 5, 5, 10, 10, 10, 15)

#: Retried because one 502 from somebody else's CDN must not turn into an
#: hourly "the monitor is blind" in a channel people are supposed to read.
FETCH_ATTEMPTS = 3
FETCH_BACKOFF = (2, 5)

TIMEOUT = 60
AGENT = "EverseLife-SubnetWatch (+https://everse.life)"

OK = 0
CAUGHT = 4
UNREACHABLE = 5
NO_ADDRESS = 6
LISTS_UNUSABLE = 7
BROKEN = 20
#: Worst first. Being on the lists outranks everything because it is both the
#: known cause and the one with an obvious next step; a blind monitor is last
#: because it is a problem with us, not with the site.
PRECEDENCE = (CAUGHT, UNREACHABLE, NO_ADDRESS, LISTS_UNUSABLE)

#: Told apart from a node's own refusal on purpose. A node that says "timed
#: out" has tried and failed, which is about us; a node that says nothing
#: before the budget runs out is check-host being slow, which is not.
PENDING = "check-host gave no verdict in time"


class ProbeUnavailable(Exception):
    """check-host would not answer. Not our outage, and not worth an alert."""


def verdict(found: set[int]) -> int:
    """The one code to exit with when several findings apply."""
    for code in PRECEDENCE:
        if code in found:
            return code
    return OK


def as_aware(stamp: dt.datetime) -> dt.datetime:
    """A timestamp that can be subtracted from `now`.

    `Last-Modified: ... -0000` is legal and means "zone unknown", and
    `parsedate_to_datetime` hands back a naive datetime for it. Subtracting
    that from an aware `now` raises, which used to leave the script exiting
    with a traceback over a header nobody controls.
    """
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.UTC)


def fetch(url: str, accept: str | None = None) -> tuple[str, dt.datetime | None]:
    """The body of `url`, and what it claims as its last change."""
    headers = {"User-Agent": AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    last: Exception | None = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                body = answer.read().decode("utf-8", "replace")
                stamp = answer.headers.get("Last-Modified")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as failure:
            last = failure
            if attempt < len(FETCH_BACKOFF):
                time.sleep(FETCH_BACKOFF[attempt])
    else:
        raise OSError(f"{FETCH_ATTEMPTS} attempts failed, last: {last}")

    if not stamp:
        return body, None
    try:
        return body, as_aware(email.utils.parsedate_to_datetime(stamp))
    except (TypeError, ValueError):
        # An unparseable date is not a reason to fall over: the caller decides
        # what a missing timestamp means.
        return body, None


def networks(body: str) -> list[ipaddress.IPv4Network]:
    """Every line that parses as a network. A bare address counts as its /32."""
    found = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network):
            found.append(net)
    return found


def targets(names: list[str]) -> tuple[list[tuple[str, ipaddress.IPv4Address]], list[str]]:
    """Addresses to check, and the names that gave none.

    A literal address is taken as itself -- that is the "before paying for it"
    case. The lists are IPv4 only, so AAAA answers are dropped here rather
    than pretended about.
    """
    checked: list[tuple[str, ipaddress.IPv4Address]] = []
    lost: list[str] = []
    for name in names:
        try:
            checked.append((name, ipaddress.IPv4Address(name)))
            continue
        except ValueError:
            pass
        try:
            answers = socket.getaddrinfo(name, None, socket.AF_INET)
        except OSError:
            lost.append(name)
            continue
        for address in sorted({answer[4][0] for answer in answers}):
            checked.append((name, ipaddress.IPv4Address(address)))
    return checked, lost


def classify(answer: object) -> str | None:
    """What one node said: None if it connected, otherwise why not.

    Read by what is present rather than by what is missing: a connection
    carries a timing, a refusal carries an error. Anything else is the service
    saying something we do not understand, and an unfamiliar shape must not be
    read as an outage -- guessing "refused" here would raise an alarm about
    the site every time check-host changed its wording.
    """
    if answer is None:
        return PENDING
    first = answer[0] if isinstance(answer, list) and answer else answer
    if not isinstance(first, dict):
        return PENDING
    if "error" in first:
        return str(first["error"] or "refused")
    if "time" in first:
        return None
    return PENDING


def split_listed(
    address: ipaddress.IPv4Address,
    covering: list[ipaddress.IPv4Network],
    listed: list[ipaddress.IPv4Network],
) -> tuple[list[str], list[str]]:
    """Registry entries inside our block, split into ours and the neighbours'.

    Membership, not string equality: the exact list is addresses today but may
    carry a range tomorrow, and `144.91.67.224/27` holding our address would
    otherwise read as somebody else's problem -- the opposite of the truth,
    and the only question this second download exists to answer.
    """
    ours, neighbours = [], []
    for entry in listed:
        if not any(entry.subnet_of(block) for block in covering):
            continue
        if address in entry:
            ours.append(str(entry))
        else:
            neighbours.append(str(entry))
    return sorted(set(ours)), sorted(set(neighbours))


def _ask(url: str) -> dict:
    try:
        body, _ = fetch(url, accept="application/json")
        answer = json.loads(body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as failure:
        raise ProbeUnavailable(str(failure)) from failure
    if not isinstance(answer, dict):
        raise ProbeUnavailable(f"unexpected answer: {answer!r}")
    return answer


def probe(address: ipaddress.IPv4Address) -> dict[str, str | None]:
    """Open TCP 443 from the Russian nodes. Node -> None if it connected, else why not."""
    query = urllib.parse.urlencode(
        [("host", f"{address}:{PROBE_PORT}")] + [("node", node) for node in PROBE_NODES]
    )
    started = _ask(f"{CHECK_HOST}/check-tcp?{query}")
    request_id = started.get("request_id")
    if not request_id:
        raise ProbeUnavailable(started.get("error") or "no request id in the answer")

    # What the service actually accepted, and only the nodes it places in
    # Russia. Node names do get retired, and falling back to the names we
    # asked for would quietly leave the probe collecting nothing forever.
    accepted = started.get("nodes")
    if not isinstance(accepted, dict) or not accepted:
        raise ProbeUnavailable("check-host accepted none of the nodes")
    asked = [
        node
        for node, where in accepted.items()
        if isinstance(where, list) and where and where[0] == "ru"
    ]
    if not asked:
        raise ProbeUnavailable(f"no Russian node among {sorted(accepted)}")

    collected: dict = {}
    for wait in PROBE_WAITS:
        time.sleep(wait)
        collected = _ask(f"{CHECK_HOST}/check-result/{request_id}")
        if all(collected.get(node) is not None for node in asked):
            break

    return {node: classify(collected.get(node)) for node in asked}


def run_probe(addresses: list[ipaddress.IPv4Address]) -> bool:
    """Print what Russia sees. False only when a node tried and failed for every address.

    True therefore covers "everything connected" and "nothing was found out",
    which are different things -- the printed lines tell them apart, and only
    the first is good news.
    """
    reached = True
    for address in addresses:
        try:
            answers = probe(address)
        except ProbeUnavailable as failure:
            # Deliberately not a failure of ours: see the module docstring.
            print(f"probe   {address}: check-host unavailable -- {failure}")
            continue
        good = [node for node, why in answers.items() if why is None]
        bad = {node: why for node, why in answers.items() if why is not None}
        short = ", ".join(sorted(node.split(".")[0] for node in good)) or "none"
        print(f"probe   {address}: {len(good)}/{len(answers)} nodes connected ({short})")
        for node, why in sorted(bad.items()):
            print(f"        {node.split('.')[0]}: {why}")
        refused = [why for why in bad.values() if why != PENDING]
        if not good and not refused:
            print(f"        (no verdict from any node -- nothing learned about {address})")
        elif not good:
            reached = False
    return reached


def survey(args: argparse.Namespace) -> int:
    checked, lost = targets(args.target)
    found: set[int] = set()
    for name in lost:
        print(f"!! no IPv4 answer for {name}")
        found.add(NO_ADDRESS)
    if not checked:
        print("nothing to check: not one name gave an address")
        return NO_ADDRESS

    unique = sorted({address for _, address in checked})
    if not args.no_probe and not run_probe(unique):
        found.add(UNREACHABLE)

    try:
        body, stamp = fetch(AGGREGATED)
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        print(f"!! the list is unreadable: {AGGREGATED}\n   {failure}")
        return verdict(found | {LISTS_UNUSABLE})

    blocks = networks(body)
    if not blocks:
        print(f"!! the list parsed to nothing: {AGGREGATED}")
        return verdict(found | {LISTS_UNUSABLE})
    if stamp is None:
        print(f"!! the list does not say when it changed: {AGGREGATED}")
        return verdict(found | {LISTS_UNUSABLE})

    hours = (dt.datetime.now(dt.UTC) - stamp).total_seconds() / 3600
    if hours > args.max_age_hours:
        print(
            f"!! the list is stale: {AGGREGATED}\n"
            f"   last changed {stamp:%Y-%m-%d %H:%M} UTC, {hours:.0f} h ago"
        )
        return verdict(found | {LISTS_UNUSABLE})

    print(f"list: {len(blocks)} blocks, changed {stamp:%Y-%m-%d %H:%M} UTC ({hours:.0f} h ago)")

    caught = []
    for name, address in checked:
        covering = [block for block in blocks if address in block]
        if covering:
            caught.append((address, covering))
            print(f"CAUGHT  {address} ({name}) by {', '.join(str(b) for b in covering)}")
        else:
            print(f"clean   {address} ({name})")

    if not caught:
        return verdict(found)
    found.add(CAUGHT)

    # Only now is the exact list worth its 700 KB: it says whether the address
    # in the registry is ours or a neighbour's, and those need opposite answers.
    try:
        exact_body, _ = fetch(EXACT)
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        print(f"   (could not name the listed addresses: {failure})")
        return verdict(found)

    listed = networks(exact_body)
    for address, covering in caught:
        ours, neighbours = split_listed(address, covering, listed)
        if ours:
            print(f"   {address}: listed itself -- {', '.join(ours)}")
        if neighbours:
            print(f"   {address}: listed neighbours -- {', '.join(neighbours)}")
        if not ours and not neighbours:
            print(f"   {address}: the block is listed whole, no single address named")
    return verdict(found)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "target",
        nargs="*",
        default=list(DOMAINS),
        help="names or addresses to check (default: the site's own names)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=int,
        default=MAX_AGE_HOURS,
        help=f"older than this and the list is not trusted (default: {MAX_AGE_HOURS})",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="skip check-host and read the lists only",
    )
    args = parser.parse_args()
    try:
        return survey(args)
    except Exception:
        # Never let a traceback pick the exit code: 1 is what Python returns
        # on its own, and the caller would read it as a verdict.
        traceback.print_exc()
        print("!! the check fell over -- the verdict above, if any, means nothing")
        return BROKEN


if __name__ == "__main__":
    sys.exit(main())
