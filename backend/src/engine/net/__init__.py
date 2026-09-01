# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""The Net: correspondence and channels over any distance (D-044, D-069, D-222).

The room (`engine/chat.py`) is a conversation: those nearby hear, nothing is
kept. The Net is **correspondence**: a letter is kept, read later, and it takes
the road to arrive. Two things are decided here and nowhere else.

## The delay

    delay = seconds of the road between the two * comm.delay_per_second

The road is the fastest path on foot between the writer's body and the
reader's -- the same edges, the same seconds as walking them (`travel.route`).
Between planets the road is the passage: `ship.base_hours` for this hour's sky,
so a letter to Pyroxis takes longer when Pyroxis stands across the star. No
road at all on one planet -- islands, a hull in flight -- counts as the sea:
a climb to orbit and a descent back, `ship.ascent_hours` plus
`ship.descent_hours`, because that is what carrying anything across such a gap
actually takes (D-245). A body is where the distance is measured from, so an identity
without one **reads but does not write**; a letter *to* somebody without a
body arrives at once -- the Net holds them everywhere, and there is nowhere to
measure to.

A letter's delay is measured once, on sending, and written into it: the
reader sees the letter when `delivered_at` has come. A post's delay is
measured on **reading**, from the node the author stood in to the reader's
node now: one post, many readers, each on their own road.

## Why it is cheap

The path is Dijkstra over the whole graph, and the graph is read from the
database. Neither is done per letter:

* the edge table is read into memory once and trusted for `runtime.NET_GRAPH_TTL`;
  a laid road shows in the delays within that, which is all a delay needs;
* the distance map **from one source node** is computed once and kept for
  `runtime.NET_REACH_CACHE` sources: the writer's next letter from the same
  place, and every reader of one post, look the answer up.

Between planets nothing is walked at all: orbits are arithmetic.
"""

from src.engine.net._base import (  # noqa: F401
    NetError,
    NoBody,
    NotAllowed,
)
from src.engine.net.channels import (  # noqa: F401
    ChannelView,
    Post,
    channels,
    city_channel,
    create_channel,
    find_channels,
    may_post,
    post,
    read_channel,
    subscribe,
    unread_posts,
    unsubscribe,
)
from src.engine.net.letters import (  # noqa: F401
    Letter,
    ThreadView,
    delivered,
    find_people,
    open_thread,
    read_thread,
    threads,
    unread_letters,
    write,
)
from src.engine.net.road import (  # noqa: F401
    Adjacency,
    Graph,
    delay_between,
    forget_graph,
    road_seconds,
)
