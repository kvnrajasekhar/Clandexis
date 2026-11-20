import pytest
from pydantic import ValidationError
from app.schemas.transaction import TransactionCandidate
from app.services.interpreter import GeminiExtraction


def test_new_extractions_require_current_version_but_snapshots_remain_readable():
    raw = {"transaction_type": "sale", "schema_version": "1.0"}
    assert TransactionCandidate.model_validate(raw).schema_version == "1.0"
    with pytest.raises(ValidationError):
        GeminiExtraction.model_validate({"transactions": [raw]})
    raw["schema_version"] = "1.1"
    assert GeminiExtraction.model_validate({"transactions": [raw]}).transactions[0].schema_version == "1.1"
