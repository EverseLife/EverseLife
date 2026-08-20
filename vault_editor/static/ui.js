// Small shared bits: element building and the one modal the tool needs.

export function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key === 'html') node.innerHTML = value;
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

// A yes/no question with an optional extra checkbox. Resolves to null when the
// answer is no, otherwise to the state of the checkbox.
export function ask({ title, body, ok = 'Да', danger = true, extra = null, extraChecked = false }) {
  const modal = document.getElementById('modal');
  const bodyBox = document.getElementById('modal-body');
  document.getElementById('modal-title').textContent = title;
  bodyBox.replaceChildren(typeof body === 'string' ? h('div', { class: 'body', text: body }) : body);

  let box = null;
  if (extra) {
    box = h('input', { type: 'checkbox', checked: extraChecked });
    bodyBox.append(h('label', {}, box, extra));
  }
  const okButton = document.getElementById('modal-ok');
  const cancelButton = document.getElementById('modal-cancel');
  okButton.textContent = ok;
  okButton.className = danger ? 'danger' : 'primary';
  modal.hidden = false;

  return new Promise((resolve) => {
    const done = (value) => {
      modal.hidden = true;
      okButton.removeEventListener('click', yes);
      cancelButton.removeEventListener('click', no);
      document.removeEventListener('keydown', key);
      resolve(value);
    };
    const yes = () => done({ extra: box ? box.checked : false });
    const no = () => done(null);
    const key = (event) => {
      if (event.key === 'Escape') no();
      if (event.key === 'Enter') yes();
    };
    okButton.addEventListener('click', yes);
    cancelButton.addEventListener('click', no);
    document.addEventListener('keydown', key);
  });
}

export function num(value) {
  if (value == null || value === '') return '';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return Number.isInteger(number) ? String(number) : String(Math.round(number * 1000) / 1000);
}

// Русское число: «1 вещь», «2 вещи», «5 вещей». Строка на экране обязана
// читаться как речь, иначе инструмент выглядит недоделанным.
export function plural(count, one, few, many) {
  const tens = count % 100;
  const units = count % 10;
  if (units === 1 && tens !== 11) return one;
  if (units >= 2 && units <= 4 && (tens < 12 || tens > 14)) return few;
  return many;
}

export function things(count) {
  return `${count} ${plural(count, 'вещь', 'вещи', 'вещей')}`;
}

// --------------------------------------------------------------------- время
//
// В файле время лежит в часах — так его считает сборка, и трогать это незачем.
// А читает и правит его человек, для которого 0.004167 не число: это пятнадцать
// секунд. Доли всегда одни и те же — часы, минуты, секунды: сутки завели бы
// четвёртую клетку ради редкого случая, а часы в ней всё равно понадобились бы.

const SECOND = 1 / 3600;

export const TIME_PARTS = ['hours', 'minutes', 'seconds'];
export const TIME_LABEL = { hours: 'ч', minutes: 'мин', seconds: 'с' };

export function splitHours(hours) {
  let left = Math.max(0, Math.round(Number(hours || 0) / SECOND));
  const whole = Math.floor(left / 3600);
  left -= whole * 3600;
  const minutes = Math.floor(left / 60);
  return { hours: whole, minutes, seconds: left - minutes * 60 };
}

export function joinHours({ hours = 0, minutes = 0, seconds = 0 }) {
  return (hours * 3600 + minutes * 60 + seconds) * SECOND;
}

/** Время словами: «2 ч 34 мин», «15 с», «50 ч 30 мин». Нули внутри опускаются. */
export function spellTime(hours) {
  if (hours == null || hours === '') return '';
  const split = splitHours(hours);
  const shown = TIME_PARTS
    .filter((part) => split[part] > 0)
    .map((part) => `${split[part]} ${TIME_LABEL[part]}`);
  return shown.length ? shown.join(' ') : `0 ${TIME_LABEL.seconds}`;
}
