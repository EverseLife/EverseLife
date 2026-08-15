/**
 * Карта — один граф, четыре слоя показа (D-045, D-097).
 *
 * Вся вселенная — граф локаций, и ходят только по нему. Слои — абстракция
 * показа, чтобы в графе можно было ориентироваться:
 *
 * - **Космос** — планеты и корабли;
 * - **Планета** — города и крупные одиночные локации;
 * - **Город** — застройка: кольца вокруг биопринтера;
 * - **Локация** — субузлы: этажи дома, комнаты комплекса.
 *
 * Узел верхнего слоя — представитель своей группы: клик по «Столице Терры» на
 * планетном слое раскрывает её застройку, а дорога «в столицу» на самом деле
 * ведёт в конкретный узел-вход. Рёбра между группами проецируются на
 * представителей — граф при этом остаётся одним и тем же.
 *
 * ## Карта физическая
 *
 * Узлы — тела в силовой симуляции, как граф в Obsidian: рёбра — пружины,
 * узлы расталкиваются, и живую раскладку можно поправить руками — схватить
 * узел и перетащить, если он лёг неудачно. Фон таскается панорамой, колесо
 * приближает. Клик остаётся кликом: короткое нажатие без движения — это
 * «раскрыть» либо «идти».
 *
 * Пока идёшь, точка ползёт по ребру, и войти никуда нельзя. Дошёл — «Войти».
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { Hint } from "../Hint";
import {
  SURFACE,
  spell,
  type Look,
  type MapNode,
  type Outlook,
  type RoadWork,
  type Session,
  type WorldMap,
} from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  onEnter: () => void;
};

const W = 880;
const H = 540;

const LAYERS = [
  { id: "space", label: "космос" },
  { id: "planet", label: "планета" },
  { id: "city", label: "город" },
  { id: "location", label: "локация" },
] as const;
type LayerId = (typeof LAYERS)[number]["id"];

/** Тело узла в симуляции: положение, скорость и «прибит ли рукой». */
type Mass = { x: number; y: number; vx: number; vy: number; pinned: boolean };

//: Ручки физики. Числа интерфейса, а не мира: балансу они не принадлежат.
//: Пружина мягкая, затухание сильное, скорость ограничена: раскладка
//: расползается плавно, а не скачками.
const SPRING = 0.014;
const REPULSE = 7500;
const REPULSE_RADIUS = 250;
const CENTERING = 0.005;
const DAMPING = 0.8;
const SLEEP_SPEED = 0.02;
const MAX_SPEED = 4;

/**
 * Память раскладки: узел, однажды улёгшийся, помнит своё место по ключу —
 * через смену слоя, уход с таба карты и дорогу. Пересеивание с нуля выглядит
 * как «карту перетрясло», и случается оно теперь только с по-настоящему
 * новыми узлами. Живёт на уровне модуля: жизнь компонента короче жизни клиента.
 */
const REMEMBERED = new Map<string, { x: number; y: number }>();

/** Засеянная стартовая точка: одна и та же карта просыпается похоже. */
function seedPoint(key: string): { x: number; y: number } {
  let seed = 0;
  for (const ch of key) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
  return {
    x: W / 2 + Math.cos(seed) * 150,
    y: H / 2 + Math.sin(seed * 7) * 120,
  };
}

const DASH: Record<string, string | undefined> = { trail: "4 6" };

