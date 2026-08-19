// The graph itself: boxes, arrows, panning, and the highlight that answers the
// only question the picture is for -- what does this thing touch.
//
// Nothing here knows about recipes. It draws laid-out nodes and edges and reports
// clicks; what a click means is decided in app.js.

import { ellipsis, layout } from './layout.js';

const NS = 'http://www.w3.org/2000/svg';

export const KIND_COLOUR = {
  raw: 'var(--kind-raw)',
  operation: 'var(--kind-operation)',
  class: 'var(--kind-class)',
  virtual: 'var(--kind-virtual)',
  station: 'var(--kind-station)',
  furniture: 'var(--kind-furniture)',
  tool: 'var(--kind-tool)',
  gear: 'var(--kind-gear)',
  vehicle: 'var(--kind-vehicle)',
  material: 'var(--kind-material)',
  consumable: 'var(--kind-consumable)',
  money: 'var(--kind-money)',
};

export function colourOf(node) {
  if (!node) return 'var(--kind-class)';
  if (node.type === 'recipe') return KIND_COLOUR[node.kind] || KIND_COLOUR.material;
  return KIND_COLOUR[node.type] || KIND_COLOUR.class;
}

function el(name, attrs = {}) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== undefined && value !== null) node.setAttribute(key, value);
  }
  return node;
}

