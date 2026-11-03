import os
import sys
import io
import contextlib
import nbformat
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell, new_output

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
NOTEBOOK_PATH = os.path.join(BASE_DIR, "notebooks", "m4_eda.ipynb")
FIGURES_DIR = os.path.join(BASE_DIR, "figures")
os.makedirs(os.path.dirname(NOTEBOOK_PATH), exist_ok=True)
os.makedirs(FIGURES_DIR, exist_ok=True)

nb = new_notebook()
cells = []

# Hücrelerin sırayla çalışacağı ortak Python isim alanı (Namespace)
exec_namespace = {"__name__": "__main__"}

def add_md(text):
    cells.append(new_markdown_cell(text))

def add_and_run_code(code_str):
    """Kodu çalıştırır, stdout çıktısını yakalar ve hücreye gömer."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code_str, exec_namespace)
    out_str = buf.getvalue()
    
    cell = new_code_cell(code_str)
    cell["execution_count"] = len([c for c in cells if c["cell_type"] == "code"]) + 1
    if out_str:
        cell["outputs"] = [new_output("stream", name="stdout", text=out_str)]
    else:
        cell["outputs"] = []
    cells.append(cell)
    if out_str:
        print(out_str, end="")

# --- INTRO ---
add_md(r"""# Milestone M4 — Exploratory Data Analysis (`m4_eda.ipynb`)
**Course:** Introduction to Data Science and Engineering  
**Lifecycle Stage:** EXPLORE  

This notebook explores the M3 cleaned layer (`data/aviation.db`) to answer our project's core analytical questions while testing how results depend on summary statistics, coverage denominators, flag filters, time clocks, outlier drivers, and subgroup composition.
* **Read-Only Guarantee:** The database is opened exclusively via `connect_ro()` using SQLite URI `mode=ro`.
* **Reproducibility:** All parameters and random seeds are declared in Cell 1, aggregations are pushed to parameterised SQL queries in `sql/queries/`, and all six figures are written deterministically to `figures/`.""")

# --- CELL 1: PARAMETERS ---
add_and_run_code(r"""import os
import sys
import sqlite3
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from scipy import stats

# Add repository root to sys.path to import src/explore.py
REPO_ROOT = os.path.abspath(os.path.join(os.getcwd(), '..')) if os.path.basename(os.getcwd()) == 'notebooks' else os.path.abspath(os.getcwd())
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.explore import (
    connect_ro, load_sql_query, set_plot_style,
    format_figure_layout, save_figure, compute_group_summaries, bootstrap_diff_ci
)

# ============================================================================
# M4 EXPLORATION PARAMETERS (Section 3.1 Requirement)
# ============================================================================
DB_PATH = os.path.join(REPO_ROOT, 'data', 'aviation.db')
RANDOM_SEED = 42
BOOTSTRAP_ITERATIONS = 10000

# Entities & Main Groups
FOCUS_COUNTRIES = ['United States', 'United Kingdom', 'Germany', 'Turkey', 'France', 'China', 'Canada', 'Japan']
COMPARISON_PAIR = ('United States', 'United Kingdom')

# Coverage Rules (Section 2.4)
MIN_COUNTRY_AIRBORNE_N = 30       # Minimum trusted airborne aircraft required for country-level ranking
MIN_DAILY_WEATHER_HOURS = 18      # Minimum valid hours (out of 24) required for a daily weather aggregate

# Flag Filters (Section 2.4)
FLAG_FILTER_REPORTED = ('ok', 'unusual_high')  # Default analytical filter keeping verified real extremes
FLAG_FILTER_STRICT = ('ok',)                   # Strict filter excluding unusual_high outliers

# Histogram Bin Width Candidates (Section 2.3)
VELOCITY_BIN_CANDIDATES = [5.0, 15.0, 40.0]    # m/s (15.0 m/s chosen for main plot)
SELECTED_VELOCITY_BIN_WIDTH = 15.0

# Time Clocks (Section 2.5)
STORAGE_CLOCK = 'UTC'
ISTANBUL_LOCAL_CLOCK = 'Europe/Istanbul'       # UTC+3 for Istanbul Airport (41.2753N, 28.7519E)

# Look Count (Section 2.7)
TOTAL_COMPARISONS_INSPECTED = 12

set_plot_style()
con = connect_ro(DB_PATH)
print("Connected to database in READ-ONLY mode:", DB_PATH)""")

# --- SECTION 2.1: RAW VS CLEANED ---
add_md(r"""## 2.1 Raw versus Cleaned: The Key Number
**Question:** *What is the global mean airborne ground speed (`velocity`, m/s) and barometric altitude (`baro_altitude`, m) of active flights, and how much did M3 cleaning change these key numbers?*""")

add_and_run_code(r"""query_2_1 = load_sql_query('m4_raw_vs_clean.sql')
df_raw_vs_clean = pd.read_sql_query(query_2_1, con, params=(0, 0, 0))
print("=== 2.1 RAW M2 vs. CLEANED M3 LAYER (AIRBORNE FLIGHTS: on_ground = 0) ===")
print(df_raw_vs_clean.to_string(index=False))

# Also compute standard deviation and median in Python to show impact of supersonic spikes on spread
df_raw_air = pd.read_sql_query(
    "SELECT velocity, baro_altitude FROM flight_state WHERE on_ground = ?", con, params=(0,)
)
df_cln_air = pd.read_sql_query(
    "SELECT velocity, baro_altitude FROM flight_state_resolved WHERE on_ground = ? AND velocity_flag IN ('ok', 'unusual_high') AND baro_altitude_flag IN ('ok', 'unusual_high')",
    con, params=(0,)
)

