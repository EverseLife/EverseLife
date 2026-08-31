// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A password field with an eye (D-187).
 *
 * Showing the password means opening the eye: the lid rises, the pupil looks
 * at the line and follows the typing, a glint passes over the field, and dots
 * become letters. Hiding -- the lid lowers. One button, one state; everything else is CSS.
 *
 * The pupil follows the length of what is entered: the longer the password,
 * the further right the gaze. This is not a strength indicator -- the eye
 * just looks where the typing is.
 */

import { useId, useState } from "react";
import { t } from "../locale";

type Props = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoComplete?: string;
  disabled?: boolean;
  /** Mark the field as erroneous: a border, not text -- the parent gives the text. */
  invalid?: boolean;
};

export function Secret({
  value,
  onChange,
  //: A default read at render time rather than at module load: the words are
  //: learnt after the first paint, and a constant here would freeze the
  //: language the client booted with.
  placeholder = t("ui-secret-password"),
  autoComplete = "current-password",
  disabled,
  invalid,
}: Props) {
  const [open, setOpen] = useState(false);
  //: The toggle counter is the glint's key: every opening plays it anew.
  const [blink, setBlink] = useState(0);
  const id = useId();

  //: The gaze: from -6 to +6 horizontally, by the length of what is typed.
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
        aria-label={open ? t("ui-secret-hide-label") : t("ui-secret-show-label")}
        title={open ? t("ui-secret-hide") : t("ui-secret-show")}
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
