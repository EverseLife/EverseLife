// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

// One WebGL scene behind every page: procedural nebula and three star layers
// with depth parallax from the pointer and the scroll. On the world page the
// same shader also renders the planets of the carousel -- stars and worlds
// share one space and one camera, so travelling between planets moves the
// whole sky, not a card. No libraries: a fullscreen triangle and one
// fragment shader. Where WebGL is unavailable, lacks highp fragments, fails
// to compile, or loses its context, the old 2D starfield takes over; with
// reduced motion either path draws a still frame and repaints it on scroll.
(() => {
  const host = document.getElementById("space");
  if (!host) return;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sizeKey = () => innerWidth + "x" + innerHeight;

  // ── The 2D fallback: sparse, crisp, a slow drift with depth ────────────
  let fellBack = false;
  const fallback = (canvas) => {
    if (fellBack) return;
    fellBack = true;
    let ctx = canvas.getContext("2d");
    if (!ctx) {
      // a canvas that has surrendered itself to WebGL cannot change context:
      // a fresh node takes its place and its attributes
      const fresh = canvas.cloneNode(false);
      canvas.replaceWith(fresh);
      canvas = fresh;
      ctx = canvas.getContext("2d");
      if (!ctx) return;
    }
    let w, h, stars = [], mx = 0, my = 0, tx = 0, ty = 0, sizedFor = "";
    const resize = () => {
      // regenerate only on a real change of size: a scrolling mobile browser
      // fires resize as its address bar hides, and the sky must not reshuffle
      if (sizedFor === sizeKey()) return;
      sizedFor = sizeKey();
      const dpr = Math.min(devicePixelRatio || 1, 2);
      w = canvas.clientWidth || innerWidth; h = canvas.clientHeight || innerHeight;
      canvas.width = Math.max(1, w * dpr); canvas.height = Math.max(1, h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const n = Math.min(170, Math.floor(w * h / 11000));
      stars = Array.from({ length: n }, () => ({
        x: Math.random() * w, y: Math.random() * h,
        z: .25 + Math.random() * .75,
        r: .5 + Math.random() * (Math.random() < .08 ? 1.6 : .8),
        a: .25 + Math.random() * .55,
        ph: Math.random() * Math.PI * 2, sp: .3 + Math.random() * .7,
      }));
    };
    const draw = (t) => {
      ctx.clearRect(0, 0, w, h);
      tx += (mx - tx) * .04; ty += (my - ty) * .04;
      const sy = scrollY * .04;
      for (const s of stars) {
        const tw = reduced ? 1 : .8 + .2 * Math.sin(t / 1400 * s.sp + s.ph);
        const x = s.x + tx * s.z * 18;
        let y = s.y + ty * s.z * 18 - sy * s.z;
        y = ((y % h) + h) % h;
        ctx.globalAlpha = s.a * tw * (.55 + .45 * s.z);
        ctx.fillStyle = "#e6ecf7";
        ctx.beginPath(); ctx.arc(x, y, s.r * s.z, 0, Math.PI * 2); ctx.fill();
      }
      ctx.globalAlpha = 1;
    };
    const frame = (t) => {
      if (sizedFor !== sizeKey()) resize();
      draw(t);
      for (const s of stars) { s.x -= s.z * .02; if (s.x < -2) s.x = w + 2; }
      requestAnimationFrame(frame);
    };
    addEventListener("resize", () => { resize(); if (reduced) draw(0); });
    addEventListener("pointermove", (e) => {
      mx = e.clientX / innerWidth - .5; my = e.clientY / innerHeight - .5;
    }, { passive: true });
    resize();
    if (reduced) {
      draw(0);
      addEventListener("scroll", () => draw(0), { passive: true });
    } else {
      requestAnimationFrame(frame);
    }
  };

  const gl = host.getContext("webgl", {
    alpha: false, antialias: false, depth: false, stencil: false,
    powerPreference: "low-power",
  });
  if (!gl) { fallback(host); return; }

  // The shader asks for highp, which WebGL 1 does not promise in fragments;
  // mediump would degrade the hash, so such devices get the 2D sky instead.
  const highp = gl.getShaderPrecisionFormat
    && gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT);
  if (!highp || highp.precision === 0) { fallback(host); return; }

  const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

  // The nebula is domain-warped fbm on Terra's blues with a faint violet
  // core; the stars are three hashed cell layers moving at different speeds.
  // Two planet slots (A rests or departs, B arrives) reuse one surface
  // routine: raycast sphere, longitude-periodic terrain, embossed relief,
  // drifting clouds, emissive cracks, hologram mode, atmosphere rim.
  const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_par;
uniform float u_scroll;
uniform vec4 u_pA;
uniform vec4 u_pB;
uniform vec3 u_aBase; uniform vec3 u_aLand; uniform vec3 u_aAtm; uniform vec3 u_aPrm; uniform float u_aSeed;
uniform vec3 u_bBase; uniform vec3 u_bLand; uniform vec3 u_bAtm; uniform vec3 u_bPrm; uniform float u_bSeed;
uniform float u_top;

float hash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) {
    v += a * noise(p);
    p = p * 2.03 + vec2(17.0, 9.0);
    a *= 0.5;
  }
  return v;
}
float starLayer(vec2 uv, float density, float t) {
  vec2 id = floor(uv), f = fract(uv) - 0.5;
  float h = hash(id);
  float on = step(1.0 - density, h);
  vec2 off = vec2(hash(id + 11.1), hash(id + 27.7)) - 0.5;
  float d = length(f - off * 0.8);
  float tw = 0.7 + 0.3 * sin(t * (0.5 + h) + h * 6.28);
  return on * smoothstep(0.05 + h * 0.05, 0.0, d) * tw;
}

