// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Rule } from "../../Rule";
import { useSession } from "../../actions";
import type { Props } from "./shared";


/** The door of one's own location: shut for entry, and two lists (D-204).
 *
 * Shutting stops **entry**, not passage: through a shut location one still
 * walks, so a neighbour whose home stands behind this one is never cut off from
 * it. The lists are two, and where they contradict each other the black one
 * wins -- one line to learn instead of a roster that flipped its meaning.
 */
export function Door({ look, busy, act }: Props) {
  const session = useSession();
  const node = look.node;
  //: One field per list: typing a name to let in and a name to keep out are
  //: different intentions, and a shared field would make them one slip apart.
  const [friend, setFriend] = useState("");
  const [foe, setFoe] = useState("");
  if (!node) return null;

  //: The lists come only to the holder (D-204); the window is the holder's too.
  const allowed = node.door?.allowed ?? [];
  const barred = node.door?.barred ?? [];
  const shut = Boolean(node.gated);

  const strike = (name: string) => (
    <button
      key={name}
      onClick={() => act(() => session.send("gate.list", { who: name, strike: true }))}
      disabled={busy}
      title="убрать из списка"
    >
      {name} ✕
    </button>
  );

  const name = (who: string, allow: boolean, clear: () => void) =>
    act(async () => {
      await session.send("gate.list", { who: who, allowed: allow });
      clear();
    });

  return (
    <>
      <h3>
        Вход
        <Rule>
          Вошедший распоряжается тем, что лежит на земле: дверь и сундук — защита, а не
          правило «не бери».
        </Rule>
      </h3>
      <div className="row">
        <button
          onClick={() => act(() => session.send("gate.set", { closed: !shut }))}
          disabled={busy}
        >
          {shut ? "Открыть вход" : "Закрыть вход"}
        </button>
        <span className="note">
          {shut
            ? "Закрыта: входят хозяин и белый список."
            : "Открыта: входят все, кроме чёрного списка."}
          {" Пройти насквозь можно всегда — и выйти тоже: закрыть вход при госте нельзя."}
        </span>
      </div>

      <div className="row">
        <input
          value={friend}
          onChange={(e) => setFriend(e.target.value)}
          placeholder="имя"
          title="кого пускать в закрытую локацию"
        />
        <button
          onClick={() => name(friend.trim(), true, () => setFriend(""))}
          disabled={busy || !friend.trim()}
        >
          В белый список
        </button>
        {allowed.length > 0 ? (
          <span className="note">Пускаем: {allowed.map(strike)}</span>
        ) : (
          <span className="note">
            {shut ? "Пока никого: входите только вы." : "Пригодится, когда закроете вход."}
          </span>
        )}
      </div>

      <div className="row">
        <input
          value={foe}
          onChange={(e) => setFoe(e.target.value)}
          placeholder="имя"
          title="кого не пускать вовсе"
        />
        <button
          onClick={() => name(foe.trim(), false, () => setFoe(""))}
          disabled={busy || !foe.trim()}
        >
          В чёрный список
        </button>
        {barred.length > 0 ? (
          <span className="note">Не пускаем: {barred.map(strike)}</span>
        ) : (
          <span className="note">Чёрный список сильнее белого: названный тут не войдёт.</span>
        )}
      </div>
    </>
  );
}
