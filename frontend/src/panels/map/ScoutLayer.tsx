// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scout on the map (D-321), drawn: the field, the line to the cursor,
 * the cross where the finger landed, and the offer beside them.
 *
 * What the scout **is** is `map/useScout`; this is what it looks like. Two
 * components rather than one because the two halves hang in two different
 * parents -- the marks inside the map's own `svg`, the offer in the pane
 * beside it -- and a component cannot be in two places at once.
 *
 * The offer takes the hand -- `act`, `busy`, the refusal -- from above
 * rather than calling `useActions` for itself: that hook keeps a `busy` and
 * a refusal of its own per caller, and a second copy here would quietly let
 * the survey be pressed while a walk was still running. The socket is not
 * like that: it is one object in a context, so it is read here.
 */

import { useEffect, useRef } from "react";

import { useSession } from "../../actions";
import type { Scouting } from "../../api";
import { placeAt, project, type Eye } from "./globe";
import type { Point } from "./model";
import { Aim, ScoutField } from "./Nodes";
import { Survey } from "./Survey";
import type { Scout } from "./useScout";
import { dotOn } from "./useWalker";

//: The ring on the point being opened, map units -- the aim's own size.
const RUN_AIM_R = 5;

/** The marks on the glass: inside the map's `svg`, above the ground and
 *  below the nodes, so a node is never covered by the field it stands in. */
export function ScoutLayer({
  scout,
  here,
  globe,
  eye,
  radius,
}: {
  scout: Scout;
  /** Where the body stands: the mask's id is made of it, so two scenes never
   *  share one and a stale mask never clips the new field. */
  here: string;
  /** Whether the scene is a globe at all. Asked apart from the eye, because
   *  the eye keeps its last bearing when the map leaves the surface, and a
   *  cross drawn by it would land on the sky for the frame before the aim is
   *  put away. */
  globe: boolean;
  eye: Eye | null;
  radius: number | null;
}) {
  //: The aim outlives the mode: a point already named stays on the glass
  //: while the panel beside it asks what to do about it.
  const cross =
    globe && eye && radius && scout.aim ? (
      <Aim at={project(eye, radius, scout.aim)} />
    ) : null;
  if (!scout.field) return cross;
  return (
    <>
      <ScoutField
        field={scout.field}
        id={`scout-${here.replace(/[^A-Za-z0-9_-]/g, "")}`}
      />
      <line ref={scout.line} className="scout-line" visibility="hidden" />
      {cross}
    </>
  );
}

/** The offer beside the map: how far the point is, and the run. */
export function ScoutPanel({
  scout,
  busy,
  trouble,
  act,
}: {
  scout: Scout;
  busy: boolean;
  trouble: string | null;
  act: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const session = useSession();
  const at = scout.aim;
  if (!at || scout.metres === null) return null;
  return (
    <Survey
      metres={scout.metres}
      busy={busy}
      trouble={trouble}
      onGo={() => {
        //: The aim is cleared only when the run is on: a refusal keeps the
        //: point and its words on the panel.
        let sent = false;
        void act(async () => {
          await session.send("explore.survey", { lat: at.lat, lon: at.lon });
          sent = true;
        }).then(() => {
          if (sent) scout.clear();
        });
      }}
      onClear={scout.unaim}
    />
  );
}

/**
 * A run under way, drawn (D-327): the way out to the find and the scout on it.
 *
 * Until 2026-09-08 a run was a line in the sidebar and nothing on the map:
 * the player pressed "разведать", the mark stayed on the node they had left,
 * and for the next hour there was no telling a scout from somebody standing
 * still. The owner asked for the walk to be visible -- "надо показывать
 * игроку прогресс, как он двигается по ребру к будущему узлу".
 *
 * The way is drawn as a straight line rather than as a great circle. A run
 * reaches `biome.reach_m`'s far end at the most -- a hundred metres in the
 * mountains, twenty on the plain -- and over that the arc and the chord part
 * by far less than the stroke's own width on a globe a hundred kilometres
 * across; the aiming line beside it is straight for the same reason.
 *
 * The dot moves by frames and not by renders, exactly like the walker
 * (`useWalker`): a timer redrawing it would be a client's clock over the
 * server's data (D-226), and a jerk every half second besides.
 */
export function ScoutRun({
  run,
  from,
  eye,
  radius,
}: {
  run: Scouting;
  /** Where the node the run set out from is drawn, as of this render. */
  from: Point | undefined;
  eye: Eye | null;
  radius: number | null;
}) {
  const dotRef = useRef<SVGGElement | null>(null);
  const to = eye && radius ? project(eye, radius, run.place) : null;
  //: Read at frame time: the globe turns between renders while the camera
  //: follows, and a closure over this render's ends would drift off the way.
  const ends = useRef<{ from?: Point; to?: Point }>({});
  ends.current = { from, to: to && to.front ? to : undefined };
  useEffect(() => {
    let raf = 0;
    const step = () => {
      const mark = dotRef.current;
      const dot = dotOn(run, ends.current.from, ends.current.to, Date.now());
      if (mark && dot) mark.setAttribute("transform", placeAt(dot));
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    //: The stamps, not the object: `look` arrives new twice a second, and the
    //: loop would be torn down and rebuilt with it -- the very stutter the
    //: frames are here to avoid.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run.started_at, run.arrives_at]);
  if (!from || !to || !to.front) return null;
  const dot = dotOn(run, from, to, Date.now());
  return (
    <g className="scout-run">
      <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="run-way" />
      {/* Where the find will stand: the same ring the aim is drawn with, so
          that "this is the point I named" needs no explaining. */}
      <g transform={placeAt(to)} className="aim">
        <circle cx={0} cy={0} r={RUN_AIM_R} />
      </g>
      {dot && (
        <g ref={dotRef} transform={placeAt(dot)}>
          <circle cx={0} cy={0} r={5} className="walker" />
        </g>
      )}
    </g>
  );
}
