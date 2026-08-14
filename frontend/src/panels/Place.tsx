/**
 * Участок, здание и его обстановка (D-089, D-106, D-116, D-150).
 *
 * Окна разведены по смыслу, а не свалены в одно:
 *
 * - **Участок** — только у ничьего узла: городской выкупают по цене от
 *   удалённости до биопринтера (D-089), дикий занимают. Выкуп выдаёт ценную
 *   бумагу — она видна в сайдбаре, во вкладке «хозяйство» (D-116);
 * - **Здание** — у своего участка: сначала строят, потом обставляют. Станки
 *   занимают площадь (`build.slots_per_area` м² на место), поэтому площадь
 *   дома — это вместимость;
 * - **Станки** — за ними работают; ставит и уносит хозяин (D-150);
 * - **Мебель** — кровать и стеллаж: на них не работают, они обустраивают быт.
 */

import { useState } from "react";
import * as api from "../api";
import type { Bench, Look, Session, Vehicle } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  книга: any;
};

export function Place({ look, session, busy, act, книга }: Props) {
  const мой = Boolean(look.node?.mine);
  const дикий = Boolean(look.node?.wild);
  const ничей = !look.node?.owner;
  const властен = Boolean(look.city?.powers.includes("laws"));
  const цена = look.node?.price ?? null;

  //: Пустой узел: окно «Участок» с выкупом либо занятием. Как только владелец
  //: появился — или если узел не продаётся (городская застройка, жила), —
  //: этого окна нет: дальше живут «Здание», «Станки», «Мебель».
  if (ничей && (дикий || цена !== null)) {
    return (
      <>
        {/* Обоз стоит где угодно: у ничьего узла тоже грузят и распрягают. */}
        <Convoy look={look} session={session} busy={busy} act={act} />
        <section>
        <h2>Участок</h2>
        <p className="note">
          {look.node?.name} · {look.node?.area.toFixed(0)} м² · ничей
        </p>
        {дикий ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.claim"))} disabled={busy}>
              Занять участок
            </button>
            <span className="note">
              Дикая земля занимается присутственно и бесплатно; владение
              оформляется ценной бумагой — она появится в «хозяйстве» (D-116).
            </span>
          </div>
        ) : цена !== null ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              Выкупить за {api.tk(цена)} ₭
            </button>
            <span className="note">
              Цену за м² назначает город; с удалением от биопринтера участок
              дешевеет (D-089). Деньги уходят в казну, вам — ценная бумага.
            </span>
          </div>
        ) : (
          <p className="note">
            Городская земля, но цена не назначена: код-закон `land_price` пуст —
            город пока не продаёт.
          </p>
        )}
        </section>
      </>
    );
  }

  return (
    <>
      {(мой || властен) && <Building look={look} session={session} busy={busy} act={act} />}
      <Foundation look={look} session={session} busy={busy} act={act} />
      <Citizenship look={look} session={session} busy={busy} act={act} />
      <Convoy look={look} session={session} busy={busy} act={act} />
      <Equipment
        заголовок="Станки"
        вещи={look.bench ?? []}
        вид="station"
        look={look}
        session={session}
        busy={busy}
        act={act}
        книга={книга}
        пояснение="За станком работает один: пока идёт партия, второму он не отдаётся (D-150)."
      />
      <Equipment
        заголовок="Мебель"
        вещи={look.furniture ?? []}
        вид="furniture"
        look={look}
        session={session}
        busy={busy}
        act={act}
        книга={книга}
        пояснение="Мебель обустраивает быт: кровать — сон быстрее, стеллаж — хранение. На ней не работают."
      />
    </>
  );
}

