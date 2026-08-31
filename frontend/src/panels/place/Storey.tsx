// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import * as api from "../../api";
import { Refusal, useActions, useSession } from "../../actions";
import { t } from "../../locale";
import type { Props } from "./shared";
import { Equipment } from "./Equipment";
import { Ground } from "./Ground";


/**
 * The storey: the place-window of a floor above the ground (D-247).
 *
 * On the ground a node answers two questions -- what is built on it («Здание»)
 * and whose the land is («Земля») -- and upstairs **neither one has an answer**.
 * There is no ground under a floor: a storey is not bought, not sold, not
 * fenced, no city is founded on it, nothing is marked out of it and nothing is
 * built on it. It appears when the house is finished and goes down with it.
 *
 * So there is one window here and it answers the one question a floor has:
 * **what stands in it and what lies in it.** The machines and furniture that
 * make the room what it is -- a workshop, a store, a kitchen -- and the floor
 * with its chests. Plus the nameplate: a house one walks by memory wants its
 * rooms named, and «3-й этаж» twice over is two rooms nobody can tell apart.
 *
 * What the house itself is -- the type, the condition, the wear, the repair and
 * the demolition -- is the plot's window downstairs, because that is where the
 * house stands as a thing one owns. This one is about the room underfoot.
 */
export function Storey({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: Own waiting and own refusal, as every window of the stand has.
  const acting = useActions();
  const { busy, act } = acting;
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const mine = api.isMine(look);
  const home = api.houseOf(look.node);
  const floor = look.node?.storey;

  return (
    <>
      <section>
        <Refusal of={acting} />
        <h2>{t("ui-place-storey-title")}</h2>
        <p className="note">
          {look.node?.name} · {t("ui-place-area", { area: home.area.toFixed(0) })}
          {floor != null &&
            home.floors > 0 &&
            t("ui-place-storey-which", { floor, floors: home.floors })}
          {" · "}
          {t("ui-place-slots", { used: home.used, slots: home.slots })}
        </p>
        {mine &&
          (renaming ? (
            <p className="row">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-label={t("ui-place-storey-name")}
                placeholder={look.node?.name}
              />
              <button
                disabled={busy || !name.trim()}
                onClick={() =>
                  act(async () => {
                    await session.send("land.rename", { name });
                    setRenaming(false);
                  })
                }
              >
                {t("ui-place-rename-save")}
              </button>
              <button className="quiet" onClick={() => setRenaming(false)}>
                {t("ui-place-cancel")}
              </button>
            </p>
          ) : (
            <button
              className="quiet"
              onClick={() => {
                setName(look.node?.name ?? "");
                setRenaming(true);
              }}
            >
              {t("ui-place-storey-rename")}
            </button>
          ))}
        <p className="note">{t("ui-place-storey-rule")}</p>
      </section>

      {/* What makes the room what it is. A storey is a floor of the house, and
          machines take its metres by exactly the rule they take the ground
          floor's (D-106, D-247). */}
      <Equipment
        title={t("ui-place-equipment-stations")}
        things={look.bench ?? []}
        kind="station"
        look={look}
        busy={busy}
        act={act}
        note={t("ui-place-equipment-stations-rule")}
      />
      <Equipment
        title={t("ui-place-equipment-furniture")}
        things={look.furniture ?? []}
        kind="furniture"
        look={look}
        busy={busy}
        act={act}
        note={t("ui-place-equipment-furniture-rule")}
      />

      {/* And what lies on its floor and in its chests -- the same window a
          house has, because that question is the same question everywhere.
          There is no open ground beside a storey: under it is somebody's
          ceiling, and the yard stayed downstairs (D-244, D-247). */}
      <Ground look={look} where="floor" />
    </>
  );
}
