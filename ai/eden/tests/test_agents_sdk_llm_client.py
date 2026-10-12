"""Unit tests for the Agents SDK backend's structured-output path and the
removal of the ThreadPoolExecutor isolation hack (see
agents_sdk_llm_client._run_sync_isolated's docstring — it now calls
Runner.run_sync directly because navigation_api.py's handlers are plain
`def`, so FastAPI already runs them in a thread with no event loop of its
own). Runner.run_sync itself is mocked throughout — no live LLM server
needed to prove this code path.
"""
from unittest.mock import MagicMock, patch

import pytest

from source.model.navigation_models import NavigationUnderstanding


@pytest.fixture
def sdk_client(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:9000/v1")
    from source.utils.agents_sdk_llm_client import AgentsSDKLLMClient
    return AgentsSDKLLMClient()


def _run_result(final_output):
    return MagicMock(final_output=final_output)


class TestStructuredOutput:
    def test_understand_message_returns_validated_dict(self, sdk_client):
        understanding = NavigationUnderstanding(
            intent="navigate", route_id="gantt_charts", confidence=0.9,
            extracted_entity="Henderson Residence Remodel", insight="Back to it.",
        )
        with patch(
            "source.utils.agents_sdk_llm_client.Runner.run_sync",
            return_value=_run_result(understanding),
        ) as mock_run:
            result = sdk_client.understand_message("some context", "take me to it")
        mock_run.assert_called_once()
        assert result == understanding.model_dump()

    def test_agent_configured_with_output_type(self, sdk_client):
        assert sdk_client.agent.output_type is NavigationUnderstanding

    def test_unexpected_string_output_falls_back_to_tolerant_parse(self, sdk_client):
        raw = ('{"intent": "guidance", "route_id": null, "confidence": 0.7, '
               '"extracted_entity": null, "insight": "You are here."}')
        with patch(
            "source.utils.agents_sdk_llm_client.Runner.run_sync",
            return_value=_run_result(raw),
        ):
            result = sdk_client.understand_message("some context", "where am i")
        assert result["intent"] == "guidance"

    def test_hard_failure_retries_once_then_gives_up(self, sdk_client):
        with patch(
            "source.utils.agents_sdk_llm_client.Runner.run_sync",
            side_effect=Exception("connection refused"),
        ) as mock_run:
            result = sdk_client.understand_message("some context", "take me to invoices")
        assert result is None
        assert mock_run.call_count == 2

    def test_tenant_id_injected_into_run_context(self, sdk_client):
        # The verified tenant id must reach the agent run as context (for the
        # tenant-scoped lookup_project tool), never as model-visible text.
        understanding = NavigationUnderstanding(
            intent="navigate", route_id="gantt_charts", confidence=0.9,
            extracted_entity=None, insight="ok",
        )
        with patch(
            "source.utils.agents_sdk_llm_client.Runner.run_sync",
            return_value=_run_result(understanding),
        ) as mock_run:
            sdk_client.understand_message("some context", "take me to the schedule", tenant_id="tn59395620")
        _, kwargs = mock_run.call_args
        assert kwargs["context"].tenant_id == "tn59395620"


class TestLookupProjectTool:
    def test_refuses_without_tenant(self, monkeypatch):
        # No tenant in context -> the tool must NOT query the resolver at all
        # (never widen scope on the model's behalf).
        from source.utils import agents_sdk_llm_client as mod

        def _boom():
            raise AssertionError("resolver must not be called without a tenant")

        monkeypatch.setattr(mod, "_get_entity_resolver", _boom)
        out = mod._lookup_project_impl(None, "Peter Parker")
        assert "unavailable" in out.lower()

    def test_scopes_resolver_by_tenant(self, monkeypatch):
        from source.model.navigation_models import ProjectMatch
        from source.utils import agents_sdk_llm_client as mod

        seen = {}

        def _capture(name, tenant_id):
            seen["name"], seen["tenant_id"] = name, tenant_id
            return [ProjectMatch(project_uid="2079", project_name="Peter Parker",
                                 status="unknown", order_no="272000")]

        fake_resolver = MagicMock()
        fake_resolver.resolve_project.side_effect = _capture
        monkeypatch.setattr(mod, "_get_entity_resolver", lambda: fake_resolver)

        out = mod._lookup_project_impl("tn59395620", "Peter Parker")
        assert seen["tenant_id"] == "tn59395620"
        assert "Peter Parker" in out and "2079" in out

    def test_ambiguous_multiple_matches_asks_user(self, monkeypatch):
        from source.model.navigation_models import ProjectMatch
        from source.utils import agents_sdk_llm_client as mod

        fake_resolver = MagicMock()
        fake_resolver.resolve_project.return_value = [
            ProjectMatch(project_uid="1", project_name="Peter A", status="unknown", order_no=None),
            ProjectMatch(project_uid="2", project_name="Peter B", status="unknown", order_no=None),
        ]
        monkeypatch.setattr(mod, "_get_entity_resolver", lambda: fake_resolver)

        out = mod._lookup_project_impl("tn59395620", "Peter")
        assert "ambiguous" in out.lower()
        assert "Peter A" in out and "Peter B" in out


class TestRunSyncIsolation:
    def test_run_sync_isolated_calls_runner_directly_no_thread_pool(self):
        # The old implementation submitted to a dedicated ThreadPoolExecutor;
        # now that FastAPI's own handlers are sync `def` (run in FastAPI's
        # threadpool, no event loop present), Runner.run_sync can be called
        # directly without the extra indirection.
        from source.utils.agents_sdk_llm_client import _run_sync_isolated

        agent = MagicMock()
        with patch(
            "source.utils.agents_sdk_llm_client.Runner.run_sync",
            return_value="sentinel-result",
        ) as mock_run:
            result = _run_sync_isolated(agent, "hello")
        mock_run.assert_called_once_with(agent, "hello", context=None)
        assert result == "sentinel-result"
