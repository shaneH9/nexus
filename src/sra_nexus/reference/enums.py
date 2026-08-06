"""Stable categorical values for reference-data contracts."""

from enum import StrEnum


class AssetType(StrEnum):
    """Supported high-level financial instrument classes."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX_SPOT = "FX_SPOT"
    FIXED_INCOME = "FIXED_INCOME"
    RATE = "RATE"
    COMMODITY = "COMMODITY"
    CRYPTO = "CRYPTO"


class EntityType(StrEnum):
    """Kinds of canonical entities that external events may reference."""

    COMPANY = "COMPANY"
    GOVERNMENT = "GOVERNMENT"
    COUNTRY = "COUNTRY"
    PERSON = "PERSON"
    COMMODITY = "COMMODITY"
    INDUSTRY = "INDUSTRY"
    SECTOR = "SECTOR"
    CENTRAL_BANK = "CENTRAL_BANK"
    ECONOMIC_INDICATOR = "ECONOMIC_INDICATOR"
    GEOGRAPHIC_REGION = "GEOGRAPHIC_REGION"