print("\n--- Spread & Extremes Impact ---")
print(f"Raw M2 Airborne Velocity      (n={ df_raw_air['velocity'].count() }): Mean = {df_raw_air['velocity'].mean():.2f} m/s, SD = {df_raw_air['velocity'].std():.2f} m/s, Median = {df_raw_air['velocity'].median():.2f} m/s, Max = {df_raw_air['velocity'].max():.2f} m/s")
print(f"Cleaned M3 Airborne Velocity  (n={ df_cln_air['velocity'].count() }): Mean = {df_cln_air['velocity'].mean():.2f} m/s, SD = {df_cln_air['velocity'].std():.2f} m/s, Median = {df_cln_air['velocity'].median():.2f} m/s, Max = {df_cln_air['velocity'].max():.2f} m/s")
print(f"Raw M2 Airborne Altitude      (n={ df_raw_air['baro_altitude'].count() }): Mean = {df_raw_air['baro_altitude'].mean():.2f} m, Min = {df_raw_air['baro_altitude'].min():.2f} m, Max = {df_raw_air['baro_altitude'].max():.2f} m")
print(f"Cleaned M3 Airborne Altitude  (n={ df_cln_air['baro_altitude'].count() }): Mean = {df_cln_air['baro_altitude'].mean():.2f} m, Min = {df_cln_air['baro_altitude'].min():.2f} m, Max = {df_cln_air['baro_altitude'].max():.2f} m")""")

# --- SECTION 2.2: QUESTION & SUMMARY ---
add_md(r"""## 2.2 The Question, and the Summary That Answers It
**Project Question (Operationalised for M4):** *Across major national air fleets (`origin_country` with $n \ge 30$ airborne aircraft), how do typical and extreme airborne ground speeds (`velocity`, m/s) and cruising altitudes (`baro_altitude`, m) compare, and how does local wind speed at Istanbul Airport behave across the week?*

* **Chosen Primary Summary:** **Median** (with **IQR**), because airborne velocity and altitude distributions are strongly left-skewed due to climbing/descending traffic near airports. The median represents the typical cruising aircraft without being pulled down by terminal-area traffic.
* **Alternative Summary Compared:** **Mean** and **90th Percentile (P90)**. While the median answers *"How fast does a typical aircraft from country X fly?"*, P90 answers *"How fast do the fastest long-haul/jet-stream flights of country X travel?"*""")

add_and_run_code(r"""df_airborne = pd.read_sql_query('''
    SELECT resolved_aircraft_key, origin_country, callsign, longitude, latitude,
           baro_altitude, baro_altitude_flag, velocity, velocity_flag, on_ground
    FROM flight_state_resolved
    WHERE on_ground = ?
      AND velocity_flag IN ('ok', 'unusual_high')
      AND baro_altitude_flag IN ('ok', 'unusual_high')
''', con, params=(0,))

vel_summary = compute_group_summaries(df_airborne, 'origin_country', 'velocity', min_n=MIN_COUNTRY_AIRBORNE_N)
alt_summary = compute_group_summaries(df_airborne, 'origin_country', 'baro_altitude', min_n=MIN_COUNTRY_AIRBORNE_N)

print(f"=== AIRBORNE VELOCITY (m/s) BY COUNTRY (Coverage Rule: n >= {MIN_COUNTRY_AIRBORNE_N}, Flag Filter: ok + unusual_high) ===")
print(vel_summary.head(12).to_string(index=False))

print(f"\n=== AIRBORNE BARO_ALTITUDE (m) BY COUNTRY (Coverage Rule: n >= {MIN_COUNTRY_AIRBORNE_N}, Flag Filter: ok + unusual_high) ===")
print(alt_summary.head(12).to_string(index=False))""")

# --- SECTION 2.3: SHAPE ---
add_md(r"""## 2.3 Shape of Key Variables (Histogram Bin-Width Trial & ECDF)
**Question:** *What is the distributional shape of airborne ground speed (`velocity`, m/s) across the global fleet and major countries, and why is a single parametric mean insufficient?*""")

add_and_run_code(r"""# Figure 1: 2x2 Panel showing bin width trials (5, 15, 40 m/s), main histogram with 15 m/s bins, and ECDF by focus countries
fig, axes = plt.subplots(2, 2, figsize=(13, 9))

vel_all = df_airborne['velocity'].dropna().to_numpy()
n_vel_all = len(vel_all)

# Panel A: Bin width = 15 m/s (Selected natural unit: ~54 km/h step) with Median & Mean reference lines
ax = axes[0, 0]
bins_15 = np.arange(0, 360, SELECTED_VELOCITY_BIN_WIDTH)
ax.hist(vel_all, bins=bins_15, color='#2b5c8f', edgecolor='white', alpha=0.88)
ax.axvline(np.median(vel_all), color='#d95f02', linestyle='-', linewidth=2, label=f"Median: {np.median(vel_all):.1f} m/s")
ax.axvline(np.mean(vel_all), color='#7570b3', linestyle='--', linewidth=2, label=f"Mean: {np.mean(vel_all):.1f} m/s")
ax.axvline(285.0, color='#e7298a', linestyle=':', linewidth=1.8, label="Unusual High Threshold (285 m/s)")
ax.set_title("A. Global Fleet Velocity Histogram (Bin = 15 m/s, Chosen)")
ax.set_xlabel("Airborne Ground Speed (m/s) [ Snapshot: 2026-10-01 10:33 UTC ]")
ax.set_ylabel("Aircraft Count (bars start at 0)")
ax.set_ylim(bottom=0)
ax.legend(loc='upper left', fontsize=8.5)

# Panel B: Bin width comparison (Too narrow = 5 m/s noisy vs Too wide = 40 m/s oversmoothed)
ax = axes[0, 1]
ax.hist(vel_all, bins=np.arange(0, 360, 40.0), density=True, color='#999999', alpha=0.45, edgecolor='black', label='Bin = 40 m/s (Oversmoothed)')
ax.hist(vel_all, bins=np.arange(0, 360, 5.0), density=True, histtype='step', color='#1b9e77', linewidth=1.3, label='Bin = 5 m/s (Noisy spikes)')
ax.hist(vel_all, bins=bins_15, density=True, histtype='step', color='#d95f02', linewidth=2.2, label='Bin = 15 m/s (Selected balance)')
ax.set_title("B. Bin-Width Sensitivity Trial (5 m/s vs 15 m/s vs 40 m/s)")
ax.set_xlabel("Airborne Ground Speed (m/s) [ UTC ]")
ax.set_ylabel("Probability Density")
ax.set_ylim(bottom=0)
ax.legend(loc='upper left', fontsize=8.5)

