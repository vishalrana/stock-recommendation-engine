"""
Abstract Universe Provider Base
===============================
Defines the interface for security universe discovery across different markets.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from src.universe.models import SecurityRecord


class UniverseProvider(ABC):
    """Abstract base class for security universe providers."""

    @property
    @abstractmethod
    def market(self) -> str:
        """Market identifier (e.g. 'US', 'IN')."""
        pass

    @abstractmethod
    def get_universe(self, as_of_date: Optional[str] = None) -> List[SecurityRecord]:
        """
        Return the list of structured security records for the universe.
        If as_of_date is provided, point-in-time filters may be applied.
        """
        pass

    @abstractmethod
    def get_tickers(self, as_of_date: Optional[str] = None) -> List[str]:
        """
        Return the list of canonical ticker symbols in the universe.
        """
        pass

    @abstractmethod
    def get_security_record(self, ticker: str) -> Optional[SecurityRecord]:
        """
        Retrieve the structured SecurityRecord for a single ticker symbol, or None if not found.
        """
        pass
