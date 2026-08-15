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
  //: Власть распоряжается **городской** землёй, а не всякой: выкупленный
  //: участок стоит на территории города, но хозяин у него человек, и движок
  //: откажет власти так же, как прохожему (`station.may_build`).
  const властен = Boolean(
    look.node?.city && !look.node?.owner && look.city?.powers.includes("laws"),
  );
  const цена = look.node?.price ?? null;

  //: Пустой узел: окно «Участок» с выкупом либо занятием. Как только владелец
  //: появился — или если узел не продаётся (городская застройка, жила), —
  //: этого окна нет: дальше живут «Здание», «Станки», «Мебель».
  if (ничей && (дикий || цена !== null)) {
    return (
      <>
        {/* Обоз стоит где угодно: у ничьего узла тоже грузят и распрягают. */}
        <Convoy look={look} session={session} busy={busy} act={act} />
        {/* Лес ничейного узла рубит любой пришедший (D-177). */}
        <Gather look={look} session={session} busy={busy} act={act} книга={книга} />
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
              Бесплатно и присутственно; бумага на владение появится в
              «хозяйстве» (D-116).
            </span>
          </div>
        ) : цена !== null ? (
          <div className="row">
            <button onClick={() => act(() => session.send("land.buy"))} disabled={busy}>
              Выкупить за {api.tk(цена)} ₭
            </button>
            <span className="note">
              Цена от удалённости до биопринтера (D-089): деньги в казну,
              вам — бумага на землю.
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
      <Plot look={look} session={session} busy={busy} act={act} />
      {(мой || властен) && <Building look={look} session={session} busy={busy} act={act} />}
      <Gather look={look} session={session} busy={busy} act={act} книга={книга} />
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
        пояснение="Мебель обустраивает быт: кровать — сон быстрее, сундук — хранение. На ней не работают."
      />
      <Storages look={look} session={session} busy={busy} act={act} />
    </>
  );
}

/** Хранилища узла: сундук, стеллаж и всё, у чего в вольте есть вместимость.
 *
 * Сам сундук виден всякому — он стоит в комнате. Открыть его вправе тот, кто
 * распоряжается узлом: хозяин, а на городской земле — власть (D-181). Предел
 * один и тот же, что у рук и у трюма, — килограммы.
 */
