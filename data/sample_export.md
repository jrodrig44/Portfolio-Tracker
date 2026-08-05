# Dividend Portfolio Tracker Export

## AI Context Summary
This section is written first so AI tools can understand the portfolio snapshot immediately.

Export Version: 2026.08
Generated: 2026-08-05T19:18:54
Currency: USD

Total Portfolio Value: $2,400.00
Cost Basis: $2,400.00
Unrealized Gain/Loss: $0.00
Estimated Annual Income: $55.00
Weighted Portfolio Yield: 2.29%
Projection contribution note: Projection calculations use the regular $280 biweekly contribution assumption and do not include ESPP contributions unless otherwise stated.
Number of Holdings: 5
Largest Holding: HD
Largest Holding Weight: 41.67%
Tracker Health Score: 58.1/100
Health Score Basis: Tracker-generated health score based on this export's methodology; not an objective portfolio rating.
Data Completeness: Direct sector classification coverage 100.00% of direct holdings by market value; fund look-through coverage 100.01% of fund holdings by market value; overall resolved exposure coverage 100.01% of total portfolio market value.
Strongest Areas: Data Quality 90.0, Income Stability 85.7, Dividend Growth Potential 61.7
Weakest Areas: Diversification 33.3, Total Return Potential 38.8, Concentration 50.0
Primary Alert: Concentration alert: HD is 41.7% of portfolio.

## Concentration Context
- Largest holding by value: HD (41.67% of portfolio value).
- That same holding contributes 18.18% of estimated annual income.
- Largest income contributor: JEPI (36.36% of estimated annual income).
- Employer-stock concentration detected. Apply ESPP-specific context (discount, purchase schedule, and employment exposure) and avoid automatic sell recommendations.

## ESPP Context
- ESPP contribution per paycheck: $100.00
- ESPP annualized contribution: $2,600.00
- ESPP purchase frequency: approximately_every_6_months
- ESPP discount: 15.0%
- ESPP holding weight: 41.67%
- ESPP income contribution: 18.18%
- Employer exposure notes: Treat employer-stock concentration separately and avoid automatic sell recommendations.

## Employer Stock / ESPP Context
- Employer stock ticker: HD
- Employer capital concentration: 41.67%
- Employer income concentration: 18.18%
- Concentration alert triggered: Yes
- HD (The Home Depot) is the user's employer stock and is acquired through an employer-sponsored Employee Stock Purchase Plan (ESPP).

## Contribution Context
- Regular biweekly contributions: $0.00
- ESPP contributions: $100.00
- Total annual contributions: $2,600.00
- Total Regular Biweekly Contributions: $0.00
- Estimated Annual HD ESPP Contributions: $2,600.00
- HD ESPP Purchase Frequency: approximately_every_6_months
- HD ESPP Discount: 15.0%
- Per-holding contribution breakdown:
- HD: regular $0.00, espp $100.00, special $0.00, total $100.00
- JEPI: regular $0.00, espp $0.00, special $0.00, total $0.00
- SDIV: regular $0.00, espp $0.00, special $0.00, total $0.00
- O: regular $0.00, espp $0.00, special $0.00, total $0.00
- REET: regular $0.00, espp $0.00, special $0.00, total $0.00

## Health Score
- diversification: 33.3
- concentration: 50.0
- income stability: 85.7
- dividend growth potential: 61.7
- total return potential: 38.8
- risk: 55.8
- data quality: 90.0

## Portfolio Health and Alerts
- Health score: 58.1/100
- Largest holding concentration: HD is 41.7% of portfolio.
- Employer stock concentration should be evaluated separately due to ESPP participation.
- High-yield alert: one or more holdings exceed 10% yield.
- Option-income ETF alert: income may be unstable.
- REIT distribution alert: REITs may reduce stability.

## Tracker Health Score Metadata
- data_quality_explanation: Data quality accounts for unresolved sectors, missing dividend inputs, and unresolved fund look-through.
- income_stability_explanation: Income stability penalizes REIT and option-income concentration and rewards broad ETF exposure.
- risk_explanation: Risk penalizes concentration and REIT-heavy exposure while recognizing bond allocation risk reduction.

