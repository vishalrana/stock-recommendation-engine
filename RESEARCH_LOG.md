\# Research Log



\## Experiment 1



Volume Threshold Sensitivity



Universe:



100 S\&P 500 Stocks



Results:



| Volume | Signals | Win Rate | Expectancy |

| ------ | ------: | -------: | ---------: |

| 1.5x   |      73 |   28.57% |      1.72% |

| 1.2x   |     193 |   31.87% |      2.40% |

| 1.0x   |     389 |   31.76% |      2.57% |



Conclusion:



Volume > 1.5x is too restrictive.



Candidate Threshold:



Volume > 1.0x



\---



\## Experiment 2



Filter Ablation Study



Universe:



100 S\&P 500 Stocks



Results:



| Strategy         | Signals | Win Rate | Expectancy |

| ---------------- | ------: | -------: | ---------: |

| Baseline         |     390 |   32.08% |      2.63% |

| No Volume        |    1230 |   33.99% |      2.96% |

| No Current RSI   |     453 |   33.79% |      3.63% |

| No RSI Pullback  |    1302 |   32.81% |      2.03% |

| No Pattern       |    2494 |   36.25% |      3.55% |

| Price Trend Only |   36301 |   32.85% |      3.02% |



Conclusions:



1\. Pattern filter removes many opportunities.

2\. Pattern filter did not improve win rate.

3\. Pattern filter did not improve expectancy.

4\. RSI Pullback appears valuable.

5\. Volume threshold should be reduced.



\---



\## Current Hypothesis



Candidate Strategy Version 1.1



Rules:



\* Price > 50 DMA > 200 DMA

\* RSI Pullback

\* Current RSI Recovery

\* Volume > 1.0x

\* No Pattern Filter

\* 3R Target



Status:



Awaiting full S\&P 500 validation.



\---



\## Research Rules



1\. Use cached Parquet data.

2\. Avoid redownloads.

3\. Do not optimize solely for win rate.

4\. Focus on expectancy and sample size.

5\. Validate all changes with historical testing.



