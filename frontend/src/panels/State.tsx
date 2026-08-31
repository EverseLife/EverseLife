// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The state tab: the figures one governs by (D-124, D-140, D-154).
 *
 * Two sections stand under it -- the economy and the people -- and they used to
 * ask the world for themselves, each sending `city.survey`, `city.panel` and
 * `world.metrics` on mount. That was six round trips for three answers, two
 * "Пересчитать" buttons that recomputed different halves of one page, and, for
 * as long as the answers were in flight, the same sentence printed twice:
 *
 *     Вы вне города: за стенами законов нет.
 *     Вы вне города: за стенами законов нет.
 *
 * -- a statement about the world, made before the world had been asked, and
 * wrong wherever a city does exist. So the reading lives here, once, and the
 * sections below are given what was read. Three states, not two: not asked yet,
 * asked and there is no city, asked and here it is.
 *
 * Reading is remote (D-140); **changing** anything is not -- authority is
 * in-person and decides in the administration (D-155).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useSession } from "../actions";
import { Refused } from "../api";
import type { CityPanel, CityView, Look } from "../api";
import { Economy } from "./Economy";
import { t } from "../locale";
import { Population } from "./Population";

/** What both sections read, in one answer set. */
export type StateView = {
  city: CityView;
  panel: CityPanel | null;
  world: Record<string, number>;
};

/** Four states, not three: "не спросили", "спросили и города нет", "спросили и
 *  вот он", and "спросить не вышло". The last was the same lie the old panels
 *  told -- a socket that timed out printed "Вы вне города", a statement about
 *  the world made when the world had not answered at all. The server tells the
 *  two apart (`Refused` is an answer; anything else is a failure), so the
 *  screen can too. */
type Trouble = { failed: true };

export function State({ look, busy }: { look: Look; busy: boolean }) {
  const session = useSession();
  const [view, setView] = useState<StateView | null | undefined | Trouble>(undefined);
  //: Which reading is the current one. Walking out of a city while its answers
  //: are in flight used to be harmless -- one panel showed the wrong city for a
  //: moment -- and is not any more: one reading now feeds the whole tab, so a
  //: late answer would overwrite the right one.
  const asked = useRef(0);

  const reload = useCallback(async () => {
    const mine = ++asked.current;
    try {
      //: There is no "no city" answer to have: `city.survey` refuses where
      //: there is no city (`views._city`), so the refusal below is the answer.
      const summary = await session.send("city.survey");
      //: The panel and the world figures know nothing of each other: asking
      //: them one after the other spent a round trip for the ordering alone.
      const [snapshot, metrics] = await Promise.all([
        session.send("city.panel"),
        session.send("world.metrics"),
      ]);
      if (mine !== asked.current) return;
      setView({
        city: summary.city as CityView,
        panel: (snapshot.panel as CityPanel) ?? null,
        world: (metrics.metrics as Record<string, number>) ?? {},
      });
    } catch (trouble) {
      if (mine !== asked.current) return;
      setView(trouble instanceof Refused ? null : { failed: true });
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key]);

  if (view === undefined) {
    return <p className="note">{t("ui-city-asking")}</p>;
  }
  if (view === null) {
    return <p className="note">{t("ui-city-outside")}</p>;
  }
  if ("failed" in view) {
    return (
      <>
        <p className="trouble">{t("ui-city-silent")}</p>
        <button className="quiet" onClick={() => void reload()} disabled={busy}>
          {t("ui-city-again")}
        </button>
      </>
    );
  }

  return (
    <>
      <Economy view={view} />
      <Population view={view} busy={busy} />
      <button className="quiet" onClick={() => void reload()} disabled={busy}>
        {t("ui-city-recount")}
      </button>
    </>
  );
}
