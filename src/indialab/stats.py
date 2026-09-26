"""Inference for monthly return series: HAC t-stats, block bootstrap, Reality Check, deflated Sharpe."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as st


def newey_west_t(x: pd.Series, lags: int | None = None) -> tuple[float, float]:
    """Mean and Newey-West t-statistic of a series (Bartlett kernel)."""
    x = pd.Series(x).dropna().to_numpy(float)
    n = len(x)
    lags = int(np.floor(4 * (n / 100) ** (2 / 9))) if lags is None else lags
    e = x - x.mean()
    s = e @ e / n
    for L in range(1, lags + 1):
        s += 2 * (1 - L / (lags + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean()), float(x.mean() / np.sqrt(s / n))


def stationary_bootstrap_idx(n: int, n_boot: int, mean_block: float, rng: np.random.Generator) -> np.ndarray:
    """Politis-Romano stationary bootstrap indices, shape (n_boot, n)."""
    p = 1.0 / mean_block
    idx = np.empty((n_boot, n), dtype=int)
    idx[:, 0] = rng.integers(0, n, n_boot)
    jump = rng.random((n_boot, n)) < p
    fresh = rng.integers(0, n, (n_boot, n))
    for t in range(1, n):
        idx[:, t] = np.where(jump[:, t], fresh[:, t], (idx[:, t - 1] + 1) % n)
    return idx


def cagr(r: np.ndarray, periods: int = 12) -> np.ndarray:
    r = np.asarray(r, float)
    return np.exp(np.log1p(r).sum(axis=-1) * periods / r.shape[-1]) - 1


def bootstrap_cagr_diff(a: pd.Series, b: pd.Series, n_boot: int = 5000, mean_block: float = 12,
                        seed: int = 0) -> dict:
    """95% interval for CAGR(a) - CAGR(b), resampling months jointly."""
    df = pd.concat([a, b], axis=1).dropna()
    x = df.to_numpy(float)
    idx = stationary_bootstrap_idx(len(x), n_boot, mean_block, np.random.default_rng(seed))
    d = cagr(x[idx, 0]) - cagr(x[idx, 1])
    point = float(cagr(x[:, 0]) - cagr(x[:, 1]))
    return {"diff": point, "lo": float(np.quantile(d, 0.025)), "hi": float(np.quantile(d, 0.975)),
            "p_le_0": float((d <= 0).mean()), "n_months": len(x)}


def reality_check(variants: pd.DataFrame, benchmark: pd.Series, n_boot: int = 5000, mean_block: float = 12,
                  seed: int = 0) -> dict:
    """White (2000) Reality Check: is the BEST variant's mean excess over the benchmark
    larger than chance, given how many variants were tried? Returns the p-value."""
    df = variants.join(benchmark.rename("_b"), how="inner").dropna()
    d = df.drop(columns="_b").to_numpy(float) - df[["_b"]].to_numpy(float)
    n = len(d)
    mean = d.mean(axis=0)
    v = np.sqrt(n) * mean.max()
    idx = stationary_bootstrap_idx(n, n_boot, mean_block, np.random.default_rng(seed))
    boot = (d[idx].mean(axis=1) - mean) * np.sqrt(n)
    vb = boot.max(axis=1)
    return {"p_value": float((vb >= v).mean()), "best": str(df.columns[int(mean.argmax())]),
            "best_mean_excess_pm": float(mean.max()), "n_variants": d.shape[1], "n_months": n}


def deflated_sharpe(r: pd.Series, n_trials: int, sr_var_trials: float, periods: int = 12) -> dict:
    """Bailey & Lopez de Prado (2014) deflated Sharpe ratio (per-period SR internally).

    sr_var_trials: variance of the (per-period) Sharpe ratios across the trials tried.
    Returns the probability that the true Sharpe exceeds the best-of-N-by-luck threshold.
    """
    x = pd.Series(r).dropna().to_numpy(float)
    T = len(x)
    sr = x.mean() / x.std(ddof=1)
    g3 = st.skew(x)
    g4 = st.kurtosis(x, fisher=False)
    emc = 0.5772156649
    N = max(int(n_trials), 1)
    if N > 1:
        sr0 = np.sqrt(sr_var_trials) * ((1 - emc) * st.norm.ppf(1 - 1 / N) + emc * st.norm.ppf(1 - 1 / (N * np.e)))
    else:
        sr0 = 0.0
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return {"sharpe_annual": float(sr * np.sqrt(periods)), "sr0_annual": float(sr0 * np.sqrt(periods)),
            "dsr": float(st.norm.cdf(z)), "n_trials": N, "T": T}


def spa(variants: pd.DataFrame, benchmark: pd.Series, n_boot: int = 5000, mean_block: float = 12,
        seed: int = 0) -> dict:
    """Hansen (2005) Superior Predictive Ability test (studentized, consistent p-value).

    Unlike the Reality Check, noisy poor variants cannot hide a genuinely good one: each
    variant's excess is scaled by its own standard error, and clearly bad variants are
    not re-centred to zero.
    """
    df = variants.join(benchmark.rename("_b"), how="inner").dropna()
    d = df.drop(columns="_b").to_numpy(float) - df[["_b"]].to_numpy(float)
    n = len(d)
    mean = d.mean(axis=0)
    idx = stationary_bootstrap_idx(n, n_boot, mean_block, np.random.default_rng(seed))
    bm = d[idx].mean(axis=1)                                   # (n_boot, k)
    omega = np.sqrt(n) * bm.std(axis=0)                        # bootstrap s.e. of sqrt(n)*mean
    omega = np.where(omega > 0, omega, np.nan)
    t = np.sqrt(n) * mean / omega
    stat = max(np.nanmax(t), 0.0)
    keep = t >= -np.sqrt(2 * np.log(np.log(n)))
    mu_c = np.where(keep, mean, 0.0)
    tb = np.sqrt(n) * (bm - mu_c) / omega
    p = float((np.maximum(np.nanmax(tb, axis=1), 0.0) >= stat).mean())
    return {"p_value": p, "best": str(df.columns[int(np.nanargmax(t))]), "best_t": float(np.nanmax(t)),
            "n_variants": d.shape[1], "n_months": n}
