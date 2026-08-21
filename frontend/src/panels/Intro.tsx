// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The word on waking (D-182).
 *
 * There are no NPCs, no quests, and nobody else to explain to the printed who
 * they are and why the world is empty. This is the only place where the game
 * speaks about itself -- and therefore it is strictly bounded: **not a single
 * lore secret**. Here only what the printed already knows about themselves.
 * What happened to the Forerunners, whose sample the machine prints and what
 * lies under Aurora's ice is not our business: lore is given out in fragments
 * and is mined (10-world/01).
 *
 * The second half is the first three steps. Not a quest and not a checked
 * chain but an answer to "what to do" -- the very path by which a newcomer
 * pays for themselves in a couple of hours (60-meta/00).
 *
 * The window closes with a button and opens again from the header: what was
 * read once must not become unavailable, and what was not read -- mandatory.
 */


type Props = {
  onClose: () => void;
};

export function Intro({ onClose }: Props) {
  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label="Вы открыли глаза">
      <section className="intro">
        <h2>Вы открыли глаза</h2>

        <p>
          Настоящие люди — Предтечи — построили машину, которая печатает людей,
          и исчезли. Их города лежат подо льдом, их руины — под столицей; живых
          свидетелей не осталось ни одного.
        </p>
        <p>
          Машина работает до сих пор. Тело, в котором вы стоите, собрано ею
          минуту назад: мышление держит процессор, а имя, знания и счёт хранит
          Сеть. Поэтому смерть здесь отнимает вещи, но не вас.
        </p>
        <p>
          Наследство досталось целым и пустым: чертежи есть, руда в земле, дорог
          нет. Продолжать дело Предтечей больше некому — кроме вас и таких же
          напечатанных. Всё, что появится в этом мире, сделают люди.
        </p>

        <h3>С чего начать</h3>
        <ol>
          <li>
            Осмотреться в узле и собрать простое: камень, хворост, волокно.
            Действие — предмет, и это первое, что стоит проверить руками.
          </li>
          <li>
            Взять первый рецепт в Библиотеке столицы. Знаний вам не выдали
            никаких: ремесло — это знание, и за ним надо прийти.
          </li>
          <li>
            Продать сделанное на терминале. Цену здесь назначают люди, а не мир:
            первый заработок и есть первая встреча с ними.
          </li>
        </ol>

        <div className="row">
          <button onClick={onClose}>Начать</button>
          <span className="note">
            Открыть снова — знак «?» в шапке.
          </span>
        </div>
      </section>
    </div>
  );
}
