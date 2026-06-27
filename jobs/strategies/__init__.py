from .pullback import PullbackRecoveryStrategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy
from .sector_rotation import SectorRotationStrategy

STRATEGIES = [
    PullbackRecoveryStrategy(),
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    SectorRotationStrategy(),
]
