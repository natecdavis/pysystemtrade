# Literature Review: Alpha and Beta Factors in Cryptocurrency Markets

## Overview

This review synthesizes the academic and practitioner research on systematic risk factors, return anomalies, and alpha signals identified in cryptocurrency markets—both spot and perpetual futures. The goal is to catalog everything that could be incorporated into a systematic crypto trading system. The literature has expanded rapidly since ~2018, and we now have a reasonably mature set of findings, though the field remains younger and less settled than equities.

---

## 1. Foundational Factor Models

### 1.1 The Liu-Tsyvinski-Wu Three-Factor Model (C-3)

The seminal factor model for crypto comes from Liu, Tsyvinski, and Wu (2022, *Journal of Finance*). Using data from CoinMarketCap across 1,707 coins, they establish that **three factors—cryptocurrency market (CMKT), size (CSMB), and momentum (CMOM)**—capture the cross-sectional expected cryptocurrency returns.

Key findings from the C-3 model:

- Ten cryptocurrency characteristics form successful long-short strategies generating sizable and statistically significant excess returns, and all are subsumed by the three-factor model.
- Stock market factor models (Fama-French 3-factor, Carhart 4-factor, FF 5-factor) do *not* explain the cross-section of crypto returns—crypto factors are crypto-specific.
- Cryptocurrencies have effectively no exposure to traditional stock market, macroeconomic, currency, or commodity factors.
- Momentum strategies perform significantly better among larger coins. The above-median-size momentum strategy generates statistically significant 4.2% weekly returns, while the below-median-size version is insignificant at 0.6%.

**Practical implication:** The market beta of any crypto position is dominated by crypto-specific systematic risk, not equity beta. A systematic trader should factor in CMKT exposure as the primary beta, with size and momentum as the next two systematic drivers.

### 1.2 The Cong-Karolyi-Tang-Zhao Five-Factor Model (C-5)

Cong, Karolyi, Tang, and Zhao (2022, SSRN/ABFER Best Paper) extend the C-3 model using 4,007 crypto assets to a **five-factor model: CMKT + CSMB + CMOM + Value (VAL) + Network Adoption (NET)**.

Key contributions:

- **Crypto "Value" factor:** Constructed using the Active-Addresses-to-Market-Cap ratio—the crypto analogue of book-to-market. High on-chain activity relative to market cap predicts higher returns.
- **Network Adoption factor:** Based on the growth rate in total addresses with non-zero balance. Coins with faster user adoption earn a premium.
- Momentum exists only in large-cap cryptocurrencies; smaller coins exhibit *reversals*.
- The value premium is larger for smaller cryptocurrencies.
- The C-5 model significantly outperforms the C-3 model and all other tested specifications in pricing the cross-section, both in-sample and out-of-sample.
- Significant market segmentation exists across token categories (currencies, platforms, DeFi, etc.), analogous to country effects in international equity markets.

**Practical implication:** For cross-sectional strategies, the active-address-to-market-cap ratio and address growth rate are implementable signals. The segmentation finding also suggests that within-category relative value strategies may have merit.

### 1.3 Bianchi and Babiak — Latent Factor Model via IPCA

Bianchi and Babiak (2021/2023) apply Instrumented Principal Component Analysis (IPCA) to cryptocurrency returns and identify three latent factors related to **liquidity, size/market, and downside risk**. A four-factor IPCA model produces an out-of-sample total R² of 11.5% for individual daily crypto returns. Critically, they show that return predictability comes from risk-based channels (factor exposures) rather than mispricing, suggesting these premia may be persistent.

---

## 2. Individual Factors — Detailed Evidence

### 2.1 Momentum

Momentum is the most extensively studied crypto anomaly. The evidence is nuanced:

**Time-series momentum (TSMOM):**
- Liu and Tsyvinski (2021, *Review of Financial Studies*) document strong time-series momentum at 1-to-4-week horizons. Current market returns predict future returns up to eight weeks ahead.
- Han, Kang, and Ryu (2024, SSRN) conduct the most comprehensive analysis under realistic assumptions. Their conclusion: evidence of time-series momentum is strong, but evidence of cross-sectional momentum is weak once you account for transaction costs and daily price fluctuations. The momentum effect is concentrated among *winners*; losers often rebound and inflict significant losses on short legs.
- Overreaction is a likely cause of momentum, but what drives overreaction is unclear.

