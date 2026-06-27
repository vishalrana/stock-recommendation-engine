from .pullback import PullbackRecoveryStrategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy
from .sector_rotation import SectorRotationStrategy
from .pead import PEADStrategy

STRATEGIES = [
    PullbackRecoveryStrategy(),
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    SectorRotationStrategy(),
    PEADStrategy(),
]
