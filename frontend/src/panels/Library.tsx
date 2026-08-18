/**
 * The Library: a recipe catalog with search and pages (D-053, D-076).
 *
 * Anyone may take: free, unconditional, without citizenship -- the Library
 * refuses nobody. Its only restriction is geographic: **it does not work
 * remotely**, so this table is visible only to whoever stands in it.
 *
 * The knowledge store grows, and without order turns into a dump of a
 * thousand recipes with names like "nail 2 final" -- in the game the Sage
 * watches over that. Here the same task is solved with what the client has:
 * search by name, machine and inputs plus pages.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Look, Session } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

/** How many catalog rows to show at a time. A display quantity, not a game one. */
const PAGE = 8;

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Library({ look, session }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [book, setBook] = useState<any>(null);
  const [crops, setCrops] = useState<{ id: string; name: string }[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);

  useEffect(() => {
    void api.recipes().then(setBook);
    void api.plants().then((p) => setCrops(p.plants));
  }, []);

  const all: any[] = book?.recipes ?? [];
  const query = search.trim().toLowerCase();
  const found = all.filter(
    (recipe) =>
      !query ||
      recipe.name.toLowerCase().includes(query) ||
      (recipe.station ?? "").toLowerCase().includes(query) ||
      recipe.inputs.some((entry: string) => entry.toLowerCase().includes(query)),
  );

  const pages = Math.max(1, Math.ceil(found.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = found.slice(current * PAGE, current * PAGE + PAGE);

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        Библиотека
        <Rule>
          Бесплатно и без условий, но только придя; переписывание стоит выносливости.
        </Rule>
      </h2>
      <div className="row">
        <input
          type="search"
          value={search}
          placeholder="рецепт, станция или вход"
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
        <span className="note">
          {found.length} из {all.length}
        </span>
      </div>

      <table className="catalog">
        <thead>
          <tr>
            <th>рецепт</th>
            <th>ур.</th>
            <th>станция</th>
            <th>из чего</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {shown.map((recipe) => (
            <tr key={recipe.name}>
              <td>{recipe.name}</td>
              <td className="num">{recipe.level}</td>
              <td className="note">{recipe.station ?? "—"}</td>
              <td className="note">{recipe.inputs.join(", ") || "—"}</td>
              <td>
                {look.knows.includes(recipe.name) ? (
                  <span className="note">знаю</span>
                ) : (
                  <button
                    className="quiet"
                    onClick={() =>
                      act(() => session.send("library.copy", { recipe: recipe.name }))
                    }
                    disabled={busy}
                  >
                    Взять
                  </button>
                )}
              </td>
            </tr>
          ))}
          {shown.length === 0 && (
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
          onClick={() => setPage(current - 1)}
          disabled={current === 0}
        >
          ←
        </button>
        <span className="note">
          страница {current + 1} из {pages}
        </span>
        <button
          className="quiet"
          onClick={() => setPage(current + 1)}
          disabled={current >= pages - 1}
        >
          →
        </button>
      </div>
      <h3>Агротехника</h3>
      <div className="row">
        {crops.map((crop) => {
          const learned = (look.agrotech ?? []).includes(crop.id);
          return (
            <button
              key={crop.id}
              className="quiet"
              onClick={() =>
                act(() => session.send("breed.agrotech", { culture: crop.id }))
              }
              disabled={busy || learned}
              title={
                learned
                  ? "агротехника уже в личности"
                  : "взять норму культуры: бесплатно, навсегда"
              }
            >
              {crop.name}
              {learned ? " ✓" : ""}
            </button>
          );
        })}
      </div>
      <p className="note">
        Агротехника базовых культур — для всех: с ней грядка показывает норму,
        а не симптом. Взятое помечено ✓.
      </p>
    </section>
  );
}
