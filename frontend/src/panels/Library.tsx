/**
 * Библиотека: каталог рецептов с поиском и страницами (D-053, D-076).
 *
 * Взять может любой: бесплатно, без условий, без гражданства — Библиотека не
 * отказывает никому. Единственное её ограничение географическое: **удалённо она
 * не работает**, и потому эта таблица видна только тому, кто в ней стоит.
 *
 * Хранилище знаний растёт, и без порядка превращается в свалку из тысячи
 * рецептов с именами вроде «гвоздь 2 финальный» — за этим в игре смотрит
 * Мудрец. Здесь та же задача решается тем, что доступно клиенту: поиск по
 * названию, станку и входам плюс страницы.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Look, Session } from "../api";

/** Сколько строк каталога показывать за раз. Величина показа, а не игры. */
const PAGE = 8;

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Library({ look, session, busy, act }: Props) {
  const [книга, setКнига] = useState<any>(null);
  const [культуры, setКультуры] = useState<{ id: string; name: string }[]>([]);
  const [поиск, setПоиск] = useState("");
  const [страница, setСтраница] = useState(0);

  useEffect(() => {
    void api.recipes().then(setКнига);
    void api.plants().then((p) => setКультуры(p.plants));
  }, []);

  const все: any[] = книга?.recipes ?? [];
  const запрос = поиск.trim().toLowerCase();
  const найдено = все.filter(
    (рецепт) =>
      !запрос ||
      рецепт.name.toLowerCase().includes(запрос) ||
      (рецепт.station ?? "").toLowerCase().includes(запрос) ||
      рецепт.inputs.some((вход: string) => вход.toLowerCase().includes(запрос)),
  );

  const страниц = Math.max(1, Math.ceil(найдено.length / PAGE));
  const текущая = Math.min(страница, страниц - 1);
  const видно = найдено.slice(текущая * PAGE, текущая * PAGE + PAGE);

  return (
    <section>
      <h2>Библиотека</h2>
      <div className="row">
        <input
          type="search"
          value={поиск}
          placeholder="рецепт, станок или вход"
          onChange={(e) => {
            setПоиск(e.target.value);
            setСтраница(0);
          }}
        />
        <span className="note">
          {найдено.length} из {все.length}
        </span>
      </div>

      <table className="catalog">
        <thead>
          <tr>
            <th>рецепт</th>
            <th>ур.</th>
            <th>станок</th>
            <th>из чего</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {видно.map((рецепт) => (
            <tr key={рецепт.name}>
              <td>{рецепт.name}</td>
              <td className="num">{рецепт.level}</td>
              <td className="note">{рецепт.station ?? "—"}</td>
              <td className="note">{рецепт.inputs.join(", ") || "—"}</td>
              <td>
                {look.knows.includes(рецепт.name) ? (
                  <span className="note">знаю</span>
                ) : (
                  <button
                    className="quiet"
                    onClick={() =>
                      act(() => session.send("library.copy", { recipe: рецепт.name }))
                    }
                    disabled={busy}
                  >
                    Взять
                  </button>
                )}
              </td>
            </tr>
          ))}
          {видно.length === 0 && (
            <tr>
              <td colSpan={5} className="note">
                ничего не нашлось
              </td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="row">
        <button
          className="quiet"
          onClick={() => setСтраница(текущая - 1)}
          disabled={текущая === 0}
        >
          ←
        </button>
        <span className="note">
          страница {текущая + 1} из {страниц}
        </span>
        <button
          className="quiet"
          onClick={() => setСтраница(текущая + 1)}
          disabled={текущая >= страниц - 1}
        >
          →
        </button>
      </div>
      <h3>Агротехника</h3>
      <div className="row">
        {культуры.map((культура) => {
          const изучена = (look.agrotech ?? []).includes(культура.id);
          return (
            <button
              key={культура.id}
              className="quiet"
              onClick={() =>
                act(() => session.send("breed.agrotech", { culture: культура.id }))
              }
              disabled={busy || изучена}
              title={
                изучена
                  ? "агротехника уже в личности"
                  : "взять норму культуры: бесплатно, навсегда"
              }
            >
              {культура.name}
              {изучена ? " ✓" : ""}
            </button>
          );
        })}
      </div>
      <p className="note">
        Агротехника базовых культур — для всех: с ней грядка показывает норму,
        а не симптом (D-057). Взятое помечено ✓.
      </p>

      <p className="note">
        Бесплатно и без условий, но только придя (D-053); переписывание стоит
        выносливости (D-148).
      </p>
    </section>
  );
}
