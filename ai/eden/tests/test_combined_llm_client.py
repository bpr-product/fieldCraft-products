"""Unit tests for the combined (LangGraph + Agents SDK) backend. It
delegates entirely to AgentsSDKLLMClient.understand_message (which handles
structured output AND its own hard-failure retry) instead of reimplementing
run+parse logic. The graph is a straight-through single node -- no redundant
graph-level retry on top of the SDK client's own. AgentsSDKLLMClient is mocked.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def combined_client(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9000/v1")
    with patch("source.utils.agents_sdk_llm_client.AgentsSDKLLMClient.__init__", return_value=None):
        from source.utils.combined_llm_client import CombinedLLMClient
        client = CombinedLLMClient()
    client._sdk_client = MagicMock()
    return client


def test_understand_message_delegates_to_sdk_client(combined_client):
    combined_client._sdk_client.understand_message.return_value = {
        "intent": "navigate", "route_id": "invoice_list", "confidence": 0.9,
        "extracted_entity": None, "insight": "Here are the invoices.",
    }
    result = combined_client.understand_message("some context", "take me to invoices", "tn59395620")
    assert result["route_id"] == "invoice_list"
    # tenant_id threaded through to the SDK client (for its tenant-scoped tool)
    combined_client._sdk_client.understand_message.assert_called_once_with(
        "some context", "take me to invoices", "tn59395620"
    )


def test_no_redundant_graph_level_retry(combined_client):
    # Resiliency lives in AgentsSDKLLMClient.understand_message (its own
    # internal retry), NOT here -- so a None result is delegated exactly once
    # by the graph, never re-run at this level (which used to cause up to 4
    # total agent runs).
    combined_client._sdk_client.understand_message.return_value = None
    result = combined_client.understand_message("some context", "take me to invoices")
    assert result is None
    assert combined_client._sdk_client.understand_message.call_count == 1