**Cross-sectional momentum (XSMOM):**
- Liu et al. (2022) find that 1-to-4-week look-back cross-sectional momentum generates significant long-short returns with nearly monotonic quintile patterns.
- Grobys et al. (2023, *Quantitative Finance*) replicate 34 cross-sectional anomalies across 3,900 coins and find "factor momentum"—past-winning anomaly factors continue to outperform. However, this autocorrelation primarily stems from size and volatility anomalies.
- Dobrynskaya documents both short-term momentum (1-4 weeks) and longer-term reversals, with the loser portfolio driving most of the reversal.

**Momentum crashes:**
- Grobys et al. (2025, *Financial Markets and Portfolio Management*) show that crypto momentum is subject to severe crashes. A single cryptocurrency can cause the entire momentum portfolio to become insignificant. Volatility management (scaling positions inversely with realized vol) effectively mitigates these crashes—analogous to Barroso and Santa-Clara (2015) in equities.

**Practical implication:** Time-series momentum on a diversified crypto portfolio is the most robust implementation. Cross-sectional momentum works but primarily on the long side among large caps. Volatility-scaling is essential. Short-horizon (1-4 week) signals dominate.

### 2.2 Size

The size effect is one of the most robust cross-sectional factors:

- Liu et al. (2022) document that small cryptocurrencies earn significantly higher average returns. The long-short portfolio (small minus big) is statistically significant.
- The economic intuition parallels equities: small coins are less liquid, so investors demand a liquidity premium.
- Shen et al. (2020) and others confirm significant negative relation between size and future returns.
- However, small-cap crypto is notoriously difficult to trade—many micro-cap coins have minimal liquidity, wide spreads, and high implementation costs. The size premium likely reflects *compensation for illiquidity* rather than pure alpha.

**Practical implication:** Size is a strong factor in explaining returns, but capturing the small-cap premium requires careful attention to execution costs and market impact. For a Carver-style system, it's more relevant as a risk factor to control exposure to than as an alpha source.

### 2.3 Trend / Technical Signals (CTREND)

Babiak and Zaremba (2025, *JFQA*) propose CTREND, an aggregate crypto trend factor built from 28 popular technical signals including momentum oscillators, moving averages, volume-based indicators, and volatility measures, combined via machine learning.

Key findings:

- CTREND is a reliable predictor of the cross-section of crypto returns across subperiods and various market states (high/low volatility, bull/bear).
- A long-short strategy based on CTREND significantly outperforms standalone momentum (CMOM), size (CSMB), and market (CMKT) factors in terms of Sharpe ratio.
- The effect survives a robustness check across 55,296 alternative implementations (varying sample prep, data cleaning, forecast methods, portfolio designs).
- 2-week momentum remains the only individual signal that retains significance alongside CTREND in a kitchen-sink regression—most other signals are subsumed.

**Practical implication:** Combining multiple technical signals via an ensemble/ML approach outperforms any individual indicator. This is highly compatible with Carver's framework of combining multiple trading rules and weighting by forecast diversification multiplier.

### 2.4 Value

The concept of "value" in crypto is necessarily different from equities (no book value):

- Cong et al. (2022) define crypto value as the **Active-Addresses-to-Market-Cap ratio**. Coins that are "cheap" relative to their on-chain usage earn a premium.
- Bhambhwani, Delikouras, and Korniotis (2022) explore production-cost-based valuations (hash rate, energy costs) as fundamental anchors.
- The value premium is plausibly driven by compensation for on-chain activity risk—coins with high activity relative to price may be riskier in ways not captured by other factors.

Other value-like proxies studied include NVT ratio (Network Value to Transactions), price-to-new-address ratio, and Metcalfe's Law-based fair value models.

**Practical implication:** For implementable value signals, the active-address-to-market-cap ratio is the most academically validated. NVT and Metcalfe-based signals have practitioner adoption but less rigorous academic testing.

### 2.5 Liquidity / Volume

- Liquidity is consistently identified as a priced factor. Kozlowski et al. (2021) show that reversal effects are more evident among less liquid, smaller-cap cryptocurrencies, driven by both market inefficiency and liquidity compensation.
- Amihud illiquidity measures adapted for crypto predict returns cross-sectionally.
- Volume factors (dollar volume, turnover) generate significant long-short returns in Liu et al. (2022)—low-volume coins outperform high-volume coins after risk adjustment.
- Babiak and Erdis (2022) show that costly arbitrage (measured by trading activity variations) matters for explaining crypto anomalies.

