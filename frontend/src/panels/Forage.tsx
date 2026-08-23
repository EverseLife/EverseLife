// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Foraging: the empty land of the place gives up what lies on it (D-210).
 *
 * The bare-hand gathering of D-196 was three buttons in the "Лес" window --
 * "gather N of this" -- and read as one more workbench. This window is a walk
 * over the plot instead, and it asks one question at a time:
 *
 * - **nothing to choose before starting.** What the land gives is listed with
 *   its odds, and the search is one button;
 * - **one find, one decision.** The find shows itself when its term is up:
 *   take it into the hands and the foraging ends there, or leave it lying and
 *   the search goes on -- that is what leaving it means. After taking, the
 *   window says what was picked up and offers the two ways on: search again or
 *   walk off. Nothing restarts by itself (D-211);
 * - **the numbers are the land's.** Empty area and the mean time of a search
 *   are on the window: more empty land is faster, and the player sees why a
 *   built-up yard is slow before wondering.
 *
 * The window exists only where the server says foraging is possible at all
 * (`look.forage`): the land is ours or nobody's and there is room to walk.
 */

import { useEffect, useState } from "react";
import type { Look } from "../api";
import { spell } from "../api";
import { busyWith, FORAGE } from "../busy";
import { Deadline } from "../Deadline";
import { Hint } from "../Hint";
import { Refusal, useActions, useRefresh, useSession } from "../actions";

type Props = {
  look: Look;
};

export function Forage({ look }: Props) {
  const session = useSession();
  //: Own waiting and own refusal: full hands refuse this window, not the map.
  const acting = useActions();
  const { busy, act } = acting;
  const refresh = useRefresh();
  const foraging = look.forage ?? null;
  const state = foraging?.state ?? "idle";
  const readyAt = foraging?.ready_at ?? null;
  //: What was just picked up, kept only until the next decision: the server
  //: knows nothing of it -- the find is in the hands and the search is over
  //: (D-211) -- but the player must see what came of the button they pressed,
  //: and be offered the two ways on from there.
  const [took, setTook] = useState<{ goods: string; units: number } | null>(null);
  //: One body does one thing (D-211): while it is at another, the search is
  //: not begun, and the button says so instead of collecting a refusal.
  const elsewhere = busyWith(look, [FORAGE]);

  //: The find shows itself on the clock, and the poll comes every five
  //: seconds: a search of twenty seconds would spend a quarter of its life
  //: already found and not yet shown. So the world is reread right at the term.
  useEffect(() => {
    if (state !== "searching" || !readyAt) return;
    const wait = new Date(readyAt).getTime() - Date.now();
    const timer = setTimeout(() => void refresh().catch(() => {}), Math.max(0, wait) + 300);
    return () => clearTimeout(timer);
  }, [state, readyAt, refresh]);

  if (!foraging) return null;
  const found = foraging.found;

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        Собирательство{" "}
        <Hint>
          Пустая земля — участок без пятна застройки — отдаёт то, что на ней
          лежит. Что найдётся, не выбирают: поиск идёт временем, по сроку земля
          показывает одну находку. Нужна — подобрать в руки, и на этом поиск
          закончен: идти по участку снова или уйти, решаете вы. Не нужна —
          «искать дальше», и поиск продолжится сам. Каждый поиск стоит сил —
          найден он или пропущен. Чем больше пустой
          земли, тем быстрее находка. Уйдёте с места — поиск прервётся вместе
          с ненайденным.
        </Hint>
      </h2>
      <p className="note">
        пустой земли {foraging.area.toFixed(0)} м²
        {foraging.seconds != null && ` · находка примерно за ${spell(foraging.seconds)}`}
        {` · сил ${foraging.stamina} за поиск`}
      </p>

      {state === "idle" && took && (
        <div className="find" role="status">
          <span>
            подобрано: <b>{took.goods}</b> ×{took.units}
          </span>
          <span className="note">поиск закончен: искать дальше или уйти</span>
        </div>
      )}

      {state === "found" && found && (
        <div className="find" role="status">
          <span>
            нашлось: <b>{found.goods}</b> ×{found.units}
          </span>
          <span className="note">
            {found.mass.toFixed(1)} кг · кач. {found.quality.toFixed(0)}
          </span>
        </div>
      )}

      <div className="row">
        {state === "idle" && (
          <>
            <button
              onClick={() =>
                act(async () => {
                  setTook(null);
                  await session.send("forage.start");
                })
              }
              disabled={busy || !foraging.allowed || elsewhere !== null}
              title={
                elsewhere
                  ? elsewhere
                  : foraging.allowed
                    ? "пойти по участку: находка покажется по сроку"
                    : "здесь больше не ищут: земля чужая или застроена"
              }
            >
              {took ? "Искать дальше" : "Начать собирательство"}
            </button>
            {took && (
              <button
                className="quiet"
                onClick={() => setTook(null)}
                disabled={busy}
                title="закончить: находка уже в руках"
              >
                Закончить
              </button>
            )}
          </>
        )}
        {state === "searching" && readyAt && (
          <>
            <span className="note">
              ищете · находка покажется через{" "}
              <Deadline until={readyAt} since={foraging.started_at} label="поиск" />
            </span>
            <button
              className="quiet"
              onClick={() => act(() => session.send("forage.stop"))}
              disabled={busy}
              title="закончить: потраченные силы не вернутся"
            >
              Закончить
            </button>
          </>
        )}
        {state === "found" && (
          <>
            <button
              onClick={() =>
                act(async () => {
                  await session.send("forage.take");
                  setTook(found ? { goods: found.goods, units: found.units } : null);
                })
              }
              disabled={busy}
              title="в руки; поиск на этом заканчивается — искать дальше решать вам"
            >
              Подобрать
            </button>
            <button
              className="quiet"
              onClick={() => act(() => session.send("forage.pass"))}
              disabled={busy}
              title="оставить лежать — и искать дальше"
            >
              Искать дальше
            </button>
            <button
              className="quiet"
              onClick={() => act(() => session.send("forage.stop"))}
              disabled={busy}
              title="закончить собирательство; находка останется лежать"
            >
              Закончить
            </button>
          </>
        )}
      </div>

      {/* What the land gives at all, most likely first: the player must know
          what to expect before spending an hour walking here. */}
      <p className="note">
        здесь находят:{" "}
        {foraging.finds
          .map((entry) => `${entry.goods} ×${entry.units} (${Math.round(entry.share * 100)}%)`)
          .join(", ")}
      </p>
    </section>
  );
}
