// The glue: state, the list on the left, the graph in the middle, the form on
// the right, and the strip at the bottom where the vault's own check speaks.
//
// One rule holds the thing together: after every write the state is reloaded
// from the file and `tools/build.py --check` runs. The editor never claims a
// save is good -- the vault's own check says so, in its own words.

import { api } from './api.js';
import { colourOf, KIND_COLOUR } from './graphview.js';
import { createGraph } from './graphview.js';
import { neighbourhood } from './layout.js';
import { createPanel } from './panel.js';
import { h, plural, things } from './ui.js';

const TYPE_LABEL = {
  raw: 'сырьё',
  operation: 'операции',
  class: 'классы',
  station: 'станции',
  furniture: 'мебель',
  tool: 'инструменты',
  gear: 'снаряжение',
  vehicle: 'транспорт',
  material: 'материалы',
  consumable: 'расходники',
  money: 'монета',
};

const app = {
  state: null,
  nodes: new Map(),
  // «Руками» и «Стройка» рецептами не делаются, но половина лестницы начинается
  // на них. Во вкладке станций они узлы; в лестнице рецептов их нет.
  extra: new Map(),
  tab: 'recipes',
  selected: null,
  query: '',
  hidden: new Set(),
  keyOnly: false,
  // Режим свой у каждой вкладки: в рецептах чаще смотрят окрестность одной
  // вещи, в станциях — сперва всё дерево.
  mode: 'focus',
  modes: { recipes: 'focus', stations: 'all' },
  back: 2,
  forward: 1,
  stations: false,
  amounts: true,
};

const dom = {
  list: document.getElementById('list'),
  filters: document.getElementById('filters'),
  search: document.getElementById('search'),
  counts: document.getElementById('counts'),
  path: document.getElementById('source-path'),
  stale: document.getElementById('stale'),
  legend: document.getElementById('legend'),
  hint: document.getElementById('graph-hint'),
  consoleBox: document.getElementById('console'),
  consoleOut: document.getElementById('console-out'),
  consoleTitle: document.getElementById('console-title'),
};

const graph = createGraph(document.getElementById('graph'), {
  onSelect: (name) => select(name),
  onFocus: (name) => { select(name, { focus: true }); },
});

const panel = createPanel(document.getElementById('panel'), {
  getNode: (name) => app.nodes.get(name) || app.extra.get(name),
  vocabulary: () => app.state.vocabulary,
  onSelect: (name) => select(name),
  onWrite: afterWrite,
  notify: (text, bad) => say(text, bad),
});

// ------------------------------------------------------------------- loading

async function load(keepSelection = true) {
  const state = await api.state();
  app.state = state;
  app.nodes = new Map(state.nodes.map((node) => [node.name, node]));
  app.extra = new Map(state.stations
    .filter((station) => station.virtual)
    .map((station) => [station.name, { name: station.name, type: 'virtual', depth: station.depth }]));
  dom.path.textContent = state.source;
  dom.stale.hidden = !state.stale;
  const { recipes, raw, operations } = state.counts;
  dom.counts.textContent = `${recipes} ${plural(recipes, 'рецепт', 'рецепта', 'рецептов')}`
    + ` · ${raw} ${plural(raw, 'вид', 'вида', 'видов')} сырья`
    + ` · ${operations} ${plural(operations, 'операция', 'операции', 'операций')}`;
  document.getElementById('act-undo').disabled = !state.undo;

  let names = document.getElementById('all-names');
  if (!names) {
    names = h('datalist', { id: 'all-names' });
    document.body.append(names);
  }
  names.replaceChildren(...state.vocabulary.names.map((name) => h('option', { value: name })));

  renderFilters();
  renderLegend();
  renderList();
  if (!keepSelection || !known(app.selected)) app.selected = null;
  drawGraph();
}

function known(name) {
  return app.nodes.has(name) || app.extra.has(name);
}

function nodeOf(name) {
  return app.nodes.get(name) || app.extra.get(name);
}

function station(name) {
  return app.state.stations.find((item) => item.name === name);
}

// ---------------------------------------------------------------------- list

function typeOf(node) {
  return node.type === 'recipe' ? node.kind : node.type;
}

