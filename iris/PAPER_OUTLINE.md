# Research paper outline (write it yourself; figures and tables are generated)

1. **Introduction**: the backtest problem; question; hypotheses H1-H6; contribution.
2. **Background**: anomalies (momentum, reversal, low-vol, 52-week high); survivorship and delisting bias; data snooping.
3. **Data**: NSE bhavcopies; identity linking; corporate actions (the unadjusted-prevclose finding; detector validation); coverage table (E1).
4. **Method**
   4.1 Synthetic ground truth (E0)
   4.2 Universe, signals, timing
   4.3 Frictions: cost model, capacity, tax model
   4.4 Statistics: Newey-West, bootstrap, SPA/RC, deflated Sharpe
   4.5 Pre-registration and variant logging
5. **Results**
   5.1 E0: bias by rule (figure e0)
   5.2 Gross replication (E2 table, decile figure)
   5.3 Net of reality (E3 table, capacity figure, sub-periods)
   5.4 Multiple testing and hypothesis verdicts
   5.5 Bias on real data vs prediction (E4 figure; H6)
6. **Robustness**: extended sample 2007+, delisting returns, dividends, brokerage.
7. **Discussion**: what it means for investors and researchers; limitations.
8. **Conclusion**
9. **References** (REFERENCES.md); **Appendix**: pre-registration, code and data availability, AI-use statement.

Source for every number: `reports/RESULTS_real.md`. Source for figures: `figures/real/` and `figures/e0_bias_vs_missing_data.png`.