/** Здание своего участка: сколько застроено, сколько мест, стройка. */
function Building({ look, session, busy, act }: Omit<Props, "книга">) {
  const дом = look.node?.building;
  const участок = look.node?.area ?? 0;
  const [площадь, setПлощадь] = useState(20);
  if (!дом) return null;

  const свободно = Math.max(0, участок - дом.area);

  return (
    <section>
      <h2>Здание</h2>
      <p className="note">
        {look.node?.name} · участок {участок.toFixed(0)} м²
        {look.node?.owner ? ` · хозяин ${look.node.owner}` : ""}
        {look.node?.cut_off && " · отключён за неуплату"}
      </p>
      {дом.area > 0 ? (
        <p>
          застроено <b>{дом.area.toFixed(0)} м²</b> · мест под оборудование{" "}
          <b>
            {дом.used} из {дом.slots}
          </b>
        </p>
      ) : (
        <p className="note">
          Здания нет — только двор. Станки и мебель ставят в здание: сначала
          строят (D-106).
        </p>
      )}
      {look.node?.mine && свободно > 0 && (
        <div className="row">
          <input
            type="number"
            min={1}
            max={Math.floor(свободно)}
            value={площадь}
            onChange={(e) => setПлощадь(Number(e.target.value))}
            title="площадь пристройки, м²"
          />
          <button
            onClick={() => act(() => session.send("build.construct", { area: площадь }))}
            disabled={busy || площадь <= 0 || площадь > свободно}
          >
            Строить {площадь} м²
          </button>
          <span className="note">
            Материалы — брус, доски, верёвка на каждый метр — спишутся сразу;
            здание встанет по сроку. Свободно {свободно.toFixed(0)} м² двора.
          </span>
        </div>
      )}
    </section>
  );
}

/** Гражданство: одно на человека, вход по уставу, выход с задержкой (D-160).
 *
 * Вступают в администрации — там же, где город принимает всякое решение
 * (D-155), — поэтому окно живёт в локации, а не в сайдбаре. Порядок приёма
 * показан всегда: «свободно», «по заявке» и «по приглашению» ведут себя
 * по-разному, и человек должен понимать, чего ждать.
 */
function Citizenship({ look, session, busy, act }: Omit<Props, "книга">) {
  const город = look.city ?? null;
  const своё = look.citizenship ?? null;
  if (!город?.hall && !своё) return null;

  const порядок: Record<string, string> = {
    open: "принимают свободно",
    application: "по заявке с одобрением",
    invite: "только по приглашению",
  };

  return (
    <section>
      <h2>Гражданство</h2>
      {своё ? (
        <p>
          состоите в <b>{своё.city}</b>
          {своё.leaving_at && (
            <> · выходите: гражданство спадёт {new Date(своё.leaving_at).toLocaleString()}</>
          )}
        </p>
      ) : (
        <p className="note">Вы нигде не состоите: гость платит пошлины, но не налоги.</p>
      )}

      {город?.hall && (
        <div className="row">
          {город.citizen ? (
            <span className="note">Это ваш город.</span>
          ) : город.requested ? (
            <span className="note">
              {город.admission === "invite"
                ? "Вас позвали: примите приглашение."
                : "Заявка подана — ждёт решения власти."}
            </span>
          ) : null}
          {!город.citizen && (
            <button
              onClick={() => act(() => session.send("city.join", {}))}
              disabled={busy || Boolean(своё)}
              title={
                своё
                  ? "гражданство одно на человека: сначала выйти из прежнего города"
                  : порядок[город.admission]
              }
            >
              {город.requested && город.admission === "invite"
                ? "Принять приглашение"
                : "Вступить в граждане"}
            </button>
          )}
          <span className="note">{город.name}: {порядок[город.admission]}</span>
        </div>
      )}

      {своё && !своё.leaving_at && (
        <div className="row">
          <button onClick={() => act(() => session.send("city.leave", {}))} disabled={busy}>
            Выйти из гражданства
          </button>
          <span className="note">
            Выход свободен, но не мгновенен: гражданство спадёт по сроку — чтобы
            нельзя было выйти прямо перед приговором (D-160).
          </span>
        </div>
      )}
    </section>
  );
}