function renderFilters() {
  if (app.tab === 'stations') {
    // Фильтровать нечего: станции и так один тип. Вместо фишек — счёт.
    const idle = app.state.stations.filter((item) => !item.makes.length).length;
    dom.filters.replaceChildren(h('span', {
      class: 'note-line',
      text: `${app.state.stations.length} станций, из них ${idle} пока ничего не делают`,
    }));
    return;
  }
  const present = [...new Set(app.state.nodes.map(typeOf))];
  const order = Object.keys(TYPE_LABEL);
  present.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  dom.filters.replaceChildren(
    ...present.map((type) => h('button', {
      class: 'chip' + (app.hidden.has(type) ? '' : ' on'),
      style: `color:${KIND_COLOUR[type] || 'inherit'}`,
      text: TYPE_LABEL[type] || type,
      onclick: () => {
        if (app.hidden.has(type)) app.hidden.delete(type); else app.hidden.add(type);
        renderFilters();
        renderList();
        if (app.mode === 'all') drawGraph();
      },
    })),
    h('button', {
      class: 'chip' + (app.keyOnly ? ' on' : ''),
      text: '★ вехи',
      title: 'только ступени лестницы',
      onclick: () => { app.keyOnly = !app.keyOnly; renderFilters(); renderList(); },
    }),
  );
}

function matches(node) {
  if (app.hidden.has(typeOf(node))) return false;
  if (app.keyOnly && !node.is_key) return false;
  const query = app.query.trim().toLowerCase();
  if (!query) return true;
  const haystack = [
    node.name,
    node.station || '',
    node.kind || node.type,
    ...(node.inputs || []),
    ...(node.operations || []),
  ].join(' ').toLowerCase();
  return haystack.includes(query);
}

