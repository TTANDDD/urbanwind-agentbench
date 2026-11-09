/* Hero: particles advected through the real task-061 velocity field (OpenFOAM, iteration 400, z = 0.44 m). */
(() => {
  const F = window.UW.field, cv = document.getElementById("wind"); if (!cv || !F) return;
  const dec = s => new Int8Array(Uint8Array.from(atob(s), c => c.charCodeAt(0)).buffer);
  const QX = dec(F.ux), QY = dec(F.uy), NX = F.nx, NY = F.ny, dx = (F.x1 - F.x0) / (NX - 1), dy = (F.y1 - F.y0) / (NY - 1);
  const RAMP = [[60,84,136],[77,187,213],[0,160,135],[145,209,194],[243,155,127],[230,75,53]];
  const ramp = t => { t = Math.max(0, Math.min(0.999, t)) * (RAMP.length - 1); const i = t | 0, f = t - i, a = RAMP[i], b = RAMP[i + 1];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f]; };
  const under = document.createElement("canvas"); under.setAttribute("aria-hidden", "true");
  Object.assign(under.style, { position: "absolute", inset: "0", width: "100%", height: "100%" });
  cv.parentNode.insertBefore(under, cv);
  const ctx = cv.getContext("2d"), uctx = under.getContext("2d");
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches || /[?&]static\b/.test(location.search);
  let W, H, dpr, s, ox, oy, P = [], N, running = true, raf;

  function vel(wx, wy) {
    const i = Math.round((wx - F.x0) / dx), j = Math.round((wy - F.y0) / dy);
    if (i < 0 || j < 0 || i >= NX || j >= NY) return null;
    const k = j * NX + i, a = QX[k], b = QY[k]; if (a === -128) return null;
    return [a / 127 * F.scale, b / 127 * F.scale];
  }
  const toPx = (wx, wy) => [ox + wx * s, oy - wy * s];
  const toW = (px, py) => [(px - ox) / s, (oy - py) / s];
  function spawn(p, anywhere) {
    const [xa] = toW(0, 0), [xb] = toW(W, 0);
    const x0 = Math.max(F.x0, xa), x1 = Math.min(F.x1, xb);
    p.x = x0 + Math.random() * (x1 - x0);
    p.y = F.y0 + Math.random() * (F.y1 - F.y0); p.life = 40 + Math.random() * 110;
  }
  function layout() {
    dpr = Math.min(2, devicePixelRatio || 1); W = cv.clientWidth; H = cv.clientHeight;
    for (const c of [cv, under]) { c.width = W * dpr; c.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    s = Math.max(H / (F.y1 - F.y0), W / 15.5);            // px per metre, cover the viewport
    ox = W * (W > 900 ? 0.72 : 0.5); oy = H / 2;            // building (origin) sits right of the headline
    // underlay: faint speed field + building
    const img = uctx.createImageData(NX, NY), dark = document.documentElement.dataset.theme === "dark";
    for (let j = 0; j < NY; j++) for (let i = 0; i < NX; i++) {
      const k = j * NX + i, o = ((NY - 1 - j) * NX + i) * 4, a = QX[k], b = QY[k];
      if (a === -128) { img.data.set([51, 65, 85, 0], o); continue; }
      const c = ramp(Math.hypot(a, b) / 127 * F.scale / 1.45); img.data.set([c[0], c[1], c[2], dark ? 70 : 60], o);
    }
    const tmp = document.createElement("canvas"); tmp.width = NX; tmp.height = NY; tmp.getContext("2d").putImageData(img, 0, 0);
    uctx.setTransform(dpr, 0, 0, dpr, 0, 0); uctx.clearRect(0, 0, W, H); uctx.imageSmoothingEnabled = true;
    const [px0, py0] = toPx(F.x0, F.y1), [px1, py1] = toPx(F.x1, F.y0);
    uctx.drawImage(tmp, px0, py0, px1 - px0, py1 - py0);
    bldColor = dark ? "#cbd5e1" : "#334155";
    N = Math.round(Math.min(2600, W * H / 520)); P = Array.from({ length: N }, () => { const p = {}; spawn(p, true); return p; });
    ctx.clearRect(0, 0, W, H);
  }
  let bldColor = "#334155";
  function drawBuilding(c) { const [bx, by] = toPx(-0.5, 0.5); c.globalCompositeOperation = "source-over"; c.fillStyle = bldColor;
    c.beginPath(); c.roundRect ? c.roundRect(bx, by, s, s, 3) : c.rect(bx, by, s, s); c.fill(); }
  const BINS = 6, step = 0.022;
  function frame() {
    ctx.globalCompositeOperation = "destination-out"; ctx.fillStyle = "rgba(0,0,0,0.075)"; ctx.fillRect(0, 0, W, H);
    ctx.globalCompositeOperation = "source-over"; ctx.lineWidth = 1.25; ctx.lineCap = "round";
    const paths = Array.from({ length: BINS }, () => new Path2D());
    for (const p of P) {
      const v = vel(p.x, p.y); if (!v || --p.life < 0) { spawn(p, false); continue; }
      const m = vel(p.x + v[0] * step * 0.5 * 2.2, p.y + v[1] * step * 0.5 * 2.2) || v;
      const nx = p.x + m[0] * step * 2.2, ny = p.y + m[1] * step * 2.2;
      const [ax, ay] = toPx(p.x, p.y), [bx, by] = toPx(nx, ny);
      const b = Math.min(BINS - 1, (Math.hypot(m[0], m[1]) / 1.45 * BINS) | 0);
      paths[b].moveTo(ax, ay); paths[b].lineTo(bx, by); p.x = nx; p.y = ny;
      if (bx < -10 || bx > W + 10 || by < -10 || by > H + 10) spawn(p, false);
    }
    for (let b = 0; b < BINS; b++) { const c = ramp((b + 0.5) / BINS); ctx.strokeStyle = `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${(0.22 + 0.7 * b / (BINS - 1)).toFixed(2)})`; ctx.stroke(paths[b]); }
    drawBuilding(ctx);
    if (running) raf = requestAnimationFrame(frame);
  }
  layout();
  if (reduce) { for (let i = 0; i < 90; i++) { running = false; frame(); } }
  else raf = requestAnimationFrame(frame);
  let rt, lastW = W, lastH = H;
  new ResizeObserver(() => { const w = cv.clientWidth, hh = cv.clientHeight; if (w === lastW && hh === lastH) return; lastW = w; lastH = hh;
    clearTimeout(rt); rt = setTimeout(() => { layout(); if (reduce) for (let i = 0; i < 90; i++) frame(); }, 120); }).observe(cv);
  new IntersectionObserver(([e]) => { if (reduce) return; const vis = e.isIntersecting;
    if (vis && !running) { running = true; raf = requestAnimationFrame(frame); } else if (!vis) { running = false; cancelAnimationFrame(raf); } }).observe(cv);
  document.addEventListener("uw-theme", layout);
})();