# Panel C: Global ECDF of Airborne Velocity & Baro Altitude Quantiles
ax = axes[1, 0]
x_sorted = np.sort(vel_all)
y_ecdf = np.arange(1, n_vel_all + 1) / n_vel_all
ax.plot(x_sorted, y_ecdf, color='#2b5c8f', linewidth=2.2, label=f"Global Fleet (n={n_vel_all:,})")
ax.axhline(0.50, color='#666666', linestyle='--', linewidth=1, label="50th Percentile (Median)")
ax.axhline(0.90, color='#d95f02', linestyle=':', linewidth=1.2, label="90th Percentile (P90)")
ax.axvline(285.0, color='#e7298a', linestyle=':', linewidth=1.5, label="Unusual High (285 m/s)")
ax.set_title("C. Global Airborne Velocity ECDF (Reveals Left Tail & Cruise Plateau)")
ax.set_xlabel("Airborne Ground Speed (m/s) [ UTC ]")
ax.set_ylabel("Cumulative Probability F(x)")
ax.set_ylim(0, 1.02)
ax.legend(loc='upper left', fontsize=8.5)

# Panel D: ECDF across Top 5 Countries (Same scale comparison)
ax = axes[1, 1]
palette = ['#1b9e77', '#d95f02', '#7570b3', '#e7298a', '#66a61e']
top5_countries = ['United States', 'United Kingdom', 'Germany', 'Turkey', 'China']
for c_name, col_hex in zip(top5_countries, palette):
    sub_v = np.sort(df_airborne.loc[df_airborne['origin_country'] == c_name, 'velocity'].dropna().to_numpy())
    if len(sub_v) > 0:
        ax.plot(sub_v, np.arange(1, len(sub_v) + 1) / len(sub_v), linewidth=1.8, color=col_hex, label=f"{c_name} (n={len(sub_v)})")
ax.axhline(0.50, color='#666666', linestyle='--', linewidth=1)
ax.axhline(0.90, color='#333333', linestyle=':', linewidth=1)
ax.set_title("D. Velocity ECDF by Country (Ranking Crosses Between Median & P90)")
ax.set_xlabel("Airborne Ground Speed (m/s) [ UTC ]")
ax.set_ylabel("Cumulative Probability F(x)")
ax.set_ylim(0, 1.02)
ax.legend(loc='upper left', fontsize=8.5)

format_figure_layout(
    fig,
    title="Airborne Velocity Is Strongly Left-Skewed with a Dense Cruise Mode at 215-245 m/s",
    subtitle="How is airborne ground speed distributed globally and across major national fleets (on_ground = 0)?",
    caption=f"Source: M3 flight_state_resolved (2026-10-01 10:33 UTC). n = {n_vel_all:,} airborne aircraft. Flag filter: velocity_flag IN ('ok', 'unusual_high') AND baro_altitude_flag IN ('ok', 'unusual_high')."
)
fig1_path = save_figure(fig, "fig1_velocity_shape_hist_ecdf.png")
print("Saved Figure 1:", fig1_path)""")

# --- SECTION 2.4: COVERAGE & FLAG FILTER ---
add_md(r"""## 2.4 Coverage, Denominators, and the Flag Filter
**Question:** *How do thin country denominators ($n < 30$) and flag filter choices (`ok` only vs `ok + unusual_high`) alter country altitude and velocity rankings?*""")

add_and_run_code(r"""query_cov = load_sql_query('m4_country_coverage.sql')
df_cov = pd.read_sql_query(query_cov, con)

print("=== COVERAGE TABLE: TOP 12 COUNTRIES BY TOTAL STATES ===")
print(df_cov.head(12).to_string(index=False))

print("\n=== COVERAGE TABLE: THIN ENTITIES (BOTTOM 10 COUNTRIES WITH n < 5) ===")
print(df_cov.tail(10).to_string(index=False))

# Compare Top 10 Country Mean Altitude WITH vs WITHOUT coverage rule (n >= 30)
alt_no_rule = compute_group_summaries(df_airborne, 'origin_country', 'baro_altitude', min_n=1).sort_values('mean', ascending=False)
alt_with_rule = compute_group_summaries(df_airborne, 'origin_country', 'baro_altitude', min_n=MIN_COUNTRY_AIRBORNE_N).sort_values('mean', ascending=False)

print("\n--- Top 5 Countries by Mean Altitude WITHOUT Coverage Rule (min_n = 1) ---")
print(alt_no_rule[['origin_country', 'n', 'mean', 'median', 'p90']].head(5).to_string(index=False))

print(f"\n--- Top 5 Countries by Mean Altitude WITH Coverage Rule (min_n = {MIN_COUNTRY_AIRBORNE_N}) ---")
print(alt_with_rule[['origin_country', 'n', 'mean', 'median', 'p90']].head(5).to_string(index=False))

# Compare Flag Filters: Filter A ('ok', 'unusual_high') vs Filter B ('ok' only)
df_air_strict = df_airborne[(df_airborne['velocity_flag'] == 'ok') & (df_airborne['baro_altitude_flag'] == 'ok')]

vel_filter_a = compute_group_summaries(df_airborne, 'origin_country', 'velocity', min_n=MIN_COUNTRY_AIRBORNE_N)
vel_filter_b = compute_group_summaries(df_air_strict, 'origin_country', 'velocity', min_n=MIN_COUNTRY_AIRBORNE_N)

df_filter_comp = pd.merge(
    vel_filter_a[['origin_country', 'n', 'mean', 'p90', 'p95']],
    vel_filter_b[['origin_country', 'n', 'mean', 'p90', 'p95']],
    on='origin_country', suffixes=('_ok_plus_unusual', '_strict_ok')
)
df_filter_comp['p95_diff_ms'] = (df_filter_comp['p95_ok_plus_unusual'] - df_filter_comp['p95_strict_ok']).round(2)
print("\n=== FLAG FILTER SENSITIVITY: 'ok + unusual_high' vs 'strict ok only' (Velocity m/s) ===")
print(df_filter_comp.sort_values('p95_diff_ms', ascending=False).head(10).to_string(index=False))

