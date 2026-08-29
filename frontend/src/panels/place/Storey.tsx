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
        <h2>Этаж</h2>
        <p className="note">
          {look.node?.name} · {home.area.toFixed(0)} м²
          {floor != null && home.floors > 0 && ` · ${floor}-й из ${home.floors}`}
          {" · "}мест под оборудование {home.used} из {home.slots}
        </p>
        {mine &&
          (renaming ? (
            <p className="row">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-label="имя этажа"
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
              Переименовать этаж
            </button>
          ))}
        <p className="note">
          Дом стоит на участке внизу: тип, состояние, ремонт и снос — там же.
          Обрушится он — этаж падёт с ним, и всё, что на нём стояло и лежало.
        </p>
      </section>

      {/* What makes the room what it is. A storey is a floor of the house, and
          machines take its metres by exactly the rule they take the ground
          floor's (D-106, D-247). */}
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
          There is no open ground beside a storey: under it is somebody's
          ceiling, and the yard stayed downstairs (D-244, D-247). */}
      <Ground look={look} where="floor" />
    </>
  );
}