## Data Completeness
- direct sector coverage: 100.00%
- fund look-through coverage: 100.01%
- unresolved exposure percentage: 0.00%
- overall resolved exposure: 100.01%

## Data Quality Note
- Dividend yield data is sourced dynamically from Yahoo Finance.
- Annualized income is estimated from the current Yahoo Finance dividend yield and current market price.
- Dividend yields may change over time and should not be interpreted as guaranteed future income.
- Dividend payment schedules and future distribution amounts are not guaranteed.
- Look-through sector exposure is incomplete where Yahoo Finance does not provide underlying fund sector weights. The affected fund holdings remain included in portfolio and asset-allocation totals; only their underlying sector decomposition is unresolved.

## Data Definitions
- Total Portfolio Value: Current market value of all holdings.
- Contribution Context: Includes regular biweekly contributions and special contributions per holding so the AI can evaluate whether reallocation or contribution changes are warranted.

## Contribution Context Table
```text
ticker regular_biweekly_contribution espp_biweekly_contribution special_contribution total_contribution contribution_type contribution_frequency purchase_frequency employer_stock espp_enabled      espp_purchase_frequency espp_discount_pct                                                                                                            contribution_note
    HD                           0.0                      100.0                  0.0              100.0              espp               biweekly           6_months           True         True approximately_every_6_months              15.0 HD is treated as an ESPP contribution made every biweekly paycheck, with purchases occurring approximately every six months.
  JEPI                           0.0                        0.0                  0.0                0.0           regular                    N/A                N/A          False        False                          N/A               N/A                                                                                                                             
  SDIV                           0.0                        0.0                  0.0                0.0           regular                    N/A                N/A          False        False                          N/A               N/A                                                                                                                             
     O                           0.0                        0.0                  0.0                0.0           regular                    N/A                N/A          False        False                          N/A               N/A                                                                                                                             
  REET                           0.0                        0.0                  0.0                0.0           regular                    N/A                N/A          False        False                          N/A               N/A                                                                                                                             
```

## Asset Allocation

## Asset Allocation Table
```text
asset_type market_value annual_income portfolio_weight_pct
      ETFs       1200.0          40.0                 50.0
     REITs        200.0           5.0             8.333333
    Stocks       1000.0          10.0            41.666667
```

## Exposure Interpretation
- Direct sector exposure represents individually held securities with a resolved sector classification.
- Look-through sector exposure represents estimated underlying sector exposure from funds using available Yahoo Finance fund-sector weights.
- Unresolved fund look-through exposure represents fund assets whose underlying sector weights could not be resolved.

## Direct Sector Exposure

## Direct Sector Exposure Table
```text
           sector market_value annual_income portfolio_weight_pct pct_of_resolved_direct_sector_exposure
Consumer Cyclical       1000.0          10.0            41.666667                              83.333333
      Real Estate        200.0           5.0             8.333333                              16.666667
```

## Direct Holdings by Sector

## Direct Holdings by Sector Table
```text
   classification         holdings market_value annual_income portfolio_weight_pct
             ETFs JEPI, REET, SDIV       1200.0          40.0                 50.0
Consumer Cyclical               HD       1000.0          10.0            41.666667
      Real Estate                O        200.0           5.0             8.333333
```

## Look-Through Sector Exposure

