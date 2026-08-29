// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import * as api from "../../api";
import { Refusal, useActions, useSession } from "../../actions";
import type { Props } from "./shared";
import { Equipment } from "./Equipment";
import { Ground } from "./Ground";


/**
 * The compartment: the place-window of a node aboard a ship (D-240).
 *
 * On the ground a node answers two questions -- what is built on it («Здание»)
 * and whose the land is («Земля») -- and aboard **neither one has an answer**.
 * There is no ground under a hull (D-201): a compartment is not bought, not
 * sold, not fenced, no city is founded in it and it is not demolished by the
 * square metre; it appears whole when a keel is laid and goes with the ship.
 * Those two windows nevertheless stood in every room aboard, offering to buy
 * the floor of a cabin in flight.
 *
 * So there is one window here and it answers the one question a compartment
 * has: **what stands in it and what lies in it.** The machines and furniture
 * that make the room what it is -- an engine room, a bridge, a hold -- and the
 * floor with its chests. Plus the nameplate, because a hull one walks by
 * memory wants its rooms named; the plate is the same one a plot wears
 * (`land.rename`, D-178) and the same right nails it on.
 *
 * What the hull itself is -- thrust, mass, air, the floor plan -- is the
 * «Корабль» window, and the orders it takes are the console's. This one is
 * about the room underfoot.
 */
export function Berth({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: Own waiting and own refusal, as every window of the stand has.
  const acting = useActions();
  const { busy, act } = acting;
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState(false);
  const mine = api.isMine(look);
  const home = api.houseOf(look.node);

  return (
    <>
      <section>
        <Refusal of={acting} />
        <h2>Отсек</h2>
        <p className="note">
          {look.node?.name} · {home.area.toFixed(0)} м² · мест под оборудование {home.used} из{" "}
          {home.slots}
        </p>
        {/* The nameplate. A hull is walked by memory, and «Отсек» twice over is
            two rooms nobody can tell apart. */}
        {mine &&
          (renaming ? (
            <p className="row">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-label="имя отсека"
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
                Назвать
              </button>
              <button className="quiet" onClick={() => setRenaming(false)}>
                Отмена
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
              Переименовать отсек
            </button>
          ))}
      </section>

      {/* What makes the room what it is. A compartment aboard is a building
          from its first second (D-106, D-202), so machines take its area by
          exactly the rule they take a house's. */}
      <Equipment
        title="Рабочие станции"
        things={look.bench ?? []}
        kind="station"
        look={look}
        busy={busy}
        act={act}
        note="За рабочей станцией работает один: пока идёт партия, второму она не отдаётся."
      />
      <Equipment
        title="Мебель"
        things={look.furniture ?? []}
        kind="furniture"
        look={look}
        busy={busy}
        act={act}
        note="Мебель обустраивает быт: кровать — сон быстрее, сундук — хранение. На ней не работают."
      />

      {/* And what lies on its floor and in its chests -- the same window a
          house has, because that question is the same question everywhere.
          A hull has no open ground beside it: the compartment **is** the
          building, and it covers the whole node (D-244). */}
      <Ground look={look} where="floor" />
    </>
  );
}
