"""Stable categorical values for reference-data contracts."""

from enum import StrEnum


class AssetType(StrEnum):
    """Supported high-level financial instrument classes."""

    EQUITY = "EQUITY"
    ETF = "ETF"
    INDEX = "INDEX"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FOREX = "FOREX"
    CRYPTO = "CRYPTO"
    OTHER = "OTHER"


class EntityType(StrEnum):
    """Kinds of canonical entities that external events may reference."""

    COMPANY = "COMPANY"
    PERSON = "PERSON"
    COUNTRY = "COUNTRY"
    GOVERNMENT = "GOVERNMENT"
    CENTRAL_BANK = "CENTRAL_BANK"
    REGULATOR = "REGULATOR"
    SECTOR = "SECTOR"
    INDUSTRY = "INDUSTRY"
    COMMODITY = "COMMODITY"
    CURRENCY = "CURRENCY"
    ECONOMIC_INDICATOR = "ECONOMIC_INDICATOR"
    GEOGRAPHIC_REGION = "GEOGRAPHIC_REGION"
    OTHER = "OTHER"