**Practical implication:** Liquidity is both a risk factor and a practical constraint. For a systematic system, liquidity filters are essential for the tradeable universe, and the illiquidity premium can be harvested by tilting toward less liquid (but still tradeable) coins.

### 2.6 Volatility and Downside Risk

**Idiosyncratic volatility (IVOL):**
- The IVOL puzzle exists in crypto too: Liu et al. (2022) document that high-IVOL coins underperform (negative IVOL premium), consistent with the lottery-preference explanation.
- Leirvik (2022) confirms IVOL is priced in the cross-section.

**Downside risk:**
- Zhang, Li, Xiong, and Wang (2021, *Journal of Banking & Finance*) find that downside risk is compensated by a significant premium. Idiosyncratic downside risk (VaR, Expected Shortfall) predicts higher future returns.
- Dobrynskaya (2023, *International Review of Financial Analysis*) documents that downside *market* beta (systematic downside risk) is priced regardless of whether you use a crypto market index or equity market index.
- Volatility plays a dominant role in explaining the downside risk premium—more so than skewness or kurtosis.

**Inverse leverage effect:**
- Unlike equities, crypto markets exhibit an *inverse* leverage effect: positive returns increase future volatility more than negative returns. This is attributed to the retail-heavy investor base and speculative behavior.

**Practical implication:** Volatility scaling (à la Carver) is well-supported. The IVOL anomaly suggests avoiding lottery-like micro-caps. For portfolio construction, controlling downside beta exposure matters.

### 2.7 Short-Term Reversal

- Dobrynskaya documents significant reversal at horizons beyond 4 weeks, driven primarily by the loser portfolio rebounding.
- Cong et al. (2022) show that among smaller cryptocurrencies, *reversals* dominate rather than momentum.
- The 1-day skip in momentum construction (standard in the literature) is designed to avoid the very-short-term reversal effect.

**Practical implication:** Mean-reversion rules can work, especially at shorter intraday/daily horizons and among smaller coins. In a Carver framework, this supports including convergent (mean-reversion) rules alongside divergent (trend-following) rules.

### 2.8 Sentiment and Investor Attention

This is a rich and crypto-specific area:

**Google Trends / Search Volume:**
- Liu and Tsyvinski (2021) establish that proxies for investor attention strongly forecast future cryptocurrency returns.
- Aslanidis et al. (2022) find bidirectional information flows between a crypto-specific Google Trends index and returns, with effects lasting up to 6 days.
- Quantpedia's (2024) empirical analysis finds that combining a sentiment signal (from Google Trends) with a price trend signal improves strategy performance. The mixed model captures upside when sentiment and trend agree, and tactically exits when they diverge.

**Fear & Greed Index:**
- Recent work examines the cross-sectional pricing of sentiment *beta*—how sensitive each coin's price is to shifts in the Bitcoin Fear & Greed Index. Coins with high negative sentiment beta (those that fall most during fear) earn a premium, consistent with a risk-compensation story.

**Social Media (Twitter/Reddit):**
- NLP-based sentiment from Twitter and Reddit has been extensively studied. Results are mixed for direct price prediction, but tweet volume (not just sentiment polarity) appears to have short-term predictive power.
- Herding behavior is well-documented in crypto, driven by social influence and public sentiment.

**Practical implication:** Google Trends and the Fear & Greed Index are accessible, low-cost sentiment signals. They work best as regime filters (risk-on/risk-off) rather than direct alpha signals. For a systematic system, consider them as overlay indicators that adjust position sizing or rule weighting.

---

## 3. Perpetual Futures — Specific Factors

### 3.1 Funding Rate as a Signal

Perpetual futures dominate crypto derivatives trading ($100B+ daily volume). The funding rate mechanism—periodic payments from longs to shorts (or vice versa) to anchor the perp price to spot—creates unique trading signals.

**Presto Research (2024):**
- Funding rate changes explain ~12.5% of price variation over a 7-day period (statistically significant but weak for single-asset prediction).
- However, funding rate data is valuable for **cross-sectional relative price prediction** across multiple assets. A statistical arbitrage alpha using funding rate data shows favorable performance metrics, though with very high turnover requiring refinement.

