# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# Storage: hands, ground, floor, chest, vessel (D-181, D-230, D-244).
#
# NAME($id) turns the stable key of a thing into a word of this language
# (D-251): `iron_ore` goes over the wire, the reader sees "Iron ore".
#
# Two incompatible habits of Fluent, which is why the file looks like this:
#   -- a line break in the TEXT of a value survives into the refusal, so the
#      text is written on one line, however long it turns out;
#   -- the variants of a select ({ $x -> ... }) must each stand on their own
#      line, and those breaks do not reach the text.

storage-not-in-hands = this thing is not in hand: you put down your own, and out of your hands
storage-nothing-to-put = there is nothing to put down
storage-nothing-to-take = there is nothing to take
storage-nothing-to-pick = there is nothing to pick up
storage-nothing-to-hand = there is nothing to hand over
storage-not-in-storage = this thing is not in the storage
storage-not-on-ground = this thing does not lie here
storage-not-in-hands-to-hand = you do not have this thing in hand

storage-mismatch = “{ NAME($goods) }” does not go into “{ NAME($chest) }”: { $why ->
        [vessel] a vessel takes liquid only
       *[chest] liquid is kept in a vessel
    }
storage-chest-full = “{ NAME($chest) }” has { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg free, and this is { NUMBER($mass, minimumFractionDigits: 1, maximumFractionDigits: 1) } kg
storage-not-a-storage = “{ NAME($chest) }” is not a storage: nothing goes into it
storage-relic = “{ NAME($goods) }” is a relic of the Forerunners: it is neither picked up nor carried away
storage-built-in-place = { NAME($goods) }: built in place, not picked up
storage-station-fuel = “{ NAME($goods) }” at a station is its fuel: what has been poured in is not picked back up

storage-no-building = there is no building here: things go on the ground only
storage-storey-not-yard = this is a storey, not a yard: under it there is a floor, not ground
storage-no-room = { $inside ->
        [true] the building has
       *[false] the ground has
    } { NUMBER($free, minimumFractionDigits: 1, maximumFractionDigits: 1) } m² free, and this needs { NUMBER($needed, minimumFractionDigits: 1, maximumFractionDigits: 1) } m². Build more, put up chests or cart it off

storage-passing-through = “{ $node }” is another's closed location, you are passing through: passing through you neither take nor put down
storage-not-yours = the storage is not yours: you do not reach into another's chest. The owner of the node may open it, and on city land the authority may

storage-dead-puts = a dead body puts nothing down
storage-dead-picks = a dead body picks nothing up
storage-dead-hands = a dead body hands nothing over
storage-dead-moves = a dead body shifts nothing
storage-hands-only = things are put down out of the hands
storage-body-off-node = the body is outside the node
storage-storage-not-here = this storage is not here
storage-person-not-here = this person is not here
storage-dead-receives = nothing is handed to the dead
storage-self-hand = there is nothing to hand to yourself
