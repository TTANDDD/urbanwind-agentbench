"""Draw the data figures of the UrbanWind-AgentBench manuscript.

Usage:
    python plot_figures.py <data_dir> <fig_dir>

Style: Nature-like (Arial 7 pt, 0.6 pt axes, no top/right spines, NPG palette).
Widths follow Nature column sizes: single column 89 mm, double column 183 mm.
Forms are chosen per data job: heatmap, dot matrix, waffle, lollipop,
forest plot, ECDF, slope chart, violin plot, waterfall, status matrix, strip plot
and Cleveland dot plot.
"""
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Rectangle

MM = 1 / 25.4
SINGLE, DOUBLE = 89 * MM, 183 * MM
BASE_C = "#B0B0B0"   # base agent
WP_C = "#3C5488"     # WindPilot
ACC = "#E64B35"      # highlight
GRID = "#D9D9D9"     # reference lines
INK2 = "#595959"     # secondary text
NPG = ["#3C5488", "#4DBBD5", "#00A087", "#F39B7F", "#8491B4", "#91D1C2", "#7E6148"]
BLUE_MAP = LinearSegmentedColormap.from_list("navy", ["#F4F6FB", "#C9D3EA", "#8491B4", "#3C5488", "#1F2C4F"])

plt.rcParams.update({
    "font.family": "Arial", "font.size": 7, "axes.labelsize": 7, "axes.titlesize": 7,
    "xtick.labelsize": 6.5, "ytick.labelsize": 6.5, "legend.fontsize": 6.5,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5,
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "savefig.dpi": 600, "pdf.fonttype": 42, "svg.fonttype": "none",
})


def read(path):
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def panel(ax, label, x=-0.02):
    ax.text(x, 1.04, label, transform=ax.transAxes, fontsize=8, fontweight="bold", ha="right", va="bottom")


def save(fig, out, name):
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}", bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def bare(ax):
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])


def spread(values, gap):
    """Shift sorted label positions apart so that neighbours are at least `gap` apart."""
    order = np.argsort(values)
    pos = np.array(values, dtype=float)
    for k in range(1, len(order)):
        a, b = order[k - 1], order[k]
        if pos[b] - pos[a] < gap:
            pos[b] = pos[a] + gap
    return pos


# Fig. 2 — heatmap + dot matrix
def fig02(data, out):
    rows = read(data / "fig02_family_difficulty.csv")
    soft = {r["family"]: set(r["software"].split(";")) for r in read(data / "fig02_family_software.csv")}
    fig, (a, b) = plt.subplots(1, 2, figsize=(DOUBLE, 64 * MM), gridspec_kw={"width_ratios": [0.9, 1], "wspace": 0.04})
    m = np.array([[int(r["D%d" % i]) for i in range(1, 6)] for r in rows])
    a.imshow(m, cmap=BLUE_MAP, vmin=0, vmax=20, aspect="auto")
    for i in range(m.shape[0]):
        for j in range(5):
            if m[i, j]:
                a.text(j, i, m[i, j], ha="center", va="center", fontsize=6.5, color="white" if m[i, j] >= 12 else "#222222")
    a.set_xticks(range(5), ["D1", "D2", "D3", "D4", "D5"])
    a.set_yticks(range(len(rows)), [r["family"] for r in rows])
    a.set_xticks(np.arange(-0.5, 5), minor=True)
    a.set_yticks(np.arange(-0.5, len(rows)), minor=True)
    a.grid(which="minor", color="white", linewidth=1.5)
    a.tick_params(which="both", length=0)
    for s in a.spines.values():
        s.set_visible(False)
    a.set_xlabel("Professional difficulty")
    a.xaxis.set_label_position("top")
    a.xaxis.tick_top()
    panel(a, "a")
    cols = [("Python", {"python"}), ("QGIS/GDAL", {"qgis", "gdal", "geopandas"}), ("Gmsh", {"gmsh"}),
            ("OpenFOAM", {"openfoam"}), ("PALM", {"palm", "palm_csd"}), ("NetCDF", {"netcdf"}),
            ("ParaView/PyVista", {"paraview", "pyvista"}), ("SciPy/pandas", {"scipy", "pandas", "scikit-learn", "pymoo"})]
    for i, r in enumerate(rows):
        for j, (_, keys) in enumerate(cols):
            on = bool(soft[r["family"]] & keys)
            b.scatter(j, i, s=30 if on else 5, color=WP_C if on else "#E3E3E3", zorder=3, linewidths=0)
    b.set_xticks(range(len(cols)), [c[0] for c in cols], rotation=40, ha="left")
    b.xaxis.tick_top()
    b.set_yticks(range(len(rows)), [])
    b.set_xlim(-0.6, len(cols) - 0.4)
    b.set_ylim(len(rows) - 0.5, -0.5)
    for s in b.spines.values():
        s.set_visible(False)
    b.tick_params(length=0)
    panel(b, "b", x=0.0)
    save(fig, out, "Fig2_task_distribution")


