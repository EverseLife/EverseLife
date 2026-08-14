/**
 * Разговор с сервером.
 *
 * Две поверхности, и они разные не случайно:
 *
 * - `/public/*` — чтение, доступное всем: справочники, ступени, стаканы.
 *   Цены знают все (D-047), и прятать их незачем;
 * - `/session/ws` — единственное место, где игрок **действует**. Удобного REST
 *   для «сделать удар» нет и не будет: он превратил бы добычу в скрипт
 *   (60-meta/01-anti-cheat).
 *
 * Протокол скучный: одна команда — один ответ. Отсюда очередь ниже.
 */

//: Адрес сервера по умолчанию — тот же хост, откуда открыта страница. Иначе
//: зашедший по локальной сети искал бы сервер у себя на телефоне: `localhost`
//: у каждого свой. Явный `VITE_API` перекрывает это, когда сервер не рядом.
//:
//: В разработке сервер живёт отдельным портом, в бою — за одним источником с
//: клиентом, на пути `/api`: так собранный образ не знает боевого домена и
//: годится любому, а сокет получает `wss://` без отдельной настройки.
const HTTP =
  import.meta.env.VITE_API ??
  (import.meta.env.PROD
    ? `${window.location.origin}/api`
    : `${window.location.protocol}//${window.location.hostname}:8000`);
const WS = HTTP.replace(/^http/, "ws") + "/session/ws";

export type Thing = {
  id: string;
  goods: string;
  amount: number;
  quality: number | null;
  tier: string;
  condition: number;
  /** Вид блюда: сочетание решает вид, а не качество (D-128). */
  flavor: string | null;
  /** Съедобность — из данных вольта, а не из догадок клиента. */
  food: boolean;
  /** Годится в котёл: продукт, а не кирка (16-cooking). */
  ingredient: boolean;
  spoils_at: string | null;
  /** Проба монеты в тысячных: качества у монеты нет, есть металл (D-016). */
  fineness: number | null;
  /** Клеймо: чья это работа (D-058). */
  maker: string | null;
  /** У семян: сорт и сила партии, % (D-057). */
  variety: string | null;
  vigor: number | null;
  /** У аккумулятора: заряд с учётом саморазряда (D-071). */
  charge: number | null;
  /** Вес единицы, кг, и слот, если это снаряжение (D-146). */
  mass: number;
  slot: string | null;
};

/** Носимое: сколько несёт, сколько может и что надето (D-146). */
export type Carry = {
  load: number;
  capacity: number;
  slots: string[];
  equipped: Record<string, { id: string; goods: string }>;
};

/** Транспорт, стоящий в узле: в него впрягаются, а не встают за него (D-157). */
export type Vehicle = {
  id: string;
  goods: string;
  condition: number;
  /** Грузоподъёмность трюма, кг. Пусто — вольт её не назвал. */
  capacity: number | null;
  /** Множитель к скорости пешего: тачка медленнее ног, повозка быстрее. */
  speed_k: number;
  /** Занят чужой упряжкой. */
  taken: boolean;
};

/** Обоз: во что впряжён и что везёт (D-157). */
export type Convoy = {
  id: string;
  type_key: string;
  condition: number;
  capacity: number;
  /** Сколько уже везёт, кг. */
  mass: number;
  speed_k: number;
  heavy: boolean;
  cargo: { id: string; type_key: string; amount: number; quality: number | null }[];
};

/** Дорога как работа на ребре (D-107, D-158). */
export type RoadWork = {
  edge: string;
  /** Куда ведёт. */
  to: string;
  surface: "trail" | "road" | "paved";
  /** Состояние покрытия 0…100: без содержания зарастает. */
  condition: number;
  seconds: number;
  /** Следующая ступень, либо пусто у тракта. */
  next: "road" | "paved" | null;
  /** Сколько полотна возьмёт укладка ступени, и сколько — подсыпка. */
  needs: number | null;
  mend_needs: number | null;
  /** Сколько полотна в руках прямо сейчас. */
  at_hand: number;
  working: boolean;
};

/** Куда отсюда можно, сколько это стоит времени и сколько — тела (D-147). */
export type Exit = {
  key: string;
  name: string;
  surface: "trail" | "road" | "paved";
  seconds: number;
  /** Расход выносливости на дорогу. С транспортом — ноль. */
  stamina: number;
};

