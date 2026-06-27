from .pullback import PullbackRecoveryStrategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy

STRATEGIES = [
    PullbackRecoveryStrategy(),
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
]