export function createGraph(svg, { onSelect, onFocus, onHover } = {}) {
  svg.replaceChildren();
  const defs = el('defs');
  for (const [id, colour] of [['arrow', '#5b6675'], ['arrow-hot', '#7aa2f7']]) {
    const marker = el('marker', {
      id, viewBox: '0 0 8 8', refX: 7, refY: 4,
      markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse',
    });
    marker.append(el('path', { d: 'M0,0 L8,4 L0,8 z', fill: colour }));
    defs.append(marker);
  }
  svg.append(defs);

  const viewport = el('g');
  const edgeLayer = el('g');
  const labelLayer = el('g');
  const nodeLayer = el('g');
  viewport.append(edgeLayer, labelLayer, nodeLayer);
  svg.append(viewport);

  const view = { x: 0, y: 0, k: 1 };
  let size = { width: 0, height: 0 };
  let edgeEls = [];
  let nodeEls = new Map();
  let selected = null;

  const apply = () => {
    viewport.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
    labelLayer.style.display = view.k < 0.55 ? 'none' : '';
  };

  // -- interaction -----------------------------------------------------------

  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const px = event.clientX - rect.left;
    const py = event.clientY - rect.top;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const next = Math.min(3, Math.max(0.12, view.k * factor));
    view.x = px - ((px - view.x) * next) / view.k;
    view.y = py - ((py - view.y) * next) / view.k;
    view.k = next;
    apply();
  }, { passive: false });

  // Panning listens on the window rather than capturing the pointer on the svg.
  // Capture would retarget the `click` that follows to the svg itself, and the
  // node under the finger would never hear it -- the boxes would be dead to a
  // real mouse while answering fine to a scripted click.
  let dragging = null;
  let dragged = false;

  const onMove = (event) => {
    if (!dragging) return;
    const dx = event.clientX - dragging.x;
    const dy = event.clientY - dragging.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) dragging.moved = true;
    view.x = dragging.vx + dx;
    view.y = dragging.vy + dy;
    apply();
  };
  const endDrag = () => {
    if (!dragging) return;
    dragged = dragging.moved;
    dragging = null;
    svg.classList.remove('drag');
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', endDrag);
    window.removeEventListener('pointercancel', endDrag);
  };
  svg.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    dragged = false;
    dragging = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y, moved: false };
    svg.classList.add('drag');
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', endDrag);
    window.addEventListener('pointercancel', endDrag);
  });

  // -- highlight -------------------------------------------------------------

  function highlight(name) {
    if (!name) {
      for (const edge of edgeEls) edge.element.classList.remove('dim', 'hot');
      for (const [, entry] of nodeEls) entry.group.classList.remove('dim');
      return;
    }
    const touched = new Set([name]);
    for (const edge of edgeEls) {
      const hot = edge.data.from === name || edge.data.to === name;
      edge.element.classList.toggle('hot', hot);
      edge.element.classList.toggle('dim', !hot);
      if (hot) touched.add(edge.data.from).add(edge.data.to);
    }
    for (const [key, entry] of nodeEls) entry.group.classList.toggle('dim', !touched.has(key));
  }

  // -- drawing ---------------------------------------------------------------

  function render(nodes, edges, options = {}) {
    const placed = layout(nodes, edges);
    size = { width: placed.width, height: placed.height };
    edgeLayer.replaceChildren();
    labelLayer.replaceChildren();
    nodeLayer.replaceChildren();
    edgeEls = [];
    nodeEls = new Map();

    const byName = new Map(placed.nodes.map((node) => [node.name, node]));
    for (const edge of placed.edges) {
      const path = el('path', {
        d: edge.path,
        class: `link ${edge.rel}`,
        stroke: colourOf(byName.get(edge.from)),
        'stroke-opacity': edge.rel === 'input' || edge.rel === 'made' ? 0.5 : 0.35,
        'marker-end': 'url(#arrow)',
      });
      path.append(el('title', {}));
      path.lastChild.textContent = `${edge.from} → ${edge.to}` + (edge.via ? `  (${edge.via})` : '');
      edgeLayer.append(path);
      edgeEls.push({ element: path, data: edge });
      if (options.amounts && edge.amount != null && edge.rel === 'input') {
        const text = el('text', { x: edge.lx, y: edge.ly, class: 'amount' });
        text.textContent = format(edge.amount);
        labelLayer.append(text);
      }
    }

    for (const node of placed.nodes) {
      const group = el('g', { class: 'node' + (node.is_key ? ' key' : '') });
      const colour = colourOf(node);
      const rect = el('rect', {
        x: node.x, y: node.y, width: node.w, height: node.h, rx: 2,
        fill: colour, 'fill-opacity': 0.13, stroke: colour,
        'stroke-dasharray': node.cut_candidate ? '4 2' : null,
      });
      const label = el('text', { x: node.x + 8, y: node.y + node.h / 2 + 4 });
      label.textContent = ellipsis(node.name, node.w);
      group.append(rect, label);
      if (node.is_key) {
        group.append(el('circle', {
          cx: node.x + node.w - 5, cy: node.y + 5, r: 2.6, fill: 'var(--warn)',
        }));
      }
      const tip = el('title');
      tip.textContent = tooltip(node);
      group.append(tip);
      group.addEventListener('pointerenter', () => { highlight(node.name); onHover?.(node); });
      group.addEventListener('pointerleave', () => { highlight(selected); onHover?.(null); });
      group.addEventListener('click', (event) => {
        if (dragged) return;
        event.stopPropagation();
        onSelect?.(node.name);
      });
      group.addEventListener('dblclick', (event) => {
        event.stopPropagation();
        onFocus?.(node.name);
      });
      nodeLayer.append(group);
      nodeEls.set(node.name, { group, rect, node });
    }
    setSelected(selected);
  }

  function setSelected(name) {
    selected = name;
    for (const [key, entry] of nodeEls) entry.group.classList.toggle('sel', key === name);
    highlight(name);
  }

  function fit(padding = 24) {
    const rect = svg.getBoundingClientRect();
    if (!size.width || !size.height || !rect.width) return;
    const k = Math.min(
      (rect.width - padding) / size.width,
      (rect.height - padding) / size.height,
      1.4,
    );
    view.k = Math.max(0.12, k);
    view.x = (rect.width - size.width * view.k) / 2;
    view.y = (rect.height - size.height * view.k) / 2;
    apply();
  }

  function centreOn(name) {
    const entry = nodeEls.get(name);
    const rect = svg.getBoundingClientRect();
    if (!entry || !rect.width) return;
    view.x = rect.width / 2 - (entry.node.x + entry.node.w / 2) * view.k;
    view.y = rect.height / 2 - (entry.node.y + entry.node.h / 2) * view.k;
    apply();
  }

  svg.addEventListener('click', () => highlight(selected));

  return { render, setSelected, fit, centreOn, get scale() { return view.k; } };
}

export function format(value) {
  if (value == null) return '';
  const number = Number(value);
  if (Number.isInteger(number)) return String(number);
  return String(Math.round(number * 1000) / 1000);
}

function tooltip(node) {
  const lines = [node.name];
  if (node.type === 'recipe') {
    lines.push(`${node.kind} · на станции: ${node.station}`);
    if (node.inputs?.length) lines.push(`из: ${node.inputs.join(', ')}`);
  } else if (node.type === 'raw') {
    lines.push('сырьё');
  } else if (node.type === 'operation') {
    lines.push(`операция: ${(node.operations || []).join(', ')}`);
  } else if (node.type === 'class') {
    lines.push(`класс инструмента: ${(node.members || []).join(', ')}`);
  } else if (node.type === 'virtual') {
    lines.push('рабочее место без рецепта: руки либо стройплощадка');
  }
  if (node.depth != null) lines.push(`ступень ${node.depth}`);
  if (node.labor_hours != null) lines.push(`труд: ${format(node.labor_hours)} ч`);
  if (node.mass != null) lines.push(`масса: ${format(node.mass)} кг`);
  return lines.join('\n');
}
