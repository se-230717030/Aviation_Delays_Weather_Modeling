# Exploratory Analysis — Aviation Delays & Weather Modeling

## 1. Summary

This exploratory data analysis investigates how typical and extreme airborne ground speeds and cruising altitudes compare across major national air fleets, and how local meteorological conditions behave across a seven-day window. Our exploration reveals that airborne velocity and altitude are highly left-skewed, necessitating robust median metrics, and that while altitude strongly dictates ground speed during climb phases (Pearson $r = 0.90$), this relationship completely flattens and vanishes in the high-cruise stratosphere ($r = -0.01$). We are highly confident in these physical flight regime shifts and weather diurnal cycles; however, our confidence in direct country-to-country speed comparisons remains strictly exploratory, as the single global polling snapshot heavily distorts national averages through time-zone-driven flight phase mixtures. These findings would change significantly if we acquired longitudinal, multi-day trajectory data to normalize time-of-day departure banks across different time zones. Additionally, during this exploration, we identified outdated UN country names (Libya, Moldova, Vietnam) failing to aggregate in our M3 layer, which we successfully corrected via `data/mappings/country_mapping.csv` as a documented Back-to-Cleaning fix.

## 2. Raw versus cleaned

To understand the impact of our M3 data quality layer on our core continuous variables, we computed the global airborne mean velocity and altitude on both the raw M2 `flight_state` table and the resolved M3 `flight_state_resolved` view.

**Query 1: Raw M2 Data**

```
SELECT 
    COUNT(*) AS n_rows,
    ROUND(AVG(velocity), 2) AS mean_velocity_ms,
    ROUND(AVG(baro_altitude), 2) AS mean_altitude_m,
    ROUND(MAX(velocity), 2) AS max_velocity_ms,
    ROUND(MIN(baro_altitude), 2) AS min_altitude_m
FROM flight_state 
WHERE on_ground = 0;

```

*Result 1 (Raw M2):* $n = 7,177$ rows. Mean Velocity = 186.92 m/s, Max Velocity = 2,160.22 m/s. Mean Altitude = 7,329.85 m, Min Altitude = -152.40 m.

**Query 2: Cleaned M3 Data (Filtered for `ok` and `unusual_high`)**

```
SELECT 
    COUNT(*) AS n_rows,
    ROUND(AVG(velocity), 2) AS mean_velocity_ms,
    ROUND(AVG(baro_altitude), 2) AS mean_altitude_m,
    ROUND(MAX(velocity), 2) AS max_velocity_ms,
    ROUND(MIN(baro_altitude), 2) AS min_altitude_m
FROM flight_state_resolved 
WHERE on_ground = 0 
  AND velocity_flag IN ('ok', 'unusual_high') 
  AND baro_altitude_flag IN ('ok', 'unusual_high');

```

*Result 2 (Cleaned M3):* $n = 7,120$ rows. Mean Velocity = 187.45 m/s, Max Velocity = 348.37 m/s. Mean Altitude = 7,365.59 m, Min Altitude = 0.00 m.

**Explanation:**
The raw M2 dataset contained physically impossible supersonic telemetry glitches (up to 2,160.22 m/s, or $\approx$ Mach 6.3) and uncalibrated severe negative QNE altitudes (-152.40 m) which artificially distorted the bounds and standard deviations of the fleet. By querying our M3 cleaned layer and explicitly filtering for `ok` and `unusual_high` flags, we successfully removed these impossible artifacts—capping the maximum valid speed at 348.37 m/s (just below the speed of sound) and the minimum airborne altitude at 0.0 m—while fully preserving genuine, physical jet-stream extremes.

## 3. The question and its summary

**The Question:** Across major national air fleets (defined as having $n \ge 30$ airborne aircraft), how do typical and extreme airborne ground speeds and cruising altitudes compare, and how does local wind speed at Istanbul Airport behave across the week?

**The Chosen Summary:** To answer this question, we selected the **Median** (supported by the Interquartile Range, IQR) as our primary summary statistic. Because global air traffic consists of a dense cluster of aircraft in the high-altitude cruise phase mixed with a long, trailing left tail of slow, low-altitude aircraft climbing and descending in terminal areas, parametric summaries like the mean are heavily downward-biased. The median accurately captures the true "typical" cruising behavior of a country's fleet without being disproportionately dragged down by the volume of local airport traffic at the time of the snapshot.