/** Пока идёшь — тебя нет: всё присутственное закрыто (D-107). */
export type Transit = {
  to: string;
  to_key: string;
  from_key: string;
  started_at: string;
  arrives_at: string;
  /** Автопуть (D-045): конечная цель маршрута, если она дальше этого отрезка. */
  final?: string;
  final_key?: string;
  legs_left?: number;
};

/** Карта мира: узлы и рёбра. Города и магистрали публичны (D-097). */
export type MapNode = {
  key: string;
  name: string;
  /** Слой показа: мир — один граф, слои — способ на него смотреть (D-045). */
  layer: "space" | "planet" | "city" | "location";
  /** Группа, в которую узел входит: локация → город → планета. */
  parent: string | null;
  ring: number | null;
  exit: boolean;
};
export type MapEdge = { a: string; b: string; surface: Exit["surface"]; seconds: number };
export type WorldMap = { nodes: MapNode[]; edges: MapEdge[] };

/** Реплика, как её слышит стоящий в локации (D-043, D-050). */
export type ChatLine = {
  id: string;
  who: string;
  kind: "speech" | "action" | "ooc";
  quiet: boolean;
  text: string;
  overheard: boolean;
  source: string | null;
  at: string;
};

/** Кружок: состав виден, содержание — нет. */
export type Circle = { id: string; name: string | null; members: string[]; mine: boolean };

/** Во что обойдётся заход разведки отсюда (D-156).
 *
 * Цена — свойство места, а не игрока: нехоженая окрестность отдаёт находку за
 * минуты, исхоженная — за часы и не всегда. Показывается до выхода, иначе
 * читается как случайность движка. */
export type Outlook = {
  /** Сколько находок уже сделано от этого узла. */
  explored: number;
  minutes: { min: number; max: number };
  /** Наибольшая цена выносливостью — по самому долгому заходу. */
  stamina: number;
  chance: number;
};

export type Look = {
  identity: string;
  money: string;
  knows: string[];
  /** Взятая агротехника: культуры, чью норму личность уже изучила (D-057). */
  agrotech: string[];
  orders: Order[];
  reservations: Reservation[];
  batches: Batch[];
  carry?: Carry;
  body: {
    id: string;
    stamina: number;
    sleeping_since: string | null;
    sleeping_home: boolean;
    /** До этого момента расход выносливости снижен: обед, а не бафф (D-119). */
    satiated_until: string | null;
  } | null;
  node: {
    key: string;
    name: string;
    layer: "space" | "planet" | "city" | "location";
    library: boolean;
    stations: string[];
    /** Свойства-признаки места («лес», «выход»): по ним видна добыча места (D-177). */
    features: string[];
    fertility: number;
    /** Чей участок: хозяйство ведёт владелец (06-farming). */
    owner: string | null;
    mine: boolean;
    city: boolean;
    /** Ничей и дикий: такой занимают присутственно (D-152). */
    wild: boolean;
    /** Отключён за неуплату: станки не работают (D-149). */
    cut_off: boolean;
    /** Площадь участка, м² (D-125). */
    area: number;
    /** Здание и вместимость: станок занимает площадь (D-106). */
    building: { area: number; slots: number; used: number };
    /** Цена выкупа пустого городского участка, минорными единицами (D-089). */
    price: number | null;
  } | null;
  /** Город, на территории которого стоим, и свои права в нём (D-154, D-155). */
  city?: {
    id: string;
    name: string;
    node: string;
    /** Права строками: крупные (`treasury`) и точечные (`law:import_duty`). */
    powers: string[];
    /** Здесь ли стоит администрация: решения принимаются в ней (D-155). */
    hall: boolean;
    /** Гражданин ли этого города (D-160). */
    citizen: boolean;
    /** Как принимают: свободно, по заявке либо по приглашению. */
    admission: "open" | "application" | "invite";
    /** Заявка подана либо приглашение получено — ждёт своей стороны. */
    requested: boolean;
  } | null;
  /** Где состоит личность: гражданство одно и видно отовсюду (D-160). */
  citizenship?: {
    city: string | null;
    since: string;
    /** Заявление о выходе подано: гражданство спадёт к этому сроку. */
    leaving_at: string | null;
  } | null;
  /** Идущий заход разведки, если он есть (D-152). */
  survey?: { returns_at: string } | null;
  /** Можно ли здесь основать город и чего для этого не хватает (D-159).
   *  Пусто — место не годится: чужая земля, чужой город либо не планета. */
  foundation?: {
    missing: string[];
    needs: { role: string; any_of: string[] }[];
  } | null;
  /** Свой обоз: во что впряжён и что везёт (D-157). */
  convoy?: Convoy | null;
  /** Транспорт, стоящий в этом узле: впрягаются в то, что рядом. */
  vehicles?: Vehicle[];
  /** Тела нет — личность в облаке: где печататься и идёт ли печать (D-033). */
  printers?: Printer[];
  printing?: { ready_at: string } | null;
  /** Станки узла поимённо: за какой можно встать прямо сейчас (D-150). */
  bench?: Bench[];
  /** Мебель узла: кровать и стеллаж — не станки, окно у них своё (D-090). */
  furniture?: Bench[];
  /** Свои ценные бумаги на участки: электронные документы Сети (D-116). */
  deeds?: DeedView[];
  inventory: Thing[];
  stall?: Thing[];
  veins?: { id: string; resource: string; richness: number }[];
  exits?: Exit[];
  travel?: Transit | null;
  /** Открытый забой переживает уход игрока: вернулся — сессия на месте. */
  mining?: Sight | null;
};

