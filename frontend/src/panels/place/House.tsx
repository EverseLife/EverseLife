// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useEffect, useState } from "react";
import * as api from "../../api";
import { Refusal, useActions, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { buildingKindName, goodsName } from "../../names";
import { Deadline } from "../../Deadline";
import { Gauge } from "../../Gauge";
import { TierPick } from "../../Tier";
import { ownOrWild, type Props } from "./shared";
import { Demolition } from "./Demolition";
import { Equipment } from "./Equipment";
import { Repair } from "./Repair";


/** The building: raise it, take it apart -- and furnish it.
 *
 * What lies **inside** it -- the floor and the chests -- is put into the same
 * window by the stand (D-238), because things lie in a building rather than
 * beside it; under an open sky the same surface belongs to the land instead.
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
  const names = useNames();
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
  //: Why there is no bill: the refusal is part of the answer, same as the
  //: workshop's forecast -- a swallowed refusal leaves a stale bill lying
  //: under fresh parameters.
  const [refusal, setRefusal] = useState<string | null>(null);
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
  //: The bill counts itself while the player is still choosing (D-238, the
  //: workshop's `craft.plan` pattern): the estimate is a read, the debounce
  //: keeps it one question per pause, and the one button left is the one that
  //: builds. The shop window of types comes back with the bill, so the first
  //: answer also fills the picker. Above the early return on purpose -- a
  //: hook that sometimes does not run is a hook React counts wrong.
  const key = look.node?.key;
  const buildable = ownOrWild(look);
  useEffect(() => {
    if (!buildable) return;
    let dropped = false;
    const timer = setTimeout(() => {
      void session
        .send("build.estimate", { area, floors, kind: kind || undefined })
        .then((answer: any) => {
          if (dropped) return;
          take(answer);
          setRefusal(null);
        })
        .catch((error: unknown) => {
          if (dropped) return;
          setBill(null);
          setRefusal(error instanceof Error ? error.message : String(error));
        });
    }, 300);
    return () => {
      dropped = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, buildable, area, floors, kind]);
  if (!look.node) return null;

  const going = home.sites;
  //: Ground already promised to a site is ground taken (D-218): the engine
  //: counts it, and the window must count it the same way -- otherwise it
  //: offers metres the order will refuse.
  const started = going.reduce((sum, work) => sum + work.area, 0);
  const free = Math.max(0, plot - home.ground - started);

  const picked = kind || shelf[0]?.kind || "";

  const short = (bill?.materials ?? []).filter((m: any) => m.have < m.need);

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>{t("ui-place-house-title")}</h2>
      {/* The house as a state card (D-238): the condition on a track, the
          wear as a warning chip -- instead of the figures buried in a sentence. */}
      {home.area > 0 ? (
        <div className="state-card">
          <div className="card-head">
            <b>{home.kind ? buildingKindName(names, home.kind) : t("ui-place-house-default")}</b>
            <span className="note">
              {t("ui-place-house-summary", {
                area: home.area.toFixed(0),
                floors: home.floors,
                ground: home.ground.toFixed(0),
                used: home.used,
                slots: home.slots,
              })}
            </span>
          </div>
          {home.condition != null && (
            <Gauge
              label={t("ui-place-house-condition")}
              value={home.condition}
              reading={`${home.condition.toFixed(0)}%`}
            />
          )}
          {home.decay > 0 && (
            <div className="chips">
              <span className="chip warn" title={t("ui-place-house-decay-hint")}>
                {t("ui-place-house-decay")}{" "}
                <b>{t("ui-place-house-decay-rate", { decay: home.decay })}</b>
              </span>
            </div>
          )}
          {/* Floors above the ground are places of their own, climbed by a
              staircase (D-247): each has its own floor and its own slots. */}
          {home.floors > 1 && (
            <p className="note">
              {t("ui-place-house-storeys", { count: home.floors - 1 })}
            </p>
          )}
        </div>
      ) : (
        <p className="note">{t("ui-place-house-none")}</p>
      )}

      {/* A site under way speaks the deadline bar, like every other term. */}
      {going.map((w, i) => (
        <div className="doing" key={`${w.ready_at}-${i}`}>
          <span className="doing-what">
            {t("ui-place-house-site", {
              area: w.area.toFixed(0),
              floors: w.floors,
            })}
            {w.kind
              ? t("ui-place-house-site-kind", { kind: buildingKindName(names, w.kind) })
              : ""}
          </span>
          <span className="doing-aside note">{t("ui-place-house-site-note")}</span>
          <Deadline until={w.ready_at} label={t("ui-place-house-site-label")} size="row" />
        </div>
      ))}

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
              title={t("ui-place-house-kind-hint")}
            >
              {shelf.length === 0 && <option value="">…</option>}
              {shelf.map((k) => (
                <option key={k.kind} value={k.kind}>
                  {t("ui-place-house-kind-option", {
                    kind: buildingKindName(names, k.kind),
                    growth: k.growth,
                    decay: k.decay,
                  })}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={least}
              max={Math.floor(free)}
              value={area}
              onChange={(e) => setArea(Number(e.target.value))}
              title={t("ui-place-house-footprint")}
            />
            <input
              type="number"
              min={1}
              value={floors}
              onChange={(e) => setFloors(Number(e.target.value))}
              title={t("ui-place-house-floors")}
            />
            <span className="note">
              {t("ui-place-house-plan", {
                area,
                floors,
                living: area * floors,
                free: free.toFixed(0),
                least,
              })}
            </span>
          </div>

          {!bill &&
            (refusal ? (
              <p className="reason">{refusal}</p>
            ) : (
              <p className="note">{t("ui-place-house-counting")}</p>
            ))}
          {bill && (
            <>
              <table>
                <tbody>
                  {bill.materials.map((m: any) => (
                    <tr key={m.goods}>
                      <td>{goodsName(names, m.goods)}</td>
                      <td className={m.have < m.need ? "note" : undefined}>
                        {t("ui-place-materials-have", {
                          have: m.have.toFixed(1),
                          need: m.need.toFixed(1),
                        })}
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
                  disabled={
                    //: The bill echoes what it was counted for: while the
                    //: typed parameters run ahead of it, the button waits for
                    //: the recount rather than billing one thing and building another.
                    busy ||
                    short.length > 0 ||
                    area > free ||
                    area < least ||
                    bill.area !== area ||
                    bill.floors !== floors
                  }
                >
                  {t("ui-place-house-build", { area, floors })}
                </button>
                <span className="note">
                  {short.length > 0
                    ? t("ui-place-short", {
                        what: short.map((m: any) => goodsName(names, m.goods)).join(", "),
                      })
                    : t("ui-place-house-term", {
                        hours: (bill.minutes / 60).toFixed(1),
                        kind: buildingKindName(names, bill.kind),
                      })}
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
    </>
  );
}
