// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

(() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const raf = (fn) => requestAnimationFrame(fn);
  // The ships fly on SMIL, which the CSS rule for reduced motion cannot reach.
  if (reduced) {
    document.querySelectorAll(".system svg, h1 svg").forEach((s) => s.pauseAnimations());
    // the ribbon's clip would otherwise stay shut: show it whole and still
    document.querySelectorAll("h1 .accent path").forEach((p) => p.removeAttribute("clip-path"));
  }

  // ── Words: the page tells the script which language it is written in ───
  //    The pages themselves are two hand-written translations, so the only
  //    strings kept here are the ones this file writes into the page: the
  //    carousel's arrow labels, the living interface fragment and the form's
  //    answers.
  const SAID = {
    ru: {
      prevPlanet: "Предыдущая планета",
      nextPlanet: "Следующая планета",
      day: (n) => `сутки ${n}`,
      soon: "вот-вот",
      minutes: (m) => `${m} мин`,
      hours: (h, m) => `${h} ч ${m} мин`,
      talk: [
        ["say", "Йорун", "беру уголь партиями по пятьдесят, кто везёт с шахты?"],
        ["act", "Веста", "осматривает наковальню и качает головой"],
        ["say", "Хальвар", "к вечеру привезу сорок, цена как в стакане, задаток вперёд"],
        ["ooc", "Йорун", "кто-нибудь знает, какая ставка на этой неделе?"],
        ["say", "Веста", "две с половиной, совет держит. кредит под оборот, если что"],
        ["act", "Хальвар", "впрягается в повозку и выходит к воротам"],
        ["say", "Йорун", "задаток отправила. до Поймы полтора часа, не задерживайся"],
      ],
      // the last price walks inside the spread; the decimal mark is the
      // page's, so the moving figure matches the book printed beside it
      prices: ["3,20", "3,05", "3,20", "3,25", "3,05"],
      signedUp: "Готово. Одно письмо, когда мир откроется, — и больше ничего. До тех пор мы в ",
      failed: "Что-то пошло не так. Попробуйте ещё раз.",
      offline: "Сеть не ответила. Попробуйте ещё раз.",
    },
    en: {
      prevPlanet: "Previous planet",
      nextPlanet: "Next planet",
      day: (n) => `day ${n}`,
      soon: "any moment",
      minutes: (m) => `${m} min`,
      hours: (h, m) => `${h} h ${m} min`,
      talk: [
        ["say", "Jorunn", "buying coal in lots of fifty, who is hauling from the pit?"],
        ["act", "Vesta", "looks over the anvil and shakes her head"],
        ["say", "Halvard", "forty by evening, price off the book, deposit up front"],
        ["ooc", "Jorunn", "anyone know what the rate is this week?"],
        ["say", "Vesta", "two and a half, the council is holding it. credit against turnover if you need it"],
        ["act", "Halvard", "harnesses the cart and heads for the gate"],
        ["say", "Jorunn", "deposit sent. ninety minutes to Rivermeadow, do not dawdle"],
      ],
      prices: ["3.20", "3.05", "3.20", "3.25", "3.05"],
      signedUp: "Done. One letter when the world opens, and nothing else. Until then we are on ",
      failed: "Something went wrong. Try again.",
      offline: "The network did not answer. Try again.",
    },
  };
  //    Looked up by the page's own language, and Russian when we have no
  //    words for it -- a third language then reads the original rather than
  //    printing `undefined` where a sentence should be.
  const WORDS = SAID[document.documentElement.lang] || SAID.ru;

  // ── First paint: let the hero rise ─────────────────────────────────────
  document.fonts && document.fonts.ready
    ? document.fonts.ready.then(() => document.documentElement.classList.add("ready"))
    : document.documentElement.classList.add("ready");
  setTimeout(() => document.documentElement.classList.add("ready"), 900);

  // ── The header parts from the hero once the page moves ──────────────
  const top = document.querySelector("header.top");
  const onScroll = () => top.classList.toggle("scrolled", scrollY > 24);
  addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // ── Reveal on scroll: once, staged ────────────────────────────────────
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }, { rootMargin: "0px 0px -10% 0px", threshold: .08 });
  document.querySelectorAll(".rv, .rv-stag").forEach((el) => io.observe(el));

  // the refusal table: row by row, so the strikes land one after another
  const rows = [...document.querySelectorAll("#no-table tbody tr")];
  const rio = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) {
      const i = rows.indexOf(e.target);
      setTimeout(() => e.target.classList.add("in"), reduced ? 0 : i * 90);
      rio.unobserve(e.target);
    }
  }, { threshold: .3 });
  rows.forEach((r) => rio.observe(r));

  // ── Ticker: duplicate once so the loop is seamless ────────────────────
  const ticker = document.getElementById("ticker");
  if (ticker) ticker.innerHTML += ticker.innerHTML;

  // ── Fine-pointer stagecraft: cursor ring, magnetic buttons ────────────
  const fine = matchMedia("(pointer: fine)").matches;
  if (fine && !reduced) {
    // the ring trails the native cursor and swells over anything clickable
    const ring = document.createElement("div");
    ring.className = "cursor";
    ring.setAttribute("aria-hidden", "true");
    document.body.appendChild(ring);
    let cx = innerWidth / 2, cy = innerHeight / 2, rx = cx, ry = cy;
    addEventListener("pointermove", (e) => {
      cx = e.clientX; cy = e.clientY;
      ring.classList.add("live");
      ring.classList.toggle("hot", !!(e.target instanceof Element && e.target.closest("a, button, summary, .sel")));
    }, { passive: true });
    document.documentElement.addEventListener("pointerleave", () => ring.classList.remove("live"));
    const chase = () => {
      rx += (cx - rx) * .16; ry += (cy - ry) * .16;
      ring.style.transform = `translate(${rx}px, ${ry}px)`;
      raf(chase);
    };
    raf(chase);

    // magnetic buttons: a small pull toward the pointer, a spring back out
    // the pull goes through custom properties so :hover and :active in the
    // stylesheet still compose their own offsets on top
    document.querySelectorAll(".btn").forEach((btn) => {
      btn.addEventListener("pointermove", (e) => {
        const r = btn.getBoundingClientRect();
        const dx = e.clientX - (r.left + r.width / 2);
        const dy = e.clientY - (r.top + r.height / 2);
        btn.style.setProperty("--mx", `${dx * .14}px`);
        btn.style.setProperty("--my", `${dy * .3}px`);
      });
      btn.addEventListener("pointerleave", () => {
        btn.style.removeProperty("--mx");
        btn.style.removeProperty("--my");
      });
    });
  }

  // ── The hero system flies in the space behind it ────────────────────
  // the pointer moves it at a depth between the shader's star layers and the
  // scroll lets it fall behind the page; the figure itself carries a filled
  // entry animation whose final keyframe pins `transform`, so the drift goes
  // on the svg inside it instead
  const heroSystem = document.querySelector(".hero .system svg");
  if (heroSystem && !reduced && fine) {
    let px = 0, py = 0, lx = 0, ly = 0;
    addEventListener("pointermove", (e) => {
      px = e.clientX / innerWidth - .5; py = e.clientY / innerHeight - .5;
    }, { passive: true });
    const drift = () => {
      lx += (px - lx) * .05; ly += (py - ly) * .05;
      const y = Math.min(scrollY, innerHeight);
      heroSystem.style.transform = `translate(${lx * 26}px, ${y * .18 + ly * 20}px) scale(${1 + y * .0001})`;
      raf(drift);
    };
    raf(drift);
  }

  // ── Ships: real routes between moving planets ─────────────────────────
  // A ship departs one planet and chases another. The target keeps moving,
  // so the course is a pursuit curve with a bounded turn rate and a soft
  // push off the star; planet positions are read from the orbit groups'
  // actual CSS transforms, so the ships stay honest about where ports are.
  const shipsLayer = document.querySelector(".hero .system .ships");
  if (shipsLayer && !reduced) {
    const svgNS = "http://www.w3.org/2000/svg";
    const CENTER = 320, SPEED = 30, TURN = 1.6;
    const orbs = [...document.querySelectorAll(".hero .system .orb")].map((g) => ({
      g, body: g.querySelector(".body"),
    }));
    const portPos = (o) => {
      const m = new DOMMatrixReadOnly(getComputedStyle(o.g).transform);
      const x0 = +o.body.getAttribute("cx") - CENTER;
      const y0 = +o.body.getAttribute("cy") - CENTER;
      return { x: CENTER + m.a * x0 + m.c * y0, y: CENTER + m.b * x0 + m.d * y0 };
    };
    const mkShip = () => {
      const g = document.createElementNS(svgNS, "g");
      g.setAttribute("class", "craft");
      g.setAttribute("opacity", "0");
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("x1", "-4"); line.setAttribute("y1", "0");
      line.setAttribute("x2", "-16"); line.setAttribute("y2", "0");
      const hull = document.createElementNS(svgNS, "path");
      hull.setAttribute("d", "M6 0 L-4 -3.4 L-2 0 L-4 3.4 Z");
      g.appendChild(line); g.appendChild(hull);
      shipsLayer.appendChild(g);
      return g;
    };
    const ships = Array.from({ length: 3 }, (_, i) => ({
      el: mkShip(), flying: false, until: i * 2600,
      x: 0, y: 0, heading: 0, to: 0,
    }));
    let prev = 0;
    const sail = (ms) => {
      const dt = Math.min(.05, (ms - prev) / 1000); prev = ms;
      for (const s of ships) {
        if (!s.flying) {
          if (ms < s.until) continue;
          const from = Math.floor(Math.random() * orbs.length);
          do { s.to = Math.floor(Math.random() * orbs.length); } while (s.to === from);
          const p = portPos(orbs[from]), q = portPos(orbs[s.to]);
          s.x = p.x; s.y = p.y;
          s.heading = Math.atan2(q.y - p.y, q.x - p.x);
          s.flying = true;
          s.el.setAttribute("opacity", "1");
        }
        const q = portPos(orbs[s.to]);
        const dx = q.x - s.x, dy = q.y - s.y;
        const dist = Math.hypot(dx, dy);
        if (dist < 8) {
          s.flying = false;
          s.until = ms + 2500 + Math.random() * 6000;
          s.el.setAttribute("opacity", "0");
          continue;
        }
        // turn toward the target, but no tighter than the ship can
        let diff = Math.atan2(dy, dx) - s.heading;
        if (diff > Math.PI) diff -= 2 * Math.PI;
        if (diff < -Math.PI) diff += 2 * Math.PI;
        const cap = TURN * dt;
        s.heading += Math.max(-cap, Math.min(cap, diff));
        let vx = Math.cos(s.heading), vy = Math.sin(s.heading);
        // the star is not a waypoint: a soft radial push keeps routes around it
        const cx = s.x - CENTER, cy = s.y - CENTER;
        const cd = Math.hypot(cx, cy) || 1;
        if (cd < 56) {
          const push = (56 - cd) / 56 * 1.4;
          vx += cx / cd * push; vy += cy / cd * push;
          const vlen = Math.hypot(vx, vy);
          vx /= vlen; vy /= vlen;
          s.heading = Math.atan2(vy, vx);
        }
        s.x += vx * SPEED * dt; s.y += vy * SPEED * dt;
        s.el.setAttribute("transform", `translate(${s.x} ${s.y}) rotate(${s.heading * 180 / Math.PI})`);
      }
      raf(sail);
    };
    raf(sail);
  }

  // ── The world page: planets ride a carousel ───────────────────────────
  // The outgoing card flies past one viewport edge, the incoming one enters
  // from the other; the plain list is the no-script fallback, so everything
  // here is built at runtime and the markup stays a list.
  const planetsBox = document.querySelector(".planets");
  const planetCards = planetsBox ? [...planetsBox.querySelectorAll(".planet")] : [];
  if (planetCards.length > 1) {
    planetsBox.classList.add("carousel");
    let current = 0;
    planetCards.forEach((card, i) => {
      // the card leaves the scroll-reveal system for good: transforms belong
      // to the carousel, and `in` stays on so `.in .day .bar i` keeps the
      // day bars filled without the observer's help
      io.unobserve(card);
      card.classList.remove("rv");
      card.classList.add("in", i === 0 ? "on" : "gone-right");
    });

    const nav = document.createElement("div");
    nav.className = "car-nav";
    const mkArrow = (glyph, label, dir) => {
      const b = document.createElement("button");
      b.className = "car-btn";
      b.textContent = glyph;
      b.setAttribute("aria-label", label);
      b.addEventListener("click", () => { takeWheel(); go(current + dir, dir); });
      return b;
    };
    const tabsBox = document.createElement("div");
    tabsBox.className = "car-tabs";
    const tabs = planetCards.map((card, i) => {
      const b = document.createElement("button");
      b.className = "car-tab";
      b.textContent = card.querySelector("h3").textContent;
      b.style.setProperty("--pc", getComputedStyle(card).getPropertyValue("--pc"));
      b.setAttribute("aria-current", i === 0 ? "true" : "false");
      b.addEventListener("click", () => { takeWheel(); go(i); });
      tabsBox.appendChild(b);
      return b;
    });
    nav.appendChild(mkArrow("←", WORDS.prevPlanet, -1));
    nav.appendChild(tabsBox);
    nav.appendChild(mkArrow("→", WORDS.nextPlanet, 1));
    planetsBox.after(nav);

    const cardKey = (card) => ((card.getAttribute("style") || "").match(/--(terra|aurora|pyro|aqua)/) || [])[1];
    const park = (card, side) => {
      // place a card beyond an edge instantly, so its entry starts there
      card.classList.add("snap");
      card.classList.remove("on", "gone-left", "gone-right");
      card.classList.add(side);
      void card.offsetWidth;
      card.classList.remove("snap");
    };
    const go = (i, dirHint) => {
      i = (i + planetCards.length) % planetCards.length;
      if (i === current) return;
      const dir = dirHint != null ? dirHint : (i > current ? 1 : -1);
      const out = planetCards[current], into = planetCards[i];
      out.classList.remove("on");
      out.classList.add(dir > 0 ? "gone-left" : "gone-right");
      park(into, dir > 0 ? "gone-right" : "gone-left");
      into.classList.remove("gone-left", "gone-right");
      into.classList.add("on");
      // the scene in space.js flies the camera between the worlds; the
      // carousel only says where we are going
      dispatchEvent(new CustomEvent("everse:travel", {
        detail: { from: cardKey(out), to: cardKey(into), dir },
      }));
      current = i;
      tabs.forEach((tab, k) => tab.setAttribute("aria-current", k === i ? "true" : "false"));
    };

    // it leafs through by itself until the visitor takes the wheel
    let auto = reduced ? 0 : setInterval(() => go(current + 1, 1), 9000);
    const takeWheel = () => { if (auto) { clearInterval(auto); auto = 0; } };
    planetsBox.addEventListener("pointerdown", takeWheel);

    // a horizontal swipe over the cards leafs too -- touch only: a mouse
    // dragged across the text is selection, not navigation
    let swipeX = null;
    planetsBox.style.touchAction = "pan-y";
    planetsBox.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "touch") swipeX = e.clientX;
    });
    planetsBox.addEventListener("pointercancel", () => { swipeX = null; });
    planetsBox.addEventListener("pointerup", (e) => {
      if (swipeX !== null && Math.abs(e.clientX - swipeX) > 48) {
        const dir = e.clientX < swipeX ? 1 : -1;
        go(current + dir, dir);
      }
      swipeX = null;
    });
  }

  // ── Roles: one open at a time, the first by default ───────────────────
  document.querySelectorAll("#roles-acc .acc-h").forEach((btn) => {
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      document.querySelectorAll("#roles-acc .acc-h").forEach((b) => b.setAttribute("aria-expanded", "false"));
      btn.setAttribute("aria-expanded", open ? "false" : "true");
    });
  });

  // ── The interface fragment: it lives, but nothing in it is real data ──
  (() => {
    // the planet's clock: Terra's day is 38 hours (D-029); the epoch is illustrative
    const clock = document.getElementById("shot-clock");
    if (!clock) return;
    const DAY = 38, EPOCH = Date.parse("2026-06-20T00:00:00Z");
    const two = (n) => String(n).padStart(2, "0");
    const tickClock = () => {
      const gone = (Date.now() - EPOCH) / 3600000;
      const day = Math.floor(gone / DAY) + 1, inDay = gone % DAY;
      clock.textContent = `${WORDS.day(day)} · ${two(Math.floor(inDay))}:${two(Math.floor((inDay % 1) * 60))}`;
    };
    tickClock(); setInterval(tickClock, 15000);

    // deadline bars: fill by share, colour by remainder, words next to colour
    const spell = (s) => s <= 0 ? WORDS.soon
      : s < 3600 ? WORDS.minutes(Math.ceil(s / 60))
      : WORDS.hours(Math.floor(s / 3600), two(Math.floor((s % 3600) / 60)));
    const bars = [...document.querySelectorAll("#shot .deadline")].map((d) => ({
      el: d, bar: d.querySelector("i"), left: d.querySelector(".left"),
      total: +d.dataset.total, remain: +d.dataset.left,
    }));
    const beat = () => {
      for (const b of bars) {
        b.remain -= reduced ? 0 : 1;
        if (b.remain < -3) b.remain = b.total; // a fresh batch takes the finished one's place
        const share = Math.max(0, b.remain / b.total);
        b.bar.style.width = `${share * 100}%`;
        b.left.textContent = spell(b.remain);
        b.el.classList.toggle("near", b.remain > 0 && share < .2);
        b.el.classList.toggle("over", b.remain <= 0);
      }
    };
    beat(); setInterval(beat, 1000);

    // the talk: four voices, cycling; the speaker is the only thing painted
    const lines = WORDS.talk;
    const box = document.getElementById("chat-lines");
    let li = 0;
    const push = () => {
      const [kind, who, text] = lines[li % lines.length]; li++;
      const p = document.createElement("p");
      p.className = "line" + (kind === "act" ? " action" : kind === "ooc" ? " ooc" : "");
      p.innerHTML = kind === "act" ? `<b>${who}</b> ${text}` : kind === "ooc" ? `<b>[${who}]</b> ${text}` : `<b>${who}</b>: ${text}`;
      box.appendChild(p);
      while (box.children.length > 5) box.removeChild(box.firstChild);
    };
    push(); push(); push();
    if (!reduced) setInterval(push, 5200);

    // a deal now and then: the last price moves inside the spread, a row flashes
    const book = document.querySelectorAll("#book tbody tr");
    const last = document.getElementById("last");
    const prices = WORDS.prices;
    let pi = 0;
    if (!reduced) setInterval(() => {
      pi = (pi + 1) % prices.length; last.textContent = prices[pi];
      const row = book[[2, 3, 2, 1, 3][pi]]; row.classList.remove("flash"); void row.offsetWidth; row.classList.add("flash");
    }, 7300);
  })();

  // ── Analytics events: the two actions the page is measured by ─────────
  //    Guarded by `window.gtag`: a blocked script must not break a click.
  document.querySelectorAll('a[href*="discord.gg"]').forEach((a) => {
    a.addEventListener("click", () => {
      if (window.gtag) gtag("event", "discord_click");
    });
  });

  // ── The signup form: honest answers ───────────────────────────────────
  document.querySelectorAll("form.signup").forEach((form) => {
    const msg = form.querySelector(".form-msg");
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const button = form.querySelector("button");
      msg.textContent = ""; msg.className = "form-msg"; button.disabled = true;
      try {
        const res = await fetch("/api/signup", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: form.email.value,
            website: form.website.value,
            // The page's own language, not the browser's: a refusal must come
            // back in the language being read, whatever the locale says.
            lang: document.documentElement.lang,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          // Not "thank you": what happens next, and where the project lives meanwhile.
          msg.innerHTML = WORDS.signedUp +
            '<a class="ext" href="https://discord.gg/eKhM3H9tKk" target="_blank" rel="noopener noreferrer">Discord</a>.';
          msg.className = "form-msg ok"; form.email.value = "";
          // the conversion this page exists for
          if (window.gtag) gtag("event", "signup_success");
        } else {
          msg.textContent = data.error || WORDS.failed;
          msg.className = "form-msg err";
        }
      } catch {
        msg.textContent = WORDS.offline;
        msg.className = "form-msg err";
      } finally { button.disabled = false; }
    });
  });
})();
