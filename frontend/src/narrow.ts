// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Is the screen a phone's (03-screens, brief section 9).
 *
 * The vault calls the phone an obligatory platform rather than a later one: the
 * game is asynchronous, and a significant share of sessions is somebody checking
 * a convoy or casting a vote on the way somewhere. But the four permanent zones
 * cannot be four columns on 375px, so there they become four sections with a
 * navigation bar at the bottom -- **the same zones, one at a time**.
 *
 * The threshold matches the one the stylesheet already unravels the frame at:
 * two different ideas of "narrow" would put the bar and the layout out of step.
 */

import { useEffect, useState } from "react";

/** The same 56rem the frame unravels at in `index.css`. */
const PHONE = "(max-width: 56rem)";

export function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(PHONE).matches);
  useEffect(() => {
    const watch = window.matchMedia(PHONE);
    const answer = () => setNarrow(watch.matches);
    watch.addEventListener("change", answer);
    //: Rotating the phone counts as a change of screen, and so does a resize on
    //: a desktop: the state must not survive the layout it was chosen for.
    answer();
    return () => watch.removeEventListener("change", answer);
  }, []);
  return narrow;
}