/** Ценная бумага на участок: владение, оформленное документом (D-116). */
export type DeedView = {
  id: string;
  node: string | null;
  name: string | null;
  area: number | null;
  owner: string | null;
  /** Почём выдана: цена выкупа, ноль у занятой дикой земли. */
  paid: number;
  /** Выставлена на продажу: цена и адресат, если договор адресный. */
  sale_price: number | null;
  sale_to: string | null;
  issued_at: string;
};

/** Станок в узле: за станком работает один (D-150). */
export type Bench = {
  id: string;
  goods: string;
  quality: number | null;
  condition: number;
  busy: boolean;
  mine: boolean;
};

/** Дверь в мир: где напечатать тело и почём (D-028, D-033). */
export type Printer = {
  node: string;
  name: string;
  city: string | null;
  /** Тот самый вечный принтер: бесплатно, но двенадцать часов. */
  precursor: boolean;
  energy: number;
  iron: number;
  cost: number;
  minutes: number;
  iron_here: number;
  /** Город печатает за свой счёт: код-закон `body_print` (D-032). */
  at_city_expense: boolean;
};

/** Свой узел и счёт за быт (D-149). */
export type Holding = {
  node: string;
  name: string;
  area: number;
  /** Есть ли городская сеть: вне города счетов нет вовсе. */
  grid: boolean;
  energy_per_period: number;
  cost_per_period: number;
  debt: number;
  cut_off: boolean;
  last_energy: number;
};

/** Действующий код-закон города: своё решение либо умолчание вольта (D-130). */
export type Law = {
  name: string;
  unit: string | null;
  note: string | null;
  value: string | null;
  own: boolean;
};

export type Office = {
  id: string;
  who: string;
  identity: string;
  title: string;
  powers: string[];
};

/** Сводка города: устав, законы, должности, казна (D-154). */
export type CityView = {
  id: string;
  name: string;
  node: string;
  treasury: number;
  offices: Office[];
  charter: Record<string, string>;
  charter_params: Record<string, number>;
  /** Вопросы устава словами: текст живёт в вольте, а не в клиенте (D-130). */
  charter_questions: {
    id: string;
    section: string;
    question: string;
    options: { id: string; label: string }[];
  }[];
  laws: Record<string, Law>;
  powers: string[];
  /** Здесь ли принимаются решения: власть присутственна (D-155). */
  at_hall: boolean;
  lots: { key: string; name: string; area: number; owner: string | null; free: boolean }[];
  citizens: string[];
};

/** Экономическая панель города (D-124, D-140). Публичный срез виден всем. */
/** Дело в городском суде (D-166). */
export type CourtCase = {
  id: string;
  plaintiff: string | null;
  defendant: string | null;
  claim: string;
  state: "open" | "judged" | "dismissed";
  verdict: string | null;
  opened_at: string;
};

