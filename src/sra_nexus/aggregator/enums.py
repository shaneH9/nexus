"""Stable categorical values for news and event contracts."""

from enum import StrEnum


class NewsSourceType(StrEnum):
    """Kinds of providers from which raw news may be observed."""

    FINANCIAL_NEWS = "FINANCIAL_NEWS"
    WIRE = "WIRE"
    SEC = "SEC"
    COMPANY_RELEASE = "COMPANY_RELEASE"
    MACRO_CALENDAR = "MACRO_CALENDAR"
    CENTRAL_BANK = "CENTRAL_BANK"
    GOVERNMENT = "GOVERNMENT"
    GLOBAL_NEWS = "GLOBAL_NEWS"
    SOCIAL = "SOCIAL"
    SPECULATIVE = "SPECULATIVE"
    OTHER = "OTHER"


class EventType(StrEnum):
    """Stable top-level canonical event taxonomy."""

    COMPANY = "COMPANY"
    SECTOR = "SECTOR"
    MACRO = "MACRO"
    GEOPOLITICAL = "GEOPOLITICAL"
    REGULATORY = "REGULATORY"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    SYSTEMIC = "SYSTEMIC"
    COMMODITY = "COMMODITY"
    CURRENCY = "CURRENCY"
    RATE = "RATE"


class EventState(StrEnum):
    """Lifecycle states for a canonical event."""

    NEW = "NEW"
    DEVELOPING = "DEVELOPING"
    CONFIRMED = "CONFIRMED"
    UPDATED = "UPDATED"
    RESOLVED = "RESOLVED"
    RETRACTED = "RETRACTED"


class ExposureRelationType(StrEnum):
    """How an event is related to an exposed instrument."""

    DIRECT_COMPANY = "DIRECT_COMPANY"
    COMPETITOR = "COMPETITOR"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    SECTOR = "SECTOR"
    INDUSTRY = "INDUSTRY"
    COUNTRY = "COUNTRY"
    COMMODITY = "COMMODITY"
    MACRO = "MACRO"
    REGULATORY = "REGULATORY"
    OTHER = "OTHER"
