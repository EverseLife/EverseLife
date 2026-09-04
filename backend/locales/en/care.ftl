# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# The words of care (D-293): agronomy is a text assembled from the crop's
# data. Read in the Library, remembered into the "knowledge" tab, retold.
# The numbers come from the vault -- the moisture band, the feeding table,
# the hardiness -- so the text cannot lie after a retune.
#
# The crop's and the fertilizer's names stand in the nominative (D-258): the
# substitution is the subject or follows a colon, the preposition governs
# only the stage.

care-band = { CULTURE($culture) }: moisture { $min }–{ $max }, thirst { $need ->
        [1] low — seldom watered
        [3] high — watered often
       *[other] middling
    }.
care-feeding = Feeding: { $rows }. Anything else burns it; a second feeding in one stage runs to leaf instead of fruit.
care-feeding-row = “{ NAME($goods) }” { $stage ->
        [sprout] at sprouting
        [leaf] in leaf
        [bloom] in bloom
       *[fill] at filling
    }
care-feeding-none = It takes no feeding: any fertilizer burns it.
care-hardiness = Hardiness: { $hardiness } of 5.
care-crowd = Fear of crowding: { $risk } of 5. Thinning lifts the crowding but takes its own share of the harvest — and only { $until ->
        [sprout] at sprouting
        [leaf] at sprouting or in leaf
        [bloom] at sprouting, in leaf or in bloom
       *[fill] at sprouting, in leaf, in bloom or at filling
    }; what is pulled is not put back.
care-weeds = Weeds are pulled when seen: they drink its water and hold its growth back.
