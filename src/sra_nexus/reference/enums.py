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


class EntityRelationshipType(StrEnum):
    """Small structural taxonomy for the initial economic relationship graph."""

    OWNS_OR_ISSUES = "OWNS_OR_ISSUES"
    COMPETITOR = "COMPETITOR"
    CUSTOMER_OF = "CUSTOMER_OF"
    SUPPLIER_TO = "SUPPLIER_TO"
    MEMBER_OF_SECTOR = "MEMBER_OF_SECTOR"
    MEMBER_OF_INDUSTRY = "MEMBER_OF_INDUSTRY"
    LOCATED_IN = "LOCATED_IN"
    OPERATES_IN = "OPERATES_IN"
    EXPOSED_TO_COMMODITY = "EXPOSED_TO_COMMODITY"
    EXPOSED_TO_CURRENCY = "EXPOSED_TO_CURRENCY"
    REGULATED_BY = "REGULATED_BY"
    MACRO_SENSITIVE_TO = "MACRO_SENSITIVE_TO"
    OTHER = "OTHER"


class RelationshipDirection(StrEnum):
    """Whether an entity edge is directed or explicitly symmetric."""

    DIRECTED = "DIRECTED"
    SYMMETRIC = "SYMMETRIC"


class EntityInstrumentRelationType(StrEnum):
    """Supported ways a canonical entity may map to a tradable instrument."""

    PRIMARY_EQUITY = "PRIMARY_EQUITY"
    SECONDARY_EQUITY = "SECONDARY_EQUITY"
    ETF = "ETF"
    ADR = "ADR"
    OTHER = "OTHER"


class ReferenceResolutionStatus(StrEnum):
    """Deterministic outcome of a reference-data lookup."""

    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_FOUND = "NOT_FOUND"
