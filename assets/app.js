/* Page behaviour: reveal, nav, theme, benchmark/scoring/result widgets, trace replay, lightbox. */
(() => {
  const D = window.UW, $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const FC = ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4", "#91D1C2", "#DC0000", "#7E6148", "#B09C85"];
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const h = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };

  /* reveal + count-up */
  const countUp = el => { const to = +el.dataset.count, t0 = performance.now(), dur = 1400;
    const f = now => { const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 4); el.textContent = Math.round(to * e).toLocaleString(); if (k < 1) requestAnimationFrame(f); };
    reduce ? el.textContent = to : requestAnimationFrame(f); };
  const io = new IntersectionObserver(es => es.forEach(e => { if (!e.isIntersecting) return; e.target.classList.add("in");
    e.target.querySelectorAll?.("[data-count]").forEach(countUp); if (e.target.dataset.count) countUp(e.target); io.unobserve(e.target); }), { threshold: .18, rootMargin: "0px 0px -6% 0px" });
  const watch = () => $$(".reveal:not(.in), .stats, .kpi, .dots-card").forEach(e => io.observe(e));

  /* nav: solid on scroll + sliding ink under the active section */
  const nav = $("#nav"), links = $$(".nav nav a"), ink = $(".nav-ink");
  const secs = links.map(a => $(a.getAttribute("href")));
  function onScroll() {
    nav.classList.toggle("solid", scrollY > 30);
    let cur = -1; secs.forEach((s, i) => { if (s && s.getBoundingClientRect().top < innerHeight * .35) cur = i; });
    links.forEach((a, i) => a.classList.toggle("on", i === cur));
    if (cur >= 0) { const a = links[cur]; ink.style.left = a.offsetLeft + 12 + "px"; ink.style.width = a.offsetWidth - 24 + "px"; ink.style.opacity = 1; } else ink.style.opacity = 0;
  }
  addEventListener("scroll", onScroll, { passive: true }); onScroll();

  /* theme */
  $("#theme").addEventListener("click", () => { const r = document.documentElement, t = r.dataset.theme === "dark" ? "light" : "dark";
    r.dataset.theme = t; try { localStorage.setItem("uw-theme", t); } catch (e) {} document.dispatchEvent(new Event("uw-theme")); showChart(curChart); });

  /* finding: 30 dots per condition, ordered by score band */
  const bandCls = ["low", "", "num", "num", "full"];
  [["#dots-base", 1], ["#dots-wp", 2]].forEach(([id, col]) => { const host = $(id); let k = 0;
    D.bands.forEach((b, bi) => { for (let i = 0; i < b[col]; i++) { const d = h("i", "dot " + bandCls[bi]); d.style.transitionDelay = (k++ * 28) + "ms"; d.title = "score " + b[0]; host.appendChild(d); } }); });

  /* families + mosaic + small charts */
  const fam = $("#families");
  D.families.forEach((f, i) => { const c = h("article", "fam reveal"); c.style.setProperty("--c", FC[i]); c.style.transitionDelay = (i % 5) * 60 + "ms";
    const lvl = f.difficulty.map((n, j) => n ? `<i style="background:${FC[i]};opacity:${.35 + .65 * n / 20}" title="D${j + 1}: ${n} tasks"></i>` : `<i title="D${j + 1}: 0"></i>`).join("");
    c.innerHTML = `<span class="ids">${f.ids}</span><h4>${f.name}</h4><p>${f.stage}.</p><div class="diff" aria-label="Domain difficulty D1–D5">${lvl}</div><div class="tools">${f.software}</div>`;
    fam.appendChild(c); });
  const mo = $("#mosaic"), tipEl = $("#mosaic-tip"), ev = new Set(D.evaluated);
  for (let id = 1; id <= 200; id++) { const fi = Math.floor((id - 1) / 20), t = h("span", "tile" + (ev.has(id) ? " ev" : "")); t.style.setProperty("--c", FC[fi]);
    const lab = `task-${String(id).padStart(3, "0")} · ${D.families[fi].name}${ev.has(id) ? " · evaluated" : ""}`; t.setAttribute("aria-label", lab);
    t.addEventListener("mouseenter", () => tipEl.textContent = lab); mo.appendChild(t); }
  const bars = (host, rows, color) => { const max = Math.max(...rows.map(r => r[1]));
    rows.forEach((r, i) => host.insertAdjacentHTML("beforeend", `<div class="hbar"><span>${r[0]}</span><span class="t"><i style="--w:${r[1] / max * 100}%;--c:${color(i)};transition-delay:${i * 80}ms"></i></span><span class="v">${r[1]}</span></div>`)); };
  bars($("#init-chart"), D.init, i => FC[[3, 1, 0, 4, 2][i]]); bars($("#geo-chart"), D.geometry, () => "#3C5488");

  /* scoring bar */
  const STRUCT = [["Deliverable completeness", 15], ["Parseability", 10], ["Run provenance", 15], ["Raw evidence", 10], ["Metric traceability", 5], ["Decision note", 5]];
  const NUM = { "062": [["Raw profile evidence", 12], ["Reported-array recomputation", 8], ["Primary-metric recomputation", 5], ["Reference agreement", 15]],
                "093": [["Format & thresholds", 8], ["Metric recomputation", 12], ["Hidden-array agreement", 20]], "041": [["checkMesh recheck", 40]] };
  const SC = ["#3C5488", "#4f659a", "#6377a8", "#7788b5", "#8491B4", "#98a3c2"], NC = ["#00A087", "#4DBBD5", "#F39B7F", "#E64B35"];
  function score(ex) { const bar = $("#score-bar"), leg = $("#score-legend"); bar.innerHTML = ""; leg.innerHTML = '<div class="lyr">Structural · 60</div>';
    STRUCT.forEach(([n, p], i) => { const s = h("div", "seg", p >= 10 ? `<span>${p}</span>` : ""); s.style.flex = p; s.style.background = SC[i]; s.title = `${n}: ${p}`; bar.appendChild(s); leg.insertAdjacentHTML("beforeend", `<div><span>${n}</span><b>${p}</b></div>`); });
    leg.insertAdjacentHTML("beforeend", `<div class="lyr">Numerical · 40 · task-${ex}</div>`);
    NUM[ex].forEach(([n, p], i) => { const s = h("div", "seg", `<span>${p}</span>`); s.style.flex = "0.001"; s.style.background = NC[i % 4]; s.title = `${n}: ${p}`; bar.appendChild(s);
      requestAnimationFrame(() => requestAnimationFrame(() => s.style.flex = p)); leg.insertAdjacentHTML("beforeend", `<div><span>${n}</span><b>${p}</b></div>`); }); }
  const tabs = (root, attr, fn) => root.querySelectorAll("button").forEach(b => b.addEventListener("click", () => {
    root.querySelectorAll("button").forEach(x => x.setAttribute("aria-selected", x === b)); fn(b.dataset[attr]); }));
  tabs($(".score-head .tabs"), "ex", score); score("062");

  /* WindPilot pipeline (Table 4) */
  const MODS = [["Isolated execution", "Separate environments, core allocation and process management.", "Execution boundaries", "#8491B4"],
    ["Contract parsing", "Compile checklists and inspect the actual fixed conditions.", "Constraints stay in view", "#3C5488"],
    ["Execution provenance", "Record runs; organise IDs and hashes for every artefact.", "Deliverables ↔ actual execution", "#4DBBD5"],
    ["Deterministic verification", "Check dimensions, time axes, hashes and metrics against raw outputs.", "Errors with locations", "#00A087"],
    ["Bounded acceptance review", "An LLM reviewer reads a ≤ 96 KiB evidence view against the contract.", "Submit or repair", "#E64B35"]];
  MODS.forEach((m, i) => { const s = h("div", "pstep reveal"); s.style.setProperty("--c", m[3]); s.style.transitionDelay = i * 90 + "ms";
    s.innerHTML = `<span class="n">0${i + 1}</span><h4>${m[0]}</h4><p>${m[1]}</p><div class="o">→ ${m[2]}</div>`; $("#pipeline").appendChild(s); });

  /* KPIs (Table 7) */
  [["Tasks with numerical credit", 6, 17, "+11"], ["Mean task score", 62.5, 72.5, "+10.0"], ["Full-score tasks", 0, 3, "+3"], ["Mean action steps", 130.4, 104.9, "−19.5%"]]
    .forEach(([l, a, b, d]) => { const k = h("div", "kpi reveal"); k.innerHTML = `<p class="lab">${l}</p><div class="row"><span class="from">${a}</span><span class="to">${b}</span><span class="d">${d}</span></div>`; $("#kpis").appendChild(k); });

  /* charts */
  let curChart = "ecdf"; const chart = $("#chart"), note = $("#chart-note");
  function showChart(k) { curChart = k; chart.innerHTML = ""; note.textContent = window.UWCharts[k](chart); chart.setAttribute("aria-label", note.textContent); }
  tabs($("#chart-tabs"), "chart", showChart);
  new IntersectionObserver(([e], o) => { if (e.isIntersecting) { showChart("ecdf"); o.disconnect(); } }, { threshold: .25 }).observe(chart);

  window.UWApp = { watch, h, $, $$, reduce };
  document.dispatchEvent(new Event("uw-ready"));
  watch();
  // ?static: show everything at once (used for print / full-page screenshots)
  if (/[?&]static\b/.test(location.search)) { document.documentElement.classList.add("static"); $$(".reveal, .stats, .kpi, .dots-card").forEach(e => e.classList.add("in")); $$("[data-count]").forEach(e => e.textContent = e.dataset.count); showChart(curChart); }
})();
