#!/usr/bin/env python3
"""Time-series analysis of how each term is adopted in the psychology-journal corpus and in Google Books.

    python3 timeseries_analysis.py                          # all groups, writes ts_results/
    python3 timeseries_analysis.py --level era --smooth 3 --max-lag 10
    python3 timeseries_analysis.py --groups "autism/autistic,asperger"

Input: autism_timeseries.csv (made by timeseries_data.py). Needs pandas, numpy, scipy, statsmodels,
matplotlib.

For every unit (group / era / variant) the two series are put on the same years (years where the corpus
has at least --min-tokens words, within the years both corpora cover), turned into
log(1 + phrases per million), and smoothed with a centred rolling mean of --smooth years. Then:

  adoption.csv     per corpus: peak year, first year >= 10/50/90 % of the peak (smoothed series), first year
                   above --first-ppm per million, Kendall tau of the series against year, and the slope
                   of log(1 + ppm) per decade
  stationarity.csv ADF and KPSS p-values for each series, in levels and in first differences
  lead_lag.csv     cross-correlation of the first differences at lags -max_lag..+max_lag
                   (positive lag: Google Books leads the journals), the best lag and its correlation,
                   and the difference in the 50 % adoption years
  granger.csv      Granger-causality F-test p-values on the first differences, both directions, for
                   lags 1..--max-lag (smallest p-value and the lag where it occurs)
  plots/           one PNG per unit: both series (each scaled to its own maximum) and the cross-correlation
"""
import argparse
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from timeseries_data import load

HERE = Path(__file__).resolve().parent
warnings.filterwarnings("ignore")


def unit_name(r):
    if r["level"] == "variant":
        return f'{r["group"]}: {r["variant"]}'
    if r["level"] == "era":
        return f'{r["group"]}: {r["era"]}'
    return r["group"]


def prepare(sub, min_tokens, smooth):
    """Both series on common years: log(1 + ppm), smoothed. Returns a DataFrame indexed by year."""
    d = sub.set_index("year")
    d = d[(d["corpus_tokens"].fillna(0) >= min_tokens)]          # years with enough corpus text
    y0, y1 = d.index.min(), d.index.max()
    d = d.loc[y0:y1]
    out = pd.DataFrame({
        "corpus": np.log1p(d["corpus_per_million"]),
        "google": np.log1p(d["google_per_million"]),
    })
    out = out.reindex(range(y0, y1 + 1))                           # keep the year axis regular
    out = out.interpolate(limit_area="inside")                     # years dropped for low text: interpolate
    if smooth > 1:
        out = out.rolling(smooth, center=True, min_periods=1).mean()
    return out


def level_year(s, frac):
    """First year the series reaches frac of its maximum (after subtracting its minimum)."""
    lo, hi = s.min(), s.max()
    if not np.isfinite(hi) or hi - lo <= 0:
        return np.nan
    hit = s.index[(s - lo) / (hi - lo) >= frac]
    return hit[0] if len(hit) else np.nan


def adoption_row(name, level, which, raw, s, first_ppm):
    yrs = s.index.values
    tau, p = stats.kendalltau(yrs, s.values) if s.nunique() > 1 else (np.nan, np.nan)
    slope = np.polyfit(yrs, s.values, 1)[0] * 10 if s.nunique() > 1 else np.nan
    above = raw.index[raw >= first_ppm]
    return {"level": level, "unit": name, "corpus": which, "years": f"{yrs.min()}-{yrs.max()}",
            "peak_year": s.idxmax() if s.max() > 0 else np.nan,
            "year_10pct": level_year(s, .10), "year_50pct": level_year(s, .50), "year_90pct": level_year(s, .90),
            "first_year_above_ppm": above[0] if len(above) else np.nan,
            "kendall_tau": tau, "kendall_p": p, "slope_per_decade": slope}


def stationarity(name, level, which, s):
    from statsmodels.tsa.stattools import adfuller, kpss
    rows = []
    for form, x in (("level", s), ("diff", s.diff().dropna())):
        r = {"level": level, "unit": name, "corpus": which, "form": form, "n": len(x)}
        if len(x) > 12 and x.nunique() > 1:
            r["adf_p"] = adfuller(x, autolag="AIC")[1]            # H0: unit root
            r["kpss_p"] = kpss(x, regression="c", nlags="auto")[1]  # H0: stationary
        rows.append(r)
    return rows


