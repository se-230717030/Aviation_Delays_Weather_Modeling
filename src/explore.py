"""
M4 Exploratory Data Analysis Helpers (src/explore.py)
Provides read-only database connections, SQL query loaders, bootstrap confidence
intervals, and standardised publication-grade plotting helpers.
"""

import os
import sqlite3
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "data", "aviation.db")
FIGURES_DIR = os.path.join(BASE_DIR, "figures")
QUERIES_DIR = os.path.join(BASE_DIR, "sql", "queries")


def connect_ro(db_path=DEFAULT_DB_PATH):
    """
    Opens the SQLite database in strict READ-ONLY mode using URI syntax.
    Guarantees that M4 exploration cannot modify any table contents.
    """
    abs_path = os.path.abspath(db_path).replace("\\", "/")
    uri = f"file:{abs_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def load_sql_query(filename):
    """Loads a reusable SQL query from sql/queries/."""
    path = os.path.join(QUERIES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def set_plot_style():
    """Configures consistent, clean matplotlib styling for all M4 figures."""
    plt.rcParams.update({
        "figure.dpi": 140,
        "savefig.dpi": 160,
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.labelsize": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.28,
        "grid.linestyle": "--",
        "legend.frameon": True,
        "legend.framealpha": 0.9
    })


def format_figure_layout(fig, title, subtitle, caption):
    """
    Applies the M4 Section 4 Figure Checklist:
    - Title states the finding
    - Subtitle states the question
    - Caption at the bottom states n, denominator, clock, and flag filter
    """
    fig.suptitle(title, fontsize=12.5, fontweight="bold", x=0.04, y=0.98, ha="left")
    fig.text(0.04, 0.925, subtitle, fontsize=10, color="#333333", ha="left", style="italic")
    fig.text(0.04, 0.015, caption, fontsize=8.5, color="#444444", ha="left")
    fig.tight_layout(rect=[0.02, 0.05, 0.98, 0.90])


def save_figure(fig, filename):
    """Saves a matplotlib figure to figures/ directory deterministically."""
    os.makedirs(FIGURES_DIR, exist_ok=True)
    out_path = os.path.join(FIGURES_DIR, filename)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def compute_group_summaries(df, group_col, value_col, min_n=1):
    """
    Computes n, Mean, SD, Median, MAD, IQR, P90, and P95 per group,
    and ranks groups by both Mean, Median, and P90 (M4 Section 2.2).
    """
    records = []
    for grp, sub in df.groupby(group_col):
        vals = sub[value_col].dropna().to_numpy()
        n = len(vals)
        if n < min_n or n == 0:
            continue
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        q25, q75 = float(np.percentile(vals, 25)), float(np.percentile(vals, 75))
        records.append({
            group_col: grp,
            "n": n,
            "mean": round(float(np.mean(vals)), 2),
            "sd": round(float(np.std(vals, ddof=1)) if n > 1 else 0.0, 2),
            "median": round(med, 2),
            "mad": round(mad, 2),
            "iqr": round(q75 - q25, 2),
            "p90": round(float(np.percentile(vals, 90)), 2),
            "p95": round(float(np.percentile(vals, 95)), 2)
        })
    res = pd.DataFrame(records)
    if not res.empty:
        res["rank_by_mean"] = res["mean"].rank(ascending=False, method="min").astype(int)
        res["rank_by_median"] = res["median"].rank(ascending=False, method="min").astype(int)
        res["rank_by_p90"] = res["p90"].rank(ascending=False, method="min").astype(int)
        res = res.sort_values("median", ascending=False).reset_index(drop=True)
    return res


def bootstrap_diff_ci(group_a, group_b, stat_func=np.median, n_boot=10000, seed=42):
    """
    Computes the observed difference (stat(A) - stat(B)) and a 95% bootstrap
    confidence interval by resampling independent airframes (M4 Section 2.7).
    """
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]

    obs_diff = float(stat_func(a) - stat_func(b))
    rng = np.random.default_rng(seed)

    boot_diffs = np.empty(n_boot, dtype=float)
    n_a, n_b = len(a), len(b)
    for i in range(n_boot):
        samp_a = a[rng.integers(0, n_a, size=n_a)]
        samp_b = b[rng.integers(0, n_b, size=n_b)]
        boot_diffs[i] = stat_func(samp_a) - stat_func(samp_b)

    ci_low, ci_high = np.percentile(boot_diffs, [2.5, 97.5])
    return {
        "n_a": int(n_a),
        "n_b": int(n_b),
        "stat_a": round(float(stat_func(a)), 2),
        "stat_b": round(float(stat_func(b)), 2),
        "diff": round(obs_diff, 2),
        "ci_95_low": round(float(ci_low), 2),
        "ci_95_high": round(float(ci_high), 2),
        "boot_distribution": boot_diffs
    }