## Look-Through Sector Exposure Table
```text
                sector                          source_holdings market_value portfolio_weight_pct
           Real Estate JEPI (yahoo), REET (yahoo), SDIV (yahoo)       464.72            19.363333
    Financial Services               JEPI (yahoo), SDIV (yahoo)       113.91              4.74625
           Industrials               JEPI (yahoo), SDIV (yahoo)   113.259991             4.719166
            Technology               JEPI (yahoo), SDIV (yahoo)        96.67             4.027917
            Healthcare               JEPI (yahoo), SDIV (yahoo)    85.679995                 3.57
     Consumer Cyclical               JEPI (yahoo), SDIV (yahoo)        84.68             3.528333
                Energy               JEPI (yahoo), SDIV (yahoo)        67.28             2.803333
    Consumer Defensive               JEPI (yahoo), SDIV (yahoo)    64.949997              2.70625
Communication Services               JEPI (yahoo), SDIV (yahoo)    48.260001             2.010833
             Utilities               JEPI (yahoo), SDIV (yahoo)        31.89              1.32875
       Basic Materials               JEPI (yahoo), SDIV (yahoo)    28.829999              1.20125
```

## Unresolved Fund Look-Through Exposure

## Unresolved Fund Exposure Table
```text
(No data available)
```

## Income Allocation by Holding

## Income Allocation by Holding Table
```text
ticker annual_income market_value income_allocation_pct
  JEPI          20.0        500.0             36.363636
  REET          12.0        300.0             21.818182
    HD          10.0       1000.0             18.181818
  SDIV           8.0        400.0             14.545455
     O           5.0        200.0              9.090909
```

## Income Allocation by Asset Type

## Income Allocation by Asset Type Table
```text
asset_type annual_income market_value income_allocation_pct
      ETFs          40.0       1200.0             72.727273
     REITs           5.0        200.0              9.090909
    Stocks          10.0       1000.0             18.181818
```

## Income Quality Classification
```text
ticker asset_type dividend_yield_pct market_value  income_quality_classification
    HD      Stock                1.0       1000.0                Stable dividend
  JEPI        ETF               11.0        500.0 Option-income ETF distribution
  SDIV        ETF               12.0        400.0          Variable distribution
     O       REIT                5.0        200.0              REIT distribution
  REET        ETF                4.0        300.0          Variable distribution
```

## Economic Exposure Allocation

## Economic Exposure Table
```text
economic_exposure_category market_value annual_income portfolio_weight_pct
                  Equities       2200.0          50.0            91.666667
                     REITs        200.0           5.0             8.333333
```

## Dividend Data Metadata
- source: Yahoo Finance
- data_type: current_yield
- methodology: Current dividend yield used to estimate annualized income
- verification_status: Not verified

## Supplemental Fundamental Data
(No data available)

## Supplemental Yahoo Finance Fundamental Data
(No data available)

## Supplemental Earnings Data
(No data available)

## Supplemental Yahoo Finance Earnings and Estimates
(No data available)

## Supplemental Fund Data
(No data available)

## Supplemental Yahoo Finance Fund Data
(No data available)

## Supplemental Historical Price Data

## Supplemental Yahoo Finance Historical Price Data
- This section contains compact supplemental historical market-price context from Yahoo Finance for the holdings in this portfolio.
- Your existing portfolio history remains the authoritative source of truth for portfolio-level historical data; the Yahoo data is supplemental market-price context only.
- HD: supplemental Yahoo history 2024-08-06 to 2026-08-05; start 331.72, end 353.14; return 6.46%; 52w high 420.45, low 286.95; drawdown -28.84%; volatility 1.50%
- JEPI: supplemental Yahoo history 2024-08-06 to 2026-08-05; start 46.91, end 57.51; return 22.60%; 52w high 57.53, low 44.65; drawdown -13.26%; volatility 0.70%
- SDIV: supplemental Yahoo history unavailable.
- O: supplemental Yahoo history 2024-08-06 to 2026-08-05; start 52.81, end 62.70; return 18.74%; 52w high 66.87, low 47.17; drawdown -19.29%; volatility 1.10%
- REET: supplemental Yahoo history 2024-08-06 to 2026-08-05; start 22.46, end 28.38; return 26.35%; 52w high 29.06, low 20.07; drawdown -18.02%; volatility 0.91%

## Historical Context
- SDIV: start_price Unavailable, end_price Unavailable, return_pct Unavailable, 52w_high Unavailable, 52w_low Unavailable, drawdown Unavailable, volatility Unavailable