# Fig. 3 — waffle + lollipop
def fig03(data, out):
    init = read(data / "fig03_init_type.csv")
    geo = read(data / "fig03_geometry.csv")[::-1]
    fig, (a, b) = plt.subplots(1, 2, figsize=(DOUBLE, 60 * MM), gridspec_kw={"width_ratios": [1.15, 1], "wspace": 0.55})
    colors = [NPG[0], NPG[1], NPG[3], NPG[2], "#C8C8C8"]
    cells = [k for k, r in enumerate(init) for _ in range(int(r["count"]))]
    ncol = 20
    for n, k in enumerate(cells):
        col, row = n % ncol, n // ncol
        a.add_patch(Rectangle((col, -row), 0.86, 0.86, color=colors[k], linewidth=0))
    a.set_xlim(-0.2, ncol)
    a.set_ylim(-len(cells) / ncol - 0.3, 1.1)
    a.set_aspect("equal")
    bare(a)
    for k, r in enumerate(init):
        a.scatter([], [], marker="s", s=22, color=colors[k], label=f"{r['init_type']} ({r['count']})")
    a.legend(loc="upper center", bbox_to_anchor=(0.5, -0.02), ncol=2, handletextpad=0.3, columnspacing=1.0)
    a.set_title("200 tasks, one square per task", fontsize=6.5, color=INK2, pad=3)
    panel(a, "a")
    y = np.arange(len(geo))
    v = [int(r["count"]) for r in geo]
    b.hlines(y, 0, v, color=WP_C, linewidth=1.0)
    b.scatter(v, y, s=22, color=WP_C, zorder=3)
    for i, c in enumerate(v):
        b.text(c + 3, i, str(c), va="center", fontsize=6.5)
    b.set_yticks(y, [r["geometry"] for r in geo])
    b.set_xlabel("Number of tasks")
    b.set_xlim(0, 112)
    b.tick_params(axis="y", length=0)
    b.spines["left"].set_visible(False)
    panel(b, "b", x=-0.45)
    save(fig, out, "Fig3_initial_state_geometry")




# Fig. 7 — forest plot of paired differences
def fig07(data, out):
    rows = read(data / "fig07_forest.csv")[::-1]
    fig, ax = plt.subplots(figsize=(SINGLE, 36 * MM))
    for i, r in enumerate(rows):
        d, lo, hi = float(r["diff"]), float(r["ci_low"]), float(r["ci_high"])
        ax.hlines(i, lo, hi, color=WP_C, linewidth=1.4)
        ax.scatter(d, i, marker="D", s=30, color=WP_C, zorder=3)
        ax.text(hi + 1.2, i, f"+{d:.1f}  [{lo:.1f}, {hi:.1f}]", va="center", fontsize=6.2)
        ax.text(-23.5, i - 0.3, f"{float(r['base_mean']):.1f} → {float(r['windpilot_mean']):.1f}", va="center", fontsize=5.8, color=INK2)
    ax.axvline(0, color=GRID, linewidth=0.8, zorder=0)
    ax.set_yticks(range(len(rows)), [f"{r['group']} (n = {r['n']})" for r in rows])
    ax.tick_params(axis="y", length=0, pad=2)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(-24, 52)
    ax.set_xticks([0, 10, 20, 30])
    ax.spines["bottom"].set_bounds(0, 35)
    ax.set_xlabel("Difference in mean task score (WindPilot − base agent)")
    ax.set_ylim(-0.6, len(rows) - 0.4)
    save(fig, out, "Fig7_main_results")


