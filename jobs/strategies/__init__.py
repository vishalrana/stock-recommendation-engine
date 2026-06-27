from .pullback import PullbackRecoveryStrategy
from .trend_following import TrendFollowingStrategy
from .mean_reversion import MeanReversionStrategy
from .sector_rotation import SectorRotationStrategy
from .pead import PEADStrategy
from .week_52_high import Week52HighStrategy
from .cross_sectional import CrossSectionalMomentumStrategy

STRATEGIES = [
    PullbackRecoveryStrategy(),
    TrendFollowingStrategy(),
    MeanReversionStrategy(),
    SectorRotationStrategy(),
    PEADStrategy(),
    Week52HighStrategy(),
    CrossSectionalMomentumStrategy(),
]
