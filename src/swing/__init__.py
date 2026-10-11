"""
Swing Signals Module
====================
Export core universe, events, rules, exits, book, and evaluate functionality.
"""

from src.swing.universe import (
    get_u1_universe,
    get_u2_members_on_date,
    check_liquid,
    compute_breadth,
    select_u1_top500,
)
from src.swing.events import (
    has_earnings_within_5_sessions,
    is_event_locked,
    has_recent_neg8k,
    check_catalyst_gap_8k,
    is_routine_purchase,
    detect_insider_clusters_on_date,
)
from src.swing.rules import (
    SLEEVES,
    SLEEVE_PRIORITY,
    SwingSignal,
    check_g0,
    generate_sleeve_t_signal,
    generate_sleeve_e_signal,
    generate_sleeve_n_signal,
    generate_sleeve_p_signal,
    generate_sleeve_i_signal,
)
from src.swing.exits import (
    SwingTrade,
    simulate_swing_trade,
    max_holding_sessions_for_sleeve,
)
from src.swing.book import SwingBook
from src.swing.evaluate import (
    block_bootstrap_ci_10k,
    paired_block_diff_10k,
    evaluate_sleeve_trades,
    decide_sleeve_status,
    monte_carlo_trade_shuffle,
)