/** Основание города: четыре постройки, а не монета (D-023, D-098, D-159).
 *
 * Окно показывается только там, где основание вообще возможно, — на своём узле
 * планеты вне чужого города. Список недостающего виден **до** попытки: порог
 * входа — постройки, и человек должен понимать, каких именно ему не хватает.
 *
 * Земля при основании уходит городу: дальше её раздаёт власть, а не хозяин
 * двора (D-089), и об этом сказано прямо здесь, а не выясняется потом.
 */
function Foundation({ look, session, busy, act }: Omit<Props, "книга">) {
  const основание = look.foundation ?? null;
  const [имя, setИмя] = useState("");
  if (!основание) return null;

  const готово = основание.missing.length === 0;

  return (
    <section>
      <h2>Основание города</h2>
      <table>
        <tbody>
          {основание.needs.map((нужда) => (
            <tr key={нужда.role}>
              <td>{основание.missing.includes(нужда.role) ? "—" : "✓"}</td>
              <td>{нужда.role}</td>
              <td className="note">{нужда.any_of.join(" · ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <input
          value={имя}
          onChange={(e) => setИмя(e.target.value)}
          placeholder="название города"
          disabled={!готово}
        />
        <button
          onClick={() => act(() => session.send("city.found", { name: имя }))}
          disabled={busy || !готово || !имя.trim()}
        >
          Основать город
        </button>
      </div>
      <p className="note">
        {готово
          ? "Участок станет территорией города: бумага на него погасится, а раздавать землю дальше будет власть (D-089). Основатель получает все полномочия."
          : "Порог входа — постройки, а не монета: город-однодневка не должен стоить одного клика (D-023)."}
      </p>
    </section>
  );
}

/** Обоз: во что впряжён, что везёт и во что можно впрячься здесь (D-157).
 *
 * Груз едет **в трюме**, а не в руках: это единственный способ увезти больше
 * `inventory.carry_mass`. Из рук в трюм и обратно перекладывают присутственно —
 * на ходу трюм закрыт.
 *
 * Отдельным окном от станков намеренно: в повозку не встают работать, в неё
 * впрягаются, и путать эти два действия нельзя.
 */
function Convoy({ look, session, busy, act }: Omit<Props, "книга">) {
  const обоз = look.convoy ?? null;
  const стоят = (look.vehicles ?? []).filter((т) => !т.taken);
  const [сколько, setСколько] = useState<Record<string, number>>({});
  if (!обоз && стоят.length === 0) return null;

  const число = (id: string, всего: number) => сколько[id] ?? всего;
  const задать = (id: string, значение: number) =>
    setСколько((было) => ({ ...было, [id]: значение }));

  //: Что из рук имеет вес: невесомое грузить незачем, оно и так едет.
  const в_руках = look.inventory.filter((вещь) => вещь.mass > 0);

  return (
    <section>
      <h2>Обоз</h2>
      {обоз ? (
        <>
          <p>
            впряжён: <b>{обоз.type_key}</b> · трюм{" "}
            <b>
              {обоз.mass.toFixed(1)} из {обоз.capacity.toFixed(0)} кг
            </b>{" "}
            · скорость ×{обоз.speed_k} · сост. {обоз.condition.toFixed(0)}
          </p>
          {обоз.cargo.length > 0 && (
            <table>
              <tbody>
                {обоз.cargo.map((вещь) => (
                  <tr key={вещь.id}>
                    <td>{вещь.type_key}</td>
                    <td className="note">{вещь.amount.toFixed(1)}</td>
                    <td>
                      <input
                        type="number"
                        min={0}
                        max={вещь.amount}
                        value={число(вещь.id, вещь.amount)}
                        onChange={(e) => задать(вещь.id, Number(e.target.value))}
                      />
                    </td>
                    <td>
                      <button
                        className="quiet"
                        onClick={() =>
                          act(() =>
                            session.send("transport.unload", {
                              item: вещь.id,
                              amount: число(вещь.id, вещь.amount),
                            }),
                          )
                        }
                        disabled={busy}
                        title="выгрузить в руки — сколько поместится"
                      >
                        Выгрузить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {в_руках.length > 0 && (
            <div className="row">
              {в_руках.map((вещь) => (
                <button
                  key={вещь.id}
                  className="quiet"
                  onClick={() =>
                    act(() => session.send("transport.load", { item: вещь.id }))
                  }
                  disabled={busy}
                  title={`${(вещь.mass * вещь.amount).toFixed(1)} кг`}
                >
                  Погрузить: {вещь.goods}
                </button>
              ))}
            </div>
          )}
          <div className="row">
            <button
              onClick={() => act(() => session.send("transport.unharness"))}
              disabled={busy}
            >
              Распрячься
            </button>
            <span className="note">
              Обоз останется стоять здесь вместе с грузом. Бездорожье транспорт
              не пускает вовсе: до найденного разведкой узла телега доедет
              только по дороге (D-107).
            </span>
          </div>
        </>
      ) : (
        <div className="row">
          {стоят.map((телега: Vehicle) => (
            <button
              key={телега.id}
              onClick={() =>
                act(() => session.send("transport.harness", { item: телега.id }))
              }
              disabled={busy}
              title={
                телега.capacity === null
                  ? "вольт не назвал грузоподъёмности"
                  : `${телега.capacity.toFixed(0)} кг · скорость ×${телега.speed_k}`
              }
            >
              Впрячься: {телега.goods}
            </button>
          ))}
          <span className="note">
            Груз едет в трюме, а не в руках: это и есть ответ на предел
            носимого (D-146, D-157).
          </span>
        </div>
      )}
    </section>
  );
}

/** Общее окно оборудования: станки и мебель различаются только видом. */
function Equipment({
  заголовок,
  вещи,
  вид,
  look,
  session,
  busy,
  act,
  книга,
  пояснение,
}: Props & {
  заголовок: string;
  вещи: Bench[];
  вид: "station" | "furniture";
  пояснение: string;
}) {
  const мой = Boolean(look.node?.mine);
  const властен = Boolean(look.city?.powers.includes("laws"));

  //: Что из рук можно поставить здесь: вид — из данных вольта (D-090).
  const в_руках = look.inventory.filter((вещь) =>
    (книга?.recipes ?? []).some(
      (р: any) => р.name === вещь.goods && р.kind === вид,
    ),
  );

  //: Окно молчит там, где сказать нечего: ни вещей в узле, ни своих в руках.
  if (вещи.length === 0 && !((мой || властен) && в_руках.length > 0)) return null;

  const дом = look.node?.building;
  const мест_нет = дом ? дом.used >= дом.slots : true;

  return (
    <section>
      <h2>{заголовок}</h2>
      {вещи.length > 0 && (
        <table>
          <tbody>
            {вещи.map((вещь) => (
              <tr key={вещь.id}>
                <td>{вещь.goods}</td>
                <td className="note">
                  {вещь.quality === null ? "" : `качество ${вещь.quality.toFixed(0)}`}
                  {вещь.condition < 100 && ` · сост. ${вещь.condition.toFixed(0)}`}
                </td>
                <td className="note">
                  {вид === "station"
                    ? вещь.busy
                      ? вещь.mine
                        ? "занят вами"
                        : "занят"
                      : "свободен"
                    : ""}
                </td>
                <td>
                  {(мой || властен) && (
                    <button
                      className="quiet"
                      onClick={() =>
                        act(() => session.send("station.take", { item: вещь.id }))
                      }
                      disabled={busy || вещь.busy}
                      title="забрать в руки"
                    >
                      Забрать
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {(мой || властен) && в_руках.length > 0 && (
        <div className="row">
          {в_руках.map((вещь) => (
            <button
              key={вещь.id}
              onClick={() => act(() => session.send("station.place", { item: вещь.id }))}
              disabled={busy || мест_нет}
              title={
                мест_нет
                  ? "в здании нет места: стройте больше либо уносите лишнее"
                  : "поставить в здание"
              }
            >
              Поставить: {вещь.goods}
            </button>
          ))}
          {мест_нет && (
            <span className="note">в здании нет свободных мест (D-106)</span>
          )}
        </div>
      )}
      <p className="note">{пояснение}</p>
    </section>
  );
}
