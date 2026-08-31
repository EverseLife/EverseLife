# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the monitor must not get wrong.

Every case here is a way the check could lie rather than break: call an
outage clean, call a quiet third party an outage, or blame a neighbour for a
listing that is ours. All of them were live defects once, found in review
before the first run, and all of them live in functions that need no network
-- which is the whole reason they were pulled out of the request paths.
"""

from __future__ import annotations

import datetime as dt
import ipaddress

import subnet_watch as sw


def net(text: str) -> ipaddress.IPv4Network:
    return ipaddress.ip_network(text)


class TestNetworks:
    def test_a_bare_address_is_its_own_block(self):
        assert sw.networks("144.91.67.143") == [net("144.91.67.143/32")]

    def test_junk_and_comments_are_skipped_not_fatal(self):
        body = "# comment\n\n144.91.67.0/24\nnot-an-address\n2a02:c207::1\n13.140.145.0/24\n"
        assert sw.networks(body) == [net("144.91.67.0/24"), net("13.140.145.0/24")]


class TestAsAware:
    def test_zone_unknown_becomes_utc(self):
        # `-0000` is legal and means "zone unknown"; the naive datetime it
        # parses to used to blow up the subtraction against `now`.
        naive = dt.datetime(2026, 8, 31, 1, 45, 19)
        aware = sw.as_aware(naive)
        assert aware.tzinfo is not None
        assert (dt.datetime.now(dt.UTC) - aware).total_seconds() > 0

    def test_a_zone_that_was_given_is_kept(self):
        stamp = dt.datetime(2026, 8, 31, 1, 45, 19, tzinfo=dt.UTC)
        assert sw.as_aware(stamp) is stamp


class TestClassify:
    def test_a_timing_means_it_connected(self):
        assert sw.classify([{"address": "13.140.145.20", "time": 0.044}]) is None

    def test_an_error_means_it_tried_and_failed(self):
        assert sw.classify([{"error": "Connection timed out"}]) == "Connection timed out"

    def test_silence_is_not_evidence(self):
        assert sw.classify(None) == sw.PENDING

    def test_an_unfamiliar_shape_is_not_an_outage(self):
        # Guessing "refused" here would alarm the channel about the site every
        # time check-host changed its wording.
        for odd in ([], [None], "surprise", [{"state": "queued"}], {"nodes": 1}):
            assert sw.classify(odd) == sw.PENDING


class TestSplitListed:
    block = [net("144.91.67.0/24")]

    def test_a_neighbour_is_a_neighbour(self):
        ours, neighbours = sw.split_listed(
            ipaddress.IPv4Address("144.91.67.239"), self.block, [net("144.91.67.143/32")]
        )
        assert (ours, neighbours) == ([], ["144.91.67.143/32"])

    def test_a_range_holding_us_is_ours(self):
        # The exact list is single addresses today. A range tomorrow used to
        # read as somebody else's problem -- the opposite of the truth, and
        # the only question the second download exists to answer.
        ours, neighbours = sw.split_listed(
            ipaddress.IPv4Address("144.91.67.239"), self.block, [net("144.91.67.224/27")]
        )
        assert (ours, neighbours) == (["144.91.67.224/27"], [])

    def test_entries_outside_our_block_are_ignored(self):
        ours, neighbours = sw.split_listed(
            ipaddress.IPv4Address("144.91.67.239"), self.block, [net("13.140.159.4/32")]
        )
        assert (ours, neighbours) == ([], [])


class TestVerdict:
    def test_nothing_found_is_success(self):
        assert sw.verdict(set()) == sw.OK

    def test_the_lists_outrank_everything(self):
        found = {sw.CAUGHT, sw.UNREACHABLE, sw.NO_ADDRESS, sw.LISTS_UNUSABLE}
        assert sw.verdict(found) == sw.CAUGHT

    def test_a_real_outage_outranks_a_blind_monitor(self):
        assert sw.verdict({sw.UNREACHABLE, sw.LISTS_UNUSABLE}) == sw.UNREACHABLE

    def test_no_verdict_collides_with_python_or_argparse(self):
        # 1 is a traceback and 2 is a bad command line. A verdict sharing
        # either would have announced a crash as "the address is blocked".
        assert 1 not in sw.PRECEDENCE and 2 not in sw.PRECEDENCE
        assert sw.BROKEN not in sw.PRECEDENCE


class TestFetch:
    """The one request-path defect worth a fake: a header nobody controls."""

    def _serve(self, monkeypatch, body: str, headers: dict[str, str]):
        class Answer:
            def __init__(self):
                self.headers = headers

            def read(self):
                return body.encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        monkeypatch.setattr(sw.urllib.request, "urlopen", lambda *a, **k: Answer())

    def test_an_unparseable_date_is_not_a_traceback(self, monkeypatch):
        self._serve(monkeypatch, "1.2.3.4\n", {"Last-Modified": "garbage"})
        body, stamp = sw.fetch("https://example.invalid/list")
        assert stamp is None and body.strip() == "1.2.3.4"

    def test_zone_unknown_survives_the_round_trip(self, monkeypatch):
        self._serve(monkeypatch, "1.2.3.4\n", {"Last-Modified": "Mon, 31 Aug 2026 01:45:19 -0000"})
        _, stamp = sw.fetch("https://example.invalid/list")
        assert stamp is not None and stamp.tzinfo is not None

    def test_a_missing_header_is_not_a_traceback(self, monkeypatch):
        self._serve(monkeypatch, "1.2.3.4\n", {})
        _, stamp = sw.fetch("https://example.invalid/list")
        assert stamp is None