# Fig. 8 — empirical cumulative distribution
def fig08(data, out):
    rows = read(data / "fig08_ecdf.csv")
    fig, ax = plt.subplots(figsize=(SINGLE, 60 * MM))
    ax.axvspan(60.5, 102, color="#EEF1F8", zorder=0, linewidth=0)
    ax.text(81, 0.04, "Numerical score obtained", ha="center", fontsize=6, color=WP_C)
    for cond, c in (("Base agent", BASE_C), ("WindPilot", WP_C)):
        v = np.sort([float(r["score"]) for r in rows if r["condition"] == cond])
        yv = np.arange(1, len(v) + 1) / len(v)
        ax.step(np.r_[25, v, 102], np.r_[0, yv, 1], where="post", color=c, linewidth=1.6, label=cond)
        above = int((v > 60).sum())
        ax.text(101, 1 - above / len(v) + 0.02, f"{above}/30 > 60", ha="right", va="bottom", fontsize=6, color=c, fontweight="bold")
    ax.axvline(60, color=GRID, linewidth=0.8, zorder=1)
    ax.set_xlim(25, 102)
    ax.set_ylim(0, 1.03)
    ax.set_xlabel("Task score")
    ax.set_ylabel("Cumulative fraction of tasks")
    ax.legend(loc="upper left")
    save(fig, out, "Fig8_score_distribution")


# Fig. 9 — slope chart
def fig09(data, out):
    rows = read(data / "fig09_abl_tasks.csv")
    fig, ax = plt.subplots(figsize=(SINGLE, 80 * MM))
    for r in rows:
        b, w = float(r["base"]), float(r["windpilot"])
        up = w > b
        ax.plot([0, 1], [b, w], color=WP_C if up else "#C8C8C8", linewidth=1.1 if up else 0.9, zorder=2 if up else 1)
        ax.scatter([0, 1], [b, w], s=14, color=[BASE_C, WP_C if up else "#C8C8C8"], zorder=3, linewidths=0)
    ends = {}
    for r in rows:
        ends.setdefault(round(float(r["windpilot"]), 1), []).append(r)
    ys = sorted(ends)
    for yv, py in zip(ys, spread(ys, 4.2)):
        rs = ends[yv]
        label = ", ".join(r["task"][5:] for r in rs) if len(rs) > 1 else f"{rs[0]['task'][5:]} {rs[0]['name']}"
        ax.text(1.06, py, f"{label}  {yv:g}", va="center", fontsize=5.8, color="#333333")
    ax.set_xticks([0, 1], ["Base agent", "WindPilot"])
    ax.set_xlim(-0.15, 2.05)
    ax.set_ylim(25, 106)
    ax.set_ylabel("Task score")
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(axis="x", length=0)
    ax.axhline(60, xmin=0, xmax=0.53, color=GRID, linewidth=0.8, zorder=0)
    ax.set_title("13 ABL tasks: 9 up · 4 unchanged · 0 down", fontsize=6.5, color=WP_C, loc="left", pad=4)
    save(fig, out, "Fig9_abl_tasks")


# Fig. 10 — violin + strip plot of action steps
def fig10(data, out):
    rows = read(data / "fig10_steps_tokens.csv")
    fig, ax = plt.subplots(figsize=(SINGLE, 62 * MM))
    rng = np.random.default_rng(0)
    for k, (key, c, name) in enumerate((("base_steps", BASE_C, "Base agent"), ("windpilot_steps", WP_C, "WindPilot")), start=1):
        v = np.array([int(r[key]) for r in rows])
        vp = ax.violinplot(v, positions=[k], widths=0.7, showextrema=False)
        for b in vp["bodies"]:
            b.set_facecolor(c)
            b.set_alpha(0.25)
            b.set_edgecolor("none")
        ax.scatter(k + rng.uniform(-0.1, 0.1, len(v)), v, s=8, color=c, linewidths=0, zorder=3)
        ax.hlines(v.mean(), k - 0.22, k + 0.22, color="#222222", linewidth=1.0, zorder=4)
        ax.text(k + 0.26, v.mean(), f"mean {v.mean():.3g}", va="center", fontsize=6)
        ax.text(k, 166, f"{int((v >= 150).sum())} at budget", ha="center", fontsize=6, color=ACC)
    ax.axhline(150, color=ACC, linewidth=0.8, zorder=1)
    ax.text(0.52, 151.5, "Step budget", color=ACC, fontsize=6, va="bottom")
    ax.set_xticks([1, 2], ["Base agent", "WindPilot"])
    ax.set_xlim(0.45, 2.7)
    ax.set_ylim(30, 172)
    ax.set_ylabel("Action steps per task")
    save(fig, out, "Fig10_action_steps")


