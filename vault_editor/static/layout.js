// Where the boxes go.
//
// The graph is a ladder, so it is drawn as one: a column per rung, and every
// arrow points to the right. The rung is the number the server computed by
// walking the ladder from bare raw material -- the same walk the vault's own
// check performs -- so a thing standing in the seventh column really does need
// six rounds of work before it can exist.
//
// Inside a column the order is chosen by the barycentre heuristic: a node sits
// across from the average of what it is connected to. Four sweeps are enough to
// untangle a graph this size; more only shuffles ties.

const NODE_H = 22;
const ROW_GAP = 9;
const COL_GAP = 74;
const PAD = 40;
const MAX_W = 220;

let ruler = null;

export function measure(text, font = '12px "Segoe UI", system-ui, sans-serif') {
  if (!ruler) ruler = document.createElement('canvas').getContext('2d');
  ruler.font = font;
  return ruler.measureText(text).width;
}

export function nodeWidth(label) {
  return Math.min(MAX_W, Math.max(56, Math.ceil(measure(label)) + 18));
}

export function ellipsis(label, width) {
  if (measure(label) <= width - 17) return label;
  let cut = label;
  while (cut.length > 3 && measure(cut + '…') > width - 17) cut = cut.slice(0, -1);
  return cut + '…';
}

// Lay out a set of nodes and the edges between them. `nodes` and `edges` are the
// visible subset; everything else has already been filtered out by the caller.
export function layout(nodes, edges) {
  if (!nodes.length) return { nodes: [], edges: [], width: 0, height: 0 };

  const byName = new Map(nodes.map((node) => [node.name, node]));
  const live = edges.filter((edge) => byName.has(edge.from) && byName.has(edge.to));

  // -- columns ---------------------------------------------------------------
  const fallback = Math.max(0, ...nodes.map((n) => (n.depth == null ? 0 : n.depth))) + 1;
  const rung = (node) => (node.depth == null ? fallback : node.depth);
  const rungs = [...new Set(nodes.map(rung))].sort((a, b) => a - b);
  const column = new Map(rungs.map((value, index) => [value, index]));

  const columns = rungs.map(() => []);
  for (const node of nodes) columns[column.get(rung(node))].push(node);
  for (const col of columns) col.sort((a, b) => a.name.localeCompare(b.name, 'ru'));

  // -- order inside a column -------------------------------------------------
  const inbound = new Map(nodes.map((n) => [n.name, []]));
  const outbound = new Map(nodes.map((n) => [n.name, []]));
  for (const edge of live) {
    outbound.get(edge.from).push(edge.to);
    inbound.get(edge.to).push(edge.from);
  }

  const place = new Map();
  const reindex = () => {
    columns.forEach((col) => col.forEach((node, index) => place.set(node.name, index)));
  };
  reindex();

  const sweep = (order, neighbours) => {
    for (const index of order) {
      const col = columns[index];
      const weight = new Map();
      col.forEach((node, position) => {
        const near = neighbours.get(node.name).map((name) => place.get(name)).filter((v) => v != null);
        weight.set(node.name, near.length ? near.reduce((a, b) => a + b, 0) / near.length : position);
      });
      col.sort((a, b) => weight.get(a.name) - weight.get(b.name) || a.name.localeCompare(b.name, 'ru'));
      reindex();
    }
  };

  const down = columns.map((_, index) => index);
  const up = [...down].reverse();
  for (let pass = 0; pass < 4; pass += 1) {
    sweep(down, inbound);
    sweep(up, outbound);
  }

  // -- coordinates -----------------------------------------------------------
  const widths = columns.map((col) => Math.max(...col.map((node) => nodeWidth(node.name)), 60));
  const heights = columns.map((col) => col.length * (NODE_H + ROW_GAP) - ROW_GAP);
  const tallest = Math.max(...heights);

  let x = PAD;
  const placed = [];
  columns.forEach((col, index) => {
    const width = widths[index];
    let y = PAD + (tallest - heights[index]) / 2;
    for (const node of col) {
      placed.push({ ...node, x, y, w: width, h: NODE_H, column: index });
      y += NODE_H + ROW_GAP;
    }
    x += width + COL_GAP;
  });

  const box = new Map(placed.map((node) => [node.name, node]));
  const drawn = live.map((edge) => {
    const from = box.get(edge.from);
    const to = box.get(edge.to);
    const x1 = from.x + from.w;
    const y1 = from.y + from.h / 2;
    const x2 = to.x;
    const y2 = to.y + to.h / 2;
    const bend = Math.max(24, (x2 - x1) * 0.45);
    return {
      ...edge,
      path: `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`,
      lx: x1 + Math.max(10, (x2 - x1) * 0.16),
      ly: y1 + (y2 - y1) * 0.1 - 3,
      backwards: x2 <= x1,
    };
  });

  return {
    nodes: placed,
    edges: drawn,
    width: x - COL_GAP + PAD,
    height: tallest + PAD * 2,
  };
}

// Which nodes are visible in focus mode: the chosen thing, what it is made of
// down to `back` steps, and what is made out of it up to `forward` steps.
export function neighbourhood(name, edges, back, forward, withStations) {
  const keep = (edge) => withStations || edge.rel === 'input';
  const up = new Map();
  const downstream = new Map();
  for (const edge of edges) {
    if (!keep(edge)) continue;
    if (!up.has(edge.to)) up.set(edge.to, []);
    if (!downstream.has(edge.from)) downstream.set(edge.from, []);
    up.get(edge.to).push(edge.from);
    downstream.get(edge.from).push(edge.to);
  }
  const seen = new Set([name]);
  const walk = (map, limit) => {
    let front = [name];
    for (let step = 0; step < limit; step += 1) {
      const next = [];
      for (const item of front) {
        for (const near of map.get(item) || []) {
          if (seen.has(near)) continue;
          seen.add(near);
          next.push(near);
        }
      }
      if (!next.length) break;
      front = next;
    }
  };
  walk(up, back);
  walk(downstream, forward);
  return seen;
}