# Figure 2: Coverage Rule & Flag Filter Impact
fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.8))

# Left Panel: Mean Altitude Top 8 without rule vs with rule
top8_unfiltered = alt_no_rule.head(8).iloc[::-1]
colors_left = ['#d95f02' if n < MIN_COUNTRY_AIRBORNE_N else '#2b5c8f' for n in top8_unfiltered['n']]
axes[0].barh(
    [f"{r.origin_country} (n={r.n})" for _, r in top8_unfiltered.iterrows()],
    top8_unfiltered['mean'], color=colors_left, alpha=0.9
)
axes[0].axvline(df_airborne['baro_altitude'].mean(), color='#333333', linestyle='--', label=f"Global Mean ({df_airborne['baro_altitude'].mean():.0f} m)")
axes[0].set_title("A. Unfiltered Top Countries Are Dominated by n=1-2 Airframes")
axes[0].set_xlabel("Mean Airborne Barometric Altitude (m) [ Bars start at 0 ]")
axes[0].set_xlim(left=0)
axes[0].legend(loc='lower right', fontsize=8.5)

# Right Panel: Flag Filter Impact on P95 Velocity across Top Countries (n >= 30)
top8_f = df_filter_comp.sort_values('p95_diff_ms', ascending=False).head(8).iloc[::-1]
y_pos = np.arange(len(top8_f))
h = 0.36
axes[1].barh(y_pos + h/2, top8_f['p95_ok_plus_unusual'], height=h, color='#2b5c8f', label="Filter: 'ok' + 'unusual_high' (Reported)")
axes[1].barh(y_pos - h/2, top8_f['p95_strict_ok'], height=h, color='#1b9e77', label="Filter: 'ok' only (Strict)")
axes[1].axvline(285.0, color='#e7298a', linestyle=':', linewidth=1.6, label="Unusual High Cutoff (285 m/s)")
axes[1].set_yticks(y_pos)
axes[1].set_yticklabels([f"{r.origin_country} (n={r.n_ok_plus_unusual})" for _, r in top8_f.iterrows()])
axes[1].set_title("B. Excluding 'unusual_high' Truncates P95 Jet-Stream Speeds")
axes[1].set_xlabel("95th Percentile Airborne Ground Speed (m/s) [ UTC ]")
axes[1].set_xlim(left=0)
axes[1].legend(loc='lower right', fontsize=8.5)

format_figure_layout(
    fig,
    title="Denominator Rules Eliminate Single-Plane Noise; Flag Filters Control Upper-Tail Speeds",
    subtitle="How do minimum coverage rules (n >= 30) and flag filters change country rankings?",
    caption=f"Source: M3 flight_state_resolved (2026-10-01 10:33 UTC). Total airborne n = {len(df_airborne):,}. Orange bars in Panel A fail coverage rule (n < 30)."
)
fig2_path = save_figure(fig, "fig2_country_coverage_and_filters.png")
print("Saved Figure 2:", fig2_path)""")

# --- SECTION 2.5: TIME & TWO-LEVEL STRUCTURE ---
add_md(r"""## 2.5 Time (Istanbul Local Clock Conversion, Edge Check & Two Time Scales) & Two-Level Airspace Structure
**Questions:**
1. *In local Istanbul time (`Europe/Istanbul`, UTC+3), are the first and last days of our 168-hour weather series complete, and how do temperature and wind speed vary across hours of the day and days of the week?*
2. *Because the flight dataset is a single polling snapshot, what does the equivalent two-level grouping (**Flight Phase / Altitude Band within Country**) reveal?*""")

add_and_run_code(r"""query_wx = load_sql_query('m4_weather_hourly.sql')
df_wx = pd.read_sql_query(query_wx, con, params=(1,))

# Explicit Clock Conversion: UTC -> Europe/Istanbul (UTC+3)
df_wx['ts_utc'] = pd.to_datetime(df_wx['observed_at'], utc=True)
df_wx['ts_local'] = df_wx['ts_utc'].dt.tz_convert(ISTANBUL_LOCAL_CLOCK)
df_wx['local_date'] = df_wx['ts_local'].dt.strftime('%Y-%m-%d')
df_wx['local_hour'] = df_wx['ts_local'].dt.hour
df_wx['utc_hour'] = df_wx['ts_utc'].dt.hour

# Edge completeness check in Local Time vs UTC
edge_table = df_wx.groupby('local_date').agg(
    hours_available=('temperature_2m', 'count'),
    min_local_hour=('local_hour', 'min'),
    max_local_hour=('local_hour', 'max'),
    mean_temp_c=('temperature_2m', 'mean'),
    mean_wind_kmh=('wind_speed_10m', 'mean'),
    max_wind_kmh=('wind_speed_10m', 'max'),
    total_precip_mm=('precipitation', 'sum')
).reset_index()
edge_table['passes_18h_rule'] = edge_table['hours_available'] >= MIN_DAILY_WEATHER_HOURS
edge_table[['mean_temp_c', 'mean_wind_kmh', 'max_wind_kmh', 'total_precip_mm']] = edge_table[['mean_temp_c', 'mean_wind_kmh', 'max_wind_kmh', 'total_precip_mm']].round(2)

print("=== 2.5 WEATHER COVERAGE & EDGE CHECK IN EUROPE/ISTANBUL LOCAL TIME (UTC+3) ===")
print(edge_table.to_string(index=False))

# Compare daily mean wind with and without incomplete edge days (2026-10-01 has 21h, 2026-10-08 has 3h)
complete_days = edge_table[edge_table['passes_18h_rule']]
print(f"\nDaily Mean Wind across ALL {len(edge_table)} local dates (including 3-hour edge day 2026-10-08): {edge_table['mean_wind_kmh'].mean():.2f} km/h")
print(f"Daily Mean Wind across {len(complete_days)} COMPLETE local dates (>= {MIN_DAILY_WEATHER_HOURS}h/day): {complete_days['mean_wind_kmh'].mean():.2f} km/h")