/** Примитив санкции из вольта: движок исполняет не все (D-166). */
export type SanctionKind = { id: string; name: string; enforced: boolean };

/** Идущее голосование граждан (D-161). */
export type CityVote = {
  id: string;
  kind: "law" | "election" | "recall" | "charter" | "council";
  /** Кто голосует: все граждане либо члены совета (D-164). */
  voters: "citizens" | "council";
  law: string | null;
  value: unknown;
  /** Кандидаты на выборах: выдвигаются сами, пока голосование идёт (D-162). */
  candidates: { id: string; name: string | null; votes: number }[];
  /** За кого отдан свой голос на выборах. */
  choice: string | null;
  closes_at: string;
  threshold: "simple" | "two_thirds" | "unanimous";
  /** Доля имеющих право, нужная для кворума; 0 — кворум не требуется. */
  quorum: number;
  electorate: number;
  yes: number;
  no: number;
  /** Свой голос, если подан. */
  mine: boolean | null;
  may_vote: boolean;
};

export type CityPanel = {
  city: string;
  window_hours: number;
  at: string;
  /** Без администрации город слеп: данные не обновляются. */
  blind: boolean;
  full: boolean;
  market: { trades: number; volume: number; prices: Record<string, number> };
  people: { here: number; printed: number };
  production: {
    mined: Record<string, number>;
    harvested: number;
    crafted: Record<string, number>;
  };
  energy: { stored: number; tariff: number; spent_work: number; spent_home: number };
  goods: Record<string, number>;
  /** Ввоз, вывоз, ходки и собранная пошлина за окно (D-123, D-124). */
  trade: {
    imported: Record<string, number>;
    exported: Record<string, number>;
    trips_in: number;
    trips_out: number;
    duty_collected: number;
  };
  treasury?: {
    balance: number;
    collected: Record<string, number>;
    spent: Record<string, number>;
  };
};

/** Крупные права. Точечные — `law:<id>` — собираются из каталога законов. */
export const POWERS: Record<string, string> = {
  laws: "все законы",
  charter: "устав",
  treasury: "казна",
  offices: "должности",
  land: "участки",
  dashboard: "панель города",
  justice: "суд",
};

/** Право на один закон: `law:import_duty` (D-155). */
export const LAW_SCOPE = "law:";

export const SURFACE: Record<Exit["surface"], string> = {
  trail: "бездорожье",
  road: "дорога",
  paved: "тракт",
};

/** Время в пути словами: секунды для шага по городу, минуты для дороги. */
export function spell(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} с`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} мин`;
  return `${(seconds / 3600).toFixed(1)} ч`;
}

export type Order = {
  id: string;
  side: "buy" | "sell";
  goods: string;
  tier: string;
  price: number;
  left: number;
};

export type Batch = {
  id: string;
  work: "make" | "repair" | "recycle";
  output: string;
  units: number;
  quality: number;
  ready_at: string;
};

/** Всё, что игрок видит о забое. Устойчивости свода здесь нет и быть не может. */
export type Sight = {
  sign: string;
  mined: number;
  swings: number;
  timbers: number;
  stamina: number;
  pace: "steady" | "fast";
  state: "active" | "left" | "collapsed";
  session: string;
};

export type Plan = {
  output: string;
  units: number;
  quality: number;
  spread: number;
  ceiling: number;
  accuracy: number;
  waste: number;
  minutes: number;
  consumes: Record<string, number>;
  /** Промышленный уклад: партия идёт на автомате (D-035). */
  auto: boolean;
  /** Сколько энергии съест автомат и во что это обойдётся по тарифу города. */
  energy: number;
  energy_cost: number;
};

/** Бронь: единственный способ купить удалённо — с задатком и сроком (D-047). */
export type Reservation = {
  id: string;
  goods: string;
  tier: string;
  amount: number;
  price: number;
  deposit: number;
  node: string;
  node_key: string;
  expires_at: string;
};

export type Level = { price: number; amount: number };
export type Book = {
  node: string;
  type_key: string;
  tier: string;
  bids: Level[];
  asks: Level[];
  last: number | null;
  spread: number | null;
};

export class Refused extends Error {}