**Coinbase Institutional (2024):**
- Funding rates are trailing indicators of price rather than leading indicators, but prolonged periods of extreme funding rates do precede elevated volatility.
- Funding rates skew positive not just because of bullish positioning but because of base interest rate mechanisms and clamping functions in their calculation.

**Practical implication:** Funding rate is most useful as: (1) a carry signal—earning funding while hedged, and (2) a cross-sectional relative-value signal—going long coins with negative funding, short coins with extreme positive funding. The signal has alpha but decays quickly and requires careful execution.

### 3.2 Perpetual Futures Basis / Carry Trade

**He, Manela, Ross, and von Wachter (2022/2024) — "Fundamentals of Perpetual Futures":**
- They derive no-arbitrage prices for perpetual futures and show that deviations in crypto are *considerably larger* than in traditional currency markets.
- A simple trading strategy exploiting deviations from no-arbitrage bounds generates a **Sharpe ratio of 1.8** for retail investors (with standard Binance fees) and **up to 3.5** for market makers. Performance is even better for ETH and altcoins than BTC.
- These returns generate significant alphas relative to both the C-3 and C-5 factor models.

**BIS Working Paper ("Crypto Carry"):**
- The crypto carry trade (long spot, short futures to earn the basis) is driven by retail investor demand for leveraged long exposure through futures.
- Investor attention (Google Trends) is strongly correlated with the demand for leveraged crypto exposure, which drives the basis wider.
- Deviations diminish over time as crypto markets mature and become more efficient.

**Fan, Jiao, Lu, and Tong (2024) — "Risk and Return of Cryptocurrency Carry Trade":**
- A cross-sectional carry trade strategy (buying high-interest cryptos, shorting low-interest ones) yields an annualized return of 43.4% with a Sharpe ratio of 0.74.
- Carry returns cannot be explained by prevailing crypto factors (market, size, momentum, volatility, liquidity, downside risk).
- Instead, a significant portion of crypto carry returns can be attributed to a premium for equity market volatility risk—revealing a cross-asset linkage.

**Funding Rate Arbitrage:**
- A study by ScienceDirect (2025) on funding rate arbitrage across both CEX (Binance, Bitmex) and DEX (ApolloX, Drift) platforms finds diversification benefits, as funding rate arbitrage exhibits no correlation with HODL strategies.

**Practical implication:** Carry/basis strategies are among the highest Sharpe opportunities in crypto, but they require: careful margin management, multi-exchange execution capability, and awareness that the premium is compressing over time as the market matures. For a Carver-style system trading perps, funding drag is a cost when long and a benefit when short—this should be explicitly modeled.

### 3.3 Cross-Exchange Arbitrage

Makarov and Schoar (2020) document persistent arbitrage opportunities across crypto exchanges. While these have diminished significantly since 2017-2018, residual inefficiencies remain, particularly during periods of high volatility and across geographies with capital controls.

---

## 4. On-Chain Factors

On-chain data provides crypto-specific signals unavailable in traditional markets:

### 4.1 Network Metrics
- **Active addresses** (and growth rate): Priced in the cross-section per Cong et al. (2022).
- **Transaction volume (USD):** On-chain transaction volume growth is a network adoption proxy.
- **NVT Ratio:** Network Value to Transactions—high NVT suggests overvaluation.
- **Metcalfe's Law:** Research shows Bitcoin's value can be modeled as proportional to the square of active users, though this is more of a long-term valuation anchor.

### 4.2 Exchange Flows
- Net inflows to exchanges are a bearish signal (selling pressure). Net outflows are bullish (accumulation).
- Stablecoin inflows to exchanges often precede buy-side volatility.
- These signals are available from providers like Glassnode, CryptoQuant, and Nansen.

### 4.3 Whale / Smart Money Tracking
- Monitoring large wallet movements provides lead time on price moves.
- Nansen's "Smart Money" labels identify wallets with historically strong performance.
- Tracking VC fund and known institutional wallet flows can surface information about upcoming large trades.

### 4.4 NUPL and MVRV
- **NUPL (Net Unrealized Profit/Loss):** Measures aggregate profitability of held coins. High unrealized profits suggest potential selling pressure.
- **MVRV (Market Value to Realized Value):** Below 1 suggests undervaluation; above 1.5-2 suggests overheating.

### 4.5 Supply Dynamics
- Token emission schedules, halving events, and staking lock-ups affect supply-side dynamics.
- Coins approaching significant supply reductions (halvings) historically show pre-event momentum.