## Income Quality Analysis
- Income Quality Score: 82.0
- Distribution Stability Score: 87.1
- Yield Sustainability Score: 82.0
- Payout Risk Score: 72.0
- Monthly Dividend Weight Variance: 0.0000

### Income Type Classification Table
```text
ticker                      income_type stability_score yield_sustainability_score payout_risk_score                                                                                                          notes
    HD             Traditional Dividend           87.08                      100.0             100.0                                                                                                               
  JEPI   Option-Income ETF Distribution           87.08                       55.0              30.0 Yield exceeds 10% and may indicate elevated payout risk.; Option-income distributions can vary month to month.
  SDIV Variable / Unstable Distribution           87.08                       55.0              70.0                                                       Yield exceeds 10% and may indicate elevated payout risk.
     O                REIT Distribution           87.08                      100.0              80.0                                               REIT distributions can fluctuate with property income and rates.
  REET                REIT Distribution           87.08                      100.0              80.0                                               REIT distributions can fluctuate with property income and rates.
```

### Income Quality Alerts
- High-yield alert: one or more holdings exceed 10% yield.
- Option-income ETF alert: income may be unstable.
- REIT distribution alert: REITs may reduce stability.

### Income Quality Notes
- Income values are annualized estimates.
- High-yield ETFs may have unstable distributions.
- Option-income ETFs generate variable monthly payouts.
- REIT distributions may fluctuate based on property income.
- Dividend trends sourced from Yahoo Finance historical data.

## Top 5 Holdings
```text
ticker market_value annual_income
    HD       1000.0          10.0
  JEPI        500.0          20.0
  SDIV        400.0           8.0
  REET        300.0          12.0
     O        200.0           5.0
```

## Top 5 Income Contributors
```text
ticker annual_income market_value
  JEPI          20.0        500.0
  REET          12.0        300.0
    HD          10.0       1000.0
  SDIV           8.0        400.0
     O           5.0        200.0
```

## Holdings Data Table
```text
ticker shares estimated_annual_distribution_per_share annualized_income regular_biweekly_contribution espp_biweekly_contribution market_value asset_type             sector employer_stock espp_enabled      espp_purchase_frequency espp_discount_pct
    HD   10.0                                     0.0              10.0                           0.0                        0.0       1000.0      Stock  Consumer Cyclical           True         True approximately_every_6_months              15.0
  JEPI    5.0                                     0.0              20.0                           0.0                        0.0        500.0        ETF Financial Services          False        False                                            0.0
  SDIV    4.0                                     0.0               8.0                           0.0                        0.0        400.0        ETF        Real Estate          False        False                                            0.0
     O    2.0                                     0.0               5.0                           0.0                        0.0        200.0       REIT        Real Estate          False        False                                            0.0
  REET    1.0                                     0.0              12.0                           0.0                        0.0        300.0        ETF                             False        False                                            0.0
```

## Holdings Data (CSV)
```csv
ticker,shares,estimated_annual_distribution_per_share,annualized_income,regular_biweekly_contribution,espp_biweekly_contribution,employer_stock,espp_enabled,espp_purchase_frequency,espp_discount_pct
HD,10.0,0.0,10.0,0.0,0.0,True,True,approximately_every_6_months,15.0
JEPI,5.0,0.0,20.0,0.0,0.0,False,False,,0.0
SDIV,4.0,0.0,8.0,0.0,0.0,False,False,,0.0
O,2.0,0.0,5.0,0.0,0.0,False,False,,0.0
REET,1.0,0.0,12.0,0.0,0.0,False,False,,0.0

```

