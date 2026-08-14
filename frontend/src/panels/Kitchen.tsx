/**
 * Очаг: котёл по ролям (D-119, D-128).
 *
 * Состав блюда задан не списком, а ролями: основа, наполнитель, жир, приправа.
 * В роль кладут то, что достали. Незакрытая роль бьёт по качеству сильнее
 * плохого продукта — дешёвый жир лучше, чем никакого жира.
 *
 * Сочетание решает **вид** блюда, а не качество: «похлёбка · бобы, овощи» и
 * «похлёбка · репа» — разные блюда для рациона, хоть рецепт один. Котёл
 * варится целиком — поток, а не заказ.
 */

import { useMemo, useState } from "react";
import type { Look, Session } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

//: Роли — ключи cook.role_weights; порядок постоянный, чтобы форма не прыгала.
const ROLES = ["основа", "наполнитель", "жир", "приправа"] as const;

export function Kitchen({ look, session, busy, act }: Props) {
  //: Блюда — рецепты с ролями, которые личность знает. Имена известны из
  //: каталога; для альфы их три, и достаточно списка знаний.
  const блюда = look.knows.filter((имя) =>
    ["Хлеб", "Похлёбка", "Жаркое"].includes(имя),
  );
  const [блюдо, setБлюдо] = useState(блюда[0] ?? "Похлёбка");
  const [filling, setFilling] = useState<Record<string, string>>({});

  //: В роль кладут продукты: что съедобно — решают данные, а не клиент.
  const продукты = useMemo(
    () => [...new Set(look.inventory.filter((t) => t.ingredient).map((t) => t.goods))],
    [look.inventory],
  );

  const закрыто = ROLES.filter((role) => filling[role]).length;

  return (
    <section>
      <h2>Очаг</h2>
      {блюда.length === 0 ? (
        <p className="note">
          Ни одного блюда в личности: рецепты берут в Библиотеке (D-053).
        </p>
      ) : (
        <>
          <div className="row">
            <select value={блюдо} onChange={(e) => setБлюдо(e.target.value)}>
              {блюда.map((имя) => (
                <option key={имя}>{имя}</option>
              ))}
            </select>
            <span className="note">котёл варится целиком</span>
          </div>

          {ROLES.map((role) => (
            <div className="row" key={role}>
              <span className="role-name">{role}</span>
              <select
                value={filling[role] ?? ""}
                onChange={(e) =>
                  setFilling((f) => ({ ...f, [role]: e.target.value }))
                }
              >
                <option value="">— пусто —</option>
                {продукты.map((имя) => (
                  <option key={имя}>{имя}</option>
                ))}
              </select>
            </div>
          ))}

          <button
            onClick={() =>
              act(async () => {
                await session.send("cook.pot", { output: блюдо, filling });
                setFilling({});
              })
            }
            disabled={busy || закрыто === 0}
          >
            Сварить котёл
          </button>
          <p className="note">
            Пустая роль режет качество сильнее плохого продукта. Сочетание решает
            вид блюда — по видам считается разнообразие рациона (D-105, D-128).
            Нужна утварь в кармане: горшок или котёл.
          </p>
        </>
      )}
    </section>
  );
}
