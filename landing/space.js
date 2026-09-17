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
//
// The worlds are the game's own (D-347). The login screen draws the planet the map
// draws -- the vault's field on the GPU, coloured by biome and lit by the
// relief -- and this page shows the same four grounds: `tools/planet_pictures.py`
// bakes each planet's field into one equirectangular picture with the client's
// palette, and the shader below wraps it round a sphere and does what a
// picture cannot: the sun of the moment, the terminator, the clouds on their
// own shell, the rim of the air. A picture arrives when its planet is asked
// for and fades in; until then its world is a dark ball with a lit edge, so
// nothing waits on the network to look like a planet.
(() => {
  const host = document.getElementById("space");
  if (!host) return;

  // The sky waits its turn. Compiling the shader and reading layout cost ~4 s
  // of main-thread time on a throttled phone, and doing it during parse put
  // all of it in front of the first paint. The page's base is a dark ground
  // either way, so the stars may arrive a beat later: boot after `load`, at
  // idle. `boot` is hoisted, so the body below reads exactly as before.
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1));
  if (document.readyState === "complete") idle(boot);
  else addEventListener("load", () => idle(boot), { once: true });

  function boot() {
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

  // A phone gets the 2D sky by choice, not by failure -- unless the page
  // draws worlds. Compiling the nebula shader costs a couple of seconds of a
  // throttled phone's main thread, and on a starfield-only page it buys a
  // texture nobody without a pointer can even parallax. The world page keeps
  // WebGL everywhere: its carousel planets are painted by this shader, and
  // textual cards would be a poorer trade than the compile.
  if (matchMedia("(pointer: coarse)").matches && !document.querySelector(".planets .planet")) {
    fallback(host);
    return;
  }

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
  // routine: raycast sphere, the baked ground wrapped round it, clouds on
  // their own shell, terminator, atmosphere rim.
  const FRAG = `
precision highp float;
uniform vec2 u_res;
uniform float u_time;
uniform vec2 u_par;
uniform float u_scroll;
uniform vec4 u_pA;
uniform vec4 u_pB;
uniform sampler2D u_aGround; uniform vec3 u_aAtm; uniform vec3 u_aSky; uniform vec4 u_aWorld; uniform vec3 u_aLook;
uniform sampler2D u_bGround; uniform vec3 u_bAtm; uniform vec3 u_bSky; uniform vec4 u_bWorld; uniform vec3 u_bLook;
uniform float u_top;

//: The light. Fixed in the eye's frame and not in the planet's: the camera
//: stands still and the star with it, and the ground rolls under a
//: terminator that stays where it is -- which is what a planet looks like
//: from a window, and what makes the turn read as a turn at all.
const vec3 SUN = vec3(-0.5931, 0.3774, 0.7112);
//: The terminator's width, the night's tint and what a cloud keeps on its
//: own night: the client's own numbers (map/shade.ts, D-336), so the ball
//: goes dark here as it does on the login screen.
const float TWILIGHT = 0.12;
const vec3 NIGHT_TINT = vec3(0.32, 0.40, 0.62);
const float CLOUD_OPACITY = 0.85;
const float CLOUD_SHADE = 0.35;
const float CLOUD_NIGHT = 0.75;
const float CLOUD_FEATHER = 0.12;

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

// ── The worlds ─────────────────────────────────────────────────────────────
// Value noise on the sphere's own point, in three dimensions and not two:
// the clouds have to run round the ball without a seam, and a noise of
// longitude and latitude has one down the meridian and a knot at each pole.
float hash3(vec3 p) {
  p = fract(p * 0.3183099 + vec3(0.71, 0.113, 0.419));
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float noise3(vec3 x) {
  vec3 i = floor(x), f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(hash3(i), hash3(i + vec3(1.0, 0.0, 0.0)), f.x),
        mix(hash3(i + vec3(0.0, 1.0, 0.0)), hash3(i + vec3(1.0, 1.0, 0.0)), f.x), f.y),
    mix(mix(hash3(i + vec3(0.0, 0.0, 1.0)), hash3(i + vec3(1.0, 0.0, 1.0)), f.x),
        mix(hash3(i + vec3(0.0, 1.0, 1.0)), hash3(i + vec3(1.0, 1.0, 1.0)), f.x), f.y), f.z);
}
float fbm3(vec3 p) {
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 4; i++) {
    v += a * noise3(p);
    p = p * 2.07 + vec3(11.3, 7.1, 3.9);
    a *= 0.5;
  }
  return v;
}
// A point of the eye's sphere in the planet's own frame: the pole leans
// toward the eye by the tilt, and the ball turns under it by the spin.
vec3 spun(vec3 n, float tilt, float spin) {
  float ct = cos(tilt), st = sin(tilt);
  vec3 p = vec3(n.x, ct * n.y + st * n.z, ct * n.z - st * n.y);
  float cs = cos(spin), ss = sin(spin);
  return vec3(cs * p.x - ss * p.z, p.y, ss * p.x + cs * p.z);
}
// Where that point falls on the baked picture: longitude round, latitude up.
// The picture is uploaded north up (UNPACK_FLIP_Y), so the north pole is v=1.
vec2 onGround(vec3 p) {
  return vec2(atan(p.x, p.z) * 0.1591549 + 0.5, 0.5 + asin(clamp(p.y, -1.0, 1.0)) * 0.3183099);
}
// How much cloud stands over a point of the shell. The gate is the cover the
// clouds are cut at -- the higher, the clearer the sky, and it is what tells
// Terra's weather from Aurora's thin air and Pyroxis's ash.
float cloudAt(vec3 p, float gate, float drift) {
  float cs = cos(drift), ss = sin(drift);
  vec3 q = vec3(cs * p.x - ss * p.z, p.y, ss * p.x + cs * p.z);
  //: Stretched east to west: weather comes in bands, not in dots.
  float cover = fbm3(q * vec3(2.6, 4.2, 2.6));
  return smoothstep(gate - CLOUD_FEATHER, gate, cover) * CLOUD_OPACITY;
}
vec4 planet(vec2 frag, vec4 P, sampler2D ground, vec3 atm, vec3 sky, vec4 world, vec3 look) {
  if (P.z < 1.0 || P.w <= 0.0) return vec4(0.0);
  vec2 uv = (frag - P.xy) / P.z;
  float r2 = dot(uv, uv);
  float r = sqrt(r2);
  if (r > 1.22) return vec4(0.0);
  float tilt = world.y, spin = world.x, gate = world.z, ready = world.w;
  //: How far over the ground the clouds ride, as a share of the radius: half
  //: a kilometre (shade.CLOUD_KM) over a ball whose size is its own, so
  //: Pyroxis at seven kilometres wears its weather higher than Terra at
  //: twelve. Small, and the whole reason the clouds ring the limb instead of
  //: ending at it.
  float shell = look.x;
  float drift = u_time * 0.006;
  vec3 face = vec3(0.0);
  //: The clouds ride a sphere of their own, a little wider than the ground:
  //: the pixel's ray meets that one first, so they stand off the surface
  //: toward the limb and ring the ball past it.
  float shellR2 = r2 / (shell * shell);
  float cloud = 0.0;
  float cloudLight = 1.0;
  if (shellR2 < 1.0 && gate < 1.0) {
    vec3 ns = vec3(uv / shell, sqrt(1.0 - shellR2));
    cloud = cloudAt(spun(ns, tilt, spin), gate, drift);
    cloudLight = smoothstep(-TWILIGHT, TWILIGHT, dot(ns, SUN));
    //: Feathered at the very limb, or the cloud shell ends in a hard ring.
    cloud *= smoothstep(1.0, 0.94, sqrt(shellR2));
  }
  if (r < 1.0) {
    vec3 n = vec3(uv, sqrt(1.0 - r2));
    vec3 p = spun(n, tilt, spin);
    float daylight = smoothstep(-TWILIGHT, TWILIGHT, dot(n, SUN));
    //: The ground: the planet's own field, baked. Until it has arrived the
    //: ball is the planet's tint, dark -- a world seen from too far to make
    //: anything out, not a hole in the page.
    vec3 lit = mix(atm * 0.12, texture2D(ground, onGround(p)).rgb, ready);
    //: A cloud shades the ground under it, and the shadow goes out with the
    //: twilight as the clouds' own light does (D-336).
    float shadow = cloudAt(spun(normalize(n + SUN * 0.06), tilt, spin), gate, drift);
    lit *= 1.0 - CLOUD_SHADE * shadow * smoothstep(0.0, TWILIGHT, dot(n, SUN));
    //: The night, and what burns through it. A world whose low ground is
    //: molten does not go dark on its own night: the lava is the light.
    //: How much of a pixel is lava is read off the picture -- the baked
    //: ground is basalt everywhere but the flows, and nothing else on it is
    //: that much redder than it is blue -- so the seas keep their own glow
    //: while the rock around them goes out. The ember is nought for a world
    //: with water in its low ground, and there the night is the map's.
    float ember = clamp((lit.r - lit.b) * 2.2, 0.0, 1.0) * look.y;
    face = mix(lit * NIGHT_TINT, lit, max(daylight, ember));
    //: The air over the ground: a rim that thickens toward the limb, and
    //: only where the sun reaches it.
    face += atm * pow(1.0 - n.z, 3.0) * 0.45 * (0.15 + 0.85 * daylight);
  }
  // the disc fades out over the last 1.5% of the radius for a clean edge,
  // and the halo has to show through under that fade: where the two only
  // met at r = 1 the gap between them drew the sky as a dark hairline
  // around every world. Same expression as the halo below, which is 1.0
  // everywhere inside the disc -- the two meet at the same value.
  float cov = smoothstep(1.0, 0.985, r);
  float glow = smoothstep(1.22, 1.0, r) * 0.30;
  vec3 col = mix(atm * glow, face, cov);
  float alpha = mix(glow, 1.0, cov);
  //: The clouds over the ground AND over the halo past the limb: the shell
  //: is wider than the ball, and a cloud that stopped at the limb would
  //: leave the ball a hard-edged disc with weather painted inside it.
  vec3 cloudCol = sky * mix(CLOUD_NIGHT * NIGHT_TINT, vec3(1.0), cloudLight);
  col = mix(col, cloudCol, cloud);
  alpha = alpha + cloud * (1.0 - alpha);
  //: A world the game has not built yet is shown as a survey, not as a
  //: place: washed toward its own tint, scanned, and not quite opaque.
  //: The ghost is one for such a world -- today Aquatica, whose card
  //: says the same thing in words.
  if (look.z > 0.0) {
    //: A broad band sweeping down the ball, not scanlines: a line every few
    //: pixels over a real ground reads as a damaged picture, and moires
    //: against the screen as the ball turns.
    float sweep = 1.0 + 0.07 * sin(uv.y * 3.0 - u_time * 0.9);
    col = mix(col, mix(col, atm, 0.28) * sweep, look.z);
    alpha *= 1.0 - 0.30 * look.z;
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

  vec4 pa = planet(gl_FragCoord.xy, u_pA, u_aGround, u_aAtm, u_aSky, u_aWorld, u_aLook);
  vec4 pb = planet(gl_FragCoord.xy, u_pB, u_bGround, u_bAtm, u_bSky, u_bWorld, u_bLook);
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

  // Asking LINK_STATUS right after linking forces the driver to finish the
  // compile on the spot -- the single longest task this page had (~half a
  // second even on a desktop). With KHR_parallel_shader_compile the driver
  // compiles on its own threads while we poll a status flag that costs
  // nothing; without the extension the else-branch is the old synchronous
  // path. `launch` is hoisted, so the body below reads as before.
  const parallel = gl.getExtension("KHR_parallel_shader_compile");
  if (parallel) {
    const poll = () =>
      gl.getProgramParameter(prog, parallel.COMPLETION_STATUS_KHR) ? launch() : setTimeout(poll, 30);
    poll();
  } else launch();

  function launch() {
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
    "u_aGround", "u_aAtm", "u_aSky", "u_aWorld", "u_aLook",
    "u_bGround", "u_bAtm", "u_bSky", "u_bWorld", "u_bLook",
  ]) uni[name] = gl.getUniformLocation(prog, name);
  gl.uniform4f(uni.u_pA, 0, 0, 0, 0);
  gl.uniform4f(uni.u_pB, 0, 0, 0, 0);
  gl.uniform1f(uni.u_top, 1);
  //: One texture unit per slot, for good: which planet is in which slot
  //: changes, the unit does not.
  gl.uniform1i(uni.u_aGround, 0);
  gl.uniform1i(uni.u_bGround, 1);

  // ── The world carousel: the four planets, their pictures, the flight ───
  //
  // One row a planet: the picture of its ground (baked from the game's own
  // field by `tools/planet_pictures.py`), the tint of its air, the tone of
  // its weather, how clear its sky is, and where the eye stands over it.
  // The card's own token names the row (`--pc: var(--terra)`).
  //
  // `lat` is how far the pole leans toward the eye and `lon` where the turn
  // starts, so each world shows what it is known for when it arrives: Terra
  // its rivers from the north, Aurora its cap, Pyroxis its lava seas, and
  // Aquatica its one archipelago. `sky` is the cover this page's own cloud
  // noise is cut at -- the higher, the clearer, and 1 is a planet with no
  // weather at all. Not the vault's `weather.cloud_from`/`cloud_full`, which
  // are one pair for every planet and belong to a field this page does not
  // have: these are four numbers picked by eye against the four cards.
  // `ember` says the low ground is molten, so the night does not put it out.
  // `shell` is how far over the ground the weather rides -- half a kilometre
  // (`shade.CLOUD_KM`) over that planet's own radius, out of the field's
  // passport: 12.44 km for three of them, 7.18 for Pyroxis.
  // Whether a world is still a survey is not written here: the card says it
  // (`.planet.off`), and the globe reads it off the card.
  const WORLDS = {
    terra: { file: "terra", atm: [.50, .72, .91], cloud: [.96, .97, .99], sky: .62, shell: 1.04, lat: 22, lon: 150 },
    aurora: { file: "aurora", atm: [.84, .89, .96], cloud: [.93, .95, .99], sky: .72, shell: 1.04, lat: 38, lon: 30 },
    //: Pyroxis has no water and so no cloud: what hangs over it is ash, and
    //: it is drawn in the planet's own burnt grey rather than in a cloud's
    //: white. The page has promised ash storms since it was written; the game
    //: itself paints one cloud tone on every planet (`shade.CLOUD_TONE`), so
    //: this is the page's word and not yet the world's.
    pyro: { file: "pyroxis", atm: [.94, .54, .35], cloud: [.55, .47, .43], sky: .68, shell: 1.07, lat: 12, lon: 300, ember: 1 },
    aqua: { file: "aquatica", atm: [.36, .78, .65], cloud: [.95, .97, .98], sky: .63, shell: 1.04, lat: 8, lon: 205 },
  };
  //: How fast a world turns, degrees a second -- the login globe's own pace
  //: (`EntryGlobe.SPIN_DEG_PER_S`): slow enough to watch, not to wait for.
  const SPIN_DEG_PER_S = 2;
  const keyOf = (card) => ((card.getAttribute("style") || "").match(/--(terra|aurora|pyro|aqua)/) || [])[1];
  const worldCards = [...document.querySelectorAll(".planets .planet")].filter((c) => WORLDS[keyOf(c)]);
  const sceneOn = worldCards.length > 0;
  for (const card of worldCards) {
    // an empty slot reserves the left half; the planet is painted behind it
    const slot = document.createElement("div");
    slot.className = "globe-slot";
    slot.setAttribute("aria-hidden", "true");
    card.prepend(slot);
    card.classList.add("has-globe");
  }

  // The pictures arrive one at a time, when their planet is wanted: each is
  // a quarter of a megabyte, and a page about four planets must not spend a
  // megabyte before it says anything. A world with no picture yet is a dark
  // ball with a lit edge, and the ground fades in over FADE_MS when it lands.
  const FADE_MS = 700;
  const grounds = {};
  const blank = gl.createTexture();
  for (const unit of [gl.TEXTURE0, gl.TEXTURE1]) {
    //: Both units carry something from the start: a slot with no world in it
    //: is never drawn, but an unbound unit is a warning on every frame.
    gl.activeTexture(unit);
    gl.bindTexture(gl.TEXTURE_2D, blank);
  }
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, 1, 1, 0, gl.RGB, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0]));
  //: Complete without a mip chain, or the driver calls it unusable and hands
  //: back black with a warning a frame -- black is what it draws anyway, but
  //: not in the console.
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  const ensure = (key) => {
    const world = WORLDS[key];
    if (!world || grounds[key]) return;
    const got = { tex: null, since: 0 };
    grounds[key] = got;
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      //: The GPU may have gone while the picture was on the wire: the canvas
      //: is a 2D starfield by now, and `createTexture` on a dead context
      //: returns null.
      if (lost) return;
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, image);
      //: No mip chain, and east-west it repeats: the longitude wraps at the
      //: meridian, and a picture that clamped there drew a seam down the
      //: ball. No mip because the level would be picked off the derivative
      //: of that same wrap, which explodes on the seam and draws the coarsest
      //: level as a line -- the very artefact the wrap removes.
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.REPEAT);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      got.tex = tex;
      got.since = performance.now();
      if (lost) return;
      //: And now the next world, never before: one picture at a time, so the
      //: one the reader is looking at does not share the wire with three they
      //: have not reached. By the time the carousel leafs -- nine seconds --
      //: the next one is in.
      ensure(after(key));
      //: A still page has no frame loop to notice the arrival.
      if (reduced) draw(0);
    };
    //: A picture that does not come leaves the dark ball, and says so in the
    //: console: the page is about the planets, not about the pictures. The
    //: chain goes on past it, or one missing file would stop the other three
    //: from ever being asked for.
    image.onerror = () => {
      console.warn("planet picture:", world.file);
      ensure(after(key));
    };
    image.src = "/planets/" + world.file + ".png";
  };

  const setWorld = (side, key, ms) => {
    const world = WORLDS[key];
    const got = grounds[key];
    gl.activeTexture(side === "a" ? gl.TEXTURE0 : gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, (got && got.tex) || blank);
    gl.uniform3fv(uni["u_" + side + "Atm"], world.atm);
    gl.uniform3fv(uni["u_" + side + "Sky"], world.cloud);
    const spin = (world.lon + (reduced ? 0 : ms / 1000 * SPIN_DEG_PER_S)) * Math.PI / 180;
    //: The fade is counted off the clock and not off the frame's stamp: a
    //: still page draws with `ms` nought, and a picture would fade in
    //: backwards. On a still page there is no fade at all -- it is an
    //: animation, and the one draw that follows the picture's arrival would
    //: otherwise catch it at the dark end and leave it there.
    const ready = !got || !got.tex ? 0
      : reduced ? 1
      : Math.min(1, (performance.now() - got.since) / FADE_MS);
    gl.uniform4f(uni["u_" + side + "World"], spin, world.lat * Math.PI / 180, world.sky, ready);
    gl.uniform3f(uni["u_" + side + "Look"], world.shell, world.ember || 0, ghosts[key] ? 1 : 0);
  };

  //: Which worlds the page says are not built yet, off the cards themselves.
  const ghosts = {};
  for (const card of worldCards) ghosts[keyOf(card)] = card.classList.contains("off");
  //: The one after this in the carousel's own order -- the one it will leaf
  //: to by itself in nine seconds, and so the one to have ready by then.
  const order = worldCards.map(keyOf);
  const after = (key) => order[(order.indexOf(key) + 1) % order.length];
  if (sceneOn) ensure(order[0]);

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
    const d = e.detail || {};
    if (!WORLDS[d.from] || !WORLDS[d.to]) return;
    //: The world being flown to is asked for now, if it was not already, and
    //: before anything else this handler does: a hand on the arrows can
    //: outrun the chain that loads them one by one, and a reader without
    //: animations -- who leaves here at once -- would otherwise wait for the
    //: chain to crawl round to the planet they are looking at.
    ensure(d.to);
    if (reduced) { draw(0); return; }
    if (phase) {
      // a flight interrupted mid-way commits the ground already covered
      const t0 = Math.min(1, (performance.now() - phase.start) / FLY_MS);
      camBase += phase.dir * CAM_STEP * t0 * t0 * (3 - 2 * t0);
    }
    phase = { from: d.from, to: d.to, dir: d.dir > 0 ? 1 : -1, start: performance.now() };
  });

  const drawScene = (p, ms) => {
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
      setWorld("a", phase.from, ms);
      setWorld("b", phase.to, ms);
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
    setWorld("a", activeKey, ms);
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
    if (sceneOn) drawScene(p, ms);
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
  }
  }
})();
