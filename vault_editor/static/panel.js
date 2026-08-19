// The right-hand panel: the form that writes a recipe, and everything the build
// derived from it.
//
// The split matters and the panel keeps it visible. White fields are authored --
// they end up in `data/recipes.yaml` as written. Grey figures underneath are
// derived by `tools/build.py` from labour (D-133) and are shown only so that the
// person editing sees what their change did to the numbers.

import { api } from './api.js';
import { colourOf } from './graphview.js';
import { ask, h, num, plural } from './ui.js';

const KIND_TITLE = {
  station: 'рабочая станция',
  furniture: 'мебель',
  tool: 'инструмент',
  gear: 'снаряжение',
  vehicle: 'транспорт',
  material: 'материал',
  consumable: 'расходник',
  money: 'монета',
};

const FLAGS = [
  ['key', 'веха', 'ступень лестницы, в тексте набирается жирным'],
  ['mix', 'смесь', 'состав задан пропорцией, а не штуками (D-092)'],
  ['roles', 'роли', 'входы — это роли, а не точный состав (D-119). Только у блюд'],
  ['food', 'еда', 'годится в котёл и в рот'],
  ['hot', 'горячее', 'горячее блюдо'],
];

export function createPanel(root, deps) {
  let state = null;
  let detail = null;

  function clear() {
    state = null;
    detail = null;
    root.replaceChildren(h('div', { class: 'empty', text: 'Выберите вещь слева или на графе.' }));
  }

  async function open(name) {
    let payload;
    try {
      payload = await api.recipe(name);
    } catch (error) {
      root.replaceChildren(h('div', { class: 'empty err', text: error.message }));
      return;
    }
    detail = payload;
    if (!payload.editable) {
      state = null;
      renderInfo(payload);
      return;
    }
    state = {
      original: name,
      isNew: false,
      level: payload.level,
      section: payload.section,
      data: structuredClone(payload.data),
    };
    render();
  }

  // Where a new recipe lands when nothing else says: the level of the thing
  // being looked at, and -- on a level split into sections -- its first section,
  // because such a level keeps no list of its own.
  function placeIn(levelId, section) {
    const levels = deps.vocabulary().levels;
    const level = levels.find((item) => item.id === Number(levelId)) || levels[0];
    if (section && level.sections.some((item) => item.id === section)) return [level.id, section];
    if (!level.plain && level.sections.length) return [level.id, level.sections[0].id];
    return [level.id, null];
  }

  function openNew(defaults = {}) {
    const levels = deps.vocabulary().levels;
    const [level, section] = placeIn(defaults.level ?? levels[0]?.id, defaults.section);
    detail = null;
    state = {
      original: null,
      isNew: true,
      level,
      section,
      data: {
        name: '',
        kind: 'material',
        inputs: defaults.inputs ? [...defaults.inputs] : [''],
        station: defaults.station || 'Верстак',
      },
    };
    render();
  }

  // -- read-only things ------------------------------------------------------

  function renderInfo(payload) {
    const node = deps.getNode(payload.name) || {};
    const what = {
      raw: 'сырьё — берётся из мира, ничем не изготавливается',
      operation: 'продукт операции — делается без рецепта',
      class: 'класс инструмента — закрывается любым из списка',
      virtual: 'рабочее место без рецепта: руки либо стройплощадка. '
        + 'Рецепта у него нет и быть не может',
    }[node.type] || 'вещь вольта';

    root.replaceChildren(
      head(payload.name, node),
      h('div', { class: 'form' },
        h('div', { class: 'note-line', text: what }),
        node.type === 'operation' && node.operations
          ? h('div', { class: 'note-line', text: `операции: ${node.operations.join(', ')}` })
          : null,
        node.type === 'class' && node.members
          ? h('div', { class: 'refs' }, node.members.map((m) => refButton(m))) : null,
        derivedBlock(payload, node),
        referencesBlock(payload.references),
        h('div', { class: 'note-line' },
          'Сырьё, операции и классы правятся в файле руками: у них нет формы, '
          + 'потому что их немного и каждая строка там объясняется комментарием.'),
      ),
    );
  }

  // -- the form --------------------------------------------------------------

  function render() {
    const vocab = deps.vocabulary();
    const data = state.data;
    const node = deps.getNode(state.original) || {};
    const derived = detail?.derived?.amounts || {};

    const set = (key) => (event) => {
      const value = event.target.value;
      if (value === '') delete data[key];
      else data[key] = value;
      touch();
    };
    const setNumber = (key) => (event) => {
      const value = event.target.value;
      if (value === '') delete data[key];
      else data[key] = Number(value);
      touch();
    };

    const levels = vocab.levels;
    const level = levels.find((item) => item.id === Number(state.level));

    const form = h('div', { class: 'form' },
      h('div', { class: 'field' },
        h('label', { text: 'название' }),
        h('input', { value: data.name || '', oninput: set('name'), autofocus: state.isNew }),
      ),
      h('div', { class: 'field' },
        h('label', { text: 'тип' }),
        select(vocab.kinds, data.kind, (value) => { data.kind = value; touch(); },
          (kind) => `${kind} — ${KIND_TITLE[kind] || ''}`),
      ),
      h('div', { class: 'field' },
        h('label', { text: 'станция' }),
        select(vocab.stations, data.station, (value) => { data.station = value; touch(); }),
      ),
      h('div', { class: 'field' },
        h('label', { text: 'место' }),
        h('div', { class: 'pair' },
          select(levels.map((item) => String(item.id)), String(state.level), (value) => {
            [state.level, state.section] = placeIn(value, null);
            render();
          }, (id) => {
            const found = levels.find((item) => String(item.id) === id);
            return `${id}. ${found ? found.title : ''}`;
          }),
          level && level.sections.length
            ? select(
              [...(level.plain ? [''] : []), ...level.sections.map((s) => s.id)],
              state.section || '',
              (value) => { state.section = value || null; touch(); },
              (id) => (id ? (level.sections.find((s) => s.id === id)?.title || id) : '— без раздела'),
            )
            : h('span', { class: 'note-line', text: 'разделов нет' }),
        ),
      ),
      inputsBlock(data, derived),
      h('fieldset', {},
        h('legend', { text: 'свойства' }),
        h('div', { class: 'flags' }, FLAGS.map(([key, label, title]) => h('label', { title },
          h('input', {
            type: 'checkbox',
            checked: !!data[key],
            onchange: (event) => {
              if (event.target.checked) data[key] = true;
              else delete data[key];
              touch();
            },
          }),
          label,
        ))),
        h('div', { class: 'field', style: 'margin-top:8px' },
          h('label', { text: 'слот' }),
          select(['', ...vocab.slots], data.slot || '', (value) => {
            if (value) data.slot = value; else delete data.slot;
            touch();
          }, (slot) => slot || '— не надевается'),
        ),
        h('div', { class: 'field' },
          h('label', { text: 'масса, кг' }),
          h('input', {
            type: 'number', step: 'any', min: '0', value: num(data.mass),
            placeholder: node.mass != null ? `выводится: ${num(node.mass)}` : 'выводится сборкой',
            oninput: setNumber('mass'),
          }),
        ),
        h('div', { class: 'field' },
          h('label', { text: 'вмещает, кг' }),
          h('input', {
            type: 'number', step: 'any', min: '0', value: num(data.store),
            placeholder: 'только у хранилищ (D-181)', oninput: setNumber('store'),
          }),
        ),
        h('div', { class: 'field' },
          h('label', { text: 'пометка' }),
          h('input', { value: data.note || '', oninput: set('note'), placeholder: 'note' }),
        ),
      ),
      h('div', { class: 'err', id: 'panel-error' }),
      h('div', { class: 'panel-actions' },
        h('button', { class: 'primary', onclick: save, text: state.isNew ? 'Создать' : 'Сохранить' }),
        h('button', { onclick: () => (state.isNew ? clear() : open(state.original)), text: 'Сбросить' }),
        h('div', { class: 'spacer' }),
        state.isNew ? null : h('button', { class: 'danger', onclick: remove, text: 'Удалить' }),
      ),
      state.isNew ? null : derivedBlock(detail, node),
      state.isNew ? null : referencesBlock(detail.references),
      state.isNew ? null : sourceBlock(detail),
    );

    root.replaceChildren(head(state.isNew ? 'Новый рецепт' : state.original, node), form);
  }

  function touch() {
    const error = root.querySelector('#panel-error');
    if (error) error.textContent = '';
  }

  // -- pieces ----------------------------------------------------------------

  function head(title, node) {
    return h('div', { class: 'panel-head' },
      h('span', {
        class: 'dot',
        style: `width:9px;height:9px;border-radius:50%;background:${colourOf(node)}`,
      }),
      h('h2', { text: title }),
      node.depth != null ? h('span', { class: 'tag', text: `ступень ${node.depth}` }) : null,
    );
  }

  function inputsBlock(data, derived) {
    const rows = (data.inputs || []).map((initial, index) => {
      // The row is tied to its position, not to the name it had when drawn: the
      // name field is edited in place, and a quantity typed afterwards must land
      // on the new name, not on the one that was there a keystroke ago.
      const at = () => data.inputs[index];
      const amount = data.amounts?.[initial];
      return h('div', { class: 'inp' },
        h('input', {
          value: initial, list: 'all-names', placeholder: 'вход',
          oninput: (event) => {
            const was = at();
            const next = event.target.value;
            if (data.amounts && was in data.amounts) {
              data.amounts[next] = data.amounts[was];
              delete data.amounts[was];
            }
            if (data.highlight?.includes(was)) {
              data.highlight[data.highlight.indexOf(was)] = next;
            }
            data.inputs[index] = next;
            touch();
          },
        }),
        h('input', {
          type: 'number', step: 'any', min: '0', value: num(amount),
          placeholder: derived[initial] != null ? num(derived[initial]) : 'выв.',
          title: 'количество вручную — исключение (D-133). Пусто — выводится из трудоёмкости',
          oninput: (event) => {
            const value = event.target.value;
            data.amounts = data.amounts || {};
            if (value === '') delete data.amounts[at()];
            else data.amounts[at()] = Number(value);
            if (!Object.keys(data.amounts).length) delete data.amounts;
            touch();
          },
        }),
        h('button', {
          class: 'star' + (data.highlight?.includes(initial) ? ' on' : ''),
          title: 'узкое место ветки: в тексте набирается жирным',
          text: '★',
          onclick: () => {
            data.highlight = data.highlight || [];
            const found = data.highlight.indexOf(at());
            if (found >= 0) data.highlight.splice(found, 1);
            else data.highlight.push(at());
            if (!data.highlight.length) delete data.highlight;
            render();
          },
        }),
        h('button', {
          class: 'del', text: '×', title: 'убрать вход',
          onclick: () => {
            const gone = at();
            data.inputs.splice(index, 1);
            if (data.amounts) delete data.amounts[gone];
            if (data.highlight) data.highlight = data.highlight.filter((item) => item !== gone);
            render();
          },
        }),
      );
    });

    return h('fieldset', {},
      h('legend', { text: 'из чего делается' }),
      h('div', { class: 'inputs' }, rows),
      h('div', { class: 'panel-actions' },
        h('button', {
          text: '+ вход',
          onclick: () => { data.inputs = [...(data.inputs || []), '']; render(); },
        }),
        h('div', { class: 'spacer' }),
        h('span', {
          class: 'note-line',
          text: data.amounts ? 'количества заданы вручную' : 'количества выводит сборка',
        }),
      ),
    );
  }

  function derivedBlock(payload, node) {
    const cost = payload?.cost;
    const rows = [];
    if (node.labor_hours != null) rows.push(['труд', `${num(node.labor_hours)} ч`]);
    if (node.mass != null) rows.push(['масса', `${num(node.mass)} кг`]);
    if (payload?.derived?.amounts) {
      for (const [item, value] of Object.entries(payload.derived.amounts)) {
        rows.push([`· ${item}`, num(value)]);
      }
    }
    const totals = cost && Object.entries(cost.totals || {});
    return h('fieldset', {},
      h('legend', { text: 'выведено сборкой' }),
      h('div', { class: 'derived' },
        rows.length
          ? h('table', {}, rows.map(([left, right]) => h('tr', {},
            h('td', { class: left.startsWith('·') ? 'muted' : '', text: left }),
            h('td', { text: right }),
          )))
          : h('div', { class: 'muted', text: 'сборка ещё не считала эту вещь' }),
        totals && totals.length
          ? h('details', { style: 'margin-top:6px' },
            h('summary', { text: `в сырье: ${num(cost.mass)} кг` }),
            h('table', {}, totals.map(([item, value]) => h('tr', {},
              h('td', { class: 'muted', text: item }),
              h('td', { text: num(value) }),
            ))))
          : null,
      ),
    );
  }

  function referencesBlock(references) {
    if (!references) return null;
    const groups = [
      ['входит в', references.inputs],
      ['станция для', references.stations],
      ['в операциях', references.operations],
      ['в классах', references.classes],
      ['в списках', references.lists],
    ].filter(([, items]) => items && items.length);
    if (!groups.length) {
      return h('fieldset', {},
        h('legend', { text: 'где используется' }),
        h('div', { class: 'note-line', text: 'нигде: тупик лестницы либо конечная вещь' }));
    }
    return h('fieldset', {},
      h('legend', { text: 'где используется' }),
      groups.map(([title, items]) => h('div', { style: 'margin-bottom:6px' },
        h('div', { class: 'note-line', text: `${title} (${items.length})` }),
        h('div', { class: 'refs' }, items.map((item) => refButton(item))),
      )),
    );
  }

  function refButton(name) {
    return h('button', { class: 'ref', text: name, onclick: () => deps.onSelect(name) });
  }

  function sourceBlock(payload) {
    if (!payload?.source) return null;
    return h('fieldset', {},
      h('legend', { text: 'строка в файле' }),
      h('pre', { class: 'src' },
        payload.comment?.length
          ? h('span', { class: 'cmt', text: `${payload.comment.join('\n')}\n` })
          : null,
        payload.source),
    );
  }

  function select(values, current, onchange, label = (value) => value) {
    const box = h('select', { onchange: (event) => onchange(event.target.value) });
    for (const value of values) {
      box.append(h('option', { value, selected: String(value) === String(current) }, label(value)));
    }
    return box;
  }

  // -- writing ---------------------------------------------------------------

  function collect() {
    const data = { ...state.data };
    data.name = (data.name || '').trim();
    data.inputs = (data.inputs || []).map((item) => item.trim()).filter(Boolean);
    if (data.amounts) {
      data.amounts = Object.fromEntries(
        Object.entries(data.amounts).filter(([key]) => data.inputs.includes(key)),
      );
      if (!Object.keys(data.amounts).length) delete data.amounts;
    }
    if (data.highlight) {
      data.highlight = data.highlight.filter((item) => data.inputs.includes(item));
      if (!data.highlight.length) delete data.highlight;
    }
    return data;
  }

  function fail(error) {
    const box = root.querySelector('#panel-error');
    if (box) box.textContent = error.message;
    else deps.notify(error.message, true);
  }

  async function save() {
    const data = collect();
    const body = { data, level: state.level, section: state.section };
    try {
      if (state.isNew) {
        deps.onWrite(await api.create(body), data.name);
        return;
      }
      if (data.name !== state.original) {
        const references = detail.references || {};
        const count = Object.values(references).reduce((sum, list) => sum + list.length, 0);
        if (count) {
          const answer = await ask({
            title: `Переименовать «${state.original}» → «${data.name}»`,
            body: `Старое название упоминается в ${count} ${plural(count, 'месте', 'местах', 'местах')}: `
              + `${[...references.inputs, ...references.stations, ...references.operations]
                .slice(0, 8).join(', ')}${count > 8 ? ' и других' : ''}. `
              + 'Без обновления ссылок лестница развалится, и проверка это покажет.',
            ok: 'Переименовать',
            danger: false,
            extra: 'обновить ссылки во всём файле',
            extraChecked: true,
          });
          if (!answer) return;
          body.rename_refs = answer.extra;
        }
      }
      deps.onWrite(await api.update(state.original, body), data.name);
    } catch (error) {
      fail(error);
    }
  }

  async function remove() {
    const references = detail.references || {};
    const used = [...references.inputs, ...references.stations, ...references.operations];
    const answer = await ask({
      title: `Удалить «${state.original}»?`,
      body: used.length
        ? `Вещь используется в ${used.length} ${plural(used.length, 'месте', 'местах', 'местах')}: `
          + `${used.slice(0, 10).join(', ')}`
          + `${used.length > 10 ? ' и других' : ''}. После удаления они останутся без входа, `
          + 'и проверка покажет разрыв.'
        : 'Ни на что не ссылается. Строка будет вырезана из файла.',
      ok: 'Удалить',
      extra: detail.comment?.length ? 'удалить и комментарий над строкой' : null,
      extraChecked: true,
    });
    if (!answer) return;
    try {
      deps.onWrite(await api.remove(state.original, { with_comment: answer.extra }), null);
    } catch (error) {
      fail(error);
    }
  }

  return {
    open,
    openNew,
    clear,
    save: () => (state ? save() : null),
    get current() { return state?.original || null; },
  };
}
