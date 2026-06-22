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
  past_win_rate: number;
  expectancy_pct: number;
  historical_signals: number;
  historical_wins: number;
  historical_losses: number;
  median_holding_days: number;
}
