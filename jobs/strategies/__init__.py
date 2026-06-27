from .pullback import PullbackRecoveryStrategy
from .trend_following import TrendFollowingStrategy

STRATEGIES = [
    PullbackRecoveryStrategy(),
    TrendFollowingStrategy(),
]
