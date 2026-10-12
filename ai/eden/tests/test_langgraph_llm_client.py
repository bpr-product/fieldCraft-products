"""Unit tests for the LangGraph backend's collapsed structured-output graph
(classify -> retry-on-hard-failure -> END, no more parse/strict-retry
nodes). The langchain ChatOpenAI structured-output call is mocked — no
live LLM server needed to prove the graph wiring and return contract.
"""
from unittest.mock import MagicMock, patch

import pytest

from source.model.navigation_models import NavigationUnderstanding


@pytest.fixture
def lg_client(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9000/v1")
    from source.utils.langgraph_llm_client import LangGraphLLMClient
    return LangGraphLLMClient()


def test_understand_message_returns_validated_dict(lg_client):
    # _structured_llm is a pydantic-backed Runnable that doesn't tolerate
    # patch.object's attribute delete/restore cleanly — swap the whole
    # object for a plain mock instead.
    understanding = NavigationUnderstanding(
        intent="navigate", route_id="invoice_list", confidence=0.9,
        extracted_entity=None, insight="Here are the invoices.",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = understanding
    lg_client._structured_llm = mock_llm

    result = lg_client.understand_message("some context", "take me to invoices")
    mock_llm.invoke.assert_called_once()
    assert result == understanding.model_dump()


def test_hard_failure_retries_once_then_gives_up(lg_client):
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = Exception("timeout")
    lg_client._structured_llm = mock_llm

    result = lg_client.understand_message("some context", "take me to invoices")
    assert result is None
    assert mock_llm.invoke.call_count == 2


def test_graph_has_no_parse_node_left(lg_client):
    # The old classify->parse->retry shape collapsed to classify->retry —
    # structured output removed the need for a separate parsing node.
    nodes = set(lg_client._graph.get_graph().nodes)
    assert "parse" not in nodes
    assert "classify" in nodes
