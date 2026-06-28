import pandas as pd

def build_candidate_from_row(row: pd.Series) -> dict:
    """
    Convert a pandas DataFrame row (from the ranker) into a candidate dictionary.
    This ensures every strategy uses the SAME logic for extracting fields like context_score.
    """
    # Safely extract context_score, defaulting to 0.0 only if truly missing or NaN
    context = 0.0
    if "context_score" in row.index and pd.notna(row["context_score"]):
        context = float(row["context_score"])
    
    return {
        # --- Core fields ---
        "scan_date": str(row.get("scan_date", "")),
        "ticker": str(row.get("ticker", "")),
        "company_name": str(row.get("company_name", "")),
        "industry": str(row.get("industry", "")),
        "price": float(row.get("price", 0.0)),
        "entry_price": float(row.get("entry_price", 0.0)),
        "stop_loss": float(row.get("stop_loss", 0.0)),
        "exit_price": float(row.get("exit_price", 0.0)),
        "upside_pct": float(row.get("upside_pct", 0.0)),
        "risk_reward": float(row.get("risk_reward", 0.0)),
        "current_rsi": float(row.get("current_rsi", 0.0)),
        "volume_ratio": float(row.get("volume_ratio", 0.0)),
        "adx_value": float(row.get("adx_value", 0.0)),
        "macd_histogram": float(row.get("macd_histogram", 0.0)),
        "rsi_min_10d": float(row.get("rsi_min_10d", 0.0)),
        "ema20": float(row.get("ema20", 0.0)),
        "narrative": str(row.get("narrative", "")),
        "strategy": str(row.get("strategy", "")),
        "strategy_name": str(row.get("strategy_name", "")),
        "tier_label": str(row.get("tier_label", "Unrated")),
        "composite_score": float(row.get("composite_score", 0.0)),
        "quality_score": float(row.get("quality_score", 0.0)),
        "score": float(row.get("score", 0.0)),
        "regime": str(row.get("regime", "bull")),
        "is_fallback": bool(row.get("is_fallback", False)),
        # --- Metrics ---
        "past_win_rate": float(row.get("win_rate", row.get("past_win_rate", 0.0))),
        "expectancy_pct": float(row.get("expectancy_pct", 0.0)),
        "total_trades": int(row.get("total_trades", 0)),
        "wins": int(row.get("wins", 0)),
        "losses": int(row.get("losses", 0)),
        # --- Targets ---
        "target_1": float(row.get("target_1", 0.0)) if pd.notna(row.get("target_1")) else None,
        "target_2": float(row.get("target_2", 0.0)) if pd.notna(row.get("target_2")) else None,
        "target_3": float(row.get("target_3", 0.0)) if pd.notna(row.get("target_3")) else None,
        "target_1_pct": float(row.get("target_1_pct", 0.0)),
        "target_2_pct": float(row.get("target_2_pct", 0.0)),
        "target_3_pct": float(row.get("target_3_pct", 0.0)),
        "weighted_rr": float(row.get("weighted_rr", 0.0)),
        "position_sizing": str(row.get("position_sizing", "50/30/20")),
        # --- The critical fix: context_score ---
        "context_score": context,
        # --- Guardrails ---
        "is_blocked": False,
        "blocked_reason": None,
    }