**Alternative Summary Comparison:** We compared our primary median against the **Mean** and the **90th Percentile (P90)** to test ranking stability. We found that the choice of summary drastically alters national rankings. For example, Qatar ranks 1st globally in median airborne altitude (10,972.8 m) but falls to 3rd by mean and 2nd by P90. Similarly, the Netherlands ranks 8th in median velocity (224.54 m/s) but drops to 15th by mean (192.59 m/s) and jumps to 5th by P90 (259.35 m/s). This discrepancy is a direct finding: while the median answers *"How fast does a typical aircraft from this country fly?"*, the P90 answers *"How fast do the most extreme, jet-stream-assisted long-haul flights from this country travel?"*, proving that the summary choice fundamentally changes the insight.

## 4. What the data is made of

Before establishing comparisons, we must understand the underlying distributional shape, the required coverage thresholds (denominators), and the specific data quality flags defining our analytical universe.

**Shape:** As demonstrated in Figure 1 above, airborne ground speed is not normally distributed. It exhibits strong left-skewness and a highly concentrated bimodal cruise plateau between 215 m/s and 245 m/s. To visualize this without hiding structural features (a weakness of standard box plots), we utilized a histogram. We tested bin widths of 5.0 m/s (which introduced excessive noise), 40.0 m/s (which oversmoothed the cruise mode), and settled on **15.0 m/s** as our natural unit, providing a balanced, high-fidelity view of the traffic distribution. We paired this with an Empirical Cumulative Distribution Function (ECDF) plot, which elegantly reveals that the 90th percentile (P90) separates standard commercial traffic from high-speed outliers across all major countries.

**Coverage and Denominators:** A count of our data reveals extreme entity imbalance: while the United States ($n = 1,483$ airborne) and the United Kingdom ($n = 582$ airborne) dominate the snapshot, many nations like Libya, Bolivia, and Tanzania report exactly $n = 1$ active aircraft.

* **Coverage Rule:** We established a strict minimum denominator rule of $n \ge 30$ **trusted airborne aircraft** for any country-level aggregate, because smaller samples represent individual flight routes rather than national fleet behaviors.

* **Rule Impact:** Without this rule ($n \ge 1$), Bolivia and Trinidad and Tobago falsely lead the global altitude rankings with an average of 12,192.00 m (derived from a single aircraft each). When the $n \ge 30$ coverage rule is applied, the true operational leaders emerge, with Saudi Arabia (10,540.50 m, $n=41$) and Egypt (10,429.36 m, $n=44$) taking the top positions.

* **Weather Coverage Rule:** For daily weather aggregates, we established a rule requiring $\ge 18$ **valid hours per 24-hour day** to prevent partial-day bias.

**The Flag Filter:** Throughout this exploration, our standard filter requires `on_ground = 0`, `velocity_flag IN ('ok', 'unusual_high')`, and `baro_altitude_flag IN ('ok', 'unusual_high')`. We explicitly tested the sensitivity of this choice against a strict filter that only accepts `ok` flags. As seen in Figure 2 (Panel B), utilizing the strict filter quietly deletes genuine physical extremes, artificially truncating South Korea's P95 jet-stream speed by a massive 31.54 m/s and Canada's by 8.31 m/s. We report the inclusive `ok` + `unusual_high` filter because dropping these valid outliers destroys the most meteorologically interesting tail of our dataset.

## 5. Time

Time introduces significant structural variations in both our weather series and our flight phase cross-sections.

**The Clock:** The Open-Meteo and OpenSky APIs provide timestamps in UTC. For our weather aggregations, we explicitly converted `observed_at` from `UTC` to the local time zone `Europe/Istanbul` (UTC+3) using `tz_convert`. Grouping diurnal human and weather behavior by raw UTC is misleading; aligning the clock to local time correctly synchronizes peak solar heating with afternoon wind gusts.

**The Edges:** We inspected the chronological edges of our 7-day weather series. In local Istanbul time, the first day (2026-10-01) contains 21 hours, while the trailing edge day (2026-10-08) contains only 3 hours (midnight to 02:00). Including this incomplete, calm 3-hour nocturnal edge day drags the overall dataset mean wind speed down to 14.69 km/h. By applying our $\ge 18$ hours/day coverage rule, we drop this edge artifact, raising the true daily mean wind speed to 16.07 km/h.

