/* Result charts drawn as SVG from the paper's figure data (window.UW). */
window.UWCharts = (() => {
  const D = window.UW, NS = "http://www.w3.org/2000/svg";
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  function el(tag, a = {}, parent) { const e = document.createElementNS(NS, tag); for (const k in a) e.setAttribute(k, a[k]); if (parent) parent.appendChild(e); return e; }
  function svg(w, h) { return el("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "xMidYMid meet" }); }
  function txt(p, x, y, s, cls = "tk", anchor = "middle", extra = {}) { const t = el("text", { x, y, class: cls, "text-anchor": anchor, ...extra }, p); t.textContent = s; return t; }
  function line(p, x1, y1, x2, y2, a) { return el("line", { x1, y1, x2, y2, ...a }, p); }
  function drawPath(p, d, a) { const e = el("path", { d, fill: "none", ...a }, p); requestAnimationFrame(() => { const L = e.getTotalLength(); e.style.setProperty("--len", L); e.classList.add("draw"); }); return e; }
  function tip(host) { let t = host.querySelector(".ctip"); if (!t) { t = document.createElement("div"); t.className = "ctip"; host.appendChild(t); } return t; }
  function hover(host, node, label) {
    const t = tip(host);
    node.addEventListener("mouseenter", () => { t.textContent = label; t.style.opacity = 1; });
    node.addEventListener("mousemove", e => { const r = host.getBoundingClientRect(); t.style.left = (e.clientX - r.left + 12) + "px"; t.style.top = (e.clientY - r.top - 30) + "px"; });
    node.addEventListener("mouseleave", () => { t.style.opacity = 0; });
  }
  function axes(g, X, Y, xt, yt, xl, yl, w, h, m) {
    for (const v of yt) { line(g, m.l, Y(v), w - m.r, Y(v), { class: "gr" }); txt(g, m.l - 10, Y(v) + 4, v.label ?? v, "tk", "end"); }
    for (const v of xt) txt(g, X(v), h - m.b + 20, v, "tk");
    line(g, m.l, h - m.b, w - m.r, h - m.b, { class: "ax" });
    if (xl) txt(g, (m.l + w - m.r) / 2, h - 8, xl, "lab");
    if (yl) txt(g, 16, (m.t + h - m.b) / 2, yl, "lab", "middle", { transform: `rotate(-90 16 ${(m.t + h - m.b) / 2})` });
  }
  const C = () => ({ base: css("--base"), wp: css("--wp"), num: css("--num"), ink: css("--ink"), line: css("--line-2") });

  function ecdf(host) {
    const w = 980, h = 420, m = { l: 70, r: 30, t: 20, b: 56 }, c = C(), s = svg(w, h);
    const X = v => m.l + (v - 20) / 80 * (w - m.l - m.r), Y = v => h - m.b - v * (h - m.t - m.b);
    el("rect", { x: X(60), y: m.t, width: X(100) - X(60), height: h - m.t - m.b, fill: c.num, opacity: 0.07 }, s);
    txt(s, X(80), m.t + 18, "total > 60 → positive numerical score", "tk");
    axes(s, X, Y, [20, 40, 60, 80, 100], [0, .25, .5, .75, 1], "Task score", "Share of tasks ≤ score", w, h, m);
    [["Base agent", c.base], ["WindPilot", c.wp]].forEach(([k, col], idx) => {
      const v = D.ecdf[k]; let d = `M${X(20)},${Y(0)}`;
      v.forEach((x, i) => { d += `L${X(x)},${Y(i / v.length)}L${X(x)},${Y((i + 1) / v.length)}`; }); d += `L${X(100)},${Y(1)}`;
      drawPath(s, d, { stroke: col, "stroke-width": 3, "stroke-linejoin": "round" });
      line(s, m.l + 24, m.t + 30 + idx * 26, m.l + 52, m.t + 30 + idx * 26, { stroke: col, "stroke-width": 3 }); txt(s, m.l + 62, m.t + 34 + idx * 26, `${k} · mean ${(v.reduce((a, b) => a + b, 0) / v.length).toFixed(1)}`, "lab", "start", { fill: col });
    });
    host.appendChild(s);
    return "Empirical cumulative distribution of task scores (30 tasks). A total above 60 guarantees a positive numerical score under the scoring rules.";
  }
  function forest(host) {
    const w = 980, h = 300, m = { l: 210, r: 150, t: 30, b: 56 }, c = C(), s = svg(w, h);
    const X = v => m.l + (v + 5) / 35 * (w - m.l - m.r);
    axes(s, X, () => 0, [-5, 0, 5, 10, 15, 20, 25, 30], [], "WindPilot − base agent, mean task score", "", w, h, m);
    line(s, X(0), m.t, X(0), h - m.b, { stroke: c.line, "stroke-dasharray": "4 4" });
    D.forest.forEach((f, i) => {
      const y = m.t + 50 + i * 90;
      txt(s, m.l - 20, y - 4, f.group, "lab", "end"); txt(s, m.l - 20, y + 16, `n = ${f.n}`, "tk", "end");
      drawPath(s, `M${X(f.lo)},${y}L${X(f.hi)},${y}`, { stroke: c.ink, "stroke-width": 2 });
      const dmd = el("path", { d: `M${X(f.diff)},${y - 11}l11,11l-11,11l-11,-11z`, fill: c.wp, class: "pop", style: `animation-delay:${.6 + i * .2}s` }, s);
      hover(host, dmd, `${f.group}: +${f.diff.toFixed(1)} [${f.lo.toFixed(1)}, ${f.hi.toFixed(1)}]`);
      txt(s, w - m.r + 16, y - 4, `+${f.diff.toFixed(1)}`, "lab", "start", { fill: c.wp });
      txt(s, w - m.r + 16, y + 16, `${f.base.toFixed(1)} → ${f.wp.toFixed(1)}`, "tk", "start");
    });
    host.appendChild(s);
    return "Paired differences in mean task score; lines are 95% bootstrap confidence intervals (10 000 resamples).";
  }
  function abl(host) {
    const w = 980, h = 460, m = { l: 70, r: 260, t: 24, b: 50 }, c = C(), s = svg(w, h);
    const xa = m.l + 80, xb = w - m.r - 40, Y = v => h - m.b - (v - 20) / 85 * (h - m.t - m.b);
    for (const v of [20, 40, 60, 80, 100]) { line(s, m.l, Y(v), w - m.r, Y(v), { class: "gr" }); txt(s, m.l - 10, Y(v) + 4, v, "tk", "end"); }
    txt(s, xa, h - 16, "Base agent", "lab"); txt(s, xb, h - 16, "WindPilot", "lab");
    const rows = [...D.abl].sort((a, b) => b.wp - a.wp || b.base - a.base);
    const ly = []; rows.forEach(r => { let y = Y(r.wp); while (ly.some(v => Math.abs(v - y) < 17)) y += 17; ly.push(y); r._ly = y; });
    rows.forEach((r, i) => {
      const up = r.wp > r.base, col = up ? c.num : c.base;
      drawPath(s, `M${xa},${Y(r.base)}L${xb},${Y(r.wp)}`, { stroke: col, "stroke-width": up ? 2.4 : 1.6, opacity: up ? 1 : .7 });
      for (const [x, v] of [[xa, r.base], [xb, r.wp]]) { const d = el("circle", { cx: x, cy: Y(v), r: 5, fill: col, class: "pop", style: `animation-delay:${.3 + i * .05}s` }, s); hover(host, d, `${r.task} ${r.name}: ${r.base.toFixed(0)} → ${r.wp.toFixed(1)}`); }
      txt(s, xb + 18, r._ly + 4, `${r.task.replace("task-", "")} ${r.name}`, "tk", "start", { fill: up ? css("--ink-2") : css("--ink-3") });
    });
    host.appendChild(s);
    return "Scores for the 13 ABL tasks. Teal lines improved; grey lines did not change. Mean ABL gain: +18.5 points.";
  }
  function steps(host) {
    const w = 980, h = 420, m = { l: 70, r: 30, t: 24, b: 56 }, c = C(), s = svg(w, h);
    const Y = v => h - m.b - (v - 40) / 120 * (h - m.t - m.b), cols = [["base", "Base agent", c.base, 300], ["wp", "WindPilot", c.wp, 680]];
    axes(s, () => 0, Y, [], [40, 60, 80, 100, 120, 140, 160], "", "Action steps per task", w, h, m);
    line(s, m.l, Y(150), w - m.r, Y(150), { stroke: css("--npg-red"), "stroke-dasharray": "6 5", "stroke-width": 1.5 });
    txt(s, w - m.r - 4, Y(150) - 8, "150-step limit", "tk", "end", { fill: css("--npg-red") });
    D.steps.base.forEach((b, i) => { const a = D.steps.wp[i]; line(s, 300 + ((i * 37) % 23 - 11) * 6, Y(b), 680 + ((i * 53) % 23 - 11) * 6, Y(a), { stroke: css("--line"), "stroke-width": 1 }); });
    cols.forEach(([k, lab, col, cx], ci) => {
      const v = D.steps[k], mean = v.reduce((a, b) => a + b, 0) / v.length;
      v.forEach((x, i) => { const d = el("circle", { cx: cx + ((i * (ci ? 53 : 37)) % 23 - 11) * 6, cy: Y(x), r: 5.5, fill: col, opacity: .85, class: "pop", style: `animation-delay:${.1 + i * .02}s` }, s); hover(host, d, `${lab}: ${x} steps`); });
      line(s, cx - 90, Y(mean), cx + 90, Y(mean), { stroke: c.ink, "stroke-width": 2.5 });
      txt(s, cx + 100, Y(mean) + 4, `mean ${mean.toFixed(1)}`, "lab", "start"); txt(s, cx, h - 22, lab, "lab");
      txt(s, cx, m.t + 8, `${v.filter(x => x >= 150).length} tasks hit the limit`, "tk");
    });
    host.appendChild(s);
    return "Action steps per task (grey links pair the same task). Mean steps fell 19.5% and mean tokens 12.9%.";
  }
  function stages(host) {
    const w = 980, h = 420, m = { l: 70, r: 30, t: 30, b: 90 }, c = C(), s = svg(w, h), st = D.stages;
    const X = i => m.l + 80 + i * (w - m.l - m.r - 160) / (st.length - 1), Y = v => h - m.b - (v - 55) / 25 * (h - m.t - m.b);
    axes(s, () => 0, Y, [], [55, 60, 65, 70, 75, 80], "", "Mean task score", w, h, m);
    let d = ""; st.forEach((r, i) => { d += (i ? `L${X(i)},${Y(st[i - 1][2])}L` : "M") + `${X(i)},${Y(r[2])}`; });
    drawPath(s, d, { stroke: c.wp, "stroke-width": 2.5 });
    st.forEach((r, i) => {
      const dd = el("circle", { cx: X(i), cy: Y(r[2]), r: 8, fill: i === st.length - 1 ? c.wp : css("--surface"), stroke: c.wp, "stroke-width": 2.5, class: "pop", style: `animation-delay:${.4 + i * .25}s` }, s);
      hover(host, dd, `${r[0]} · n = ${r[1]} · mean ${r[2].toFixed(1)}`);
      txt(s, X(i), Y(r[2]) - 18, r[2].toFixed(1), "lab");
      if (i) txt(s, X(i), Y(r[2]) + 30, (r[2] - st[i - 1][2] >= 0 ? "+" : "") + (r[2] - st[i - 1][2]).toFixed(1), "tk", "middle", { fill: c.num });
      const words = r[0].split(" "); let a = "", lines = [];
      for (const wd of words) { if ((a + " " + wd).length > 22) { lines.push(a); a = wd; } else a = a ? a + " " + wd : wd; } lines.push(a);
      lines.forEach((ln, j) => txt(s, X(i), h - m.b + 26 + j * 17, ln, "tk"));
      txt(s, X(i), h - m.b + 26 + lines.length * 17, `n = ${r[1]}`, "tk", "middle", { fill: css("--ink-3") });
    });
    host.appendChild(s);
    return "Mean task scores under staged configurations. The second stage has score records for 28 tasks; differences use unrounded means.";
  }
  return { ecdf, forest, abl, steps, stages };
})();