export function GraphMap({ look, session, busy, act, onEnter }: Props) {
  const [map, setMap] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: Карта растёт разведкой (D-152), и найденный узел обязан появиться сам.
  //: Перечитываем её, когда меняется то, из-за чего карта могла измениться:
  //: свой узел, набор выходов из него и возвращение разведчика. Одной
  //: загрузки при первом показе хватало ровно до первой находки.
  const выходы = (look.exits ?? []).map((путь) => путь.key).join("|");
  const в_разведке = look.survey?.returns_at ?? "";
  useEffect(() => {
    void api.worldMap().then(setMap);
  }, [here, выходы, в_разведке]);
  const идёт = look.travel ?? null;
  const byKey = useMemo(() => {
    const out: Record<string, MapNode> = {};
    for (const node of map?.nodes ?? []) out[node.key] = node;
    return out;
  }, [map]);

  /** Представитель узла на слое: подъём по родителям до узла этого слоя. */
  const repr = useMemo(() => {
    return (key: string, layer: LayerId): string | null => {
      let cursor: MapNode | undefined = byKey[key];
      while (cursor) {
        if (cursor.layer === layer) return cursor.key;
        cursor = cursor.parent ? byKey[cursor.parent] : undefined;
      }
      return null;
    };
  }, [byKey]);

  //: Слой по умолчанию — тот, где стоишь; явное раскрытие живёт до перехода.
  const [layer, setLayer] = useState<LayerId | null>(null);
  const [cityFocus, setCityFocus] = useState<string | null>(null);
  useEffect(() => setCityFocus(null), [here]);

  const города = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) {
      if (node.layer === "city" && node.parent) out.add(node.parent);
    }
    return out;
  }, [map]);
  const мойГород = byKey[repr(here, "city") ?? ""]?.parent ?? null;
  const фокус = cityFocus ?? мойГород ?? [...города].sort()[0] ?? null;

  const базаЛокации =
    byKey[here]?.layer === "location" ? (byKey[here]?.parent ?? here) : here;
  const субузлыЕсть = useMemo(
    () =>
      (map?.nodes ?? []).some(
        (node) => node.layer === "location" && node.parent === базаЛокации,
      ),
    [map, базаЛокации],
  );

  const слои = LAYERS.filter(
    (option) =>
      (option.id !== "location" || субузлыЕсть) &&
      (option.id !== "city" || города.size > 0),
  );
  const желаемый: LayerId =
    layer ?? ((byKey[here]?.layer as LayerId | undefined) ?? "planet");
  const текущийСлой: LayerId = слои.some((s) => s.id === желаемый)
    ? желаемый
    : "planet";

  const visible = useMemo(() => {
    return (map?.nodes ?? []).filter((node) => {
      if (node.layer !== текущийСлой) return false;
      if (текущийСлой === "city") return node.parent === фокус;
      if (текущийСлой === "location") return node.parent === базаЛокации;
      return true;
    });
  }, [map, текущийСлой, фокус, базаЛокации]);

  const shownEdges = useMemo(() => {
    const seen = new Map<string, { a: string; b: string; surface: string; seconds: number }>();
    const keys = new Set(visible.map((node) => node.key));
    for (const edge of map?.edges ?? []) {
      const pa = repr(edge.a, текущийСлой);
      const pb = repr(edge.b, текущийСлой);
      if (!pa || !pb || pa === pb) continue;
      if (!keys.has(pa) || !keys.has(pb)) continue;
      const id = [pa, pb].sort().join("|");
      const known = seen.get(id);
      if (!known || edge.seconds < known.seconds) {
        seen.set(id, { a: pa, b: pb, surface: edge.surface, seconds: edge.seconds });
      }
    }
    return [...seen.values()];
  }, [map, visible, repr, текущийСлой]);

  // --- физика ---------------------------------------------------------------

  //: Тела живут в ref: симуляция крутится кадрами, а не рендерами React.
  const bodies = useRef<Map<string, Mass>>(new Map());
  const [, setFrame] = useState(0);
  //: Пока держим узел — он «прибит» и слушается мыши, а не пружин.
  const dragging = useRef<{
    key: string | null;
    moved: boolean;
    startX: number;
    startY: number;
    panX0: number;
    panY0: number;
  } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  //: Панорама и зум: viewBox и есть камера.
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });

  //: Новые узлы встают на запомненные места, а без памяти — на засеянные.
  //: Ушедшие со слоя сперва запоминаются: вернутся — лягут как лежали.
  useEffect(() => {
    const alive = new Set(visible.map((n) => n.key));
    for (const [key, body] of [...bodies.current]) {
      if (!alive.has(key)) {
        REMEMBERED.set(key, { x: body.x, y: body.y });
        bodies.current.delete(key);
      }
    }
    for (const node of visible) {
      if (!bodies.current.has(node.key)) {
        const p = REMEMBERED.get(node.key) ?? seedPoint(node.key);
        bodies.current.set(node.key, { ...p, vx: 0, vy: 0, pinned: false });
      }
    }
  }, [visible]);

  //: Уход с карты (другой таб, размонтирование) тоже сохраняет раскладку.
  useEffect(() => {
    const held = bodies.current;
    return () => {
      for (const [key, body] of held) REMEMBERED.set(key, { x: body.x, y: body.y });
    };
  }, []);

  //: Разбудить симуляцию может кто угодно (перетаскивание, прибытие), а сам
  //: цикл живёт в эффекте ниже — ссылка сшивает их без пересоздания замыканий.
  const kick = useRef<() => void>(() => {});

  //: Сама симуляция: пружины по рёбрам, расталкивание, лёгкое притяжение к
  //: центру. Засыпает, когда всё улеглось, и просыпается от любого касания.
  useEffect(() => {
    let raf = 0;
    let alive = true;
    let running = false;
    const step = () => {
      const items = [...bodies.current.entries()];
      //: Пружины: ребро тянет к длине покоя, выведенной из времени пути.
      for (const edge of shownEdges) {
        const a = bodies.current.get(edge.a);
        const b = bodies.current.get(edge.b);
        if (!a || !b) continue;
        const rest = 140 + Math.min(110, Math.sqrt(edge.seconds) * 2);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const force = (dist - rest) * SPRING;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        if (!a.pinned) {
          a.vx += fx;
          a.vy += fy;
        }
        if (!b.pinned) {
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      //: Расталкивание: узлы не слипаются в кляксу.
      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          const a = items[i][1];
          const b = items[j][1];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.max(12, Math.hypot(dx, dy));
          if (dist > REPULSE_RADIUS) continue;
          const push = REPULSE / (dist * dist);
          const fx = (dx / dist) * push;
          const fy = (dy / dist) * push;
          if (!a.pinned) {
            a.vx -= fx;
            a.vy -= fy;
          }
          if (!b.pinned) {
            b.vx += fx;
            b.vy += fy;
          }
        }
      }
      //: К центру — слабо: одинокие компоненты не уплывают за горизонт.
      let speed = 0;
      for (const [, body] of items) {
        if (!body.pinned) {
          body.vx += (W / 2 - body.x) * CENTERING;
          body.vy += (H / 2 - body.y) * CENTERING;
          body.vx *= DAMPING;
          body.vy *= DAMPING;
          //: Потолок скорости: рывок гасится в шаг, а не размазывается прыжком.
          body.vx = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, body.vx));
          body.vy = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, body.vy));
          body.x += body.vx;
          body.y += body.vy;
        }
        speed = Math.max(speed, Math.abs(body.vx), Math.abs(body.vy));
      }
      setFrame((f) => f + 1);
      //: Улеглось — спим до следующего касания: кадры даром не жгём.
      running = speed > SLEEP_SPEED || Boolean(dragging.current?.key);
      if (running) raf = requestAnimationFrame(step);
    };
    //: Пробуждение идемпотентно: пока цикл крутится, второй не заводится.
    kick.current = () => {
      if (!running && alive) {
        running = true;
        raf = requestAnimationFrame(step);
      }
    };
    kick.current();
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, [shownEdges, visible]);

  /** Разбудить цикл — без пинков телам: случайные толчки дёргали карту.
   *  Кадр форсируется сразу: перетаскиваемый узел следует за мышью, даже
   *  пока цикл ещё не проснулся. */
  const wake = () => {
    kick.current();
    setFrame((f) => f + 1);
  };

  // --- мышь: перетаскивание, панорама, зум ----------------------------------

  /** Пиксели → мир. Svg резиновый, а viewBox держит пропорции (`meet`), и по
   *  краям возникают поля — без их учёта клик попадает мимо узла. */
  const lens = () => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const worldW = W / camera.scale;
    const worldH = H / camera.scale;
    const k = Math.min(rect.width / worldW, rect.height / worldH);
    return {
      rect,
      k,
      offX: (rect.width - worldW * k) / 2,
      offY: (rect.height - worldH * k) / 2,
    };
  };

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const m = lens();
    if (!m) return { x: 0, y: 0 };
    return {
      x: camera.x + (e.clientX - m.rect.left - m.offX) / m.k,
      y: camera.y + (e.clientY - m.rect.top - m.offY) / m.k,
    };
  };

  const grabNode = (key: string) => (e: React.PointerEvent) => {
    e.stopPropagation();
    capture(e);
    const body = bodies.current.get(key);
    if (body) body.pinned = true;
    dragging.current = {
      key,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      panX0: 0,
      panY0: 0,
    };
    wake();
  };

  //: Захват указателя — удобство (drag не рвётся за краем), а не условие:
  //: указатель без захвата (тач-эмуляция, тесты) не должен ломать перенос.
  const capture = (e: React.PointerEvent) => {
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* указателя с таким id нет — перенос работает и без захвата */
    }
  };

  const grabField = (e: React.PointerEvent) => {
    capture(e);
    dragging.current = {
      key: null,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      panX0: camera.x,
      panY0: camera.y,
    };
  };

  const movePointer = (e: React.PointerEvent) => {
    const drag = dragging.current;
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;

    if (drag.key) {
      const body = bodies.current.get(drag.key);
      if (body) {
        const p = toWorld(e);
        body.x = p.x;
        body.y = p.y;
        body.vx = 0;
        body.vy = 0;
      }
      wake();
    } else if (drag.moved) {
      const m = lens();
      const k = m ? 1 / m.k : 1;
      setCamera((cam) => ({ ...cam, x: drag.panX0 - dx * k, y: drag.panY0 - dy * k }));
    }
  };

  const releasePointer = (node?: MapNode) => () => {
    const drag = dragging.current;
    dragging.current = null;
    if (!drag) return;
    if (drag.key) {
      const body = bodies.current.get(drag.key);
      if (body) body.pinned = false;
      wake();
      //: Короткое нажатие без движения — это клик, а не перестановка.
      if (!drag.moved && node) клик(node);
    }
  };

  //: Колесо над картой — зум, и только зум. React вешает wheel пассивно, и
  //: preventDefault оттуда не работает — страница прокручивалась вместе с
  //: зумом. Гасится нативным слушателем с passive: false.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const block = (e: WheelEvent) => e.preventDefault();
    svg.addEventListener("wheel", block, { passive: false });
    return () => svg.removeEventListener("wheel", block);
  }, [map, visible.length]);

  const zoom = (e: React.WheelEvent) => {
    const p = toWorld(e);
    setCamera((cam) => {
      const scale = Math.min(4, Math.max(0.4, cam.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      //: Зум к курсору: точка под мышью остаётся под мышью.
      return {
        scale,
        x: p.x - (p.x - cam.x) * (cam.scale / scale),
        y: p.y - (p.y - cam.y) * (cam.scale / scale),
      };
    });
  };

  // --- поведение узлов ------------------------------------------------------

  const walkTargets = useMemo(() => {
    const out: Record<string, { key: string; seconds: number }> = {};
    for (const exit of look.exits ?? []) {
      const p = repr(exit.key, текущийСлой);
      if (!p || p === repr(here, текущийСлой)) continue;
      const known = out[p];
      if (!known || exit.seconds < known.seconds) {
        out[p] = { key: exit.key, seconds: exit.seconds };
      }
    }
    return out;
  }, [look.exits, repr, here, текущийСлой]);

  const myRepr = repr(here, текущийСлой);

  const группы = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) if (node.parent) out.add(node.parent);
    return out;
  }, [map]);

  /**
   * Ходок движется кадрами, а не рендерами.
   *
   * Раньше его позицию пересчитывал таймер раз в полсекунды — на переходе в
   * шесть секунд это дюжина скачков вместо движения. Теперь кружок двигается
   * прямо в `requestAnimationFrame`, минуя React: React перерисовывает карту,
   * когда карта изменилась, а не шестьдесят раз в секунду ради одной точки.
   *
   * Концы отрезка берутся из тел симуляции на каждом кадре: узлы под ходоком
   * могут ещё расходиться пружинами, и точка обязана держаться ребра.
   */
  const walkerRef = useRef<SVGCircleElement | null>(null);
  useEffect(() => {
    if (!идёт) return;
    let raf = 0;
    const шаг = () => {
      const круг = walkerRef.current;
      const from = bodies.current.get(repr(идёт.from_key, текущийСлой) ?? "");
      const to = bodies.current.get(repr(идёт.to_key, текущийСлой) ?? "");
      if (круг && from && to) {
        const t0 = new Date(идёт.started_at).getTime();
        const t1 = new Date(идёт.arrives_at).getTime();
        const доля = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
        круг.setAttribute("cx", String(from.x + (to.x - from.x) * доля));
        круг.setAttribute("cy", String(from.y + (to.y - from.y) * доля));
      }
      raf = requestAnimationFrame(шаг);
    };
    raf = requestAnimationFrame(шаг);
    return () => cancelAnimationFrame(raf);
    //: В зависимостях поля перехода, а не сам `идёт`: объект приходит новым с
    //: каждым опросом сервера, и эффект пересоздавался бы дважды в секунду —
    //: то самое дёрганье, от которого мы уходим. Все читаемые поля перечислены,
    //: поэтому замыкание не устаревает; линтеру этого не доказать.
  }, [
    идёт?.from_key,
    идёт?.to_key,
    идёт?.started_at,
    идёт?.arrives_at,
    текущийСлой,
    repr,
  ]);

  if (!map) {
    return (
      <section className="map-pane">
        <p className="note">карта грузится…</p>
      </section>
    );
  }

  const at = (key: string) => bodies.current.get(key);

  const walker = (() => {
    if (!идёт) return null;
    const from = at(repr(идёт.from_key, текущийСлой) ?? "");
    const to = at(repr(идёт.to_key, текущийСлой) ?? "");
    if (!from || !to) return null;
    const t0 = new Date(идёт.started_at).getTime();
    const t1 = new Date(идёт.arrives_at).getTime();
    const share = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
    return { x: from.x + (to.x - from.x) * share, y: from.y + (to.y - from.y) * share };
  })();

  const раскрыть = (node: MapNode) => {
    if (текущийСлой === "space") setLayer("planet");
    else if (текущийСлой === "planet") {
      setCityFocus(node.key);
      setLayer("city");
    }
  };

  const идти = (node: MapNode) => {
    const target =
      walkTargets[node.key]?.key ?? (группы.has(node.key) ? null : node.key);
    if (target && !идёт && !look.survey && !busy) {
      void act(() => session.send("travel.go", { node: target }));
    }
  };

  const клик = (node: MapNode) => {
    if (busy) return;
    if (группы.has(node.key)) раскрыть(node);
    else идти(node);
  };

  const vb = `${camera.x} ${camera.y} ${W / camera.scale} ${H / camera.scale}`;

  return (
    <section className="map-pane">
      <nav className="row tabs">
        {слои.map((option) => (
          <button
            key={option.id}
            className={текущийСлой === option.id ? "" : "quiet"}
            onClick={() => setLayer(option.id)}
          >
            {option.label}
          </button>
        ))}
        <Hint>
          Узлы можно таскать мышью, фон — панорама, колесо — зум. Слои: космос,
          планета, город — один и тот же граф с разной высоты (D-045).
        </Hint>
      </nav>

      {visible.length === 0 ? (
        <p className="note">На этом слое пока ничего нет.</p>
      ) : (
        <svg
          ref={svgRef}
          viewBox={vb}
          role="img"
          aria-label="карта мира"
          onPointerDown={grabField}
          onPointerMove={movePointer}
          onPointerUp={releasePointer()}
          onPointerLeave={releasePointer()}
          onWheel={zoom}
        >
          {shownEdges.map((edge) => {
            const a = at(edge.a);
            const b = at(edge.b);
            if (!a || !b) return null;
            return (
              <g key={`${edge.a}|${edge.b}`}>
                <line
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  className={`edge ${edge.surface}`}
                  strokeDasharray={DASH[edge.surface]}
                />
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} className="edge-label">
                  {spell(edge.seconds)} · {SURFACE[edge.surface as keyof typeof SURFACE]}
                </text>
              </g>
            );
          })}

          {visible.map((node) => {
            const p = at(node.key);
            if (!p) return null;
            const mine = node.key === myRepr;
            //: Разведчик никуда не идёт: он в поле, и в узле его нет (D-152).
            //: Кнопка, которую сервер всё равно отвергнет, — обещание, которое
            //: интерфейс не вправе давать.
            const near =
              !идёт &&
              !look.survey &&
              !mine &&
              (группы.has(node.key) ? Boolean(walkTargets[node.key]) : true);
            const group = группы.has(node.key);
            return (
              <g
                key={node.key}
                className={`node ${mine ? "me" : ""} ${near || group ? "near" : ""}`}
                onPointerDown={grabNode(node.key)}
                onPointerMove={movePointer}
                onPointerUp={releasePointer(node)}
              >
                <circle cx={p.x} cy={p.y} r={mine ? 14 : group ? 12 : 10} />
                {group && <circle cx={p.x} cy={p.y} r={mine ? 18 : 16} className="halo" />}
                <text x={p.x} y={p.y - 20} className="node-label">
                  {node.name}
                </text>
                {group && текущийСлой !== "city" && (
                  <text x={p.x} y={p.y + 30} className="node-hint">
                    раскрыть
                  </text>
                )}
                {near && (
                  <text
                    x={p.x}
                    y={p.y + (group ? 44 : 30)}
                    className="node-hint"
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.stopPropagation();
                      идти(node);
                    }}
                  >
                    идти
                  </text>
                )}
              </g>
            );
          })}

          {/* Первый кадр рисуется по расчёту, дальше кружок ведёт rAF. */}
          {walker && (
            <circle
              ref={walkerRef}
              cx={walker.x}
              cy={walker.y}
              r={5}
              className="walker"
            />
          )}
        </svg>
      )}

      <div className="row map-bar">
        {идёт ? (
          <span>
            в пути: {идёт.final ?? идёт.to}
            {идёт.final ? (
              <>
                {" "}· сейчас — отрезок до «{идёт.to}», <Countdown to={идёт.arrives_at} />
                {(идёт.legs_left ?? 0) > 1 && ` · впереди ещё ${идёт.legs_left! - 1} узл.`}
              </>
            ) : (
              <>
                {" "}· придём через <Countdown to={идёт.arrives_at} />
              </>
            )}
          </span>
        ) : (
          <>
            <span className="sign">{look.node?.name}</span>
            {!look.survey && (
              <button onClick={onEnter} disabled={busy}>
                Войти
              </button>
            )}
            {/* Цена дороги телом (D-147): её видно до выхода, а не после, —
                но списком она занимала полполосы, и теперь лежит в подсказке. */}
            {!look.survey && (look.exits ?? []).length > 0 && (
              <Hint>
                Дорога стоит выносливости (D-147):{" "}
                {(look.exits ?? [])
                  .map((путь) => `${путь.name} ${цена(путь.stamina)}`)
                  .join(" · ")}
                . Идти можно в любой узел — маршрут строится сам (D-045).
              </Hint>
            )}
          </>
        )}
      </div>
      {/* Разведка живёт на карте: искать новое место логично там, где карта
          видна. Что искать — зависит от слоя: в городе ищут участок, на
          планете — место под город или жилу названной породы (D-152). */}
      {!идёт && <Search look={look} session={session} busy={busy} act={act} layer={текущийСлой} />}
      {/* Дорога — работа на ребре, и место ей там же, где рёбра видны (D-158). */}
      {!идёт && !look.survey && <Roads look={look} session={session} busy={busy} act={act} />}

      {/* Осталось только то, что меняется: пока идёшь или разведываешь,
          присутственное закрыто, и это стоит сказать прямо. Объяснения
          устройства мира ушли в подсказки. */}
      {(идёт || look.survey) && (
        <p className="note">
          {идёт
            ? "Пока идёшь, тебя нет нигде: присутственное закрыто (D-107)."
            : "Разведчик в поле: тело недоступно, как во сне (D-152)."}
        </p>
      )}
    </section>
  );
}

