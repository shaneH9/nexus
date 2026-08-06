"""Package-level smoke tests."""

import sra_nexus


def test_package_exposes_version() -> None:
    """The source-layout package should be importable after installation."""
    assert sra_nexus.__version__ == "0.1.0"
