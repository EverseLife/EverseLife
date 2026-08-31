// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The alpha's debug widget: print a thing, finish the wait (D-229).
 *
 * Deliberately **not** woven into the sidebar's tabs, though the activities
 * tab is where hurrying would belong by meaning. Two reasons, and both are about
 * the day this comes out: a tool nobody but a developer has must not read as
 * a game feature to whoever is watching over a shoulder, and when the alpha
 * ends the whole thing has to leave along one seam -- this file, the two
 * socket commands, `engine/alpha.py`, one rule in `index.css` and one block in
 * `App.tsx`. Nothing to disentangle.
 *
 * It stands outside the tabs for a working reason too: in-person tabs close
 * while the body is on the road or in the field (D-107, D-152), and the road
 * is exactly the wait a tester wants to skip.
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

type Props = {
  /** The vault's numbers as `/public/constants` serves them: the quality
   *  scale is one of them, and writing 0..100 here would be a second copy of
   *  a number the vault owns. */
  values: Record<string, any> | null;
  /** Whether there is a body to print into. In the cloud there is not, and a
   *  thing is printed into the hands: the half of the widget that does it is
   *  hidden rather than left there to be refused. Hurrying stays -- the term
   *  running there is the printing of the body itself. */
  embodied?: boolean;
};

export function Alpha({ values, embodied = true }: Props) {
  const session = useSession();
  const book = useBook();
  //: This panel's own waiting and its own refusal: printing a thing must not
  //: grey out the map, the chat and somebody else's orders.
  const acting = useActions();
  //: The order of the list is the reading order of the player's language, so
  //: the comparator is what the memo below hangs on.
  const order = useCompare();
  const { busy, act } = acting;

  const [open, setOpen] = useState(false);
  const [goods, setGoods] = useState("");
  const [amount, setAmount] = useState("1");
  const [quality, setQuality] = useState("");
  const [said, setSaid] = useState<string | null>(null);

  //: The scale the vault set (`quality.scale`). Absent -- the field simply
  //: carries no bounds, and the server's refusal names them: better an honest
  //: gap than an invented pair of numbers.
  const scale = (values?.["quality.scale"] ?? null) as { min: number; max: number } | null;

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

  //: Folded, the whole thing is **one small button** and nothing else: no
  //: card, no border, no heading, no strip of padding above the map. A tool
  //: nobody but a developer has must not sit over the scene taking a hand's
  //: width of it -- and "свернуть" that left a bordered header behind was not
  //: folding anything, only emptying it.
  if (!open) {
    return (
      <button
        className="quiet alpha-handle"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        title={t("ui-alpha-open-title")}
      >
        {t("ui-alpha-name")}
      </button>
    );
  }

  return (
    <section className="card alpha">
      <div className="row">
        <strong>{t("ui-alpha-name")}</strong>
        <button className="quiet" onClick={() => setOpen(false)} aria-expanded>
          {t("ui-alpha-fold")}
        </button>
      </div>

      {open && (
        <>
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
        </>
      )}
      <Refusal of={acting} />
    </section>
  );
}
