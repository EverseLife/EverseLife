/**
 * Хозяйство: городская сеть, аккумуляторы и счета за быт (D-071, D-135, D-149).
 *
 * Вкладка живёт в сайдбаре, а не в локации, по той же причине, по какой там
 * живут ордера: **это деньги, а не материя**. Счёт за узел приходит раз в
 * период, платится откуда угодно и ни от какого места не зависит.
 *
 * Раздел «владения» показывается только тому, у кого владения есть: у
 * большинства их нет, и пустая таблица «ваши узлы: —» была бы шумом.
 *
 * Заряд аккумулятора — единственное присутственное действие здесь, и оно
 * названо присутственным: сервер откажет, если города вокруг нет.
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { DeedView, Holding, Look, Session, Thing } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Grid = { city: string; stored: number; tariff: number };

export function Holdings({ look, session, busy, act }: Props) {
  const [сеть, setСеть] = useState<Grid | null>(null);
  const [владения, setВладения] = useState<Holding[]>([]);
  const [рынок_бумаг, setРынокБумаг] = useState<DeedView[]>([]);
  //: Аккумулятор — станок (D-179): он либо в руках, либо стоит здесь. Оба
  //: заряжаются одинаково, и держать для этого два окна незачем.
  const батареи: { id: string; goods: string; charge: number; где: string }[] = [
    ...look.inventory
      .filter((т: Thing) => т.charge != null)
      .map((т) => ({ id: т.id, goods: т.goods, charge: т.charge!, где: "в руках" })),
    ...(look.bench ?? [])
      .filter((станок) => станок.charge != null)
      .map((станок) => ({
        id: станок.id,
        goods: станок.goods,
        charge: станок.charge!,
        где: "стоит здесь",
      })),
  ];

  const reload = useCallback(async () => {
    const сетьОтвет = await session.send("energy.grid");
    setСеть((сетьОтвет.grid as Grid | null) ?? null);
    const своё = await session.send("utility.holdings");
    setВладения((своё.holdings as Holding[]) ?? []);
    //: Бумаги, которые можно купить: открытые договоры и адресованные мне.
    const бумаги = await session.send("deed.market");
    setРынокБумаг((бумаги.deeds as DeedView[]) ?? []);
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look]);

  const го = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  const долг = владения.reduce((сумма, узел) => сумма + узел.debt, 0);

  return (
    <div>
      <h3>Городская сеть</h3>
      {сеть ? (
        <p className="sign">
          {сеть.city}: в пуле {сеть.stored.toFixed(0)} · тариф {сеть.tariff} ₭ за 100
        </p>
      ) : (
        <p className="note">
          Здесь городской сети нет: вне города работают от аккумулятора, и
          заряжают его в городе.
        </p>
      )}

      <h3>Аккумуляторы</h3>
      {батареи.length === 0 ? (
        <p className="note">
          Аккумулятора нет: энергия либо в пуле города, либо в аккумуляторе.
        </p>
      ) : (
        <table>
          <tbody>
            {батареи.map((батарея) => (
              <tr key={батарея.id}>
                <td>
                  {батарея.goods}
                  <span className="note"> · {батарея.где}</span>
                </td>
                <td className="num">{батарея.charge.toFixed(0)}</td>
                <td>
                  <button
                    onClick={() => го(() => session.send("energy.charge", { item: батарея.id }))}
                    disabled={busy || !сеть || Boolean(look.travel)}
                    title={сеть ? "залить доверху по тарифу" : "здесь нет сети"}
                  >
                    Зарядить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {владения.length > 0 && (
        <>
          <h3>Владения и счета</h3>
          <table>
            <tbody>
              {владения.map((узел) => (
                <tr key={узел.node}>
                  <td>
                    {узел.name}
                    <span className="note"> · {узел.area.toFixed(0)} м²</span>
                    {узел.cut_off && <b> · отключён</b>}
                  </td>
                  <td className="num">
                    {узел.grid
                      ? `${api.tk(узел.cost_per_period)} ₭ / период`
                      : "нет сети"}
                  </td>
                  <td className="num">
                    {узел.debt > 0 ? `долг ${api.tk(узел.debt)} ₭` : "—"}
                  </td>
                  <td>
                    {узел.debt > 0 && (
                      <button
                        onClick={() =>
                          го(() => session.send("utility.pay", { node: узел.node }))
                        }
                        disabled={busy}
                      >
                        Оплатить
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Счёт считается с площади — свет, тепло, вентиляция. Не заплатил —
            узел отключён, и станки в нём стоят, пока долг не закрыт. Отобрать
            узел за долг движок не вправе: это решение суда.
            {долг > 0 && <> Сейчас долгов на {api.tk(долг)} ₭.</>}
          </p>
        </>
      )}

      <Deeds
        мои={look.deeds ?? []}
        рынок={рынок_бумаг}
        busy={busy}
        го={го}
        session={session}
      />

      <p className="note">
        Городские постройки содержит казна: энергия, которую они жгут, — расход
        города, а не посетителя (D-149).
      </p>
    </div>
  );
}

/** Ценные бумаги на участки: электронные документы Сети (D-116).
 *
 * Бумага — владение, оформленное документом: живёт при личности, переживает
 * тело и продаётся договором купли-продажи — всем либо адресно. Деньги и
 * титул переходят одной сделкой, эскроу не нужен. */
