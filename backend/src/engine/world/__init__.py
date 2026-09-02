# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Creation of what exists in the world: nodes, identities, bodies, property.

No function here creates matter out of nothing just like that: items appear
only through mining and harvest (invariant I1). `grant_item` is a tool for
development sessions and scripts, and it writes an event with an explicit
ground so that such an arrival is visible in telemetry.

## Where each part of it lives

The file grew past what one file should hold, and it was three subjects all
along -- the ground, the matter on it and the people who come to it -- so it is
three now, and this one is the door:

* `land` -- what the world is made of: a node with its area and its place on
  the map, the orbit it hangs on, the vein under it, and the title to it;
* `things` -- matter and where it lies: the pocket and the yard, what is in
  them, and the folding of stacks that keeps two heaps of one ore one heap;
* `people` -- how a person gets in: identity, body, the door they come out of
  and what they know.

The stack runs one way and only one way: `people` borrows from `land` (the
refusal `LandError`) and from `things` (the printer's lookup), and neither of
those knows a person exists. `land` and `things` do not know each other at
all -- a node is born with its yard from the model, not from a lookup. So the
only edge out of this package to `engine.city` is `people`'s, and it is the
edge that already existed.

The door publishes the whole public surface the old module had: everything
outside still writes `world.create_node`, `world.node_container`,
`from src.engine.world import body_container`, and none of it had to change.
"""

from src.engine.world.land import (  # noqa: F401
    DEFERRED,
    NO_WATER,
    ORBIT,
    ORBIT_PERIOD,
    ORBIT_PHASE,
    ORBIT_RADIUS,
    PUBLIC_SIGNS,
    RIVER,
    WATER,
    LandError,
    create_node,
    create_vein,
    epoch,
    grant_node,
    hand_over,
    has_place,
    orbit_of,
    public_signs,
)
from src.engine.world.people import (  # noqa: F401
    BIOPRINTER,
    create_identity,
    door,
    doors,
    is_door,
    learn,
    population,
    print_body,
    printer_nodes,
    spawn,
    spawn_point,
)
from src.engine.world.things import (  # noqa: F401
    LIBRARY,
    SAMENESS,
    body_container,
    contents,
    grant_item,
    has_station,
    is_library,
    move_stack,
    node_container,
    node_things,
    node_yard,
    nodes_with_station,
    stack_up,
    station_names,
    thing_kinds,
)
