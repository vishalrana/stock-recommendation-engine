export interface Recommendation {
  scan_date: string;
  ticker: string;
  company_name: string | null;
  industry: string | null;
  price: number | null;
  entry_price: number;
  stop_loss: number;
  exit_price: number;
  upside_pct: number | null;
  risk_reward: number | null;
  current_rsi: number | null;
  volume_ratio: number | null;
  score: number | null;
  regime: string | null;
  past_win_rate: number;
  expectancy_pct: number;
  historical_signals: number;
  historical_wins: number;
  historical_losses: number;
  median_holding_days: number;
  composite_score: number | null;
  tier_label: string | null;
  adx_value: number | null;
  macd_histogram: number | null;
  rsi_min_10d: number | null;
  ema20: number | null;
  is_fallback: boolean | null;
  is_momentum_exception: boolean | null;
  distance_from_high_pct: number | null;
  target_1?: number;
  target_2?: number;
  target_3?: number;
  target_1_pct?: number;
  target_2_pct?: number;
  target_3_pct?: number;
  weighted_rr?: number;
  position_sizing?: string;
}

export interface ScanLog {
  scan_date: string;
  tickers_scanned: number;
  signals_generated: number;
  signals_qualified?: number;
  signals_recommended?: number;
  scan_duration_secs: number;
  status: string;
  error_message: string | null;
  regime?: string;
}
