/* task-061 trace replay (recorded commands/outputs, trimmed), lightbox, copy-to-clipboard. */
(() => {
  const D = window.UW, A = window.UWApp, $ = A.$;
  const STAGE_EN = { "读取任务合同": "Read contract", "制定执行计划": "Plan", "生成 OpenFOAM 算例": "Generate case", "划分网格": "Mesh",
    "求解计算": "Solve", "后处理与交付": "Post-process", "确定性核验": "Verify", "验收评审": "Review", "独立评分": "Score" };
  const STAGE_C = { "Read contract": "#3C5488", "Plan": "#8491B4", "Generate case": "#00A087", "Mesh": "#4DBBD5", "Solve": "#3C5488",
    "Post-process": "#00A087", "Verify": "#F39B7F", "Review": "#E64B35", "Score": "#00A087" };
  const CAP = { 1: "List the task inputs", 3: "Read the acceptance contract", 14: "Write the plan from the contract", 30: "Case generator fails: KeyError",
    31: "Fix the key, regenerate dictionaries", 32: "blockMesh + checkMesh: 365,488 cells", 38: "simpleFoam, 400 iterations (1 rank)",
    45: "Recompute inlet RMSE from raw probes", 58: "Verification: 2 stale hashes", 59: "Re-register hashes", 62: "Review: contract says 16 ranks",
    87: "Re-solve with 16 MPI ranks", 89: "Rebind the 16-rank run record", 94: "Review: wrong run receipt cited", 98: "Fix citation, recheck 93 hashes", 102: "Independent scoring" };
  const FLAG = new Set([30, 45, 58, 62, 94]);
  const steps = D.trace.map(t => ({ ...t, stage: STAGE_EN[t.stage] || t.stage }));
  steps.push({ step: 102, stage: "Score", segs: [["event", ["[WindPilot] agent finished (step 102). Independent scorer:"]], ["grade", D.grade]] });
  const list = $("#trace-steps"), term = $("#term"), lab = $("#term-step");
  const esc = s => s.replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  steps.forEach((s, i) => { const li = document.createElement("li"), b = document.createElement("button");
    b.innerHTML = `<span class="s">${String(s.step).padStart(3, "0")}</span><span>${CAP[s.step] || s.stage}</span>`;
    b.style.setProperty("--c", STAGE_C[s.stage] || "#3C5488"); if (FLAG.has(s.step)) b.classList.add("flag");
    b.addEventListener("click", () => { stop(); show(i); }); li.appendChild(b); list.appendChild(li); });
  let cur = 0, timer = null;
  function show(i) {
    cur = (i + steps.length) % steps.length; const s = steps[cur];
    list.querySelectorAll("button").forEach((b, j) => b.classList.toggle("on", j === cur));
    list.children[cur].scrollIntoView({ block: "nearest", behavior: A.reduce ? "auto" : "smooth" });
    lab.textContent = `step ${s.step} / 102 · ${s.stage}`;
    let html = "", n = 0; const line = (cls, t) => html += `<span class="ln ${cls}" style="animation-delay:${Math.min(n++ * 35, 1400)}ms">${t}</span>\n`;
    s.segs.forEach(([k, v]) => v.forEach((t, j) => {
      if (k === "cmd") line("c", (j ? "<b>&gt;</b> " : "<b>$</b> ") + esc(t));
      else if (k === "err") line("e", esc(t)); else if (k === "event") line("v", esc(t));
      else if (k === "comment") line("m", esc(t)); else if (k === "grade") line(/SCORE/.test(t) ? "g" : "o", esc(t));
      else line("o", esc(t)); }));
    term.innerHTML = html; term.scrollTop = 0;
  }
  const play = $("#trace-play");
  function stop() { clearInterval(timer); timer = null; play.textContent = "Play"; }
  play.addEventListener("click", () => { if (timer) return stop(); play.textContent = "Pause"; show(cur + 1); timer = setInterval(() => { if (cur === steps.length - 1) return stop(); show(cur + 1); }, 3400); });
  $("#trace-prev").addEventListener("click", () => { stop(); show(cur - 1); });
  $("#trace-next").addEventListener("click", () => { stop(); show(cur + 1); });
  show(0);

  /* lightbox */
  const lb = $("#lightbox"), img = lb.querySelector("img"); let last = null;
  document.addEventListener("click", e => { const z = e.target.closest(".zoom"); if (!z) return; last = z;
    img.src = z.dataset.src; img.alt = z.querySelector("img")?.alt || "Figure"; lb.hidden = false; requestAnimationFrame(() => lb.classList.add("open")); lb.querySelector(".lb-close").focus(); });
  const close = () => { lb.classList.remove("open"); setTimeout(() => { lb.hidden = true; }, 300); last?.focus(); };
  lb.addEventListener("click", e => { if (e.target !== img) close(); });
  addEventListener("keydown", e => { if (e.key === "Escape" && !lb.hidden) close(); });

  /* soon links + copy */
  document.querySelectorAll("[data-soon]").forEach(a => a.addEventListener("click", e => e.preventDefault()));
  $("#copy-bib").addEventListener("click", async e => { try { await navigator.clipboard.writeText($("#bib").textContent); e.target.textContent = "Copied"; } catch (x) { e.target.textContent = "Select & copy"; }
    setTimeout(() => e.target.textContent = "Copy", 1600); });
})();