function Deeds({
  мои,
  рынок,
  busy,
  го,
  session,
}: {
  мои: DeedView[];
  рынок: DeedView[];
  busy: boolean;
  го: (what: () => Promise<unknown>) => Promise<void>;
  session: Session;
}) {
  const [цены, setЦены] = useState<Record<string, number>>({});
  const [кому, setКому] = useState<Record<string, string>>({});
  if (мои.length === 0 && рынок.length === 0) return null;

  return (
    <>
      <h3>Ценные бумаги</h3>
      {мои.length === 0 ? (
        <p className="note">
          Своих бумаг нет. Бумага появляется с участком: выкупили или заняли
          землю — владение оформлено документом.
        </p>
      ) : (
        <table>
          <tbody>
            {мои.map((бумага) => (
              <tr key={бумага.id}>
                <td>
                  {бумага.name ?? бумага.node}
                  <span className="note">
                    {" "}
                    · {бумага.area?.toFixed(0) ?? "?"} м²
                  </span>
                </td>
                <td className="note">
                  {бумага.sale_price != null
                    ? `продаётся за ${api.tk(бумага.sale_price)} ₭` +
                      (бумага.sale_to ? ` · для ${бумага.sale_to}` : "")
                    : "не продаётся"}
                </td>
                <td>
                  {бумага.sale_price == null ? (
                    <span className="row">
                      <input
                        type="number"
                        min={0}
                        placeholder="цена, ₭"
                        value={цены[бумага.id] ?? ""}
                        onChange={(e) =>
                          setЦены({ ...цены, [бумага.id]: Number(e.target.value) })
                        }
                        title="цена договора, ТК"
                      />
                      <input
                        placeholder="кому (пусто — всем)"
                        value={кому[бумага.id] ?? ""}
                        onChange={(e) =>
                          setКому({ ...кому, [бумага.id]: e.target.value })
                        }
                      />
                      <button
                        className="quiet"
                        onClick={() =>
                          го(() =>
                            session.send("deed.offer", {
                              deed: бумага.id,
                              price: api.minor(цены[бумага.id] ?? 0),
                              to: (кому[бумага.id] ?? "").trim() || undefined,
                            }),
                          )
                        }
                        disabled={busy || !(цены[бумага.id] > 0)}
                      >
                        Продать
                      </button>
                    </span>
                  ) : (
                    <button
                      className="quiet"
                      onClick={() =>
                        го(() =>
                          session.send("deed.offer", { deed: бумага.id, price: 0 }),
                        )
                      }
                      disabled={busy}
                    >
                      Снять с продажи
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {рынок.length > 0 && (
        <>
          <h3>Бумаги на продажу</h3>
          <table>
            <tbody>
              {рынок.map((бумага) => (
                <tr key={бумага.id}>
                  <td>
                    {бумага.name ?? бумага.node}
                    <span className="note">
                      {" "}
                      · {бумага.area?.toFixed(0) ?? "?"} м² · у {бумага.owner}
                    </span>
                  </td>
                  <td className="num">{api.tk(бумага.sale_price ?? 0)} ₭</td>
                  <td>
                    <button
                      onClick={() =>
                        го(() => session.send("deed.buy", { deed: бумага.id }))
                      }
                      disabled={busy}
                    >
                      Купить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="note">
        Бумага — электронный документ: живёт в Сети, переживает тело и
        продаётся отсюда, хоть с дороги. Титул на участок переходит вместе с
        ней (D-116).
      </p>
    </>
  );
}