# Fig. 11 — waterfall of stage-wise module addition
def fig11(out):
    steps = [("Base agent", 62.45, "total"), ("+ Isolated\nenvironment\n+ contract hints", 62.34 - 62.45, "delta"),
             ("+ Acceptance checks\n+ review\n+ runtime guard", 70.47 - 62.34, "delta"),
             ("+ Delivery closure\n+ evidence\nconsistency", 72.45 - 70.47, "delta"), ("WindPilot", 72.45, "total")]
    fig, ax = plt.subplots(figsize=(DOUBLE * 0.7, 62 * MM))
    level, xs = 0.0, np.arange(len(steps))
    for x, (name, v, kind) in zip(xs, steps):
        if kind == "total":
            ax.bar(x, v - 50, bottom=50, width=0.58, color=BASE_C if x == 0 else WP_C, linewidth=0)
            ax.text(x, v + 0.5, f"{v:.1f}", ha="center", fontsize=6.5, fontweight="bold")
            level = v
        else:
            ax.bar(x, v, bottom=level, width=0.58, color=NPG[2] if v > 0 else "#9A9A9A", linewidth=0)
            ax.text(x, max(level, level + v) + 0.5, f"{v:+.1f}", ha="center", fontsize=6.5)
            ax.plot([x - 0.71, x - 0.29], [level, level], color="#9A9A9A", linewidth=0.5)
            level += v
    ax.plot([xs[-1] - 0.71, xs[-1] - 0.29], [level, level], color="#9A9A9A", linewidth=0.5)
    ax.set_xticks(xs, [s[0] for s in steps], fontsize=5.8)
    ax.set_ylim(50, 76)
    ax.set_ylabel("Mean task score")
    save(fig, out, "Fig11_stagewise_modules")


# Fig. 13 — task-068: array coverage schematic + check-status matrix
def fig13(data, out):
    rows = read(data / "fig13_task068_checks.csv")
    steps = sorted({int(r["checkpoint_step"]) for r in rows})
    items = list(dict.fromkeys(r["item"] for r in rows))
    fig, (a, b) = plt.subplots(1, 2, figsize=(DOUBLE, 62 * MM), gridspec_kw={"width_ratios": [1, 1.25], "wspace": 0.75})
    # (a) raw sampling 20 x 52 vs delivered 52 (single time level)
    a.add_patch(Rectangle((0, 2.6), 52, 20, color="#DCE3F1", linewidth=0))
    for k in range(21):
        a.plot([0, 52], [2.6 + k, 2.6 + k], color="white", linewidth=0.35)
    a.text(26, 23.4, "Raw sampling: 20 write times × 52 probes", ha="center", fontsize=6.2, color=WP_C)
    a.add_patch(Rectangle((0, 0), 52, 1, color=ACC, linewidth=0))
    a.text(26, -1.6, "Delivered at step 91: 52 probes (time dimension missing)", ha="center", fontsize=6.2, color=ACC)
    a.annotate("", xy=(54.5, 0.5), xytext=(54.5, 22.6), arrowprops={"arrowstyle": "<->", "color": INK2, "lw": 0.6})
    a.text(55.5, 12.5, "time", rotation=90, va="center", fontsize=6, color=INK2)
    a.text(26, -4.6, "Shapes: U 20×52×3 vs 52×3;  k, ω 20×52 vs 52", ha="center", fontsize=5.8, color="#333333")
    a.set_xlim(-1, 58)
    a.set_ylim(-6, 25)
    bare(a)
    panel(a, "a")
    # (b) status matrix
    for j, s in enumerate(steps):
        for i, it in enumerate(items):
            r = next(x for x in rows if int(x["checkpoint_step"]) == s and x["item"] == it)
            fail = r["failed"] == "1"
            b.add_patch(Rectangle((j - 0.42, i - 0.38), 0.84, 0.76, color=ACC if fail else "#00A087", alpha=0.95 if fail else 0.75, linewidth=0))
            b.text(j, i, "Fail" if fail else "Pass", ha="center", va="center", fontsize=6, color="white", fontweight="bold")
    b.set_xticks(range(len(steps)), [f"Step {s}" for s in steps])
    b.set_yticks(range(len(items)), items, fontsize=6)
    b.set_xlim(-0.6, len(steps) - 0.4)
    b.set_ylim(len(items) - 0.5, -0.5)
    b.xaxis.tick_top()
    for s in b.spines.values():
        s.set_visible(False)
    b.tick_params(length=0)
    panel(b, "b", x=-0.05)
    save(fig, out, "Fig13_task068_evidence_check")