**Practical implication:** On-chain metrics are the most crypto-native alpha source. For a systematic system, the most implementable signals are exchange flow data, MVRV/NUPL as regime indicators, and address growth as a fundamental factor. Data availability has improved dramatically via APIs from Glassnode, CryptoQuant, Santiment, and Amberdata.

---

## 5. Macro and Cross-Asset Factors

While crypto-specific factors dominate, some cross-asset linkages exist:

- **Equity market volatility risk:** Fan et al. (2024) show crypto carry returns are partly a premium for equity vol risk.
- **Monetary policy:** Crypto markets respond to Fed announcements and monetary policy changes, particularly since 2020. The response depends on the type of crypto asset—currencies (BTC, ETH) show different sensitivity than protocol tokens.
- **Dollar strength:** Corbet et al. (2020) document that crypto assets respond to macro conditions including dollar liquidity and risk sentiment.
- **Regulatory sentiment:** Bonaparte and Bernile (2023) introduce a Crypto Regulation Sentiment Index showing that regulatory sentiment heightens short-term volatility without affecting long-term pricing.

**Practical implication:** For a systematic system, these macro factors are best used as regime/overlay signals rather than direct trading signals. Consider monitoring equity vol (VIX), DXY, and Fed funds futures as context variables.

---

## 6. Seasonality and Microstructure Effects

- **Intraday patterns:** Bitcoin and other major cryptos exhibit time-of-day patterns in returns and volatility, analogous to traditional market open/close effects but shifted by the 24/7 trading schedule.
- **Day-of-week effects:** Some evidence of weekend effects and day-of-week seasonality in crypto returns.
- **Futures expiration effects:** Bitcoin futures expiration dates show some impact on spot price dynamics.
- **Wash trading:** Cong et al. (2023, *Management Science*) document pervasive wash trading on unregulated exchanges, which inflates volume and can distort volume-based signals. This is a critical data quality concern.

---

## 7. Pairs Trading / Statistical Arbitrage

Palazzi (2025, *Journal of Futures Markets*) demonstrates that cointegrated pairs trading in crypto:

- Generates statistically significant positive alpha relative to BTC.
- Has modest negative beta to BTC (β ≈ -0.13), offering diversification.
- BTC returns explain only 2.7% of the strategy's return variance.
- Outperforms passive and dual-momentum approaches when combined with dynamic risk management (trailing stops, volatility filters).

**Practical implication:** Pairs/stat-arb strategies provide genuine diversification from directional crypto exposure. The key is robust cointegration testing and dynamic parameter optimization.

---

## 8. Summary Table: Factors Ranked by Evidence Strength and Implementability

| Factor | Evidence Strength | Alpha vs. Beta | Best Horizon | Implementability | Key Papers |
|--------|------------------|----------------|-------------|-----------------|------------|
| Market (CMKT) | Very Strong | Beta | All | High | Liu et al. (2022) |
| Size (CSMB) | Very Strong | Both | Weekly | Medium (liquidity constraints) | Liu et al. (2022), Cong et al. (2022) |
| Time-Series Momentum | Strong | Alpha | 1-4 weeks | High | Liu & Tsyvinski (2021), Han et al. (2024) |
| Cross-Sectional Momentum | Moderate | Alpha | 1-4 weeks | Medium (concentrated in large caps) | Liu et al. (2022), Grobys (2023) |
| Trend (CTREND) | Strong | Alpha | Multi-horizon | High | Babiak & Zaremba (2025) |
| Value (Active Addr/MCap) | Strong | Both | Weekly | Medium (data access) | Cong et al. (2022) |
| Network Adoption | Moderate-Strong | Both | Weekly | Medium | Cong et al. (2022) |
| Carry / Basis | Strong | Alpha | Daily-Weekly | High (perps required) | He et al. (2024), Fan et al. (2024) |
| Funding Rate | Moderate | Alpha | Daily | High (perps) | Presto (2024), He et al. (2024) |
| Liquidity / Illiquidity | Strong | Both | Weekly | Medium | Kozlowski et al. (2021) |
| Volatility (IVOL) | Moderate-Strong | Both | Weekly | Medium | Zhang et al. (2021), Leirvik (2022) |
| Downside Risk | Strong | Beta | Weekly | Medium | Zhang et al. (2021), Dobrynskaya (2023) |
| Short-Term Reversal | Moderate | Alpha | < 1 week | Medium-High | Dobrynskaya, Cong et al. (2022) |
| Investor Attention / Sentiment | Moderate | Alpha | Daily-Weekly | High (Google Trends, F&G Index) | Liu & Tsyvinski (2021), Aslanidis et al. (2022) |
| Exchange Flows | Moderate | Alpha | Daily | Medium (data cost) | Practitioner literature |
| MVRV / NUPL | Moderate | Alpha (regime) | Weekly-Monthly | High | Practitioner literature |
| Macro (VIX, DXY, Fed) | Moderate | Regime overlay | Variable | High | Corbet et al. (2020), BIS (2023) |
| Pairs / Cointegration | Moderate-Strong | Alpha | Daily-Weekly | Medium-High | Palazzi (2025) |

