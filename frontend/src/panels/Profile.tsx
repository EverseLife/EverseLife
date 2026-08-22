// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Somebody's card (D-222).
 *
 * What a person shows of themselves (D-187) and where they belong: name,
 * surname, line, age, citizenship, a few words. Nothing of the body -- no
 * stamina, no pocket, no whereabouts: that is not information, that is
 * surveillance. One action, and it is the reason the card exists: write.
 */

import { useEffect, useState } from "react";
import type { Card, Session } from "../api";
import { askThread } from "../people";
import { Refusal, useActions } from "../actions";

type Props = {
  session: Session;
  name: string;
  onClose: () => void;
};

export function Profile({ session, name, onClose }: Props) {
  const acting = useActions();
  const [card, setCard] = useState<Card | null>(null);
  const [missing, setMissing] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setCard(null);
    setMissing(null);
    session
      .send("identity.profile", { name })
      .then((answer) => alive && setCard(answer.profile as Card))
      .catch((error) => alive && setMissing(error instanceof Error ? error.message : String(error)));
    return () => {
      alive = false;
    };
  }, [session, name]);

  //: Escape closes, as any curtain does.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const self = card !== null && card.name === session.name;

  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label="Профиль" onClick={onClose}>
      <section className="intro profile" onClick={(e) => e.stopPropagation()}>
        <header className="row">
          <h2>
            {name}
            {card?.surname ? ` ${card.surname}` : ""}
          </h2>
          <button className="quiet" onClick={onClose} title="закрыть" aria-label="закрыть">
            ×
          </button>
        </header>
        {missing && <p className="reason">{missing}</p>}
        {card && (
          <>
            <p className="note">
              {card.line === "human" ? "человек-киборг" : "нимфа"}
              {card.age != null ? ` · ${card.age}` : ""}
              {card.city ? ` · гражданство: ${card.city}` : " · без гражданства"}
              {" · в мире с "}
              {new Date(card.since).toLocaleDateString("ru-RU")}
            </p>
            {card.about && <p className="about">{card.about}</p>}
            {!self && (
              <div className="row">
                <button
                  disabled={acting.busy}
                  onClick={() =>
                    void acting.act(async () => {
                      //: The thread is kept even before a word is written:
                      //: deciding to write is the thread (D-222).
                      await session.send("net.open", { name });
                      askThread(name);
                      onClose();
                    })
                  }
                >
                  Написать сообщение
                </button>
              </div>
            )}
            <Refusal of={acting} />
          </>
        )}
      </section>
    </div>
  );
}
