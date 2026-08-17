/**
 * Where to print for the first time (D-013, D-182).
 *
 * The newcomer's first decision, and it is deliberately about people, not
 * numbers: there is no price or term here at all -- **the first body is
 * printed at once and for free** at any door (D-040). The twelve hours of the
 * Forerunners' Printer take effect from the second print, and speaking of
 * them on this screen would be lying.
 *
 * The cards stand side by side so that in ten seconds the world's main
 * structure is seen: cities differ, they set the terms themselves, and nobody
 * has to be kind. The Forerunners' Printer is the last card: a fallback door
 * with neither residents nor a treasury, and it is always open.
 *
 * Print conditions (D-184) stand as table rows, not in text: the engine
 * enforces them, and the person must see them before clicking, not learn them from a refusal.
 */

import { useMemo, useState } from "react";
import * as api from "../api";
import type { Door } from "../api";

type Props = {
  doors: Door[];
  name: string;
  busy: boolean;
  trouble?: string | null;
  onPick: (node: string) => void;
  onBack: () => void;
};

export function Doors({ doors, name, busy, trouble, onPick, onBack }: Props) {
  //: The list comes already sorted -- populous cities first (D-187) -- and
  //: search narrows it by city or node name. An empty search is the whole list.
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return doors;
    return doors.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        (d.city ?? "").toLowerCase().includes(q) ||
        (d.precursor && "предтеч".includes(q)),
    );
  }, [doors, query]);

  return (
    <section className="wide doors-step">
      <h1>Где вас напечатать</h1>
      <p className="note center">
        {name}, тела у вас ещё нет — есть выбор машины, которая его соберёт.
        Первое тело печатается сразу и бесплатно везде; дальше за скорость
        платят.
      </p>

      <div className="row search">
        <input
          type="search"
          placeholder="найти город"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="поиск города"
        />
        <span className="note">
          {visible.length} из {doors.length} · сортировка по людям в городе
        </span>
      </div>

      {doors.length === 0 ? (
        <p className="trouble">
          В мире нет ни одного биопринтера. Этого положения быть не должно: вход
          в игру не блокируется никогда.
        </p>
      ) : visible.length === 0 ? (
        <p className="note center">Ничего не нашлось — попробуйте иначе.</p>
      ) : (
        <div className="doors">
          {visible.map((door) => (
            <section key={door.node}>
              {/* В заголовке — чем эта дверь отличается от соседней. Город
                  вынесен в строку: у столицы дверей две, и одинаковые
                  заголовки не давали бы их различить. */}
              <h2>{door.precursor ? "Принтер Предтеч" : door.name}</h2>
              <p className="note">
                {door.precursor
                  ? "Вечная машина настоящих людей: ничьей казны не требует и не откажет никому."
                  : "Городской биопринтер: работает на энергии и железе города."}
              </p>
              <table>
                <tbody>
                  <tr>
                    <td>город</td>
                    <td className="num">{door.city ?? "вне города"}</td>
                  </tr>
                  <tr>
                    <td>людей сейчас</td>
                    <td className="num">{door.city ? door.population : "—"}</td>
                  </tr>
                  <tr>
                    <td>граждан</td>
                    <td className="num">{door.city ? door.citizens : "—"}</td>
                  </tr>
                  <tr>
                    <td>подъёмные</td>
                    <td className="num">
                      {door.grant > 0 ? `${api.tk(door.grant)} ₭` : "нет"}
                    </td>
                  </tr>
                  <tr>
                    <td>первое тело</td>
                    <td className="num">сразу</td>
                  </tr>
                  {/* Условия печати (D-184). Показаны у городских дверей и
                      только у них: у Предтеч условий нет и быть не может —
                      машина ничья. */}
                  {door.city && (
                    <>
                      <tr>
                        <td>гражданство</td>
                        <td className="num">
                          {door.citizenship ? obligation(door.term) : "не требуется"}
                        </td>
                      </tr>
                      <tr>
                        <td>налог с продажи</td>
                        <td className="num">
                          {door.tax > 0 ? `${door.tax}%` : "нет"}
                        </td>
                      </tr>
                    </>
                  )}
                </tbody>
              </table>
              {/* Слово города: его пишет власть, а не движок (D-183). Молчащий
                  город показывает только числа — сочинять за него нечего. */}
              {door.about && <p className="say">«{door.about}»</p>}
              <div className="row">
                <button onClick={() => onPick(door.node)} disabled={busy}>
                  Печататься здесь
                </button>
              </div>
            </section>
          ))}
        </div>
      )}

      <p className="note">
        Подъёмные платит город из своей казны, а не мир из воздуха:
        новый житель городу выгоден, и потому за него торгуются.
      </p>
      <p className="note">
        Строки таблицы движок исполняет: обязательное гражданство наступает в
        момент печати и держит весь срок, налог удерживается с каждой продажи
. У Принтера Предтеч условий нет — машина ничья.
      </p>
      <p className="note">
        В кавычках — слово самого города. Это обещание живых людей, и движок за
        него не отвечает: не сдержали — дело суда.
      </p>
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row">
        <button className="quiet" onClick={onBack} disabled={busy}>
          ← назад
        </button>
      </div>
    </section>
  );
}

/** The obligation in words: "for 3 days" or just "mandatory". */
function obligation(days: number): string {
  if (days <= 0) return "обязательно";
  if (days < 1) return `обязательно · ${Math.round(days * 24)} ч`;
  return `обязательно · ${days % 1 === 0 ? days : days.toFixed(1)} сут`;
}
