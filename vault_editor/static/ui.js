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
