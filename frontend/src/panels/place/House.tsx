// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useEffect, useState } from "react";
import * as api from "../../api";
import { when } from "../../clock";
import { Refusal, useActions, useSession } from "../../actions";
import { TierPick } from "../../Tier";
import { ownOrWild, type Props } from "./shared";
import { Demolition } from "./Demolition";
import { Equipment } from "./Equipment";
import { Repair } from "./Repair";


/** The house: build it, take it apart -- and furnish it.
 *
 * Storeys are the point of the building part (D-125, D-145): the plot limits
 * the footprint, not the workshop -- a house grows upwards where the ground
 * does not grow sideways. The bill is shown **before** the work and against
 * what is in hand, so that "wood 12 of 30" is read at the plan and not
 * discovered at the click. Demolition (D-205) is shown the same way round:
 * the term, what comes back and what is in the way -- all before the button.
 *
 * Machines and furniture follow in the same window: both go **into the house**
 * and take its slots (D-106, D-150), so raising walls and filling them is one
 * story, not two windows. Working at a machine is another matter -- for that
 * the machine has a row of its own in the location.
 */
export function House({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: Own waiting and own refusal: this window is a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
  const home = api.houseOf(look.node);
  const plot = look.node?.area ?? 0;
  const [area, setArea] = useState(20);
  const [floors, setFloors] = useState(1);
  //: Empty means "the plainest type there is" -- the engine decides which, so
  //: that the default lives in the vault and not in two places at once.
  const [kind, setKind] = useState<string>("");
  const [bill, setBill] = useState<any>(null);
  //: The shop window and the smallest footprint outlive the bill: the bill is
  //: cleared once the order is placed, and these two must not go with it --
  //: without them the picker would empty out and the minimum would read wrong
  //: exactly at the moment the player looks at what they have just started.
  const [shelf, setShelf] = useState<any[]>([]);
  const [least, setLeast] = useState(1);
  const take = (answer: any) => {
    setBill(answer);
    setShelf(answer?.kinds ?? []);
    setLeast(answer?.area_min ?? 1);
  };
  //: Which quality of each material goes into the wall (D-058).
  const [tiers, setTiers] = useState<Record<string, string | null>>({});
  //: The shop window of types comes back with the bill, so before the first
  //: estimate there is nothing to choose from. Hence one estimate on arrival:
  //: the type is chosen before the numbers, and the numbers must already be
  //: there. Above the early return on purpose -- a hook that sometimes does not
  //: run is a hook React counts wrong.
  const key = look.node?.key;
  const buildable = ownOrWild(look);
  useEffect(() => {
    if (!buildable) return;
    let dropped = false;
    void session
      .send("build.estimate", { area, floors, kind: kind || undefined })
      .then((first: any) => {
        if (!dropped) take(first);
      })
      .catch(() => undefined);
    return () => {
      dropped = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, buildable]);
  if (!look.node) return null;

  const going = home.sites;
  //: Ground already promised to a site is ground taken (D-218): the engine
  //: counts it, and the window must count it the same way -- otherwise it
  //: offers metres the order will refuse.
  const started = going.reduce((sum, work) => sum + work.area, 0);
  const free = Math.max(0, plot - home.ground - started);

  const count = async () => {
    take(await session.send("build.estimate", { area, floors, kind: kind || undefined }));
  };
  const picked = kind || shelf[0]?.kind || "";

  const short = (bill?.materials ?? []).filter((m: any) => m.have < m.need);

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>Дом</h2>
      {home.area > 0 ? (
        <p>
          жилой площади <b>{home.area.toFixed(0)} м²</b> в{" "}
          <b>{home.floors}</b> эт. на {home.ground.toFixed(0)} м² земли · мест
          под оборудование{" "}
          <b>
            {home.used} из {home.slots}
          </b>
          {home.kind && (
            <>
              {" · "}
              {home.kind}, состояние <b>{(home.condition ?? 0).toFixed(0)}%</b>
              {home.decay > 0 && ` (−${home.decay}% в сутки)`}
            </>
          )}
        </p>
      ) : (
        <p className="note">
          Дома нет — только двор. Рабочие станции и мебель ставят в дом: сначала строят.
        </p>
      )}

      {going.length > 0 && (
        <p className="note">
          Строится:{" "}
          {going
            .map(
              (w) =>
                `${w.area.toFixed(0)} м² в ${w.floors} эт.${w.kind ? ` (${w.kind})` : ""}`,
            )
            .join(", ")}
          {" · готово "}
          {when(going[0].ready_at)}. Материалы уже в стене.
        </p>
      )}

      {/* Ничью землю за городом строит всякий пришедший (D-198): окно нужно и
          там, иначе правило есть, а руки к нему не приложить. */}
      {buildable && free > 0 && (
        <>
          <div className="row">
            {/* Тип решает три вещи разом (D-218): состав, цену следующего этажа
                и скорость порчи. Числа показаны прямо в списке — выбор делают
                до сметы, и гадать о нём игрок не должен. */}
            <select
              value={picked}
              onChange={(e) => {
                setKind(e.target.value);
                setBill(null);
              }}
              title="тип здания"
            >
              {shelf.length === 0 && <option value="">…</option>}
              {shelf.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {k.kind} · этаж ×{k.growth} · порча {k.decay}%/сут
                </option>
              ))}
            </select>
            <input
              type="number"
              min={least}
              max={Math.floor(free)}
              value={area}
              onChange={(e) => setArea(Number(e.target.value))}
              title="пятно застройки, м²"
            />
            <input
              type="number"
              min={1}
              value={floors}
              onChange={(e) => setFloors(Number(e.target.value))}
              title="этажей"
            />
            <button
              onClick={() => act(count)}
              disabled={busy || area < least || area > free}
            >
              Посчитать смету
            </button>
            <span className="note">
              {area} м² × {floors} эт. = {area * floors} м² жилой площади.
              Свободно {free.toFixed(0)} м² двора, меньше {least} м² не строится.
              {" "}Этажность не ограничена — за высоту платит смета.
            </span>
          </div>

          {bill && (
            <>
              <table>
                <tbody>
                  {bill.materials.map((m: any) => (
                    <tr key={m.goods}>
                      <td>{m.goods}</td>
                      <td className={m.have < m.need ? "note" : undefined}>
                        {m.have.toFixed(1)} из {m.need.toFixed(1)}
                      </td>
                      <td>
                        {/* Which quality goes into the wall (D-058). */}
                        <TierPick
                          things={look.inventory}
                          goods={m.goods}
                          value={tiers[m.goods]}
                          onChange={(tier) => setTiers((was) => ({ ...was, [m.goods]: tier }))}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="row">
                <button
                  onClick={() =>
                    act(async () => {
                      await session.send("build.construct", {
                        area,
                        floors,
                        kind: picked || undefined,
                        tiers: Object.fromEntries(
                          Object.entries(tiers).filter(([, tier]) => tier),
                        ),
                      });
                      setBill(null);
                    })
                  }
                  disabled={busy || short.length > 0 || area > free || area < least}
                >
                  Строить {area} м² в {floors} эт.
                </button>
                <span className="note">
                  {short.length > 0
                    ? `Не хватает: ${short.map((m: any) => m.goods).join(", ")}`
                    : `Работы на ${(bill.minutes / 60).toFixed(1)} ч; ${bill.kind}.`}
                </span>
              </div>
            </>
          )}
        </>
      )}

      {/* Сносят там же, где строят: своё — и любую ничью землю за городом, где
          труд открыт всякому (D-198, D-205). Чужую городскую застройку
          разбирают по решению суда (D-095). */}
      {home.area > 0 && buildable && (
        <>
          <Repair look={look} busy={busy} act={act} />
          <Demolition look={look} busy={busy} act={act} />
        </>
      )}
    </section>
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
    </>
  );
}