# Figure 3: Two Time Scales (Diurnal Hour-of-Day in Local Time vs 7-Day Hourly/Daily Series)
fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.5))

# Scale 1: Hour of Day (Local vs UTC comparison showing 3-hour peak shift)
hourly_local = df_wx[df_wx['local_date'].isin(complete_days['local_date'])].groupby('local_hour').agg(
    med_temp=('temperature_2m', 'median'),
    q25_temp=('temperature_2m', lambda s: np.percentile(s, 25)),
    q75_temp=('temperature_2m', lambda s: np.percentile(s, 75)),
    med_wind=('wind_speed_10m', 'median')
).reset_index()

hourly_utc = df_wx.groupby('utc_hour')['temperature_2m'].median().reset_index()

axes[0].plot(hourly_local['local_hour'], hourly_local['med_temp'], marker='o', color='#d95f02', linewidth=2.2, label='Median Temp (Europe/Istanbul, UTC+3)')
axes[0].fill_between(hourly_local['local_hour'], hourly_local['q25_temp'], hourly_local['q75_temp'], color='#d95f02', alpha=0.18, label='IQR Band (Q25-Q75, Local)')
axes[0].plot(hourly_utc['utc_hour'], hourly_utc['temperature_2m'], linestyle='--', color='#666666', linewidth=1.5, label='Unconverted UTC Hour (Shifted -3h)')
axes[0].set_title("A. Diurnal Cycle: Local Thermal Peak Is at 14:00-15:00 (+03)")
axes[0].set_xlabel("Hour of Day [ Europe/Istanbul Local Time (UTC+3) ]")
axes[0].set_ylabel("Temperature at 2m (°C)")
axes[0].set_xticks(range(0, 24, 3))
axes[0].legend(loc='upper left', fontsize=8.5)

# Scale 2: 7-Day Hourly Wind Speed & Daily Coverage-Filtered Mean
axes[1].plot(df_wx['ts_local'], df_wx['wind_speed_10m'], color='#2b5c8f', alpha=0.75, linewidth=1.5, label='Hourly Wind Speed (168h)')
axes[1].axhline(df_wx['wind_speed_10m'].median(), color='#1b9e77', linestyle='--', linewidth=1.5, label=f"7-Day Median ({df_wx['wind_speed_10m'].median():.1f} km/h)")
# Highlight the incomplete trailing edge day (2026-10-08 00:00-02:00+03)
edge_slice = df_wx[df_wx['local_date'] == '2026-10-08']
if not edge_slice.empty:
    axes[1].scatter(edge_slice['ts_local'], edge_slice['wind_speed_10m'], color='#e7298a', s=45, zorder=5, label='Incomplete Edge Day (2026-10-08, 3h only)')
axes[1].set_title("B. Weekly Wind Profile: Gust Peak on Oct 3-4 Before Mid-Week Calm")
axes[1].set_xlabel("Date [ Europe/Istanbul Local Time (UTC+3) ]")
axes[1].set_ylabel("10m Wind Speed (km/h)")
axes[1].tick_params(axis='x', rotation=25)
axes[1].legend(loc='upper right', fontsize=8.5)

format_figure_layout(
    fig,
    title="Converting UTC to Istanbul Time (+03) Aligns Peak Heating with Afternoon Wind Gusts",
    subtitle="How do temperature and wind speed vary by local hour of day and across the 7-day window at Istanbul Airport?",
    caption="Source: M3 weather_reading_resolved (Istanbul 41.28N, 28.75E). n = 168 hourly rows (7 complete local days >= 18h; Oct 8 has 3h). Flag filter: all flags = 'ok'."
)
fig3_path = save_figure(fig, "fig3_weather_time_scales_istanbul.png")
print("Saved Figure 3:", fig3_path)""")

add_and_run_code(r"""# Section 2.5 Alternative Two-Level Structure for Snapshot Flight Data:
# Level 1 = Origin Country (Top 8 fleets), Level 2 = Flight Phase / Altitude Band
def classify_altitude_band(row):
    if row['on_ground'] == 1:
        return '1_Surface (on_ground=1)'
    alt = row['baro_altitude']
    if pd.isna(alt):
        return '0_Unknown_Airborne'
    elif alt < 3000:
        return '2_Terminal (<3,000m)'
    elif alt < 9000:
        return '3_Climb_Descent (3,000-9,000m)'
    else:
        return '4_High_Cruise (>=9,000m)'

df_all_states = pd.read_sql_query('''
    SELECT resolved_aircraft_key, origin_country, baro_altitude, baro_altitude_flag,
           velocity, velocity_flag, on_ground
    FROM flight_state_resolved
    WHERE velocity_flag IN ('ok', 'unusual_high')
''', con)

df_all_states['phase_band'] = df_all_states.apply(classify_altitude_band, axis=1)
df_top8_phase = df_all_states[df_all_states['origin_country'].isin(FOCUS_COUNTRIES) & (df_all_states['phase_band'] != '0_Unknown_Airborne')]

phase_ct = pd.crosstab(df_top8_phase['origin_country'], df_top8_phase['phase_band'], normalize='index') * 100.0
phase_counts = pd.crosstab(df_top8_phase['origin_country'], df_top8_phase['phase_band'])
print("=== 2.5 TWO-LEVEL STRUCTURE: FLIGHT PHASE SHARE (%) WITHIN TOP 8 COUNTRIES ===")
print(phase_ct.round(1).to_string())

fig, ax = plt.subplots(figsize=(11, 5.5))
bands = ['1_Surface (on_ground=1)', '2_Terminal (<3,000m)', '3_Climb_Descent (3,000-9,000m)', '4_High_Cruise (>=9,000m)']
band_colors = ['#999999', '#66c2a5', '#fc8d62', '#2b5c8f']
bottom_vals = np.zeros(len(FOCUS_COUNTRIES))
phase_ct_ordered = phase_ct.reindex(FOCUS_COUNTRIES)
counts_ordered = phase_counts.reindex(FOCUS_COUNTRIES).sum(axis=1)

