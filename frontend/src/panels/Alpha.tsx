// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The alpha's debug tab: print a thing, finish the wait, pour in energy (D-229).
 *
 * A tab on the sidebar's rail, below the line with the office tab, shown to
 * the admin flag alone. It stood over the scene once, folded into a handle,
 * for a working reason -- in-person tabs close on the road and in the field
 * (D-107, D-152), and the road is exactly the wait a tester wants to skip.
 * The sidebar has no such closing: it is the remote register, open from the
 * road and from the cloud alike, so the tab keeps that property without a
 * strip of its own over the map.
 *
 * When the alpha ends the whole thing leaves along one seam, and this is the
 * list to take it out by: this file; `ALPHA_TAB` and its mentions in
 * `Sidebar.tsx` (the type, `known`, `tabs`, the rail, the render); the
 * `alpha` glyph; the `.alpha` block in `hud.css`; the `ui-alpha-*` keys in
 * `system.ftl` and `ui-side-tab-alpha*` in `shell.ftl`, both languages; on
 * the server the three commands in `api/commands/alpha.py`, `engine/alpha.py`
 * and the `alpha` touch in `push/_base.py`. A tool nobody but a developer has
 * must not read as a game feature to whoever is watching over a shoulder, and
 * a tab that is not there is the quietest way of not being a feature.
 *
 * The names to print are not asked of the server: the catalogue is already
 * here in the recipe book -- what a recipe makes, plus the materials no recipe
 * makes (D-215) -- and a key the client can derive is a key that does not
 * travel (D-225).
 */

import { useMemo, useState } from "react";
import { Refusal, useActions, useBook, useCompare, useSession } from "../actions";
import { t } from "../locale";

/** Job kinds the server may report as hurried, by message key. */
//: Every kind the server will hurry. The list on the server has grown since --
//: a keel, a passage between planets, the works on a building, the ploughing,
//: the printing of a body -- and each one missing here came back to the widget
//: as its own enum key: "срок подтянут: ship.keel".
//:
//: Keys rather than words: the map is built once at import, and a `t()` there
//: would nail the session to whatever language was being spoken then.
const MOVED: Record<string, string> = {
  "explore.survey": "ui-alpha-job-explore-survey",
  "travel.leg": "ui-alpha-job-travel-leg",
  "craft.batch": "ui-alpha-job-craft-batch",
  "ship.keel": "ui-alpha-job-ship-keel",
  "ship.flight": "ui-alpha-job-ship-flight",
  "build.finish": "ui-alpha-job-build-finish",
  "build.demolish": "ui-alpha-job-build-demolish",
  "build.repair": "ui-alpha-job-build-repair",
  "farm.plow": "ui-alpha-job-farm-plow",
  "body.print": "ui-alpha-job-body-print",
};

//: Where a print lands (D-229, addendum of 2026-09-02): the server's two words.
const HANDS = "hands";
const FLOOR = "floor";

type Props = {
  /** Whether there is a body to print into. In the cloud there is not, and a
   *  thing is printed into the hands: the half of the tab that does it is
   *  hidden rather than left there to be refused. Hurrying stays -- the term
   *  running there is the printing of the body itself. */
  embodied?: boolean;
};

