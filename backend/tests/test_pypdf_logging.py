"""pypdf's per-font warnings stay out of the log."""

import logging

from app import slides  # noqa: F401 (importing it sets the level)


def test_pypdf_logger_only_reports_errors():
    assert logging.getLogger("pypdf").level == logging.ERROR