**Two Scales:** Figure 3 visualizes meteorological trends across two distinct time scales. On the diurnal scale (Hour of Day), we observe the daily thermal peak cleanly isolated between 14:00 and 15:00 local time. On the longitudinal scale (7-Day Series), we identify a macro-level gust event peaking across October 3rd and 4th before dropping into a mid-week calm.

**Two-Level Data Structure (Flight Snapshots):** Because our aviation data lacks longitudinal time-series depth (it is a single polling batch at 10:33 UTC), we explored an alternative two-level categorical structure: **Origin Country** subdivided by **Flight Phase / Altitude Band**. Figure 4 exposes massive structural inequalities based on the time of day the snapshot was taken. At 10:33 UTC, it is morning in the Americas, resulting in the US and Canada having $>20\%$ of their fleets on the surface or in terminal climb phases. Conversely, in Asia and Europe, the active fleets are almost entirely in high-cruise configurations ($>65\%$ for Turkey). This temporal mixture heavily dictates global ranking behaviors.

## 6. Relationships

To understand the physics of the airspace, we explored the relationship between barometric altitude and ground speed.

**Visualizing and Measuring the Relationship:** As shown in Figure 5, plotting raw scatter points alongside 1,000 m binned medians reveals a steep, non-linear correlation. Across the entire trusted airborne dataset ($n=7,120$), the overall correlation is overwhelmingly positive (Pearson $r = 0.8963$, Spearman $\rho = 0.7843$), confirming that aircraft fly significantly faster as they ascend into thinner air.

**Searching for Drivers:** We rigorously tested the stability of this relationship by isolating potential drivers.

1. *Outlier Driver Check:* We first removed all 103 `unusual_high` outliers to see if extreme business jets or jet streams were forcing the correlation. The relationship held firm, marginally increasing to $r = 0.9000$, proving the outliers are not the primary driver.

2. *Shared Pattern (Regime) Check:* We then conditioned the data strictly on the High Cruise regime ($\ge 9,000$ m) to remove the shared climb/descent phase. Within this cruise subset ($n=3,641$), the relationship completely vanished, plummeting to Pearson $r = -0.0062$ and Spearman $\rho = -0.0188$.

This is a critical exploratory finding: altitude and velocity are not universally correlated. The $0.90$ correlation is entirely an artifact of the terminal climb phase; once an aircraft reaches the stratosphere, altitude no longer dictates ground speed, and engine thrust capabilities alongside horizontal wind streams take over. (Note: We also checked the weather relationship between local temperature and wind speed at Istanbul, finding only a negligible correlation of $r = 0.149$).

## 7. The comparison

We conducted a high-stakes comparison between the two largest entities in our dataset to determine whether the **United Kingdom** airborne fleet ($n=582$) flies faster or slower than the **United States** airborne fleet ($n=1,483$) at the time of our snapshot.

**The Difference and Interval:** Because individual aircraft states within a single polling second are highly skewed and bounded, we utilized a 10,000-iteration non-parametric bootstrap (resampling independent airframes with replacement, seed=42) to construct a robust 95% confidence interval. The overall median difference (UK minus US) is **-27.25 m/s** \[95% CI: -64.99 to -14.73 m/s\]. Statistically, the UK fleet is significantly slower overall.

