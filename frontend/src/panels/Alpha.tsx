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
import { Refusal, useActions, useBook, useSession } from "../actions";

/** Job kinds the server may report as hurried, in the player's words. */
//: Every kind the server will hurry, in words. The list on the server has grown
//: since -- a keel, a passage between planets, the works on a building, the
//: ploughing, the printing of a body -- and each one missing here came back to
//: the widget as its own enum key: "срок подтянут: ship.keel".
const MOVED: Record<string, string> = {
  "explore.survey": "разведка",
  "travel.leg": "переход",
  "craft.batch": "работа",
  "ship.keel": "закладка корабля",
  "ship.flight": "перелёт",
  "build.finish": "стройка",
  "build.demolish": "снос",
  "build.repair": "ремонт",
  "farm.plow": "вспашка",
  "body.print": "печать тела",
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
    return [...all].sort((a, b) => a.localeCompare(b, "ru"));
  }, [book]);

  const spawn = () =>
    act(async () => {
      const answer = await session.send("alpha.spawn", {
        goods,
        amount: Number(amount) || 1,
        //: An empty field is not zero quality: it is a thing without one, as
        //: raw material out of a vein has none.
        ...(quality.trim() === "" ? {} : { quality: Number(quality) }),
      });
      setSaid(`напечатано: ${answer.spawned} · ${answer.amount}`);
    });

  const hurry = () =>
    act(async () => {
      const answer = await session.send("alpha.hurry");
      const kinds = (answer.hurried as string[]) ?? [];
      setSaid(
        kinds.length === 0
          ? "нечего ускорять: ничего не идёт"
          : `срок подтянут: ${kinds.map((kind) => MOVED[kind] ?? kind).join(", ")}`,
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
        title="служебное окно альфы: печать вещей и досрочное завершение сроков"
      >
        Альфа
      </button>
    );
  }

  return (
    <section className="card alpha">
      <div className="row">
        <strong>Альфа</strong>
        <button className="quiet" onClick={() => setOpen(false)} aria-expanded>
          свернуть
        </button>
      </div>

      {open && (
        <>
          {embodied && (
            <>
              <label>
                <span>что напечатать</span>
                <input
                  list="alpha-goods"
                  value={goods}
                  onChange={(e) => setGoods(e.target.value)}
                  placeholder="Железная руда"
                />
              </label>
              <datalist id="alpha-goods">
                {names.map((name) => (
                  <option key={name} value={name} />
                ))}
              </datalist>

              <div className="row">
                <label>
                  <span>сколько</span>
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                  />
                </label>
                <label>
                  <span>качество</span>
                  <input
                    type="number"
                    {...(scale ? { min: scale.min, max: scale.max } : {})}
                    step="any"
                    value={quality}
                    onChange={(e) => setQuality(e.target.value)}
                    placeholder="без качества"
                  />
                </label>
              </div>
            </>
          )}

          <div className="row">
            {embodied && (
              <button onClick={spawn} disabled={busy || goods.trim() === ""}>
                Напечатать
              </button>
            )}
            <button onClick={hurry} disabled={busy}>
              Завершить сейчас
            </button>
          </div>

          {said && <p className="note">{said}</p>}
          <p className="note">
            {embodied &&
              "Печатается в руки и в журнал: у вещи записано основание «alpha», и " +
                "найти всё, что мир не заработал, можно по нему. "}
            «Завершить сейчас» двигает срок того, что вы уже начали, — разведки,
            перехода, работы, стройки, вспашки, перелёта и печати тела:
            доделывает их обычный обработчик, тот же, что и при честном
            ожидании.
          </p>
        </>
      )}
      <Refusal of={acting} />
    </section>
  );
}
