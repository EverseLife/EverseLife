# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
#
# What the world announces aloud: the chronicle lines in Discord (`herald/chronicle.py`).
#
# The chronicle goes to one channel with one audience, so -- unlike a refusal -- it is
# said not in the reader's language but in the channel's: `compose` renders it in the
# world's default language. The words still live here rather than in f-strings: a
# channel in another language is a `locale=` in one place, not a file rewritten from
# scratch.
#
# Emoji and Discord markup (`**bold**`) are part of the string: they shape the
# sentence, and where the emphasis falls inside it is the language's decision.
#
# A value is one line; the variants of a select are each on their own line.
# A variant key in Fluent is an identifier, not a string and not a boolean, so
# "is there a name", "did it pass" and the rest arrive as a `"true"`/`"false"` flag.

# What stands in place of a name when there is no name: the event line outlived the
# row it referred to. Words, not stubs -- and that is why they are here.
chronicle-someone = someone unknown
chronicle-somewhere = somewhere

chronicle-city-founded = 🏛 **The city of { $city } is founded** — { $where }. Founded by: { $who }.

chronicle-city-law-set = 📐 { $city }: code-law “{ $named ->
        [true] { LAW($law) }
       *[false] law
    }” — was { CHOICE($was) }, now { CHOICE($now) }.

chronicle-city-charter-set = 📜 { $city }: charter — “{ $named ->
        [true] { $question }
       *[false] charter question
    }” is now “{ $choice }”.

# The vote kind arrives as an enum value (`VoteKind`) and becomes a word here.
# `unknown` is not a missing variant but one of its own: the line is worth saying
# even when the vote row itself is gone -- the count is in the event.
chronicle-vote-closed = 🗳 { $city }: { $kind ->
        [law] vote on a law
        [election] election of a ruler
        [recall] recall of a ruler
        [charter] charter amendment
        [council] election to the council
       *[unknown] vote
    } — { $passed ->
        [true] passed
       *[false] did not pass
    } (for { $yes }, against { $no }, electorate { $electorate }).

chronicle-council-seated = 🪑 { $city }: the council seat goes to { $who }.

chronicle-case-judged = ⚖️ Court of { $city }. Judge: { $judge }.{ $sentenced ->
        [true] { " " }Verdict: “{ $verdict }”.
       *[false] {""}
    }{ $sanctioned ->
        [true] { " " }Sanction: { $sanction }.
       *[false] { " " }No sanction.
    }

# The rate is announced only when it moved -- decided by `_rate_decided`, not by the
# message. The explanation arrives already assembled from keys of its own: it is a
# list of reasons, and how to join them is the language's business (`i18n.join`).
chronicle-rate-decided = 🏦 Key rate{ $by_council ->
        [true] { " " }(council decision, { $city })
       *[false] {""}
    }: **{ $rate }**{ $known ->
        [true] { " " }(was { $was })
       *[false] {""}
    }.{ $explained ->
        [true] { " " }{ $why }
       *[false] {""}
    }

chronicle-explore-found = 🧭 Scouting{ $from_known ->
        [true] { " " }from node { $from_node }
       *[false] {""}
    }: the map has grown — { $what }. Scout: { $who }.
