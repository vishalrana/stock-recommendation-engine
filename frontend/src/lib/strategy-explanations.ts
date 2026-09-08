/**
 * Strategy Presentation Explanations
 * ==================================
 * Deterministic frontend presentation dictionary that translates strategy metadata
 * into clear, concise (1-2 sentences) human-readable rationale for the "Why this idea?" section.
 *
 * NOTE: This is a pure presentation helper. It must NEVER calculate, alter, or
 * reinterpret any strategy signal or quant logic.
 */

const STRATEGY_EXPLANATIONS: Record<string, string> = {
  '52-Week High': 'Trading near recent 52-week highs with supporting momentum and trend conditions.',
  'Trend Following': 'Showing sustained price strength with supportive moving average trend conditions.',
  'Pullback Recovery': 'Recovering from a technical pullback to key support with improving momentum.',
  'Mean Reversion': 'Oversold within a primary uptrend and showing technical stabilization signals.',
  'Cross-Sectional Momentum': 'Outperforming the broader market with strong relative strength and volume support.',
  'Sector Rotation': 'Sector-leading momentum setup benefiting from institutional rotation.',
  'PEAD': 'Displaying positive post-earnings momentum and technical drift.',
};

/**
 * Returns a 1-2 sentence human-readable explanation for why a stock idea was recommended.
 * If a narrative is already provided on the recommendation, it takes precedence if valid.
 */
export function getStrategyExplanation(
  strategyName?: string | null,
  customNarrative?: string | null
): string {
  if (customNarrative && customNarrative.trim().length > 0) {
    return customNarrative.trim();
  }

  if (!strategyName) {
    return 'Technical setup meets systematic criteria with favorable trend and momentum parameters.';
  }

  // Exact match
  if (STRATEGY_EXPLANATIONS[strategyName]) {
    return STRATEGY_EXPLANATIONS[strategyName];
  }

  // Case-insensitive / partial match
  const lower = strategyName.toLowerCase();
  for (const [key, explanation] of Object.entries(STRATEGY_EXPLANATIONS)) {
    if (lower.includes(key.toLowerCase()) || key.toLowerCase().includes(lower)) {
      return explanation;
    }
  }

  return 'Showing constructive technical structure with supportive trend and risk/reward parameters.';
}