function Storages({ look, session, busy, act }: Omit<Props, "книга">) {
  const [сколько, setСколько] = useState<Record<string, number>>({});
  const сундуки = look.storages ?? [];
  if (сундуки.length === 0) return null;

  const число = (id: string, всего: number) => сколько[id] ?? всего;
  const задать = (id: string, значение: number) =>
    setСколько((было) => ({ ...было, [id]: значение }));
  //: Класть имеет смысл всё, что в руках: невесомого в этом мире нет.
  const в_руках = look.inventory;

  return (
    <>
      {сундуки.map((сундук) => (
        <section key={сундук.id}>
          <h2>{сундук.goods}</h2>
          <p className="note">
            занято {сундук.mass.toFixed(1)} из {сундук.capacity.toFixed(0)} кг
          </p>
          {!сундук.mine ? (
            <p className="note">Чужое хранилище: что внутри — не ваше дело.</p>
          ) : (
            <>
              {сундук.content.length > 0 && (
                <table>
                  <tbody>
                    {сундук.content.map((вещь) => (
                      <tr key={вещь.id}>
                        <td>{вещь.goods}</td>
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
                                session.send("storage.take", {
                                  storage: сундук.id,
                                  item: вещь.id,
                                  amount: число(вещь.id, вещь.amount),
                                }),
                              )
                            }
                            disabled={busy}
                            title="забрать в руки — сколько унесёте"
                          >
                            Забрать
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {сундук.content.length === 0 && <p className="note">пусто</p>}
              {в_руках.length > 0 && (
                <div className="row">
                  {в_руках.map((вещь) => (
                    <button
                      key={вещь.id}
                      className="quiet"
                      onClick={() =>
                        act(() =>
                          session.send("storage.put", {
                            storage: сундук.id,
                            item: вещь.id,
                          }),
                        )
                      }
                      disabled={busy}
                      title={`${(вещь.mass * вещь.amount).toFixed(1)} кг`}
                    >
                      Положить: {вещь.goods}
                    </button>
                  ))}
                </div>
              )}
              <p className="note">
                Дом хранит то, что не увезти в руках; полный сундук не уносят
                (D-181).
              </p>
            </>
          )}
        </section>
      ))}
    </>
  );
}

/** Занятый участок: чей он и как называется (D-178).
 *
 * Владение — публичный факт: вошедший видит хозяина, кем бы тот ни был,
 * человеком или городом. Имя даёт тот, кто землёй распоряжается, — и меняется
 * при этом подпись на карте, а не ключ узла: на ключ ссылаются бумаги и рёбра.
 */
function Plot({ look, session, busy, act }: Omit<Props, "книга">) {
  const узел = look.node;
  const [имя, setИмя] = useState("");
  if (!узел || (!узел.owner && !узел.owner_city)) return null;

  const чей = узел.mine
    ? "ваш участок"
    : узел.owner
      ? `хозяин ${узел.owner}`
      : `земля города ${узел.owner_city}`;

  return (
    <section>
      <h2>Участок</h2>
      <p className="note">
        {узел.name} · {узел.area.toFixed(0)} м² · {чей}
        {узел.cut_off && " · отключён за неуплату"}
      </p>
      {узел.may_name && (
        <div className="row">
          <input
            value={имя}
            onChange={(e) => setИмя(e.target.value)}
            placeholder={узел.name}
            //: Повторяет `runtime.LAND_NAME_LIMIT`: предел лучше показать
            //: полем ввода, чем сообщить отказом после нажатия.
            maxLength={40}
            title="как называть это место"
          />
          <button
            onClick={() =>
              act(async () => {
                await session.send("land.rename", { name: имя });
                setИмя("");
              })
            }
            disabled={busy || !имя.trim() || имя.trim() === узел.name}
          >
            Переименовать
          </button>
          <span className="note">
            Имя увидят все на карте; ключ участка не меняется (D-178).
          </span>
        </div>
      )}
    </section>
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
            Материалы спишутся сразу, здание встанет по сроку. Свободно{" "}
            {свободно.toFixed(0)} м² двора.
          </span>
        </div>
      )}
    </section>
  );
}

/** Русские заголовки признаков места: пока признак один — лес. */
const МЕСТА: Record<string, string> = { лес: "Лес" };

/** Добыча места (D-177): рубка леса — и будущие сборы — без станка.
 *
 * Показывается там, где у узла есть признак («лес») и земля своя либо ничья:
 * чужой лес принадлежит хозяину. Партия идёт обычным крафтом — время и
 * инструмент из вольта, готовое видно в «делах».
 */
function Gather({ look, session, busy, act, книга }: Props) {
  const [сколько, setСколько] = useState(10);
  const узел = look.node;
  if (!узел) return null;
  const доступно = узел.mine || узел.wild;
  const операции = (книга?.operations ?? []).filter(
    (о: any) => о.place && (узел.features ?? []).includes(о.place),
  );
  if (!доступно || операции.length === 0) return null;

  //: Чем закрыть требование: сам предмет либо любой из класса («Топор»).
  const в_руках = new Set(look.inventory.map((вещь) => вещь.goods));
  const есть_чем = (чем: string) =>
    в_руках.has(чем) ||
    ((книга?.tool_classes?.[чем] ?? []) as string[]).some((и) => в_руках.has(и));

  return (
    <>
      {операции.map((операция: any) => (
        <section key={операция.name}>
          <h2>{МЕСТА[операция.place] ?? операция.place}</h2>
          <div className="row">
            <input
              type="number"
              min={1}
              value={сколько}
              onChange={(e) => setСколько(Number(e.target.value))}
              title="сколько добыть"
            />
            {(операция.gives as string[]).map((выход) => {
              const годится = (операция.requires as string[]).every(есть_чем);
              return (
                <button
                  key={выход}
                  onClick={() =>
                    act(() =>
                      session.send("craft.start", { output: выход, units: сколько }),
                    )
                  }
                  disabled={busy || сколько <= 0 || !годится}
                  title={
                    годится
                      ? `партия пойдёт временем, готовое — в «делах»`
                      : `нужен: ${(операция.requires as string[]).join(", ")}`
                  }
                >
                  {операция.name}: {выход}
                </button>
              );
            })}
            <span className="note">
              Нужен {(операция.requires as string[]).join(", ")}; партия идёт
              временем, готовое забирается в «делах».
            </span>
          </div>
        </section>
      ))}
    </>
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
  //: Только в администрации: и вступают, и выходят присутственно (D-155).
  if (!город?.hall) return null;

  const порядок: Record<string, string> = {
    open: "принимают свободно",
    application: "по заявке с одобрением",
    invite: "только по приглашению",
  };
  //: Гражданство, взятое условием печати, до срока не складывается (D-184).
  const связан = Boolean(
    своё?.bound_until && new Date(своё.bound_until) > new Date(),
  );

  return (
    <section>
      <h2>Гражданство</h2>
      {своё ? (
        <p>
          состоите в <b>{своё.city}</b>
          {своё.leaving_at && (
            <> · выходите: гражданство спадёт {new Date(своё.leaving_at).toLocaleString()}</>
          )}
          {/* Обязательство, принятое при печати (D-184): срок виден заранее,
              а не открывается отказом при попытке выйти. */}
          {связан && (
            <> · обязательство до {new Date(своё.bound_until!).toLocaleString()}</>
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
          <button
            onClick={() => act(() => session.send("city.leave", {}))}
            disabled={busy || связан}
            title={
              связан
                ? "срок обязательства вы приняли, выбрав дверь этого города"
                : "заявление уходит по Сети"
            }
          >
            Выйти из гражданства
          </button>
          <span className="note">
            {связан
              ? "Обязательство печати держит до своего срока (D-184)."
              : "Выход не мгновенен: гражданство спадёт по сроку (D-160)."}
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
          ? "Земля отойдёт городу, основатель получит все полномочия (D-089)."
          : "Порог входа — постройки, а не монета (D-023)."}
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
              Обоз останется здесь с грузом; по бездорожью он не идёт (D-107).
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
            Груз едет в трюме, а не в руках (D-146, D-157).
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
  //: Ставит и уносит хозяин, а на городской земле — власть (`station.may_build`).
  //: В чужом доме не вправе ни тот, ни другой.
  const властен = Boolean(
    look.node?.city && !look.node?.owner && look.city?.powers.includes("laws"),
  );

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
                  {/* У аккумулятора состояние — это заряд, а не «занят»:
                      за ним не работают, он хранит энергию (D-179). */}
                  {вещь.charge !== null
                    ? `заряд ${вещь.charge.toFixed(0)} · заряжают в «хозяйстве»`
                    : вид === "station"
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