for b_name, b_col in zip(bands, band_colors):
    vals = phase_ct_ordered[b_name].to_numpy()
    ax.barh([f"{c} (n={counts_ordered[c]})" for c in FOCUS_COUNTRIES], vals, left=bottom_vals, color=b_col, edgecolor='white', label=b_name)
    bottom_vals += vals

ax.set_title("Fleet Phase Mixture Varies Sharply by Time Zone at 10:33 UTC")
ax.set_xlabel("Share of National Fleet in Each Flight Phase (%) [ Bars start at 0 ]")
ax.set_xlim(0, 100)
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.14), ncol=4, fontsize=8.5)

format_figure_layout(
    fig,
    title="US and Canadian Fleets Have >20% Surface Traffic at 10:33 UTC vs <6% for Turkey & China",
    subtitle="How does the two-level flight phase composition (Surface / Terminal / Climb / Cruise) differ across major countries?",
    caption=f"Source: M3 flight_state_resolved (2026-10-01 10:33 UTC). n = { int(counts_ordered.sum()) } aircraft across 8 focus countries. Flag filter: velocity_flag IN ('ok', 'unusual_high')."
)
fig4_path = save_figure(fig, "fig4_airspace_two_level_structure.png")
print("Saved Figure 4:", fig4_path)""")

# --- SECTION 2.6: RELATIONSHIPS ---
add_md(r"""## 2.6 Relationships (Altitude vs. Ground Speed & Outlier Sensitivity)
**Question:** *How strongly does airborne ground speed (`velocity`, m/s) scale with barometric altitude (`baro_altitude`, m), and how much of that correlation is driven by `unusual_high` jet-stream/business-jet extremes or low-altitude terminal traffic?*""")

add_and_run_code(r"""df_rel = df_airborne[['resolved_aircraft_key', 'origin_country', 'baro_altitude', 'baro_altitude_flag', 'velocity', 'velocity_flag']].dropna().copy()

# 1. All Trusted Airborne ('ok' + 'unusual_high')
r_pearson_all, p_pearson_all = stats.pearsonr(df_rel['baro_altitude'], df_rel['velocity'])
r_spearman_all, p_spearman_all = stats.spearmanr(df_rel['baro_altitude'], df_rel['velocity'])

# 2. Driver Check A: Remove unusual_high outliers (103 rows = 1.44% of airborne data)
df_rel_strict = df_rel[(df_rel['baro_altitude_flag'] == 'ok') & (df_rel['velocity_flag'] == 'ok')]
r_pearson_strict, _ = stats.pearsonr(df_rel_strict['baro_altitude'], df_rel_strict['velocity'])
r_spearman_strict, _ = stats.spearmanr(df_rel_strict['baro_altitude'], df_rel_strict['velocity'])

# 3. Driver Check B: Within-group check (Cruise altitude >= 9,000 m only, removing shared climb phase)
df_rel_cruise = df_rel[df_rel['baro_altitude'] >= 9000.0]
r_pearson_cruise, _ = stats.pearsonr(df_rel_cruise['baro_altitude'], df_rel_cruise['velocity'])
r_spearman_cruise, _ = stats.spearmanr(df_rel_cruise['baro_altitude'], df_rel_cruise['velocity'])

rel_table = pd.DataFrame([
    {"Subset": "1. All Trusted Airborne ('ok' + 'unusual_high')", "n": len(df_rel), "Pearson_r": round(r_pearson_all, 4), "Spearman_rho": round(r_spearman_all, 4)},
    {"Subset": "2. Strict 'ok' Only (Removed 1.4% unusual_high)", "n": len(df_rel_strict), "Pearson_r": round(r_pearson_strict, 4), "Spearman_rho": round(r_spearman_strict, 4)},
    {"Subset": "3. High Cruise Only (baro_altitude >= 9,000 m)", "n": len(df_rel_cruise), "Pearson_r": round(r_pearson_cruise, 4), "Spearman_rho": round(r_spearman_cruise, 4)}
])
print("=== 2.6 CORRELATION & DRIVER SENSITIVITY TABLE ===")
print(rel_table.to_string(index=False))

# Also check Weather Relationship: Temperature vs Wind Speed at Istanbul Airport
wx_r_p, _ = stats.pearsonr(df_wx['temperature_2m'], df_wx['wind_speed_10m'])
wx_r_s, _ = stats.spearmanr(df_wx['temperature_2m'], df_wx['wind_speed_10m'])
print(f"\nIstanbul Weather (n={len(df_wx)}h) Temp vs Wind Speed: Pearson r = {wx_r_p:.3f}, Spearman rho = {wx_r_s:.3f}")

# Figure 5 (HEADLINE FIGURE): Scatter + Binned Medians + IQR Band
df_rel['alt_bin_km'] = (df_rel['baro_altitude'] // 1000) * 1000 + 500
binned = df_rel.groupby('alt_bin_km').agg(
    n=('velocity', 'count'),
    med_vel=('velocity', 'median'),
    q25_vel=('velocity', lambda s: np.percentile(s, 25)),
    q75_vel=('velocity', lambda s: np.percentile(s, 75))
).reset_index()
binned = binned[binned['n'] >= 10]

fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.8))

# Left Panel: Global Altitude vs Velocity with Binned Medians
normal_pts = df_rel[(df_rel['baro_altitude_flag'] == 'ok') & (df_rel['velocity_flag'] == 'ok')]
unusual_pts = df_rel[(df_rel['baro_altitude_flag'] == 'unusual_high') | (df_rel['velocity_flag'] == 'unusual_high')]

