// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Who else is standing here (D-043, D-222).
 *
 * A room where the only sign of another player is a line of talk is a room one
 * cannot tell apart from an empty one -- and the whole point of the location is
 * that bodies meet in it. So the talk's head names them: the label, then as
 * many names as the line holds, then how many did not fit.
 *
 * **Nothing is polled.** The room says who came and who left -- `travel.*` and
 * `body.*` are visible to everybody in the node (`api/push/_base.py`) -- and
 * the list is reread on those and on a change of room, never on a clock
 * (D-226).
 *
 * ## Why the names are measured rather than counted
 *
 * "As many as fit" is a fact about the width of the line, not about a number
 * chosen here: names run from three letters to twenty, the head shares its
 * line with the fold, and the phone's line is a third of the desktop's. A
 * constant would cut two short names off a wide screen and overflow one long
 * one on a narrow. So the whole list is laid out once in a hidden copy, and
 * the visible one is cut where the copy runs past the frame. The copy is what
 * keeps the measurement still: measuring the visible list would change it,
 * and a measurement that changes what it measures oscillates.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { useEdition, useLocale, useSession } from "../actions";
import { t } from "../locale";
import { PersonName } from "../Name";
import { whoIsHere } from "../people";

/**
 * How long a burst of comings and goings is let settle before the room is
 * asked again, ms.
 *
 * Not a poll and not a clock the panel reads state off (D-226): the ask is
 * still the room's event, this only refuses to make one request per event.
 * A step by anybody is an event to everybody standing there, so N bodies
 * milling about is N requests per step without it -- the same reason `App`
 * batches its own rereads, and the same order of delay.
 */
const SETTLE_MS = 300;

export function Here({ place }: { place: string }) {
  const session = useSession();
  const [people, setPeople] = useState<string[]>([]);
  //: Somebody arrived, left, was printed or died. All four are the room's
  //: business and reach every body standing in it.
  const edition = useEdition("travel.", "body.");

  //: A new room is a new set of people, said before it is asked: between the
  //: floors of a house (D-247) the node changes with no road in between, and
  //: the head would otherwise name the floor below for as long as the answer
  //: takes. Its own effect, so that an arrival in this room does not blank the
  //: list it is about to update.
  useEffect(() => setPeople([]), [place]);

  useEffect(() => {
    let current = true;
    //: The wait is what coalesces a burst: another event bumps `edition`, this
    //: effect is torn down before it fires, and the whole flurry costs one ask.
    //: Nothing is waited for on the first look -- the room is named at once.
    const timer = setTimeout(
      () => {
        void whoIsHere(session)
          .then((there) => {
            if (!current) return;
            const names = there.map((one) => one.name);
            //: The same list is the same list: a fresh array for a walk that
            //: changed nobody would remeasure the line for nothing.
            setPeople((known) => (known.join("|") === names.join("|") ? known : names));
          })
          .catch(() => {
            //: On the road there is no room to ask about, and the strip is
            //: hidden anyway: a refusal here is an answer, not a fault.
            if (current) setPeople([]);
          });
      },
      edition === 0 ? 0 : SETTLE_MS,
    );
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [session, place, edition]);

  const frame = useRef<HTMLSpanElement>(null);
  const copy = useRef<HTMLSpanElement>(null);
  const [fit, setFit] = useState(0);
  //: The label and the tail are words, and words change length with the
  //: language: a switch to English redraws them inside a frame of the same
  //: width, so no resize is observed and the cut would keep yesterday's number.
  const { locale } = useLocale();

  useLayoutEffect(() => {
    const box = frame.current;
    const hidden = copy.current;
    if (!box || !hidden) return;
    const measure = () => {
      const room = box.clientWidth;
      const names = [...hidden.querySelectorAll<HTMLElement>("[data-name]")];
      //: The tail is measured with the largest count it could ever show, so
      //: the cut never has to be taken back a digit later.
      const tail = hidden.querySelector<HTMLElement>("[data-tail]")?.offsetWidth ?? 0;
      let held = 0;
      for (let at = 0; at < names.length; at++) {
        const last = at === names.length - 1;
        //: `offsetLeft` inside the copy, which is positioned: the sum of every
        //: name up to this one, separators included.
        const needs = names[at].offsetLeft + names[at].offsetWidth + (last ? 0 : tail);
        if (needs > room) break;
        held = at + 1;
      }
      setFit(held);
    };
    measure();
    //: The line is as wide as the zone, and the zone changes with the window,
    //: the sidebar's fold and the phone's rotation. Layout, not data: this is
    //: not the timer D-226 forbids.
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [people, locale]);

  if (people.length === 0) return null;
  const rest = people.length - fit;
  return (
    <span className="chat-here" ref={frame}>
      {/* Not one name fits -- a narrow phone, a long circle name beside the
          label, a long name: then the count stands in place of the list. A
          tail that says "and N more" with nothing before it is not a
          sentence. */}
      {fit === 0 ? (
        <span className="note">{t("ui-chat-here-only", { rest: String(rest) })}</span>
      ) : (
        <>
          <span className="note">{t("ui-chat-here")}</span>{" "}
          {people.slice(0, fit).map((name, at) => (
            <span key={name}>
              {at > 0 ? ", " : ""}
              <PersonName name={name} />
            </span>
          ))}
          {rest > 0 && (
            <span className="note"> {t("ui-chat-here-more", { rest: String(rest) })}</span>
          )}
        </>
      )}
      {/* The measuring copy: the same strings, laid out on one line and never
          drawn. `visibility`, not `display`, because a thing not displayed has
          no width to read. The label stands in it too -- every name's offset
          is counted from the frame's edge, and the label is what it starts
          after. */}
      <span className="chat-here-copy" aria-hidden="true" ref={copy}>
        <span className="note">{t("ui-chat-here")}</span>{" "}
        {/* The same nodes as the visible list, class and separator placed the
            same way: a future rule on `.person` would change a name's width,
            and a copy shaped differently would cut the line at the wrong
            name. */}
        {people.map((name, at) => (
          <span data-name="" key={name}>
            {at > 0 ? ", " : ""}
            <span className="person">{name}</span>
          </span>
        ))}
        {/* The space is the one the visible tail carries. */}
        <span data-tail=""> {t("ui-chat-here-more", { rest: String(people.length) })}</span>
      </span>
    </span>
  );
}