export function Alpha({ embodied = true }: Props) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: printing a thing must not
  //: grey out the map, the chat and somebody else's orders.
  const acting = useActions();
  //: The order of the list is the reading order of the player's language, so
  //: the comparator is what the memo below hangs on.
  const order = useCompare();
  const { busy, act } = acting;

  const [goods, setGoods] = useState("");
  const [amount, setAmount] = useState("1");
  const [quality, setQuality] = useState("");
  const [where, setWhere] = useState(HANDS);
  const [energy, setEnergy] = useState("");
  const [said, setSaid] = useState<string | null>(null);

  //: The scale the vault set (`quality.scale`), read off the book that carries
  //: the constants to every panel (D-209): writing 0..100 here would be a
  //: second copy of a number the vault owns. Absent -- the field simply
  //: carries no bounds, and the server's refusal names them: better an honest
  //: gap than an invented pair of numbers.
  const scale = (book?.constants?.["quality.scale"] ?? null) as
    | { min: number; max: number }
    | null;

  //: Everything a thing can be called: recipe outputs and the materials no
  //: recipe makes. Sorted and deduped -- a name may be both.
  const names = useMemo(() => {
    if (!book) return [];
    const all = new Set<string>();
    for (const material of book.materials ?? []) all.add(material.name);
    for (const recipe of book.recipes) all.add(recipe.name);
    return [...all].sort(order);
  }, [book, order]);

  const spawn = () =>
    act(async () => {
      const answer = await session.send("alpha.spawn", {
        goods,
        amount: Number(amount) || 1,
        //: An empty field is not zero quality: it is a thing without one, as
        //: raw material out of a vein has none.
        ...(quality.trim() === "" ? {} : { quality: Number(quality) }),
        where,
      });
      //: Both go in as strings: they are read off a line, not summed, and
      //: `NUMBER` would put a thousands separator inside an amount.
      setSaid(
        t("ui-alpha-printed", {
          goods: String(answer.spawned),
          amount: String(answer.amount),
        }),
      );
    });

  //: Energy into the pool of the city underfoot: a test world's pool runs dry,
  //: and a dry pool hides every door that needs it (D-085, D-268, D-269).
  const energize = () =>
    act(async () => {
      const answer = await session.send("alpha.energize", { amount: Number(energy) });
      setSaid(t("ui-alpha-energized", { stored: String(answer.stored) }));
    });

  const hurry = () =>
    act(async () => {
      const answer = await session.send("alpha.hurry");
      const kinds = (answer.hurried as string[]) ?? [];
      setSaid(
        kinds.length === 0
          ? t("ui-alpha-hurry-nothing")
          : t("ui-alpha-hurried", {
              //: A kind the map does not know still shows itself, as before:
              //: the enum key is an ugly but honest answer.
              kinds: kinds.map((kind) => (MOVED[kind] ? t(MOVED[kind]) : kind)).join(", "),
            }),
      );
    });

  //: The tab's title is the panel's (`side-title`): no heading and no fold of
  //: its own -- the rail is the fold, and a second name under the first was
  //: what the widget carried when it stood over the scene.
  return (
    <section className="card flat alpha">
      <Refusal of={acting} />
      {embodied && (
        <>
          <label>
            <span>{t("ui-alpha-what")}</span>
            <input
              list="alpha-goods"
              value={goods}
              onChange={(e) => setGoods(e.target.value)}
              placeholder={t("ui-alpha-what-hint")}
            />
          </label>
          <datalist id="alpha-goods">
            {names.map((name) => (
              <option key={name} value={name} />
            ))}
          </datalist>

          <div className="row">
            <label>
              <span>{t("ui-alpha-amount")}</span>
              <input
                type="number"
                min="0"
                step="any"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </label>
            <label>
              <span>{t("ui-alpha-quality")}</span>
              <input
                type="number"
                {...(scale ? { min: scale.min, max: scale.max } : {})}
                step="any"
                value={quality}
                onChange={(e) => setQuality(e.target.value)}
                placeholder={t("ui-alpha-no-quality")}
              />
            </label>
            <label>
              <span>{t("ui-alpha-where")}</span>
              <select value={where} onChange={(e) => setWhere(e.target.value)}>
                <option value={HANDS}>{t("ui-alpha-where-hands")}</option>
                <option value={FLOOR}>{t("ui-alpha-where-floor")}</option>
              </select>
            </label>
          </div>

          <div className="row">
            <label>
              <span>{t("ui-alpha-energy")}</span>
              <input
                type="number"
                min="0"
                step="any"
                value={energy}
                onChange={(e) => setEnergy(e.target.value)}
                placeholder={t("ui-alpha-energy-hint")}
              />
            </label>
            <button onClick={energize} disabled={busy || !(Number(energy) > 0)}>
              {t("ui-alpha-energize")}
            </button>
          </div>
        </>
      )}

      <div className="row">
        {embodied && (
          <button onClick={spawn} disabled={busy || goods.trim() === ""}>
            {t("ui-alpha-print")}
          </button>
        )}
        <button onClick={hurry} disabled={busy}>
          {t("ui-alpha-finish")}
        </button>
      </div>

      {said && <p className="note">{said}</p>}
      <p className="note">
        {/* Two sentences and not one with a variant: the first belongs to
            the printing half, which is absent in the cloud, and the space
            that joins them belongs to the pair rather than to either. */}
        {embodied && `${t("ui-alpha-note-print")} `}
        {t("ui-alpha-note-hurry")}
      </p>
    </section>
  );
}
