// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A person's name wherever it is said (D-222).
 *
 * Right-click opens the one menu a name has: the card. The card is where
 * "write" lives -- so that writing to somebody is a decision made looking at
 * who they are, and an empty thread that stays in the list is that decision
 * written down. Left-click does nothing on purpose: in the room's talk a name
 * is read, not pressed, and a stray click must not cover the world with a
 * window.
 */

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { t } from "./locale";
import { askProfile } from "./people";

type Props = {
  name: string;
  /** The text to show, if not the bare name: "Tern Odd", "Tern:". */
  children?: ReactNode;
  className?: string;
};

export function PersonName({ name, children, className }: Props) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);

  //: Any press outside shuts the menu -- the same rule as the map's.
  useEffect(() => {
    if (!menu) return;
    const shut = () => setMenu(null);
    window.addEventListener("pointerdown", shut);
    window.addEventListener("keydown", shut);
    return () => {
      window.removeEventListener("pointerdown", shut);
      window.removeEventListener("keydown", shut);
    };
  }, [menu]);

  return (
    <>
      <span
        className={className ? `person ${className}` : "person"}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenu({ x: e.clientX, y: e.clientY });
        }}
      >
        {children ?? name}
      </span>
      {/* The menu floats over the page, so it is rendered on the page and not
          inside the paragraph the name is in: a block inside a `<p>` is not
          HTML, and the browser would say so. */}
      {menu &&
        createPortal(
          <div
            className="node-menu"
            role="menu"
            style={{ left: menu.x, top: menu.y }}
            onPointerDown={(e) => e.stopPropagation()}
          >
            <p className="menu-ask">{name}</p>
            <button
              role="menuitem"
              onClick={() => {
                setMenu(null);
                askProfile(name);
              }}
            >
              {t("ui-person-profile")}
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