**Composition Check (Simpson's Paradox):** Before concluding that UK aircraft are inherently slower, we checked the mixture of the two groups. As seen in Figure 6 (Panel B), while the UK and US have an identical share of climbing/terminal flights ($\approx 56\%$), their speeds within those subgroups differ wildly. In the Climb/Terminal regime ($< 9,000$ m), the UK median is 66.89 m/s while the US median is 161.05 m/s (a massive -94.16 m/s gap). However, in the High Cruise regime ($\ge 9,000$ m), the gap shrinks dramatically, with the UK at 229.88 m/s and the US at 240.63 m/s (a mere -10.75 m/s difference). The overall 27 m/s deficit is heavily driven by the fact that the UK's climbing aircraft are in much lower, slower phases of departure than the US climbing aircraft at 10:33 UTC.

*(Note on Look Count: We transparently report that we inspected 12 different pairwise country and flight-phase comparisons before identifying this specific composition effect to highlight in the report.)*

## 8. Findings

| ID | Finding | Evidence | Label | What would change it | Explored by | 
| ----- | ----- | ----- | ----- | ----- | ----- | 
| **F-01** | Airborne velocity is heavily left-skewed with a dense cruise mode, requiring the median rather than the mean for typical summaries. | Figure 1 (ECDF and Histogram) | Exploratory | A completely different airspace sample consisting purely of high-altitude oceanic traffic. | Muhammet Ali Ozturk | 
| **F-02** | Applying a strict data quality filter (`ok` only) falsely eliminates valid upper-tail jet-stream speeds, reducing P95 aggregates by up to 31 m/s. | Figure 2 & Flag Sensitivity Table | Confirmed (Arithmetic) | If the source API calibrated high-speed data anomalies prior to payload transmission. | Muhammed Osman Kara | 
| **F-03** | The strong overall correlation between altitude and speed ($r = 0.90$) is an artifact of the climb phase and completely flattens ($r = -0.01$) in high cruise. | Figure 5 & Correlation Table | Exploratory | Extracting true airspeed (TAS) and Mach numbers instead of ground speed over earth. | Oguzhan Sukru Keles | 
| **F-04** | Country-level speed rankings are heavily distorted by the time-zone-driven mixture of terminal vs. cruising flights in a single snapshot. | Figure 4 & Figure 6 (Composition Check) | Confirmed (Composition) | Analyzing a full 24-hour longitudinal trajectory dataset to normalize departure banks. | Muhammet Ali Ozturk | 
| **F-05** | The US airborne fleet is significantly faster than the UK fleet primarily because its climbing traffic operates at higher speeds at 10:33 UTC. | Figure 6 (Bootstrap CI) | Exploratory | Executing the same comparison at 22:00 UTC when the US fleet is mostly grounded. | Oguzhan Sukru Keles | 

### Back to cleaning

During our early demographic exploration, we noticed that several countries failed to group correctly because they retained outdated diplomatic or verbose UN designations that slipped past our initial profiling. We went back to the M3 layer, updated `data/mappings/country_mapping.csv`, and successfully standardized the following entities to ensure clean M4 grouping:

* `Libyan Arab Jamahiriya` $\rightarrow$ `Libya`

* `Republic of Moldova` $\rightarrow$ `Moldova`

* `Viet Nam` $\rightarrow$ `Vietnam`

### Forward to modelling

* **Snapshot Spatial-Temporal Mismatch:** M7 models attempting to predict delays based on weather cannot directly join our global 10:33 UTC flight snapshot against the 7-day Istanbul weather series. The model will require localized terminal bounding boxes and synchronous time matching.

* **Composition Effects (Simpson's Paradox):** Aggregating flight data without controlling for the altitude regime (Terminal vs. Cruise) will introduce severe mixture bias into any predictive routing model.

* **Imbalanced Target Classes:** The vast majority of national fleets lack sufficient density ($n < 30$); any global machine learning model will need aggressive hierarchical grouping or strict entity subsetting to prevent overfitting on thin routes.

## 9. What we can't say

Despite our rigorous cleaning and exploratory validation, the structural boundaries of our two datasets restrict the claims we can legitimately make:

* **We cannot calculate actual schedule delay durations:** Because the OpenSky API payload provides instantaneous physics state vectors (velocity, altitude, coordinates) but omits scheduled departure times, arrival times, and origin/destination airport IATA codes, we cannot yet determine if a flight is "delayed." We can only map extreme weather to flight pattern disruptions until longitudinal scheduled routing tables are integrated.

* **We cannot generalize global weather conditions:** Our weather data is strictly bound to a single spatial grid cell at Istanbul Airport (`41.2753N, 28.7519E`). Consequently, any correlation between weather anomalies and flight speeds must be restricted to aircraft entering that specific regional boundary, not the global fleet.

* **We cannot definitively claim one country's fleet is universally faster than another's:** Our 95% bootstrap intervals for the US vs. UK comparison apply *only* to the specific traffic mixture present at exactly 10:33 UTC on October 1, 2026. Without multi-day longitudinal polling, we cannot untangle true operational speed differences from the natural rotation of daily time-zone departure banks.