# Fig. 14 — checkpoint strip plot + Cleveland dot plot (intercepted vs repaired)
def fig14(data, out):
    cps = read(data / "fig14_checkpoints.csv")
    cat = read(data / "fig14_check_categories.csv")[::-1]
    fig, (a, b) = plt.subplots(1, 2, figsize=(DOUBLE, 64 * MM), gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.85})
    tasks = sorted({r["task"] for r in cps})
    rng = np.random.default_rng(1)
    for r in cps:
        i = tasks.index(r["task"])
        err = r["has_error"] == "1"
        a.scatter(int(r["step"]), i + rng.uniform(-0.15, 0.15), s=14 if err else 9,
                  color=ACC if err else WP_C, alpha=1 if err else 0.55, linewidths=0, zorder=3 if err else 2)
    a.axvline(90, color=GRID, linewidth=0.8, zorder=0)
    a.text(89, len(tasks) + 0.1, "Automatic delivery\ncheck (step 90)", fontsize=5.6, color=INK2, va="bottom", ha="right")
    a.text(149, len(tasks) + 0.1, "Step\nbudget", fontsize=5.6, color=ACC, va="bottom", ha="right")
    a.axvline(150, color=ACC, linewidth=0.8, zorder=0)
    a.set_yticks(range(len(tasks)), [t[5:] for t in tasks], fontsize=4.8)
    a.set_ylim(-0.8, len(tasks) + 0.9)
    a.set_xlim(0, 155)
    a.set_xlabel("Action step")
    a.set_ylabel("Task")
    a.scatter([], [], s=12, color=WP_C, alpha=0.6, label="Check passed")
    a.scatter([], [], s=14, color=ACC, label="Check found errors")
    a.legend(loc="lower left", ncol=2, bbox_to_anchor=(0, -0.3))
    panel(a, "a")
    y = np.arange(len(cat))
    for i, r in enumerate(cat):
        n_i, n_r = int(r["intercepted"]), int(r["repaired"])
        b.hlines(i, n_r, n_i, color="#C8C8C8", linewidth=1.0)
        b.scatter(n_i, i, s=26, facecolors="white", edgecolors=ACC, linewidths=1.0, zorder=3, label="Intercepted" if i == 0 else None)
        b.scatter(n_r, i, s=22, color=WP_C, zorder=4, label="Repaired before submission" if i == 0 else None)
        b.text(n_i + 3.5, i, f"{n_r}/{n_i}", va="center", fontsize=6)
    b.set_yticks(y, [r["category"] for r in cat])
    b.tick_params(axis="y", length=0)
    b.spines["left"].set_visible(False)
    b.set_xlim(0, max(int(r["intercepted"]) for r in cat) * 1.18)
    b.set_xlabel("Number of errors (30 tasks)")
    tot_i = sum(int(r["intercepted"]) for r in cat)
    tot_r = sum(int(r["repaired"]) for r in cat)
    b.set_title(f"{tot_r}/{tot_i} intercepted errors repaired", fontsize=6.5, color=WP_C, loc="left", pad=4)
    b.legend(loc="upper center", bbox_to_anchor=(0.4, -0.22), ncol=2)
    panel(b, "b", x=-0.75)
    save(fig, out, "Fig14_interventions")


def main(data_dir, fig_dir):
    data, out = Path(data_dir), Path(fig_dir)
    out.mkdir(parents=True, exist_ok=True)
    fig02(data, out)
    fig03(data, out)
    fig07(data, out)
    fig08(data, out)
    fig09(data, out)
    fig10(data, out)
    fig11(out)
    fig13(data, out)
    fig14(data, out)


if __name__ == "__main__":
    main(*sys.argv[1:3])
