// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * How the face announces its own bad ending (D-143, D-226).
 *
 * A collapse is the one thing that happens at a working which the window
 * cannot read afterwards. `look` sends the **open** session and nothing else,
 * so the moment the roof comes down `look.mining` is empty and the panel is
 * back at its "start a session" button -- with nothing said. The reply to
 * `mine.swing` carries the sight that says `collapsed`, but a reply is a
 * confirmation and not state (CLAUDE.md), and `act` throws it away by design:
 * every action ends by rereading the world.
 *
 * So the announcement comes the way every other piece of news does -- as the
 * event the engine already writes and the pump already delivers. Nothing new
 * goes on the wire for it (D-225): `mining.collapsed` reaches the miner with
 * what the roof buried in it, and this is where that is read.
 *
 * `wounded` rides in the same payload and is deliberately left there. A wound
 * is a row nobody reads: the penalty is E3 work (D-096), there is no healing
 * and no other place in the window it shows. Saying it here would promise a
 * state the game does not have yet, and 08-session-protocol licenses the
 * losses, not the wound.
 *
 * The same kind reaches everybody standing in the node, because a cave-in is
 * the room's business too, and the window must not mourn a stranger's haul.
 * What tells the copies apart is the one thing the protocol writes down --
 * "the losses to the miner alone; to the neighbours the fact"
 * (90-production/08-session-protocol) -- so the numbers are the veto. The
 * pump also names a bystander's copy and leaves one's own unnamed, but that
 * is the pump's own rule about `who` and not this one: reading it here would
 * be a second, undocumented dependency, and the notice would go silent the
 * day a name is added for some unrelated reason.
 *
 * A killed miner is not a case here: the body is gone, `look.body` is null,
 * and the whole in-person screen is replaced before this could be shown.
 */

import type { Happening } from "./session";

/**
 * What the roof took, as the miner's own copy of the event tells it.
 *
 * An object around one number, because the number may be nought -- a roof
 * that came down on the first swing buried an empty container -- and a bare
 * `0` returned here would read as "no cave-in" at every call site.
 */
export type CaveIn = {
  /** Everything mined this session, buried with it (D-143). */
  lost: number;
};

/** One's own cave-in out of what the server said, or nothing. */
export function caveIn(happening: Happening): CaveIn | null {
  if (happening.event !== "mining.collapsed") return null;
  //: Somebody else's roof: the room is told that one fell, and no more.
  if (typeof happening.lost !== "number") return null;
  return { lost: happening.lost };
}

/**
 * A swing that went to the spoil heap instead of the hands (D-301).
 *
 * A cave-in leaves rubble on the working, and it is dug out for no ore. The
 * window has no other way to know: `look.mining` carries the shift, not the
 * ground over it, and the sign speaks of the roof -- which, under a rubble,
 * reads as "it is cracking" and would be taken for a warning. So the swing
 * says what it was, and `cleared` marks the one that finished the job.
 */
export type Rubble = {
  /** The rubble is out and the working starts over. */
  cleared: boolean;
};

/** A swing at the rubble out of what the server said, or nothing. */
export function rubbleOut(happening: Happening): Rubble | null {
  if (happening.event !== "mining.rubble") return null;
  return { cleared: happening.cleared === true };
}
