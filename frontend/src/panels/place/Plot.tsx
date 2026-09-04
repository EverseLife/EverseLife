// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import * as api from "../../api";
import { Refusal, useActions, useSession } from "../../actions";
import { Glyph } from "../../Glyph";
import { dayPhase, isDay } from "../../clock";
import { t } from "../../locale";
import { EMBLEM_MARKS, EMBLEM_WORDS } from "../../marks";
import type { Props } from "./shared";
import { Door } from "./Door";
import { Foundation } from "./Foundation";
import { ownOrWild } from "./shared";
import { NumberField } from "../../NumberField";


/** The plot: whose it is, what it is called, who gets in -- and how it changes hands.
 *
 * One window for everything about the land itself (D-178, D-204): ownership,
 * the name, the door, buying an empty plot and founding a city. These used to
 * live in two windows ("Локация" and half of "Место"), and the seam between
 * them ran through one question -- "what is this land and what may I do with
 * it" -- which no window answered whole.
 *
 * Ownership is a public fact: whoever enters sees the owner, a person or a
 * city, so the window is shown to guests too -- read-only. The name is given
 * by whoever disposes of the land, and the map label changes, not the node
 * key: deeds and edges reference the key. The door and the lists belong to the
 * holder alone: civic land is regulated by citizenship and duties, not by a
 * list of names.
 */
/**
 * Marking a strip out of the land one holds.
 *
 * It lives here rather than in "Огород" because it is a thing done to
 * **land**: the window of farming is about the cycle -- ploughing, sowing, the
 * daily round, the harvest -- and that cycle has nowhere to happen until a
 * strip exists. So the land gives birth to the plot, and the plot brings its
 * own window with it.
 */
function Marking({ look, busy, act }: Props) {
  const session = useSession();
  const [name, setName] = useState("");
  const [metres, setMetres] = useState(100);
  const marked = look.node?.plots ?? 0;
  //: Nothing grows in the open ground of a climate (D-231): the server refuses,
  //: and the window says so before the refusal rather than after it.
  const climate = look.frost?.climate ?? null;

  const weather = look.node?.climate ?? null;

  if ((look.node?.fertility ?? 0) <= 0) return null;
  return (
    <div className="pocket">
      <h3>{t("ui-place-marking-title")}</h3>
      {weather && look.clock && (
        //: The place's climate (D-261): what the sowing gate will judge by.
        //: "Now" is this client's arithmetic over the planetary clock (D-225)
        //: -- alive between looks and agreeing with the drawn hand.
        <p className="note">
          {t(isDay(look.clock) ? "ui-place-climate-day" : "ui-place-climate-night", {
            now: Math.round(
              weather.temperature.mean -
                weather.temperature.swing * Math.cos(2 * Math.PI * dayPhase(look.clock)),
            ),
            low: Math.round(weather.temperature.mean - weather.temperature.swing),
            high: Math.round(weather.temperature.mean + weather.temperature.swing),
            light: isDay(look.clock) ? weather.light.day : 0,
            top: weather.light.day,
            rain: Math.round(weather.precipitation),
          })}
        </p>
      )}
      {climate ? (
        <p className="note">{t("ui-place-marking-climate", { climate })}</p>
      ) : (
        <>
          <div className="row">
            <input
              value={name}
              placeholder={t("ui-place-marking-name")}
              onChange={(e) => setName(e.target.value)}
            />
            <NumberField
              value={metres}
              onChange={(typed) => setMetres(typed ?? 0)}
              title={t("ui-place-marking-area")}
            />
            <button
              onClick={() =>
                act(async () => {
                  await session.send("farm.mark", { name, area: metres });
                  setName("");
                })
              }
              disabled={busy}
            >
              {t("ui-place-marking-mark")}
            </button>
          </div>
          <p className="note">
            {marked > 0
              ? t("ui-place-marking-marked", { count: marked })
              : t("ui-place-marking-none")}
          </p>
        </>
      )}
    </div>
  );
}