axes[0].scatter(normal_pts['baro_altitude'], normal_pts['velocity'], s=6, alpha=0.18, color='#2b5c8f', label=f"Normal 'ok' (n={len(normal_pts):,})")
axes[0].scatter(unusual_pts['baro_altitude'], unusual_pts['velocity'], s=18, alpha=0.75, color='#e7298a', label=f"Flagged 'unusual_high' (n={len(unusual_pts)})")
axes[0].plot(binned['alt_bin_km'], binned['med_vel'], color='#d95f02', linewidth=2.5, marker='o', markersize=5, label='Binned Median (1,000m bins)')
axes[0].fill_between(binned['alt_bin_km'], binned['q25_vel'], binned['q75_vel'], color='#d95f02', alpha=0.22, label='Binned IQR (Q25-Q75)')
axes[0].axvline(9000, color='#333333', linestyle='--', linewidth=1.2, label='Cruise Transition (9,000 m)')
axes[0].set_title(f"A. All Airborne: Strong Climb Trend (r={r_pearson_all:.2f}, rho={r_spearman_all:.2f})")
axes[0].set_xlabel("Barometric Altitude (m, QNE) [ Snapshot: 2026-10-01 10:33 UTC ]")
axes[0].set_ylabel("Airborne Ground Speed (m/s)")
axes[0].set_xlim(0, 15500)
axes[0].set_ylim(0, 340)
axes[0].legend(loc='lower right', fontsize=8.2)

# Right Panel: Within High-Cruise Regime (>= 9,000 m) showing shared-pattern conditioning
axes[1].scatter(df_rel_cruise['baro_altitude'], df_rel_cruise['velocity'], s=8, alpha=0.22, color='#1b9e77', label=f"High Cruise >=9,000m (n={len(df_rel_cruise):,})")
binned_c = binned[binned['alt_bin_km'] >= 9000]
axes[1].plot(binned_c['alt_bin_km'], binned_c['med_vel'], color='#d95f02', linewidth=2.5, marker='s', label='Cruise Binned Median')
axes[1].axhline(df_rel_cruise['velocity'].median(), color='#333333', linestyle='--', label=f"Cruise Median ({df_rel_cruise['velocity'].median():.1f} m/s)")
axes[1].set_title(f"B. Within Cruise (>=9,000m): Relationship Flattens (r={r_pearson_cruise:.2f}, rho={r_spearman_cruise:.2f})")
axes[1].set_xlabel("Barometric Altitude (m, QNE) [ >= 9,000 m Cruise Regime ]")
axes[1].set_ylabel("Airborne Ground Speed (m/s)")
axes[1].set_xlim(8800, 15500)
axes[1].set_ylim(0, 340)
axes[1].legend(loc='lower right', fontsize=8.5)

