# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Tell IndexNow that the site's pages are worth recrawling.

    docker compose exec landing python indexnow.py

Bing and Yandex take the notice and come within hours instead of waiting for
a schedule of their own; Google does not take part in the protocol at all and
still needs Search Console. One notice covers every page in `app.PAGES`: the
site is four pages, and a partial list would only invite a second run.

Run it when a page has actually changed. A deploy that changed nothing is not
news, and the protocol asks not to be told the same thing repeatedly.

Exit code is 0 when the notice was accepted, 1 otherwise, so a deploy step can
fail loudly rather than silently doing nothing.
"""

import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from app import INDEXNOW_KEY, INDEXNOW_KEY_RE, PAGES, SITE

ENDPOINT = "https://api.indexnow.org/indexnow"
TIMEOUT = 30.0

#: 200 -- taken; 202 -- taken, the key is still being checked. Both mean the
#: notice is in, and 202 is the usual answer for the first ever run.
ACCEPTED = (200, 202)


def main() -> int:
    if not INDEXNOW_KEY:
        print("LANDING_INDEXNOW_KEY is not set: nothing to prove ownership with.", file=sys.stderr)
        return 1
    if not INDEXNOW_KEY_RE.match(INDEXNOW_KEY):
        print("LANDING_INDEXNOW_KEY is malformed: 8..128 of [A-Za-z0-9-].", file=sys.stderr)
        return 1

    host = urlsplit(SITE).netloc
    body = json.dumps(
        {
            "host": host,
            "key": INDEXNOW_KEY,
            "keyLocation": f"{SITE}/{INDEXNOW_KEY}.txt",
            "urlList": [f"{SITE}{path}" for path in PAGES],
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
            answer.read()
            code = answer.status
    except urllib.error.HTTPError as failed:
        #: The protocol answers in codes, not prose: 403 is a key the
        #: crawler could not read back from the site, 422 a url list that
        #: does not belong to the host, 429 too many notices.
        print(f"IndexNow refused: {failed.code} {failed.reason}", file=sys.stderr)
        return 1
    except OSError as failed:
        print(f"IndexNow unreachable: {failed}", file=sys.stderr)
        return 1

    if code not in ACCEPTED:
        print(f"IndexNow answered {code}", file=sys.stderr)
        return 1
    print(f"IndexNow {code}: {len(PAGES)} pages of {host} submitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
