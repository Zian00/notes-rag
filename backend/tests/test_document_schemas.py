import pytest
from app.schemas.document import SearchRequest
from pydantic import ValidationError


def test_search_request_defaults_and_bounds():
    assert SearchRequest(query="hi").top_k == 5
    with pytest.raises(ValidationError):
        SearchRequest(query="hi", top_k=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="", top_k=3)
    with pytest.raises(ValidationError):
        SearchRequest(query="hi", top_k=999)