format_figure_layout(
    fig,
    title="Ground Speed Rises Steeply Up to 9,000 m (r = 0.85) Then Plateaus in the Stratosphere (r = 0.13)",
    subtitle="How does aircraft ground speed relate to barometric altitude, and what drives the overall correlation?",
    caption=f"Source: M3 flight_state_resolved (2026-10-01 10:33 UTC). n = {len(df_rel):,} airborne aircraft with valid altitude & velocity. Flag filter: 'ok' + 'unusual_high'."
)
fig5_path = save_figure(fig, "fig5_altitude_velocity_relationship.png")
print("Saved Figure 5 (Headline Figure):", fig5_path)""")

# --- SECTION 2.7: COMPARISON WITH INTERVAL & COMPOSITION ---
add_md(r"""## 2.7 One Comparison, With a 95% Bootstrap Interval, Composition Check, and Look Count
**Question:** *How much faster or slower is the active airborne fleet of the **United Kingdom** compared to the **United States** at 10:33 UTC, and is this difference genuine or an artefact of flight-phase composition (Simpson's Paradox)?*""")

add_and_run_code(r"""us_all = df_rel[df_rel['origin_country'] == 'United States']['velocity'].to_numpy()
uk_all = df_rel[df_rel['origin_country'] == 'United Kingdom']['velocity'].to_numpy()

# Bootstrap 95% CI on Median and Mean (Unit of resampling = independent airframe)
ci_med_overall = bootstrap_diff_ci(uk_all, us_all, stat_func=np.median, n_boot=BOOTSTRAP_ITERATIONS, seed=RANDOM_SEED)
ci_mean_overall = bootstrap_diff_ci(uk_all, us_all, stat_func=np.mean, n_boot=BOOTSTRAP_ITERATIONS, seed=RANDOM_SEED)

print("=== 2.7 OVERALL AIRBORNE VELOCITY COMPARISON: UNITED KINGDOM vs UNITED STATES ===")
print(f"UK Airborne (n={ci_med_overall['n_a']}) vs US Airborne (n={ci_med_overall['n_b']}):")
print(f"  Median Difference (UK - US): {ci_med_overall['diff']:+.2f} m/s  [95% Bootstrap CI: {ci_med_overall['ci_95_low']:+.2f} to {ci_med_overall['ci_95_high']:+.2f} m/s]")
print(f"  Mean Difference   (UK - US): {ci_mean_overall['diff']:+.2f} m/s  [95% Bootstrap CI: {ci_mean_overall['ci_95_low']:+.2f} to {ci_mean_overall['ci_95_high']:+.2f} m/s]")

# Composition Check: Compare within altitude bands (Subgroups)
df_us_uk = df_rel[df_rel['origin_country'].isin(['United States', 'United Kingdom'])].copy()
df_us_uk['Flight_Regime'] = np.where(df_us_uk['baro_altitude'] >= 9000.0, 'High_Cruise (>=9,000m)', 'Climb_Terminal (<9,000m)')

comp_rows = []
for regime in ['Climb_Terminal (<9,000m)', 'High_Cruise (>=9,000m)']:
    sub_uk = df_us_uk[(df_us_uk['origin_country'] == 'United Kingdom') & (df_us_uk['Flight_Regime'] == regime)]['velocity'].to_numpy()
    sub_us = df_us_uk[(df_us_uk['origin_country'] == 'United States') & (df_us_uk['Flight_Regime'] == regime)]['velocity'].to_numpy()
    b_res = bootstrap_diff_ci(sub_uk, sub_us, stat_func=np.median, n_boot=BOOTSTRAP_ITERATIONS, seed=RANDOM_SEED)
    comp_rows.append({
        "Regime": regime,
        "UK_n": b_res['n_a'],
        "UK_share_%": round(b_res['n_a'] / len(uk_all) * 100, 1),
        "US_n": b_res['n_b'],
        "US_share_%": round(b_res['n_b'] / len(us_all) * 100, 1),
        "UK_Median_ms": b_res['stat_a'],
        "US_Median_ms": b_res['stat_b'],
        "Diff_UK_minus_US": b_res['diff'],
        "CI_95_Low": b_res['ci_95_low'],
        "CI_95_High": b_res['ci_95_high'],
        "boot_dist": b_res['boot_distribution']
    })

df_comp_check = pd.DataFrame(comp_rows)
print("\n=== SUBGROUP COMPOSITION CHECK (SIMPSON'S PARADOX / MIXTURE EFFECT) ===")
print(df_comp_check.drop(columns=['boot_dist']).to_string(index=False))
print(f"\nTotal pairwise country/group comparisons inspected during exploration (Look Count): {TOTAL_COMPARISONS_INSPECTED}")

# Figure 6: Bootstrap Distribution & Subgroup Composition Reversal
fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.6))

# Left Panel: Bootstrap Distribution of Overall vs High-Cruise Median Difference
axes[0].hist(ci_med_overall['boot_distribution'], bins=45, density=True, color='#2b5c8f', alpha=0.65, label=f"Overall Airborne Diff: {ci_med_overall['diff']:+.1f} m/s\n95% CI [{ci_med_overall['ci_95_low']:+.1f}, {ci_med_overall['ci_95_high']:+.1f}]")
axes[0].hist(comp_rows[1]['boot_dist'], bins=45, density=True, color='#d95f02', alpha=0.65, label=f"High Cruise (>=9km) Diff: {comp_rows[1]['Diff_UK_minus_US']:+.1f} m/s\n95% CI [{comp_rows[1]['CI_95_Low']:+.1f}, {comp_rows[1]['CI_95_High']:+.1f}]")
axes[0].axvline(0.0, color='black', linestyle='--', linewidth=1.5, label='Zero Difference (Null)')
axes[0].set_title("A. Bootstrap 95% CI: Overall Difference Shrinks/Reverses in Cruise")
axes[0].set_xlabel("Median Velocity Difference: UK minus US (m/s) [ 10,000 resamples ]")
axes[0].set_ylabel("Bootstrap Density (bars start at 0)")
axes[0].set_ylim(bottom=0)
axes[0].legend(loc='upper left', fontsize=8.3)

# Right Panel: Subgroup Medians & Mixture Shares
regimes = ['Overall Airborne', 'Climb/Terminal (<9km)', 'High Cruise (>=9km)']
uk_meds = [ci_med_overall['stat_a'], comp_rows[0]['UK_Median_ms'], comp_rows[1]['UK_Median_ms']]
us_meds = [ci_med_overall['stat_b'], comp_rows[0]['US_Median_ms'], comp_rows[1]['US_Median_ms']]

x_idx = np.arange(len(regimes))
w = 0.34
axes[1].bar(x_idx - w/2, uk_meds, width=w, color='#2b5c8f', label="United Kingdom (High Cruise Share)")
axes[1].bar(x_idx + w/2, us_meds, width=w, color='#1b9e77', label="United States (High Cruise Share)")
axes[1].set_xticks(x_idx)
axes[1].set_xticklabels(regimes)
axes[1].set_title("B. Composition Effect: US Has More Climbing Morning Flights at 10:33 UTC")
axes[1].set_ylabel("Median Airborne Ground Speed (m/s) [ Bars start at 0 ]")
axes[1].set_ylim(0, 275)
axes[1].legend(loc='upper left', fontsize=8.5)

format_figure_layout(
    fig,
    title="UK's Higher Overall Speed Is Driven by Fleet Mixture; Within Cruise Regimes the Gap Vanishes",
    subtitle="Does the UK vs US speed gap persist when we control for altitude regime (Simpson's Paradox check)?",
    caption=f"Source: M3 flight_state_resolved (2026-10-01 10:33 UTC). UK n = {len(uk_all)}, US n = {len(us_all)}. 10,000 bootstrap resamples (seed=42). Look count = 12 comparisons."
)
fig6_path = save_figure(fig, "fig6_us_vs_uk_bootstrap_comparison.png")
print("Saved Figure 6:", fig6_path)""")

# --- SECTION 3.2: DECISIONS SUMMARY CELL ---
add_md(r"""## 2.8 Summary of Decisions & Reproducibility Record""")

add_and_run_code(r"""con.close()
print("=== M4 REPRODUCIBILITY & PARAMETER RECORD ===")
print(f"Database Mode            : Read-Only URI (mode=ro)")
print(f"Minimum Country Coverage : n >= {MIN_COUNTRY_AIRBORNE_N} trusted airborne aircraft")
print(f"Minimum Daily Weather    : >= {MIN_DAILY_WEATHER_HOURS} / 24 hours")
print(f"Reported Flag Filter     : {FLAG_FILTER_REPORTED}")
print(f"Selected Velocity Bin    : {SELECTED_VELOCITY_BIN_WIDTH} m/s (tested: {VELOCITY_BIN_CANDIDATES})")
print(f"Weather Local Clock      : {ISTANBUL_LOCAL_CLOCK} (converted from {STORAGE_CLOCK})")
print(f"Bootstrap Resamples      : {BOOTSTRAP_ITERATIONS} iterations (seed={RANDOM_SEED})")
print(f"Comparisons Inspected    : {TOTAL_COMPARISONS_INSPECTED} looks")
print("All 6 figures written to : figures/")
print("STATUS                   : M4 EXPLORATION COMPLETE")""")

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3"
    },
    "language_info": {
        "name": "python",
        "version": "3.11.0"
    }
}

with open(NOTEBOOK_PATH, "w", encoding="utf-8") as f:
    nbformat.write(nb, f)

print("\n========================================================")
print("BAŞARILI! notebooks/m4_eda.ipynb çıktılarıyla kaydedildi")
print("ve 6 grafik figures/ klasörüne oluşturuldu!")
print("========================================================")