type Waiting = {
  resolve: (answer: Record<string, unknown>) => void;
  reject: (error: Error) => void;
};

/** Сессия клиента. Держит сокет и очередь «команда → ответ».
 *
 * Сокет живёт не вечно: сервер и прокси режут простой. Порванная сессия
 * поднимается сама — команда, заставшая мёртвый сокет, сначала
 * переподключается и опознаётся прежним именем, и только потом уходит.
 */
export class Session {
  private socket: WebSocket | null = null;
  private queue: Waiting[] = [];
  private reviving: Promise<void> | null = null;
  account = "";
  name = "";

  /** Поднять сокет. Опознание — отдельным шагом: новичка ещё некем опознать. */
  private async connect(): Promise<void> {
    await this.close();
    const socket = new WebSocket(WS);
    this.socket = socket;

    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error("сервер не отвечает"));
    });

    socket.onmessage = (event) => {
      const waiting = this.queue.shift();
      if (!waiting) return;
      const answer = JSON.parse(event.data);
      if (typeof answer.refused === "string") {
        waiting.reject(new Refused(answer.refused));
      } else {
        waiting.resolve(answer);
      }
    };
    socket.onclose = () => {
      this.queue.forEach((w) => w.reject(new Error("сессия закрыта")));
      this.queue = [];
    };
  }

  async open(name: string): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("hello", name);
  }

  /** Новый игрок: личности ещё нет — её печатают у биопринтера (D-153). */
  async create(name: string): Promise<Record<string, unknown>> {
    await this.connect();
    return this.greet("join", name);
  }

  private async greet(cmd: string, name: string): Promise<Record<string, unknown>> {
    const hello = await this.send(cmd, { name });
    this.account = String(hello.account ?? "");
    this.name = String(hello.hello ?? name);
    return hello;
  }

  async send(cmd: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      //: Опознаться пока некем: сессии ещё не было, чинить нечего.
      if (!this.name) throw new Error("нет сессии");
      await this.revive();
    }
    const socket = this.socket!;
    return new Promise((resolve, reject) => {
      this.queue.push({ resolve, reject });
      socket.send(JSON.stringify({ cmd, ...args }));
    });
  }

  /** Поднять порванную сессию заново. Один подъём на всех, кто его застал. */
  private revive(): Promise<void> {
    this.reviving ??= (async () => {
      try {
        await this.connect();
        await this.greet("hello", this.name);
      } finally {
        this.reviving = null;
      }
    })();
    return this.reviving;
  }

  async look(): Promise<Look> {
    const answer = await this.send("look");
    return answer.look as Look;
  }

  async close(): Promise<void> {
    this.socket?.close();
    this.socket = null;
  }
}

async function read<T>(path: string): Promise<T> {
  const answer = await fetch(HTTP + path);
  if (!answer.ok) throw new Error(`${path}: ${answer.status}`);
  return answer.json();
}

export const constants = () => read<{ digest: string; values: Record<string, any> }>(
  "/public/constants",
);
export const recipes = () => read<any>("/public/recipes");
export const tiers = () => read<{ tiers: { from: number; to: number; name: string }[] }>(
  "/public/quality/tiers",
);
export const worldMap = () => read<WorldMap>("/public/map");
export const plants = () =>
  read<{
    plants: {
      id: string;
      name: string;
      gives: string;
      /** Чем сеют: семена — предмет, отдельный от урожая (D-057). */
      seed: string;
      cycle_days: number;
    }[];
  }>("/public/plants");
export const positions = (node: string) =>
  read<{ node: string; positions: { goods: string; tier: string }[] }>(
    `/public/market/${encodeURIComponent(node)}`,
  );
export const book = (node: string, goods: string, tier: string) =>
  read<Book>(
    `/public/market/${encodeURIComponent(node)}/book` +
      `?goods=${encodeURIComponent(goods)}&tier=${encodeURIComponent(tier)}`,
  );

/** Деньги приходят минорными единицами: 1 ТК = 10 000. Копейка не теряется. */
export const MONEY_SCALE = 10_000;
export const tk = (minor: number) => (minor / MONEY_SCALE).toFixed(2).replace(/\.?0+$/, "");
export const minor = (tk: number) => Math.round(tk * MONEY_SCALE);
