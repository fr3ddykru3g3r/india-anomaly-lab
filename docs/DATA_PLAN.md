# Data: what is used and why

The whole study stands or falls on having prices for firms that later disappeared. Free sources that serve only currently listed symbols (e.g. Yahoo) are survivor-biased by construction and are not used.

| Need | Source (implemented) | Why it is point-in-time | Pitfalls handled |
|---|---|---|---|
| Daily prices, all listed securities | NSE daily bhavcopies, legacy format to 5 Jul 2024, UDiFF after (`src/indialab/data/nse.py`, `bhavcopy.py`) | Each day's file lists what traded that day, including later-delisted stocks | Two formats; series EQ/BE/BZ kept; funds separated; missing archive days bridged |
| Identity | Symbol continuity + ISIN (files carry ISINs from 2011) + NSE `symbolchange.csv` (`panel.assign_ids`) | Links are formed from what each day's file says | Face-value splits issue new ISINs; symbols are reused after long gaps; renames before ISINs existed |
| Splits and bonuses | NSE PR archives, Bc files (Jul 2010 onward, weekly sample; each file covers +/-2 weeks of ex-dates) (`corpactions.py`) | Dated exchange records | **NSE's PREVCLOSE is not adjusted on ex-dates** (checked: HDFC Bank split, 19 Sep 2019), so factors must be applied explicitly; applied only when the price really gapped by the recorded ratio |
| Splits and bonuses before Jul 2010 | Gap detector: overnight gap within 3% of a standard ratio | Uses only that day's prices | Precision/recall measured against the 2010+ records (E1) |
| Dividends | Bc purposes with rupee amounts (Jul 2010 onward) | Dated | Only a sensitivity; percentage-of-face-value dividends not parsed |
| Delistings | A security that stops trading; distress flag from series BE/BZ, price < Rs 10 or -50% in its last year | Observed at the time | No official delisting-reason feed; terminal-return sensitivity (0, -30%, -100%) |
| Investable benchmark | NIFTYBEES in the same bhavcopies | Traded price | ETF, so includes tracking error and fees (that is the point) |

Rules:

- Respect each site's terms of use and rate limits: one request per second, honest user agent, resumable.
- Raw files are cached unmodified with SHA-256 hashes in `data/raw/manifest.csv` and `manifest_pr.csv`. Raw data is never edited.
- `python scripts/build_dataset.py` rebuilds every derived table from `data/raw`.
- `data/` is git-ignored (hundreds of MB). Anyone can rebuild it with `scripts/download_nse.py` and `scripts/download_pr.py`.
