/**
 * Поле пароля с глазом (D-187).
 *
 * Показать пароль — значит открыть глаз: веко поднимается, зрачок смотрит на
 * строку и провожает набор, по полю проходит блик, и точки становятся буквами.
 * Скрыть — веко опускается. Одна кнопка, одно состояние; всё остальное — CSS.
 *
 * Зрачок следит за длиной введённого: чем длиннее пароль, тем правее взгляд.
 * Это не индикатор силы — просто глаз смотрит туда, где печатают.
 */

import { useId, useState } from "react";

type Props = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
  disabled?: boolean;
  /** Пометить поле как ошибочное: рамка, а не текст — текст даёт родитель. */
  invalid?: boolean;
};

export function Secret({
  value,
  onChange,
  placeholder = "пароль",
  autoComplete = "current-password",
  disabled,
  invalid,
}: Props) {
  const [open, setOpen] = useState(false);
  //: Счётчик переключений — ключ блика: каждое открытие проигрывает его заново.
  const [blink, setBlink] = useState(0);
  const id = useId();

  //: Взгляд: от −6 до +6 по горизонтали, по длине набранного.
  const gaze = Math.min(6, Math.max(-6, value.length / 2 - 4));

  const toggle = () => {
    setOpen((was) => !was);
    setBlink((n) => n + 1);
  };

  return (
    <div className={`secret ${open ? "open" : "shut"}${invalid ? " invalid" : ""}`}>
      <input
        id={id}
        type={open ? "text" : "password"}
        placeholder={placeholder}
        autoComplete={autoComplete}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
      {/* Блик, пробегающий по полю при каждом переключении. */}
      <span key={blink} className="secret-flash" aria-hidden="true" />
      <button
        type="button"
        className="eye"
        onClick={toggle}
        disabled={disabled}
        aria-pressed={open}
        aria-controls={id}
        aria-label={open ? "скрыть пароль" : "показать пароль"}
        title={open ? "скрыть" : "показать"}
      >
        <svg viewBox="0 0 32 22" width="30" height="20" aria-hidden="true">
          {/* Открытый глаз: миндалина, радужка, зрачок. Зрачок смотрит туда,
              где печатают. */}
          <g className="eye-open">
            <path
              d="M2 11 Q16 -2 30 11 Q16 24 2 11 Z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <g className="eye-iris" style={{ transform: `translateX(${gaze}px)` }}>
              <circle cx="16" cy="11" r="5" fill="none" stroke="currentColor" strokeWidth="1.5" />
              <circle cx="16" cy="11" r="2.2" fill="currentColor" />
            </g>
          </g>
          {/* Веко: заслонка цвета фона, опускается сверху и стирает радужку. */}
          <path className="eye-lid" d="M2 11 Q16 -2 30 11 Q16 24 2 11 Z" fill="Canvas" />
          {/* Закрытый глаз: нижняя дуга и три ресницы. */}
          <g className="eye-shut" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 11 Q16 24 30 11" />
            <path d="M9 16.5 L7.5 19.5" />
            <path d="M16 18 L16 21" />
            <path d="M23 16.5 L24.5 19.5" />
          </g>
        </svg>
      </button>
    </div>
  );
}