## Calculated Metrics Table
```text
ticker current_price market_value annual_income portfolio_yield_pct cost_basis unrealized_gain             sector asset_type dividend_yield_pct
    HD         100.0       1000.0          10.0                 1.0     1000.0             0.0  Consumer Cyclical      Stock                1.0
  JEPI         100.0        500.0          20.0                 4.0      500.0             0.0 Financial Services        ETF               11.0
  SDIV         100.0        400.0           8.0                 2.0      400.0             0.0        Real Estate        ETF               12.0
     O         100.0        200.0           5.0                 5.0      200.0             0.0        Real Estate       REIT                5.0
  REET         300.0        300.0          12.0                 4.0      300.0             0.0                           ETF                4.0
```

## Calculated Metrics (CSV)
```csv
ticker,current_price,market_value,annual_income,portfolio_yield_pct,cost_basis,unrealized_gain,sector,asset_type,dividend_yield_pct
HD,100.0,1000.0,10.0,1.0,1000.0,0.0,Consumer Cyclical,Stock,1.0
JEPI,100.0,500.0,20.0,4.0,500.0,0.0,Financial Services,ETF,11.0
SDIV,100.0,400.0,8.0,2.0,400.0,0.0,Real Estate,ETF,12.0
O,100.0,200.0,5.0,5.0,200.0,0.0,Real Estate,REIT,5.0
REET,300.0,300.0,12.0,4.0,300.0,0.0,,ETF,4.0

```

## Investor Profile
- Portfolio strategy: Dividend growth and income
- Primary goal: Build long-term wealth while increasing dividend income
- Risk tolerance: Moderate
- Investment horizon: 20.0 years

## AI Analysis Instructions
AI Analysis Instructions:
1. Treat holdings data as authoritative.
2. Use AI Context Summary for convenience.
3. Identify discrepancies.
4. Evaluate concentration by holding, sector, asset type, income source.
5. Treat employer stock separately.
6. Separate facts from projections.
7. Do not assume missing information.
8. Explain trade-offs.
9. Do not recommend buy/sell solely on yield.
10. Consider total return, diversification, risk, income sustainability.
11. Evaluate capital concentration separately from income concentration.
12. Evaluate income quality separately from headline yield.
13. Recognize ESPP discount and employer exposure.

## Notes
- Income values in this export are annualized estimates based on the current dividend yield.
- The export does not claim a verified payment schedule or known calendar of dividends.

---

## AI Context Summary
- Portfolio Value: $2,400.00
- Estimated Annual Income: $55.00

## Portfolio Health
- Health score: 58.1/100

## Alerts
- Alerts are summarized in the primary export sections above.

## ESPP Context
- ESPP ticker: HD
- biweekly contribution: $100.00
- annualized contribution: $2,600.00
- purchase discount: 15.0%
- purchase frequency: approximately_every_6_months

## Contribution Context
- Regular biweekly: $280.00
- ESPP biweekly: $100.00
- Combined annual contributions: $2,600.00

## Asset Allocation
```json
{
  "asset_type": [
    {
      "asset_type": "ETF",
      "market_value": 1200.0,
      "portfolio_weight_pct": 50.0
    },
    {
      "asset_type": "REIT",
      "market_value": 200.0,
      "portfolio_weight_pct": 8.333333333333332
    },
    {
      "asset_type": "Stock",
      "market_value": 1000.0,
      "portfolio_weight_pct": 41.66666666666667
    }
  ],
  "economic_exposure": "Economic Exposure Category Market_Value  Annual_Income Portfolio_Weight\n                  Equities    $2,200.00           50.0           91.67%\n                     REITs      $200.00            5.0            8.33%"
}
```

