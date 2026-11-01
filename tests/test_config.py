from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_default_max_files_is_500():
    assert Settings.model_fields["max_files"].default == 500


def test_max_files_is_capped_at_500():
    with pytest.raises(ValidationError):
        Settings(max_files=501)