float hashS(vec2 p, float seed) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32 + seed);
  return fract(p.x * p.y);
}
float pnoise(vec2 p, float per, float seed) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float x0 = mod(i.x, per), x1 = mod(i.x + 1.0, per);
  float a = hashS(vec2(x0, i.y), seed);
  float b = hashS(vec2(x1, i.y), seed);
  float c = hashS(vec2(x0, i.y + 1.0), seed);
  float d = hashS(vec2(x1, i.y + 1.0), seed);
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float pfbm(vec2 p, float per, float seed) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 6; i++) {
    v += a * pnoise(p, per, seed);
    p = p * 2.0 + vec2(0.0, 13.7);
    per *= 2.0;
    a *= 0.5;
  }
  return v;
}
vec4 planet(vec2 frag, vec4 P, vec3 base, vec3 land, vec3 atm, vec3 prm, float seed) {
  if (P.z < 1.0 || P.w <= 0.0) return vec4(0.0);
  vec2 uv = (frag - P.xy) / P.z;
  float r2 = dot(uv, uv);
  float r = sqrt(r2);
  if (r > 1.4) return vec4(0.0);
  vec3 lightDir = normalize(vec3(-0.55, 0.35, 0.72));
  vec3 col;
  float alpha;
  if (r < 1.0) {
    vec3 n = vec3(uv, sqrt(1.0 - r2));
    float lon = atan(n.x, n.z) / 6.2831853 + 0.5 + u_time * 0.01;
    float lat = asin(clamp(n.y, -1.0, 1.0)) / 3.14159265 + 0.5;
    float Per = 9.0;
    vec2 sp = vec2(lon * Per, lat * 4.5);
    float h = pfbm(sp, Per, seed);
    float hl = pfbm(sp + vec2(-0.12, 0.09), Per, seed);
    float landM = smoothstep(0.495, 0.52, h);
    col = mix(base, land, landM) * (0.8 + 0.4 * h);
    col *= clamp(1.0 + (h - hl) * 2.4, 0.55, 1.5);
    float ridge = 1.0 - abs(2.0 * pfbm(sp * 2.0 + vec2(0.0, 7.3), Per * 2.0, seed) - 1.0);
    float cracks = smoothstep(0.86, 0.98, ridge) * prm.y;
    float cl = pfbm(sp * 2.0 + vec2(u_time * 0.04, 5.0), Per * 2.0, seed);
    float cloud = smoothstep(0.58, 0.72, cl) * prm.x;
    col = mix(col, vec3(0.93, 0.96, 1.0), cloud);
    float light = clamp(dot(n, lightDir), 0.0, 1.0);
    col *= 0.12 + 1.05 * light;
    col += atm * cracks * (1.0 - cloud) * 0.9;
    col += atm * pow(1.0 - n.z, 2.2) * 0.55;
    alpha = 1.0;
    if (prm.z > 0.5) {
      float scan = 0.75 + 0.25 * sin(frag.y * 0.7 + u_time * 2.0);
      col = mix(col, atm, 0.35) * scan;
      alpha = 0.6 + 0.15 * sin(u_time * 1.3);
    }
    alpha *= smoothstep(1.0, 0.985, r);
  } else {
    float glow = smoothstep(1.4, 1.0, r);
    col = atm * glow * 0.5;
    alpha = glow * 0.5 * (prm.z > 0.5 ? 0.55 : 1.0);
  }
  alpha *= P.w;
  return vec4(col * alpha, alpha);
}
void main() {
  vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;
  vec3 col = vec3(0.027, 0.043, 0.078);

  // the pan offset joins q BEFORE the domain warp: warp field and noise
  // then translate together and the nebula moves rigidly instead of
  // churning while the base noise slides under a screen-anchored warp
  vec2 q = uv * 1.6 + u_par * 0.15 + vec2(0.0, u_scroll * 0.00006);
  vec2 w = vec2(fbm(q + u_time * 0.008), fbm(q + vec2(5.2, 1.3) - u_time * 0.006));
  float n = fbm(q + 1.6 * w);
  float neb = smoothstep(0.45, 0.95, n);
  col += neb * vec3(0.060, 0.110, 0.210);
  col += pow(neb, 3.0) * vec3(0.110, 0.080, 0.220) * 0.8;

  col += vec3(0.90, 0.94, 1.00) * 0.90 * starLayer(uv * 30.0 + u_par * 2.0 + vec2(0.0, u_scroll * 0.0012), 0.015, u_time);
  col += vec3(0.85, 0.90, 1.00) * 0.55 * starLayer(uv * 55.0 + u_par * 1.2 + vec2(0.0, u_scroll * 0.0007) + 31.0, 0.020, u_time);
  col += vec3(0.80, 0.86, 1.00) * 0.30 * starLayer(uv * 90.0 + u_par * 0.6 + vec2(0.0, u_scroll * 0.0004) + 57.0, 0.025, u_time);

  col *= 1.0 - 0.35 * dot(uv * vec2(0.9, 1.2), uv * vec2(0.9, 1.2));

  vec4 pa = planet(gl_FragCoord.xy, u_pA, u_aBase, u_aLand, u_aAtm, u_aPrm, u_aSeed);
  vec4 pb = planet(gl_FragCoord.xy, u_pB, u_bBase, u_bLand, u_bAtm, u_bPrm, u_bSeed);
  vec4 lower = u_top < 0.5 ? pb : pa;
  vec4 upper = u_top < 0.5 ? pa : pb;
  col = col * (1.0 - lower.a) + lower.rgb;
  col = col * (1.0 - upper.a) + upper.rgb;
  gl_FragColor = vec4(col, 1.0);
}
`;

  const compile = (type, src) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
  };
  const vs = compile(gl.VERTEX_SHADER, VERT);
  const fs = compile(gl.FRAGMENT_SHADER, FRAG);
  if (!vs || !fs) { fallback(host); return; }
  const prog = gl.createProgram();
  gl.attachShader(prog, vs); gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { fallback(host); return; }
  gl.useProgram(prog);

  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const aPos = gl.getAttribLocation(prog, "a_pos");
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);

  const uni = {};
  for (const name of [
    "u_res", "u_time", "u_par", "u_scroll", "u_pA", "u_pB", "u_top",
    "u_aBase", "u_aLand", "u_aAtm", "u_aPrm", "u_aSeed",
    "u_bBase", "u_bLand", "u_bAtm", "u_bPrm", "u_bSeed",
  ]) uni[name] = gl.getUniformLocation(prog, name);
  gl.uniform4f(uni.u_pA, 0, 0, 0, 0);
  gl.uniform4f(uni.u_pB, 0, 0, 0, 0);
  gl.uniform1f(uni.u_top, 1);

  // ── The world carousel: slots, palettes, and the camera flight ─────────
  const PALETTES = {
    terra: { base: [.07, .20, .35], land: [.33, .47, .30], atm: [.50, .72, .91], prm: [.5, 0, 0], seed: 3 },
    aurora: { base: [.55, .65, .80], land: [.90, .94, .99], atm: [.84, .89, .96], prm: [.15, 0, 0], seed: 7 },
    pyro: { base: [.10, .065, .055], land: [.22, .15, .12], atm: [.94, .54, .35], prm: [.08, 1, 0], seed: 13 },
    aqua: { base: [.05, .29, .24], land: [.36, .78, .65], atm: [.36, .78, .65], prm: [.25, 0, 1], seed: 21 },
  };
  const keyOf = (card) => ((card.getAttribute("style") || "").match(/--(terra|aurora|pyro|aqua)/) || [])[1];
  const worldCards = [...document.querySelectorAll(".planets .planet")].filter((c) => PALETTES[keyOf(c)]);
  const sceneOn = worldCards.length > 0;
  for (const card of worldCards) {
    // an empty slot reserves the left half; the planet is painted behind it
    const slot = document.createElement("div");
    slot.className = "globe-slot";
    slot.setAttribute("aria-hidden", "true");
    card.prepend(slot);
    card.classList.add("has-globe");
  }
  const setPal = (side, pal) => {
    gl.uniform3fv(uni["u_" + side + "Base"], pal.base);
    gl.uniform3fv(uni["u_" + side + "Land"], pal.land);
    gl.uniform3fv(uni["u_" + side + "Atm"], pal.atm);
    gl.uniform3fv(uni["u_" + side + "Prm"], pal.prm);
    gl.uniform1f(uni["u_" + side + "Seed"], pal.seed);
  };

  // One lateral camera flight drives everything: the stars and the nebula
  // sweep one way with layered parallax, the old world (nearest of all, so
  // fastest of all) leaves past the edge, the next one enters from the
  // opposite edge and brakes into its place. CAM_STEP is how far the sky
  // itself travels per flight; the ground gained is kept.
  const CAM_STEP = 40.0;
  const FLY_MS = 1000;
  let activeKey = null, phase = null, camBase = 0;
  addEventListener("everse:travel", (e) => {
    if (!sceneOn) return;
    if (reduced) { draw(0); return; }
    const d = e.detail || {};
    if (!PALETTES[d.from] || !PALETTES[d.to]) return;
    if (phase) {
      // a flight interrupted mid-way commits the ground already covered
      const t0 = Math.min(1, (performance.now() - phase.start) / FLY_MS);
      camBase += phase.dir * CAM_STEP * t0 * t0 * (3 - 2 * t0);
    }
    phase = { from: d.from, to: d.to, dir: d.dir > 0 ? 1 : -1, start: performance.now() };
  });

  const drawScene = (p) => {
    const slot = document.querySelector(".planets .planet.on .globe-slot");
    if (!slot) { gl.uniform4f(uni.u_pA, 0, 0, 0, 0); gl.uniform4f(uni.u_pB, 0, 0, 0, 0); return; }
    const rect = slot.getBoundingClientRect();
    const k = host.width / (host.clientWidth || innerWidth);
    if (!rect.width || !k) return;
    const home = {
      x: (rect.left + rect.width / 2) * k,
      y: host.height - (rect.top + rect.height / 2) * k,
      r: rect.width / 2 * 0.86 * k,
    };
    if (phase && p !== null) {
      setPal("a", PALETTES[phase.from]);
      setPal("b", PALETTES[phase.to]);
      // Both worlds ride the same lateral flight at the same depth: the one
      // we leave clears the edge the camera pans away from, the next one
      // rolls in from the opposite edge and brakes into place.
      const exitDist = home.x + home.r * 1.6;
      const enterDist = (host.width - home.x) + home.r * 1.6;
      if (phase.dir > 0) {
        gl.uniform4f(uni.u_pA, home.x - exitDist * p, home.y, home.r, 1);
        gl.uniform4f(uni.u_pB, home.x + enterDist * (1 - p), home.y, home.r, 1);
      } else {
        gl.uniform4f(uni.u_pA, home.x + enterDist * p, home.y, home.r, 1);
        gl.uniform4f(uni.u_pB, home.x - exitDist * (1 - p), home.y, home.r, 1);
      }
      gl.uniform1f(uni.u_top, 1);
      return;
    }
    const key = keyOf(slot.parentElement);
    if (key) activeKey = key;
    if (!activeKey) return;
    setPal("a", PALETTES[activeKey]);
    gl.uniform4f(uni.u_pA, home.x, home.y, home.r, 1);
    gl.uniform4f(uni.u_pB, 0, 0, 0, 0);
    gl.uniform1f(uni.u_top, 1);
  };

  // The world page renders the whole scene near device resolution -- the
  // planet is the subject there; elsewhere the sky is a backdrop and two
  // thirds of the pixels are enough through the grain.
  const SCALE = sceneOn ? 1 : 0.66;
  const DPR_CAP = sceneOn ? 1.75 : 2;
  let mx = 0, my = 0, tx = 0, ty = 0, sizedFor = "", lost = false;
  const resize = () => {
    // clientWidth is the canvas's real CSS box: innerWidth also counts the
    // scrollbar and would shift everything painted by its width
    const dpr = Math.min(devicePixelRatio || 1, DPR_CAP) * SCALE;
    host.width = Math.max(1, Math.floor((host.clientWidth || innerWidth) * dpr));
    host.height = Math.max(1, Math.floor((host.clientHeight || innerHeight) * dpr));
    gl.viewport(0, 0, host.width, host.height);
    sizedFor = sizeKey();
  };
  const draw = (ms) => {
    tx += (mx - tx) * .04; ty += (my - ty) * .04;
    // the flight eases in and out; planets and sky share this one progress,
    // so their parallax never comes apart
    let cam = camBase, p = null;
    if (phase) {
      const ft = Math.min(1, (performance.now() - phase.start) / FLY_MS);
      p = ft * ft * (3 - 2 * ft);
      cam = camBase + phase.dir * CAM_STEP * p;
      if (ft >= 1) {
        camBase += phase.dir * CAM_STEP;
        activeKey = phase.to;
        phase = null;
        p = null;
      }
    }
    gl.uniform2f(uni.u_res, host.width, host.height);
    gl.uniform1f(uni.u_time, ms / 1000);
    gl.uniform2f(uni.u_par, tx + cam, -ty);
    gl.uniform1f(uni.u_scroll, scrollY);
    if (sceneOn) drawScene(p);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  // A tab can load while its window still measures zero (hidden panes,
  // background restores); the frame loop re-fits the canvas when that lies.
  const frame = (ms) => {
    if (lost) return;
    if (sizedFor !== sizeKey()) resize();
    draw(ms);
    requestAnimationFrame(frame);
  };

  // A GPU reset would leave the loop feeding a dead context forever: stop it
  // and hand the sky to the 2D path on a fresh canvas instead of restoring.
  host.addEventListener("webglcontextlost", () => {
    lost = true;
    for (const card of worldCards) card.classList.remove("has-globe");
    fallback(host);
  });

  addEventListener("resize", () => { if (!lost) { resize(); if (reduced) draw(0); } });
  addEventListener("pointermove", (e) => {
    mx = e.clientX / innerWidth - .5; my = e.clientY / innerHeight - .5;
  }, { passive: true });
  resize();
  if (reduced) {
    draw(0);
    // a still frame must still follow the scroll, or the parallax freezes
    addEventListener("scroll", () => { if (!lost) draw(0); }, { passive: true });
  } else {
    requestAnimationFrame(frame);
  }
})();
