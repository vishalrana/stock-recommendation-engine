"""
Position Sizer Module
======================
Cash-constrained Half-Kelly position sizing utilizing honest weighted risk-to-reward ratios.
"""

from src.ranker import calculate_normalized_sizing

__all__ = ["calculate_normalized_sizing"]