def lead_lag(name, level, d, max_lag):
    a = d["corpus"].diff().dropna()
    b = d["google"].diff().dropna()
    n = len(a)
    rows = []
    for lag in range(-max_lag, max_lag + 1):                      # lag > 0: google(t - lag) vs corpus(t)
        if lag >= 0:
            x, y = b.values[: n - lag], a.values[lag:]
        else:
            x, y = b.values[-lag:], a.values[: n + lag]
        r = np.corrcoef(x, y)[0, 1] if len(x) > 5 and x.std() > 0 and y.std() > 0 else np.nan
        rows.append((lag, r))
    cc = pd.DataFrame(rows, columns=["lag", "r"])
    best = cc.loc[cc["r"].abs().idxmax()] if cc["r"].notna().any() else None
    summary = {"level": level, "unit": name, "n_years": len(d),
               "best_lag": best["lag"] if best is not None else np.nan,
               "best_r": best["r"] if best is not None else np.nan,
               "r_lag0": cc.loc[cc["lag"] == 0, "r"].iloc[0],
               "year50_corpus_minus_google": level_year(d["corpus"], .5) - level_year(d["google"], .5)}
    return summary, cc


def granger(name, level, d, max_lag):
    from statsmodels.tsa.stattools import grangercausalitytests
    x = d[["corpus", "google"]].diff().dropna()
    rows = []
    if len(x) < 3 * max_lag + 10 or (x.std() == 0).any():
        return rows
    for cause, effect in (("google", "corpus"), ("corpus", "google")):
        try:                                                       # older statsmodels print unless verbose=False
            res = grangercausalitytests(x[[effect, cause]], maxlag=max_lag, verbose=False)
        except TypeError:
            res = grangercausalitytests(x[[effect, cause]], maxlag=max_lag)
        ps = {k: v[0]["ssr_ftest"][1] for k, v in res.items()}
        k = min(ps, key=ps.get)
        rows.append({"level": level, "unit": name, "cause": cause, "effect": effect,
                     "min_p": ps[k], "lag_of_min_p": k, **{f"p_lag{j}": ps[j] for j in ps}})
    return rows


def plot(name, d, cc, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 4), gridspec_kw={"width_ratios": [2, 1]})
    for col, lab in (("corpus", "psychology journals"), ("google", "Google Books")):
        s = d[col]
        ax[0].plot(d.index, (s - s.min()) / (s.max() - s.min()) if s.max() > s.min() else s * 0, label=lab)
    ax[0].set_title(name)
    ax[0].set_ylabel("log(1 + per million), scaled to own range")
    ax[0].legend()
    ax[1].bar(cc["lag"], cc["r"])
    ax[1].axhline(0, color="k", lw=.5)
    ax[1].set_xlabel("lag (years); positive = Google Books leads")
    ax[1].set_ylabel("correlation of first differences")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(HERE / "autism_timeseries.csv"))
    ap.add_argument("--level", default="group", choices=["group", "era", "variant"])
    ap.add_argument("--groups", default="", help="comma-separated groups to analyse (default: all)")
    ap.add_argument("--min-tokens", type=int, default=100000, help="corpus words a year needs to be used")
    ap.add_argument("--smooth", type=int, default=5, help="rolling-mean window in years (1 = none)")
    ap.add_argument("--max-lag", type=int, default=10, help="largest lag in years")
    ap.add_argument("--first-ppm", type=float, default=0.1, help="per-million level for 'first year above'")
    ap.add_argument("--min-years", type=int, default=25, help="skip units with fewer common years")
    ap.add_argument("--out", default=str(HERE / "ts_results"))
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    df = load(args.data)
    df = df[df["level"] == args.level]
    if args.groups:
        df = df[df["group"].isin([g.strip() for g in args.groups.split(",")])]
    out = Path(args.out)
    (out / "plots").mkdir(parents=True, exist_ok=True)

    adopt, stat, ll, gr = [], [], [], []
    df = df.assign(unit=df.apply(unit_name, axis=1))
    for name, sub in df.groupby("unit", sort=False):
        d = prepare(sub, args.min_tokens, args.smooth)
        if len(d) < args.min_years or d["corpus"].max() == 0 or d["google"].max() == 0:
            print(f"skipped {name!r}: {len(d)} common years, no usage in one corpus" if len(d) >= args.min_years
                  else f"skipped {name!r}: only {len(d)} common years")
            continue
        raw = sub.set_index("year")
        for which, col in (("corpus", "corpus_per_million"), ("google", "google_per_million")):
            adopt.append(adoption_row(name, args.level, which, raw[col].loc[d.index.min(): d.index.max()],
                                      d[which], args.first_ppm))
            stat += stationarity(name, args.level, which, d[which])
        summary, cc = lead_lag(name, args.level, d, args.max_lag)
        ll.append(summary)
        gr += granger(name, args.level, d, args.max_lag)
        if not args.no_plots:
            safe = "".join(c if c.isalnum() else "_" for c in name)
            plot(name, d, cc, out / "plots" / f"{safe}.png")

    for fname, rows in (("adoption", adopt), ("stationarity", stat), ("lead_lag", ll), ("granger", gr)):
        pd.DataFrame(rows).to_csv(out / f"{fname}.csv", index=False, float_format="%.5g")
    print(f"{len(ll)} units analysed -> {out}/ (adoption.csv, stationarity.csv, lead_lag.csv, granger.csv, plots/)")


if __name__ == "__main__":
    main()
