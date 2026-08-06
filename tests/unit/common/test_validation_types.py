"""Tests for reusable shared validation types."""

import pytest
from pydantic import ValidationError

from sra_nexus.common.models import ContractModel, NonBlankStr


class _TextContract(ContractModel):
    """Minimal contract used to exercise shared text validation."""

    value: NonBlankStr


def test_non_blank_string_trims_whitespace_before_storage() -> None:
    """Shared text fields should store their meaningful trimmed value."""
    contract = _TextContract(value="  Reuters  ")

    assert contract.value == "Reuters"


def test_non_blank_string_rejects_whitespace_only_value() -> None:
    """Whitespace alone should not satisfy a required text contract."""
    with pytest.raises(ValidationError, match="must not be blank"):
        _TextContract(value="   ")