## Sector Exposure
```json
{
  "direct_sector_exposure": [
    {
      "Sector": "Consumer Cyclical",
      "Market_Value": 1000.0,
      "Annual_Income": 10.0,
      "Portfolio_Weight_Pct": 41.66666666666667,
      "Pct_of_Resolved_Direct_Sector_Exposure": 83.33333333333334
    },
    {
      "Sector": "Real Estate",
      "Market_Value": 200.0,
      "Annual_Income": 5.0,
      "Portfolio_Weight_Pct": 8.333333333333332,
      "Pct_of_Resolved_Direct_Sector_Exposure": 16.666666666666664
    }
  ],
  "look_through_exposure": [
    {
      "Sector": "Real Estate",
      "Source_Holdings": "JEPI (yahoo), REET (yahoo), SDIV (yahoo)",
      "Market_Value": 464.72,
      "Portfolio_Weight": 19.363333333333337,
      "Source": "yahoo"
    },
    {
      "Sector": "Financial Services",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 113.91,
      "Portfolio_Weight": 4.74625,
      "Source": "yahoo"
    },
    {
      "Sector": "Industrials",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 113.259991,
      "Portfolio_Weight": 4.719166291666667,
      "Source": "yahoo"
    },
    {
      "Sector": "Technology",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 96.67,
      "Portfolio_Weight": 4.027916666666666,
      "Source": "yahoo"
    },
    {
      "Sector": "Healthcare",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 85.67999499999999,
      "Portfolio_Weight": 3.569999791666666,
      "Source": "yahoo"
    },
    {
      "Sector": "Consumer Cyclical",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 84.67999999999999,
      "Portfolio_Weight": 3.5283333333333333,
      "Source": "yahoo"
    },
    {
      "Sector": "Energy",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 67.28,
      "Portfolio_Weight": 2.8033333333333332,
      "Source": "yahoo"
    },
    {
      "Sector": "Consumer Defensive",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 64.9499975,
      "Portfolio_Weight": 2.706249895833333,
      "Source": "yahoo"
    },
    {
      "Sector": "Communication Services",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 48.2600012,
      "Portfolio_Weight": 2.0108333833333334,
      "Source": "yahoo"
    },
    {
      "Sector": "Utilities",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 31.8899998,
      "Portfolio_Weight": 1.3287499916666665,
      "Source": "yahoo"
    },
    {
      "Sector": "Basic Materials",
      "Source_Holdings": "JEPI (yahoo), SDIV (yahoo)",
      "Market_Value": 28.8299995,
      "Portfolio_Weight": 1.2012499791666666,
      "Source": "yahoo"
    }
  ],
  "unresolved_exposure": {
    "unresolved_fund_exposure": 0.0,
    "resolved_lookthrough_value": 1200.129984,
    "fund_total_value": 1200.0
  },
  "combined_exposure_summary": {
    "Consumer Cyclical": 45.19255237136357,
    "Real Estate": 27.695166696438385,
    "Financial Services": 4.745992957021447,
    "Industrials": 4.718910715462317,
    "Technology": 4.027698526514469,
    "Healthcare": 3.569806450949282,
    "Energy": 2.8031815130225874,
    "Consumer Defensive": 2.7061033332768023,
    "Communication Services": 2.010724482495361,
    "Utilities": 1.3286780304645363,
    "Basic Materials": 1.2011849229912372
  },
  "exposure_completeness_pct": 100.00541600000001
}
```

