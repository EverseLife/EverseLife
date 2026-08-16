/**
 * The deadline bar (D-055, brief signature 4).
 *
 * The game is asynchronous, and almost everything in it has a term: a
 * production batch, a convoy on the road, construction, a crop ripening, a vote
 * under way, a contract, gear wearing out, the window to answer a court case.
 * Until now each of those spoke its own words -- "через 4 мин", "вот-вот",
 * "забрать через 2 ч" -- and eight systems saying the same thing eight ways add
 * up to no visual language at all.
 *
 * One element covers all of them: a 2px line, filled from the left, emptying as
 * the term runs out. Colour follows the remainder -- neutral, then a warning
 * under a fifth left, then alarm at expiry. Eight systems speaking one word is
 * what makes the game recognisable on a screenshot, and none of it is decoration.
 *
 * **This is the only thing in the interface allowed to move continuously**
 * (brief section 6). Everything else is instant, because everything else is on
 * somebody's critical path.
 */

import { useEffect, useState } from "react";
import { spell } from "./api";

type Props = {
  /** When the term ends, ISO from the server. */
  until: string;
  /** When it started. Without it there is no share to fill -- only a countdown. */
  since?: string | null;
  /** What the term is about: read out to those who cannot see the bar. */
  label?: string;
  /** A short size for a table row; the default suits a card. */
  size?: "row" | "card";
};

/** Under this share of the term left, the bar warns; at zero it alarms. */
const WARN_BELOW = 0.2;

/**
 * How often the bar is redrawn.
 *
 * A second is enough: the bar is two pixels tall, and nobody can see a finer
 * step. Under `prefers-reduced-motion` the brief asks for jumps once a second
 * -- which is what this already is, so the setting only drops the CSS
 * transition that smooths between the jumps.
 */
const BEAT = 1000;

export function Deadline({ until, since, label, size = "card" }: Props) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), BEAT);
    return () => clearInterval(timer);
  }, []);

  const ends = new Date(until).getTime();
  const starts = since ? new Date(since).getTime() : null;
  const left = Math.max(0, (ends - now) / 1000);

  //: Without a beginning the share is unknowable, and a bar drawn from a guess
  //: would lie about how far along the work is. Then we show the count alone.
  const share =
    starts !== null && ends > starts
      ? Math.min(1, Math.max(0, (ends - now) / (ends - starts)))
      : null;

  const tone = left <= 0 ? "over" : share !== null && share < WARN_BELOW ? "near" : "";
  const remains = left <= 0 ? "вот-вот" : spell(left);

  return (
    <span className={`deadline ${size} ${tone}`}>
      {share !== null && (
        <span
          className="deadline-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(share * 100)}
          aria-label={label ? `${label}: осталось ${remains}` : `осталось ${remains}`}
        >
          <i style={{ width: `${(share * 100).toFixed(2)}%` }} />
        </span>
      )}
      <span className="deadline-left">{remains}</span>
    </span>
  );
}

/**
 * A line of business: what it is, how much is left, and the bar (component 2
 * of the brief's list).
 *
 * The whole of "дела" is built from these, and so is anything elsewhere that
 * reports a running work -- a machine in a location, a convoy on the map.
 */
export function Doing({
  what,
  until,
  since,
  aside,
  children,
}: {
  what: string;
  until: string;
  since?: string | null;
  /** A remark to the right of the title: quality, destination, whose it is. */
  aside?: React.ReactNode;
  /** An action the line offers, if it offers one. */
  children?: React.ReactNode;
}) {
  return (
    <div className="doing">
      <span className="doing-what">{what}</span>
      {aside && <span className="doing-aside">{aside}</span>}
      <Deadline until={until} since={since} label={what} size="row" />
      {children && <span className="doing-act">{children}</span>}
    </div>
  );
}