function renderList() {
  if (app.tab === 'stations') {
    renderStationList();
    return;
  }
  const visible = app.state.nodes.filter(matches);
  const groups = new Map();
  for (const node of visible) {
    const key = node.type === 'recipe' ? `L${String(node.level).padStart(2, '0')}` : `Z${node.type}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(node);
  }
  const levels = new Map(app.state.vocabulary.levels.map((level) => [level.id, level.title]));
  const title = (key) => (key.startsWith('L')
    ? `${Number(key.slice(1))}. ${levels.get(Number(key.slice(1))) || ''}`
    : TYPE_LABEL[key.slice(1)] || key.slice(1));

  const out = [];
  for (const key of [...groups.keys()].sort()) {
    out.push(h('div', { class: 'group', text: title(key) }));
    for (const node of groups.get(key).sort((a, b) => a.name.localeCompare(b.name, 'ru'))) {
      out.push(h('div', {
        class: 'row' + (node.name === app.selected ? ' sel' : ''),
        'data-name': node.name,
        onclick: () => select(node.name),
        ondblclick: () => select(node.name, { focus: true }),
      },
      h('span', { class: 'dot', style: `background:${colourOf(node)}` }),
      h('span', { class: 'nm', text: node.name }),
      node.is_key ? h('span', { class: 'kbd', text: '★' }) : null,
      h('span', { class: 'st', text: node.station || (node.type === 'raw' ? 'сырьё' : '') }),
      ));
    }
  }
  if (!out.length) out.push(h('div', { class: 'empty', text: 'ничего не нашлось' }));
  dom.list.replaceChildren(...out);
}

// Станции идут по ступеням, а не по алфавиту: список читается как порядок, в
// котором город их себе ставит.
function renderStationList() {
  const query = app.query.trim().toLowerCase();
  const visible = app.state.stations.filter((item) => !query
    || [item.name, item.parent || '', ...item.makes].join(' ').toLowerCase().includes(query));

  const out = [];
  let rung = null;
  for (const item of visible) {
    if (item.depth !== rung) {
      rung = item.depth;
      out.push(h('div', {
        class: 'group',
        text: item.virtual ? 'без станции' : `ступень ${rung ?? '—'}`,
      }));
    }
    const node = nodeOf(item.name) || { type: 'virtual' };
    out.push(h('div', {
      class: 'row' + (item.name === app.selected ? ' sel' : ''),
      'data-name': item.name,
      onclick: () => select(item.name),
      ondblclick: () => select(item.name, { focus: true }),
    },
    h('span', { class: 'dot', style: `background:${colourOf(node)}` }),
    h('span', { class: 'nm', text: item.name }),
    h('span', {
      class: 'st',
      title: 'рецептов на этой станции · где она сама стоит входом',
      text: item.makes.length
        ? `${item.makes.length}${item.inputs_to.length ? ` · ↑${item.inputs_to.length}` : ''}`
        : '—',
    }),
    ));
  }
  if (!out.length) out.push(h('div', { class: 'empty', text: 'ничего не нашлось' }));
  dom.list.replaceChildren(...out);
}

function renderLegend() {
  if (app.tab === 'stations') {
    dom.legend.replaceChildren(
      h('span', {}, h('i', { style: `background:${KIND_COLOUR.station}` }), 'станция'),
      h('span', {}, h('i', { style: `background:${KIND_COLOUR.virtual}` }), 'руками и стройка'),
      h('span', { text: '· сплошная стрелка — на чём станция собирается' }),
      h('span', { text: '· пунктир — что на ней делают' }),
      h('span', { text: '· точки — станция входит в состав' }),
      h('span', { text: '· двойной щелчок — раскрыть одну станцию' }),
    );
    return;
  }
  const present = [...new Set(app.state.nodes.map(typeOf))];
  dom.legend.replaceChildren(
    ...present.map((type) => h('span', {},
      h('i', { style: `background:${KIND_COLOUR[type] || '#888'}` }),
      TYPE_LABEL[type] || type,
    )),
    h('span', {}, h('i', { style: 'background:var(--warn);border-radius:50%' }), 'веха'),
    h('span', { text: '· колонка — ступень лестницы от голого сырья' }),
    h('span', { text: '· двойной щелчок — сделать центром' }),
  );
}

// --------------------------------------------------------------------- graph

function drawGraph() {
  const picture = app.tab === 'stations' ? stationPicture() : recipePicture();
  graph.render(picture.nodes, picture.edges, { amounts: app.amounts });
  graph.setSelected(app.selected);
  graph.fit();
}

// Дерево станций (D-106): верстак собирается руками, всё остальное — на
// верстаке или глубже. Плюс станции, которые входят в другие станции деталью.
function stationPicture() {
  const list = app.state.stations;
  const node = (name) => nodeOf(name) || { name, type: 'virtual', depth: 0 };

  if (app.mode === 'all') {
    const nodes = list.map((item) => node(item.name));
    const shown = new Set(nodes.map((item) => item.name));
    const edges = [];
    for (const item of list) {
      if (item.parent && shown.has(item.parent)) {
        edges.push({ from: item.parent, to: item.name, rel: 'station', via: 'собирается на' });
      }
      for (const target of item.inputs_to) {
        if (shown.has(target)) {
          edges.push({ from: item.name, to: target, rel: 'part', via: 'входит в' });
        }
      }
    }
    dom.hint.textContent = `${list.length} станций`;
    return { nodes, edges };
  }

  const chosen = station(app.selected);
  if (!chosen) {
    dom.hint.textContent = 'выберите станцию';
    return { nodes: [], edges: [] };
  }
  const edges = [];
  const names = new Set([chosen.name]);
  if (chosen.parent) {
    names.add(chosen.parent);
    edges.push({ from: chosen.parent, to: chosen.name, rel: 'station', via: 'собирается на' });
  }
  for (const made of chosen.makes) {
    names.add(made);
    edges.push({ from: chosen.name, to: made, rel: 'made', via: 'делается здесь' });
  }
  for (const target of chosen.inputs_to) {
    names.add(target);
    edges.push({ from: chosen.name, to: target, rel: 'part', via: 'входит в' });
  }
  dom.hint.textContent = `«${chosen.name}»: ${chosen.makes.length} `
    + `${plural(chosen.makes.length, 'рецепт', 'рецепта', 'рецептов')}`
    + (chosen.operations.length ? `, операций ${chosen.operations.length}` : '');
  return { nodes: [...names].map(node), edges };
}

function recipePicture() {
  const all = app.state.nodes;
  const edges = app.state.edges.filter((edge) => (app.stations ? true : edge.rel === 'input'));
  let nodes;
  if (app.mode === 'all') {
    const shown = new Set(all.filter((node) => !app.hidden.has(typeOf(node))).map((n) => n.name));
    nodes = all.filter((node) => shown.has(node.name));
    dom.hint.textContent = things(nodes.length);
  } else if (!app.selected) {
    nodes = [];
    dom.hint.textContent = 'выберите вещь';
  } else {
    const near = neighbourhood(app.selected, edges, app.back, app.forward, app.stations);
    nodes = all.filter((node) => near.has(node.name));
    dom.hint.textContent = `${things(nodes.length)} вокруг «${app.selected}»`;
  }
  const names = new Set(nodes.map((node) => node.name));
  return { nodes, edges: edges.filter((edge) => names.has(edge.from) && names.has(edge.to)) };
}

function setMode(mode, redraw = true) {
  app.mode = mode;
  app.modes[app.tab] = mode;
  for (const button of document.getElementById('mode').children) {
    button.classList.toggle('on', button.dataset.mode === mode);
  }
  if (redraw) drawGraph();
}

// Вкладка меняет только то, что нарисовано: выбранная вещь, панель справа и
// поиск переживают переключение. Со станции на её рецепт и обратно ходят часто.
function setTab(tab) {
  app.tab = tab;
  for (const button of document.getElementById('tabs').children) {
    button.classList.toggle('on', button.dataset.tab === tab);
  }
  for (const control of document.querySelectorAll('.recipes-only')) {
    control.hidden = tab === 'stations';
  }
  const modes = document.getElementById('mode');
  modes.querySelector('[data-mode="all"]').textContent = tab === 'stations'
    ? 'Все станции' : 'Вся лестница';
  modes.querySelector('[data-mode="focus"]').title = tab === 'stations'
    ? 'что делают на выбранной станции и куда она входит' : '';
  dom.search.placeholder = tab === 'stations'
    ? 'поиск: станция или что на ней делают'
    : 'поиск: название, вход, станция';
  // Фокусу нечего показывать, если выбранное — не станция.
  if (tab === 'stations' && !station(app.selected)) app.modes.stations = 'all';
  setMode(app.modes[tab], false);
  renderFilters();
  renderLegend();
  renderList();
  drawGraph();
}

function select(name, { focus = false } = {}) {
  if (!known(name)) return;
  // «Сделать центром» на общей лестнице означает уйти в фокус вокруг вещи:
  // иначе двойной щелчок там не делает ничего.
  if (focus && app.mode !== 'focus') setMode('focus', false);
  const refocus = focus || app.mode === 'focus';
  app.selected = name;
  for (const row of dom.list.querySelectorAll('.row')) {
    row.classList.toggle('sel', row.dataset.name === name);
  }
  const row = dom.list.querySelector(`.row[data-name="${CSS.escape(name)}"]`);
  row?.scrollIntoView({ block: 'nearest' });
  panel.open(name);
  if (refocus) drawGraph();
  else {
    graph.setSelected(name);
    graph.centreOn(name);
  }
}

// ------------------------------------------------------------------- console

function say(text, bad = false, title = null) {
  dom.consoleOut.textContent = text || '';
  dom.consoleTitle.textContent = title || (bad ? 'проверка нашла новые проблемы' : 'готово');
  dom.consoleTitle.className = bad ? 'bad' : 'ok';
  dom.consoleBox.classList.remove('folded');
}

function reportRun(result, what) {
  const bad = result.code !== 0;
  say(result.output || '(без вывода)', bad, `${what}: ${bad ? 'проблемы' : 'чисто'}`);
}

async function afterWrite(result, openName) {
  await load();
  if (result.check) reportRun(result.check, 'проверка вольта');
  else say('записано', false, 'записано');
  if (openName) select(openName);
  else panel.clear();
}

async function run(what, call, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = '…';
  try {
    const result = await call();
    if (result.check) reportRun(result.check, what);
    else if (result.output !== undefined) reportRun(result, what);
    else say(JSON.stringify(result), false, what);
    await load();
    if (app.selected) panel.open(app.selected);
  } catch (error) {
    say(error.message, true, `${what}: не вышло`);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

// -------------------------------------------------------------------- wiring

dom.search.addEventListener('input', (event) => {
  app.query = event.target.value;
  renderList();
});

document.getElementById('mode').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (button) setMode(button.dataset.mode);
});

document.getElementById('tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (button) setTab(button.dataset.tab);
});

for (const [id, key] of [['depth-in', 'back'], ['depth-out', 'forward']]) {
  const input = document.getElementById(id);
  const output = document.getElementById(`${id}-out`);
  input.addEventListener('input', () => {
    app[key] = Number(input.value);
    output.textContent = input.value;
    if (app.mode === 'focus') drawGraph();
  });
}

document.getElementById('show-stations').addEventListener('change', (event) => {
  app.stations = event.target.checked;
  drawGraph();
});
document.getElementById('show-amounts').addEventListener('change', (event) => {
  app.amounts = event.target.checked;
  drawGraph();
});
document.getElementById('act-fit').addEventListener('click', () => graph.fit());

document.getElementById('act-new').addEventListener('click', () => {
  const node = app.nodes.get(app.selected);
  panel.openNew(node && node.type === 'recipe'
    ? { level: node.level, section: node.section, station: node.station }
    : {});
});

document.getElementById('act-check').addEventListener('click', (event) => {
  run('проверка вольта', api.check, event.target);
});
document.getElementById('act-build').addEventListener('click', (event) => {
  run('сборка вольта', api.build, event.target);
});
document.getElementById('act-sync').addEventListener('click', (event) => {
  run('слепок в репозиторий', api.sync, event.target);
});
document.getElementById('act-undo').addEventListener('click', (event) => {
  run('откат последней правки', api.undo, event.target);
});

document.getElementById('console-toggle').addEventListener('click', (event) => {
  const folded = dom.consoleBox.classList.toggle('folded');
  event.target.textContent = folded ? 'развернуть' : 'свернуть';
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'f' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    dom.search.focus();
    dom.search.select();
  }
  if (event.key === 's' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    panel.save();
  }
});

window.addEventListener('resize', () => graph.fit());

load(false)
  .then(() => say('файл прочитан, правки пока не было', false, 'готово'))
  .catch((error) => say(error.message, true, 'не удалось прочитать вольт'));