## Income Allocation
```json
{
  "by_holding": [
    {
      "ticker": "HD",
      "annual_income": 10.0,
      "market_value": 1000.0,
      "income_allocation_pct": 18.181818181818183
    },
    {
      "ticker": "JEPI",
      "annual_income": 20.0,
      "market_value": 500.0,
      "income_allocation_pct": 36.36363636363637
    },
    {
      "ticker": "SDIV",
      "annual_income": 8.0,
      "market_value": 400.0,
      "income_allocation_pct": 14.545454545454545
    },
    {
      "ticker": "O",
      "annual_income": 5.0,
      "market_value": 200.0,
      "income_allocation_pct": 9.090909090909092
    },
    {
      "ticker": "REET",
      "annual_income": 12.0,
      "market_value": 300.0,
      "income_allocation_pct": 21.818181818181817
    }
  ],
  "by_asset_type": [
    {
      "asset_type": "ETF",
      "annual_income": 40.0,
      "income_allocation_pct": 72.72727272727273
    },
    {
      "asset_type": "REIT",
      "annual_income": 5.0,
      "income_allocation_pct": 9.090909090909092
    },
    {
      "asset_type": "Stock",
      "annual_income": 10.0,
      "income_allocation_pct": 18.181818181818183
    }
  ],
  "quality": [
    {
      "ticker": "HD",
      "income_type_classification": "traditional dividend",
      "stability_rating": 80.0,
      "growth_potential_rating": 75.0,
      "risk_notes": []
    },
    {
      "ticker": "JEPI",
      "income_type_classification": "option-income ETF distribution",
      "stability_rating": 45.0,
      "growth_potential_rating": 50.0,
      "risk_notes": [
        "Option-income ETFs can have variable payouts.",
        "High yield may indicate elevated income sustainability risk."
      ]
    },
    {
      "ticker": "SDIV",
      "income_type_classification": "traditional dividend",
      "stability_rating": 65.0,
      "growth_potential_rating": 40.0,
      "risk_notes": [
        "Lower expected dividend-growth profile.",
        "High yield may indicate elevated income sustainability risk."
      ]
    },
    {
      "ticker": "O",
      "income_type_classification": "REIT distribution",
      "stability_rating": 70.0,
      "growth_potential_rating": 60.0,
      "risk_notes": [
        "REIT distributions can be sensitive to rate cycles."
      ]
    },
    {
      "ticker": "REET",
      "income_type_classification": "traditional dividend",
      "stability_rating": 80.0,
      "growth_potential_rating": 60.0,
      "risk_notes": []
    }
  ]
}
```

## Economic Exposure
```json
"Economic Exposure Category Market_Value  Annual_Income Portfolio_Weight\n                  Equities    $2,200.00           50.0           91.67%\n                     REITs      $200.00            5.0            8.33%"
```

## Historical Context
```json
{
  "by_ticker": [
    {
      "ticker": "SDIV",
      "change_3m_pct": null,
      "change_1y_pct": null,
      "volatility": null,
      "drawdown": null,
      "supplemental_history_summary": "Historical context unavailable."
    }
  ]
}
```

## Holdings Table
```csv
ticker,shares,estimated_annual_distribution_per_share,annualized_income,regular_biweekly_contribution,espp_biweekly_contribution,market_value,asset_type,sector,employer_stock,espp_enabled,espp_purchase_frequency,espp_discount_pct
HD,10.0,0.0,10.0,0.0,0.0,1000.0,Stock,Consumer Cyclical,True,True,approximately_every_6_months,15.0
JEPI,5.0,0.0,20.0,0.0,0.0,500.0,ETF,Financial Services,False,False,,0.0
SDIV,4.0,0.0,8.0,0.0,0.0,400.0,ETF,Real Estate,False,False,,0.0
O,2.0,0.0,5.0,0.0,0.0,200.0,REIT,Real Estate,False,False,,0.0
REET,1.0,0.0,12.0,0.0,0.0,300.0,ETF,,False,False,,0.0

```

## Calculated Metrics Table
```csv
ticker,shares,estimated_annual_distribution_per_share,annualized_income,regular_biweekly_contribution,espp_biweekly_contribution,market_value,asset_type,sector,employer_stock,espp_enabled,espp_purchase_frequency,espp_discount_pct
HD,10.0,0.0,10.0,0.0,0.0,1000.0,Stock,Consumer Cyclical,True,True,approximately_every_6_months,15.0
JEPI,5.0,0.0,20.0,0.0,0.0,500.0,ETF,Financial Services,False,False,,0.0
SDIV,4.0,0.0,8.0,0.0,0.0,400.0,ETF,Real Estate,False,False,,0.0
O,2.0,0.0,5.0,0.0,0.0,200.0,REIT,Real Estate,False,False,,0.0
REET,1.0,0.0,12.0,0.0,0.0,300.0,ETF,,False,False,,0.0

```

## Notes
- Export generated from unified compute_full_portfolio_snapshot output.

Export Integrity Check:
- Snapshot completeness: 100%
- Sector resolution: 100.0%
- Income resolution: 100.0%
- Historical coverage: 20.0%