---

## 9. Key Takeaways for a Systematic Crypto Trading System

1. **Momentum and trend-following are the most robust alpha sources**, particularly time-series momentum at 1-4 week horizons. Combining multiple technical signals (CTREND approach) outperforms any individual rule.

2. **Carry/basis in perpetuals is a high-Sharpe strategy** but is compressing over time. Funding rate should be explicitly modeled as both a cost (when long) and a revenue source (when short).

3. **Size and value (active-address-based) are genuine risk factors** that explain cross-sectional variation. They can be used for portfolio tilting but require liquidity awareness.

4. **Volatility scaling is essential**—momentum crashes are severe in crypto, and inverse-volatility weighting (as in Carver's framework) is directly supported by the academic evidence.

5. **On-chain data provides the most crypto-native edge**, particularly exchange flows, address growth, and MVRV. These signals are increasingly accessible via API.

6. **Sentiment/attention works as a regime filter** rather than a direct signal—combine with trend signals for better risk-adjusted returns.

7. **Cross-sectional momentum is weaker than time-series momentum** once realistic assumptions are applied. Focus on long-only momentum among large caps rather than long-short.

8. **Market segmentation by token category matters**—different types of tokens (currencies, platforms, DeFi, memecoins) have different factor exposures, analogous to sectors in equities.

9. **The alpha opportunity is diminishing over time** as the market matures, consistent with McLean and Pontiff (2016) and Falck and Rej (2022) showing that published strategy Sharpe ratios decline by ~50% post-publication.

10. **Data quality is a first-order concern**—wash trading, exchange manipulation, and survivorship bias are all well-documented. Use reputable data sources and apply sensible filters.

---

## References (Selected)

- Babiak, M. & Zaremba, A. (2025). "A Trend Factor for the Cross Section of Cryptocurrency Returns." *JFQA*.
- Bianchi, D. & Babiak, M. (2021/2023). "A Risk-Based Explanation of Cryptocurrency Returns." Working Paper / SNB.
- Cong, L.W., Karolyi, G.A., Tang, K. & Zhao, W. (2022/2025). "Crypto Value, Factor Pricing, and Market Segmentation." *SSRN*.
- Dobrynskaya, V. "Cryptocurrency Momentum and Reversal." Working Paper.
- Fan, Z., Jiao, F., Lu, L. & Tong, X. (2024). "The Risk and Return of Cryptocurrency Carry Trade." *SSRN*.
- Grobys, K. et al. (2023). "Cryptocurrency Factor Momentum." *Quantitative Finance*, 23(12).
- Grobys, K. et al. (2025). "Cryptocurrency Momentum Has (Not) Its Moments." *Financial Markets and Portfolio Management*.
- Han, C., Kang, B. & Ryu, J. (2024). "Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market." *SSRN*.
- He, S., Manela, A., Ross, O. & von Wachter, V. (2022/2024). "Fundamentals of Perpetual Futures." *arXiv*.
- Liu, Y. & Tsyvinski, A. (2021). "Risks and Returns of Cryptocurrency." *Review of Financial Studies*, 34(6), 2689–2727.
- Liu, Y., Tsyvinski, A. & Wu, X. (2022). "Common Risk Factors in Cryptocurrency." *Journal of Finance*, 77(2), 1133–1177.
- Palazzi (2025). "Trading Games: Beating Passive Strategies in the Bullish Crypto Market." *Journal of Futures Markets*.
- Zhang, W., Li, Y., Xiong, X. & Wang, P. (2021). "Downside Risk and the Cross-Section of Cryptocurrency Returns." *Journal of Banking & Finance*, 133.
