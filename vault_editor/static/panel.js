// The right-hand panel: the form that writes a recipe, and everything the build
// derived from it.
//
// The split matters and the panel keeps it visible. White fields are authored --
// they end up in `data/recipes.yaml` as written. Grey figures underneath are
// derived by `tools/build.py` from labour (D-133) and are shown only so that the
// person editing sees what their change did to the numbers.

import { api } from './api.js';
import { colourOf } from './graphview.js';
import {
  ask, h, joinHours, num, plural, spellTime, splitHours, TIME_LABEL, TIME_PARTS,
} from './ui.js';

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
      const node = deps.getNode(name) || {};
      state = { original: name, isNew: false, readOnly: true, measure: measureState(name, node) };
      renderInfo(payload);
      return;
    }
    state = {
      original: name,
      isNew: false,
      level: payload.level,
      section: payload.section,
      data: structuredClone(payload.data),
      measure: measureState(name, deps.getNode(name) || {}),
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
      measure: { name: '', unit: '', mass: '', bulk: false, withMass: false },
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
        h('fieldset', {},
          h('legend', { text: 'измерение' }),
          measureFields(),
          h('div', { class: 'panel-actions' },
            h('button', { text: 'Записать', onclick: (event) => saveMeasure(event.target) }),
            h('div', { class: 'spacer' }),
            h('span', { class: 'note-line', id: 'panel-error' }),
          ),
        ),
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
        h('div', { class: 'flags' },
          FLAGS.map(([key, label, title]) => h('label', { title },
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
          )),
        ),
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
            placeholder: node.matter != null
              ? `не больше вошедшего: ${num(node.matter)}`
              : (node.mass != null ? `выводится: ${num(node.mass)}` : 'выводится сборкой'),
            title: 'масса единицы. Пусто — берётся от вошедшего вещества либо от '
              + 'умолчания по типу. Больше вошедшего задать нельзя: материя при '
              + 'переделе не появляется',
            oninput: setNumber('mass'),
          }),
        ),
        timeField(data, node),
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
        // Измерение стоит здесь же, хотя пишется в `meta`: для того, кто правит
        // вещь, «дробное» и «единица» — такие же её свойства, как масса, и
        // делить это на два окна значило бы объяснять читателю устройство файла.
        measureFields(),
      ),
      h('div', { class: 'err', id: 'panel-error' }),
      h('div', { class: 'panel-actions' },
        h('button', { class: 'primary', onclick: save, text: state.isNew ? 'Создать' : 'Сохранить' }),
        h('button', {
          onclick: () => (state.isNew ? clear() : open(state.original)),
          text: 'Сбросить',
          title: 'вернуть поля к тому, что записано в вольте',
        }),
      ),
      state.isNew ? null : derivedBlock(detail, node),
      state.isNew ? null : referencesBlock(detail.references),
      state.isNew ? null : sourceBlock(detail),
    );

    root.replaceChildren(
      head(state.isNew ? 'Новый рецепт' : state.original, node, { removable: !state.isNew }),
      form,
    );
  }

  function touch() {
    const error = root.querySelector('#panel-error');
    if (error) error.textContent = '';
  }

  // -- pieces ----------------------------------------------------------------

  function head(title, node, { removable = false } = {}) {
    return h('div', { class: 'panel-head' },
      h('span', {
        class: 'dot',
        style: `width:9px;height:9px;border-radius:50%;background:${colourOf(node)}`,
      }),
      h('h2', { text: title }),
      node.depth != null ? h('span', { class: 'tag', text: `ступень ${node.depth}` }) : null,
      // Удаление стоит у названия, а не под формой: оно про вещь целиком, а не
      // про то, что в форме набрано, и его не ищут среди «Сохранить».
      removable
        ? h('button', { class: 'danger', onclick: remove, text: 'Удалить', title: 'вырезать рецепт из файла' })
        : null,
    );
  }

  // -- время -----------------------------------------------------------------

  function timeField(data, node) {
    // Часы, минуты, секунды — всегда все три, даже если время в секундах: клетка
    // на своём месте читается быстрее, чем подпись, которая переезжает. В файл
    // уезжают часы: там их считает сборка.
    const derivedHours = node.step_hours;
    const split = splitHours(data.hours ?? 0);
    const hint = splitHours(derivedHours ?? 0);

    return h('div', { class: 'field' },
      h('label', {
        text: 'время',
        title: 'собственное время изготовления единицы. Пусто — растёт от глубины '
          + 'передела (D-133). Заданное вручную идёт и в количества входов',
      }),
      h('div', { class: 'time' }, TIME_PARTS.map((part) => h('label', { class: 'unit' },
        h('input', {
          type: 'number', min: '0', step: '1',
          value: data.hours == null ? '' : String(split[part]),
          placeholder: derivedHours != null ? String(hint[part]) : '0',
          onchange: (event) => {
            const typed = { ...split, [part]: Number(event.target.value || 0) };
            const total = joinHours(typed);
            if (total > 0) data.hours = Number(total.toFixed(6));
            else delete data.hours;
            render();
          },
        }),
        TIME_LABEL[part],
      ))),
    );
  }

  // -- измерение -------------------------------------------------------------

  function bulkFlag() {
    const measure = state.measure;
    return h('label', {
      title: 'весовое: количество бывает дробным (D-212). Штучное — всегда целое, '
        + 'половины слитка не бывает',
    },
    h('input', {
      type: 'checkbox',
      checked: measure.bulk,
      onchange: (event) => { measure.bulk = event.target.checked; render(); },
    }),
    'дробное');
  }

  function measureFields() {
    const measure = state.measure;
    const node = deps.getNode(measure.name) || {};
    return h('div', {},
      measure.withMass
        ? h('div', { class: 'field' },
          h('label', { text: 'масса, кг' }),
          h('input', {
            type: 'number', step: 'any', min: '0', value: num(measure.mass),
            placeholder: node.mass != null
              ? `в прошлой сборке: ${num(node.mass)}` : 'задаётся руками: выводить не из чего',
            title: 'масса единицы. У сырья и продуктов операций она основание всей '
              + 'системы масс: изделие не тяжелее того, что в него вошло',
            oninput: (event) => { measure.mass = event.target.value; touch(); },
          }),
        )
        : null,
      // «Дробное» стоит вплотную к единице не для красоты: вместе они и
      // читаются — «3 м» дробными, «5 шт» целыми, — а порознь спрашивают
      // дважды об одном.
      h('div', { class: 'field' },
        h('label', { text: 'единица' }),
        h('div', { class: 'unit-row' },
          h('input', {
            value: measure.unit, maxlength: 12,
            placeholder: measure.bulk ? 'без подписи' : 'шт.',
            title: 'дорисовывается рядом с числом: «5 шт», «3 м». Только для показа',
            oninput: (event) => { measure.unit = event.target.value; touch(); },
          }),
          bulkFlag(),
        ),
      ),
    );
  }

  function measureState(name, node) {
    const vocab = deps.vocabulary();
    return {
      name,
      unit: (vocab.units || {})[name] ?? '',
      mass: (vocab.masses || {})[name] ?? '',
      bulk: !!node.bulk,
      withMass: node.type === 'raw' || node.type === 'operation',
    };
  }

  function measureChanged() {
    const was = measureState(state.measure.name, deps.getNode(state.measure.name) || {});
    return was.unit !== state.measure.unit
      || was.bulk !== state.measure.bulk
      || String(was.mass) !== String(state.measure.mass);
  }

  function measurePayload(name) {
    const body = { unit: state.measure.unit, bulk: state.measure.bulk, name };
    if (state.measure.withMass) {
      body.mass = state.measure.mass === '' ? null : Number(state.measure.mass);
    }
    return body;
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
    if (node.labor_hours != null) rows.push(['труд', spellTime(node.labor_hours)]);
    if (node.step_hours != null) rows.push(['своё время', spellTime(node.step_hours)]);
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
        const made = await api.create(body);
        deps.onWrite(await alsoMeasure(made, data.name), data.name);
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
      const saved = await api.update(state.original, body);
      deps.onWrite(await alsoMeasure(saved, data.name), data.name);
    } catch (error) {
      fail(error);
    }
  }

  /** Дописать измерение, если его трогали. Порядок важен: сперва строка
   *  рецепта — она может отказать по составу, — и только потом `meta`. */
  async function alsoMeasure(result, name) {
    if (!measureChanged()) return result;
    return api.measure(name, measurePayload(name));
  }

  async function saveMeasure(button) {
    button.disabled = true;
    try {
      deps.onWrite(await api.measure(state.measure.name, measurePayload(state.measure.name)),
        state.measure.name);
    } catch (error) {
      fail(error);
      button.disabled = false;
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