export function Plot({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This window's own waiting and its own refusal: shutting the door here must
  //: not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;
  const node = look.node;
  const [name, setName] = useState("");
  //: `null` -- nothing picked yet: the grid then shows what is nailed on.
  const [mark, setMark] = useState<string | null>(null);
  //: Same rule for the words: `null` shows what is already written.
  const [about, setAbout] = useState<string | null>(null);
  //: Handing a plot over is asked twice: the deed is cancelled by it, and the
  //: way back is a purchase at the price list.
  const [giving, setGiving] = useState(false);
  if (!node) return null;

  //: Same three cases as the old purchase window: nobody's city land with a
  //: price, and the wild beyond the walls. An owned node is never for sale here.
  const forSale = !node.owner && (api.isWild(node) || node.price !== undefined);
  const owned = Boolean(node.owner || node.owner_city);
  if (!forSale && !owned) return null;

  //: Who the meter is charged to (D-149). Ownership does not answer it by
  //: itself: a bought plot stays civic land, yet its bill is a person's.
  const upkeep =
    node.upkeep === "owner"
      ? t("ui-place-plot-upkeep-owner")
      : node.upkeep === "city"
        ? t("ui-place-plot-upkeep-city", {
            //: A flag rather than a ternary of two Russian halves: the sentence
            //: names the city where it has one, and the message decides where.
            named: node.owner_city ? "true" : "false",
            city: node.owner_city ?? "",
          })
        : node.upkeep === "nobody"
          ? t("ui-place-plot-upkeep-nobody")
          : node.owner || node.owner_city
            ? t("ui-place-plot-upkeep-none")
            : null;

  //: Во что обходится держать участок сутки (D-127, D-220). Стоит рядом с
  //: ценой выкупа не для симметрии: ставка убывает с каждым узлом от
  //: биопринтера, поэтому центр дорог дважды — и купить, и держать, — и вторую
  //: половину счёта покупатель обязан видеть до того, как заплатит первую.
  const tax = node.tax > 0 ? t("ui-place-plot-tax", { tax: api.tk(node.tax) }) : null;

  const mine = api.isMine(look);
  const whose = mine
    ? t("ui-place-plot-mine")
    : node.owner
      ? t("ui-place-plot-owner", { owner: node.owner })
      : node.owner_city
        ? t("ui-place-plot-city", { city: node.owner_city })
        : t("ui-place-plot-nobody");

  return (
    <>
    <section>
      <Refusal of={acting} />
      <h2>{t("ui-place-plot-title")}</h2>
      <p className="note">
        {node.name} · {t("ui-place-area", { area: node.area.toFixed(0) })} · {whose}
        {node.gated && t("ui-place-plot-gated")}
        {node.cut_off && t("ui-place-plot-cut-off")}
      </p>
      {/* The place's own words: the world voice, to whoever enters (D-075). */}
      {node.about && <p className="place-about">{node.about}</p>}
      {tax && <p className="note">{tax}</p>}
      {upkeep && <p className="note">{upkeep}</p>}
      {/* Only civic land is handed over: a ship's cabin is owned too, and there
          is no city under it to take it. */}
      {mine && node.owner_city && (
        giving ? (
          <div className="row">
            <button onClick={() => act(async () => {
              await session.send("land.cede");
              setGiving(false);
            })} disabled={busy}>
              {t("ui-place-plot-cede-yes")}
            </button>
            <button onClick={() => setGiving(false)} disabled={busy}>
              {t("ui-place-cancel")}
            </button>
            <span className="note">{t("ui-place-plot-cede-rule")}</span>
          </div>
        ) : (
          <div className="row">
            <button onClick={() => setGiving(true)} disabled={busy}>
              {t("ui-place-plot-cede")}
            </button>
            <span className="note">{t("ui-place-plot-cede-note")}</span>
          </div>
        )
      )}
      {forSale &&
        (api.isWild(node) ? (
          <p className="note">{t("ui-place-plot-wild")}</p>
        ) : node.price !== undefined ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              {t("ui-place-plot-buy", { price: api.tk(node.price) })}
            </button>
            <span className="note">{t("ui-place-plot-buy-note")}</span>
          </div>
        ) : null)}
      {node.may_name && (
        <div className="row">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={node.name}
            //: Repeats `runtime.LAND_NAME_LIMIT`: better to show the limit by
            //: the input field than to report it as a refusal after a click.
            maxLength={40}
            title={t("ui-place-plot-name-hint")}
          />
          <button
            onClick={() =>
              act(async () => {
                await session.send("land.rename", { name: name });
                setName("");
              })
            }
            disabled={busy || !name.trim() || name.trim() === node.name}
          >
            {t("ui-place-plot-rename")}
          </button>
          <span className="note">{t("ui-place-plot-rename-note")}</span>
        </div>
      )}
      {node.may_name && (
        //: The map mark is nailed where the nameplate is (D-238): the same
        //: right, the same spot. The marks themselves are shown, not their
        //: names in a list -- one picks a picture by the picture. The set is
        //: the engine's closed list: the world's own signs are not offered.
        <>
          {(() => {
            //: What the grid highlights: the fresh pick, or what is already
            //: nailed on -- the owner sees their mark, not a blank grid.
            const shown = mark ?? node.emblem ?? "";
            return (
              <>
                <div
                  className="emblem-grid"
                  role="group"
                  aria-label={t("ui-place-plot-emblem-label")}
                >
                  {Object.entries(EMBLEM_MARKS).map(([mark_, glyph]) => (
                    <button
                      key={mark_}
                      type="button"
                      className="bare emblem-pick"
                      aria-pressed={shown === mark_}
                      onClick={() => setMark(shown === mark_ ? "" : mark_)}
                      title={EMBLEM_WORDS[mark_] ?? mark_}
                    >
                      <Glyph name={glyph} />
                      <span>{EMBLEM_WORDS[mark_] ?? mark_}</span>
                    </button>
                  ))}
                </div>
                <div className="row">
                  <button
                    onClick={() => act(() => session.send("land.emblem", { emblem: shown }))}
                    disabled={busy || !shown || shown === (node.emblem ?? "")}
                  >
                    {t("ui-place-plot-emblem-nail")}
                  </button>
                  <button
                    className="quiet"
                    onClick={() =>
                      act(async () => {
                        await session.send("land.emblem", { emblem: "" });
                        setMark(null);
                      })
                    }
                    disabled={busy || !node.emblem}
                    title={t("ui-place-plot-emblem-clear-hint")}
                  >
                    {t("ui-place-plot-emblem-clear")}
                  </button>
                </div>
              </>
            );
          })()}
          {/* The place's own words, beside the name and the mark (D-238):
              empty and saved means wiped. */}
          <div className="form">
            <label>
              <span>{t("ui-place-plot-about-label")}</span>
              <textarea
                value={about ?? node.about ?? ""}
                onChange={(e) => setAbout(e.target.value)}
                rows={3}
                maxLength={300}
                placeholder={t("ui-place-plot-about-hint")}
              />
            </label>
            <button
              onClick={() =>
                act(async () => {
                  await session.send("land.describe", { about: (about ?? "").trim() });
                  setAbout(null);
                })
              }
              disabled={busy || about === null || about.trim() === (node.about ?? "")}
            >
              {t("ui-place-plot-about-save")}
            </button>
          </div>
        </>
      )}
      {mine && <Door look={look} busy={busy} act={act} />}
      {/* A strip is marked on one's own land -- and on nobody's, where the
          field is open to whoever ploughs it (D-198, `farm.mark`). Asking for
          ownership alone hid the form on every wild node, and the fertile
          ground of the starting world is exactly that: the floodplain belongs
          to no one, so farming had no way in at all. */}
      {ownOrWild(look) && <Marking look={look} busy={busy} act={act} />}
    </section>
    {/* Founding a city is the plot's fate, so the section stands here:
        the server offers it only where founding is possible at all. */}
    <Foundation look={look} busy={busy} act={act} />
    </>
  );
}