/** Дороги из этого узла: что уложено, что просело и чего это стоит (D-158).
 *
 * Покрытие поднимается на ступень за `road.surface_per_edge` полотна и
 * `road.build_hours` времени: бездорожье → дорога → мощёный тракт. Без
 * содержания дорога зарастает обратно, поэтому состояние показывается всегда —
 * заросшая отрежет обоз от узла, куда он вчера проезжал.
 */
function Roads({
  look,
  session,
  busy,
  act,
}: {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const [дороги, setДороги] = useState<RoadWork[]>([]);

  useEffect(() => {
    void session
      .send("road.here")
      .then((ответ) => setДороги((ответ.roads as RoadWork[]) ?? []))
      .catch(() => setДороги([]));
    //: Пересчитывается при переходе и после каждого действия: уложенная
    //: ступень меняет и покрытие, и остаток полотна в руках.
  }, [session, look.node?.key, look.inventory]);

  if (дороги.length === 0) return null;
  const работать = (edge: string, mend: boolean) =>
    act(() => session.send("road.lay", { edge, mend }));

  return (
    <div className="row map-bar">
      {дороги.map((путь) => (
        <span key={путь.edge} className="note">
          {путь.to}: {ПОКРЫТИЕ[путь.surface]}
          {путь.surface !== "trail" && ` ${путь.condition.toFixed(0)}%`}
          {путь.working ? (
            " · идёт работа"
          ) : (
            <>
              {путь.next && путь.needs !== null && (
                <button
                  className="quiet"
                  onClick={() => работать(путь.edge, false)}
                  disabled={busy || путь.at_hand < путь.needs}
                  title={`нужно ${путь.needs.toFixed(0)} полотна, в руках ${путь.at_hand.toFixed(0)}`}
                >
                  {путь.surface === "trail" ? "Проложить" : "Мостить"}
                </button>
              )}
              {путь.mend_needs !== null && (
                <button
                  className="quiet"
                  onClick={() => работать(путь.edge, true)}
                  disabled={busy || путь.at_hand < путь.mend_needs}
                  title={`подсыпка: ${путь.mend_needs.toFixed(0)} полотна`}
                >
                  Подсыпать
                </button>
              )}
            </>
          )}
        </span>
      ))}
      <Hint>
        Покрытие поднимается на ступень за полотно и время: бездорожье → дорога
        → мощёный тракт. Без содержания дорога зарастает обратно, а по
        бездорожью обоз не идёт вовсе (D-107, D-158).
      </Hint>
    </div>
  );
}

/** Покрытие словами: игрок читает дорогу, а не enum. */
const ПОКРЫТИЕ: Record<RoadWork["surface"], string> = {
  trail: "бездорожье",
  road: "дорога",
  paved: "тракт",
};

function Countdown({ to }: { to: string }) {
  const [left, setLeft] = useState(() => (new Date(to).getTime() - Date.now()) / 1000);
  useEffect(() => {
    const timer = setInterval(
      () => setLeft((new Date(to).getTime() - Date.now()) / 1000),
      1000,
    );
    return () => clearInterval(timer);
  }, [to]);
  return <b>{left <= 0 ? "вот-вот" : spell(left)}</b>;
}

/** Разведка с карты: цель зависит от слоя, на который смотрит игрок (D-152).
 *
 * Разведчик **уходит сам**: пока заход идёт, тело в поле и недоступно, как во
 * сне. Вернуться раньше срока можно — находка тогда не состоится.
 *
 * Цена захода — свойство места (D-156): по нехоженой окрестности это минуты и
 * почти верная находка, по исхоженной — часы и бросок. Прогноз показывается до
 * выхода и обновляется при смене узла. */
function Search({
  look,
  session,
  busy,
  act,
  layer,
}: {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  layer: LayerId;
}) {
  const [породы, setПороды] = useState<string[]>([]);
  const [порода, setПорода] = useState("");
  const [прогноз, setПрогноз] = useState<Outlook | null>(null);
  const заход = look.survey ?? null;

  useEffect(() => {
    void session
      //: Прогноз просится под выбранную породу: редкая ищется хуже частой
      //: (D-151), и «шанс 90%» рядом с заказом золота был бы обманом.
      .send("explore.goals", порода ? { goal: "vein", resource: порода } : {})
      .then((ответ) => {
        setПороды((ответ.resources as string[]) ?? []);
        setПрогноз((ответ.outlook as Outlook | null) ?? null);
      })
      .catch(() => {
        setПороды([]);
        setПрогноз(null);
      });
    //: Заход меняет счёт находок узла, поэтому прогноз пересчитывается и по
    //: возвращении разведчика, а не только при переходе.
  }, [session, look.node?.key, заход?.returns_at, порода]);

  if (!заход && layer !== "city" && layer !== "planet") return null;

  const искать = (goal: string, resource?: string) =>
    act(() => session.send("explore.survey", { goal, resource }));

  return (
    <div className="row map-bar">
      {заход ? (
        <>
          <span className="note">
            вы в разведке · вернётесь <Countdown to={заход.returns_at} />
          </span>
          <button
            onClick={() => act(() => session.send("explore.cancel"))}
            disabled={busy}
          >
            Вернуться сейчас
          </button>
          <Hint>
            Повернуть назад можно в любой момент: находки не будет, потраченные
            силы не вернутся.
          </Hint>
        </>
      ) : layer === "city" ? (
        <>
          <button onClick={() => искать("lot")} disabled={busy}>
            Уйти искать участок
          </button>
          <Hint>
            Найденный участок встанет городской землёй: её выкупают у города
            (D-089). Разведчик уходит сам, до возвращения недоступен, как во
            сне, и остаётся на находке (D-185).
          </Hint>
        </>
      ) : (
        <>
          <button onClick={() => искать("site")} disabled={busy}>
            Уйти искать узел для города
          </button>
          <button onClick={() => искать("vein", порода || undefined)} disabled={busy}>
            Уйти искать жилу
          </button>
          <select value={порода} onChange={(e) => setПорода(e.target.value)}>
            <option value="">любую породу</option>
            {породы.map((имя) => (
              <option key={имя}>{имя}</option>
            ))}
          </select>
          <Hint>
            Разведчик уходит сам и до возвращения недоступен, как во сне.
            Кончатся силы — доспит в поле и продолжит. Нашёл — там и остаётся
            (D-185); чем дальше от города находка, тем длиннее к ней дорога
            (D-180).
          </Hint>
        </>
      )}
      {!заход && прогноз && <Forecast прогноз={прогноз} />}
    </div>
  );
}

/** Во что обойдётся заход отсюда (D-156). */
function Forecast({ прогноз }: { прогноз: Outlook }) {
  const { min, max } = прогноз.minutes;
  const срок = min === max ? долго(min) : размах(min, max);
  //: Шанс может быть долей процента — округление до целого показало бы ноль
  //: там, где искать всё-таки можно.
  const шанс = прогноз.chance >= 1
    ? Math.round(прогноз.chance)
    : прогноз.chance.toFixed(1);
  return (
    <span className="note">
      заход отсюда: {срок} · шанс {шанс}% · до {цена(прогноз.stamina)}{" "}
      выносливости
      {прогноз.resource && (прогноз.aim ?? 1) < 1 &&
        ` · ${прогноз.resource.toLowerCase()} редка: шанс уже в ${(1 / (прогноз.aim ?? 1)).toFixed(0)} раз`}
      {прогноз.explored > 0 &&
        ` · окрестность исхожена: находок отсюда ${прогноз.explored}`}
    </span>
  );
}

const МИНУТ_В_ЧАСЕ = 60;

function долго(минут: number): string {
  return `${счёт(минут)} ${единица(минут)}`;
}

function размах(от: number, до: number): string {
  return единица(от) === единица(до)
    ? `${счёт(от)}–${счёт(до)} ${единица(до)}`
    : `${долго(от)} – ${долго(до)}`;
}

function единица(минут: number): string {
  return минут < МИНУТ_В_ЧАСЕ ? "мин" : "ч";
}

function счёт(минут: number): string {
  if (минут < МИНУТ_В_ЧАСЕ) return String(Math.round(минут));
  const часов = минут / МИНУТ_В_ЧАСЕ;
  return часов % 1 === 0 ? String(часов) : часов.toFixed(1);
}

/** Цена дороги телом. Шаг по городу стоит доли единицы — и «0.0» тут врало бы. */
function цена(выносливость: number): string {
  if (выносливость <= 0) return "0";
  return выносливость < 0.1 ? "<0.1" : выносливость.toFixed(1);
}
