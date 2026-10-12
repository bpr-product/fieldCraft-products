"""Unit tests for the structured-output paths added across every LLM
backend (see NavigationUnderstanding in navigation_models.py). No live LLM
server is exercised — the OpenAI/LangChain/Agents SDK clients are mocked at
the point each backend calls them, so these tests prove the *code* handles
a structured response (and degrades correctly when the runtime can't
produce one) without depending on a live vLLM/Ollama endpoint.
"""
from unittest.mock import MagicMock, patch

import pytest

from source.model.navigation_models import NavigationUnderstanding
from source.utils.llm_client import LLMClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("LLM_CLIENT_BACKEND", raising=False)
    return LLMClient()


def _understanding(**overrides) -> NavigationUnderstanding:
    defaults = dict(
        intent="navigate", route_id="invoice_list", confidence=0.9,
        extracted_entity=None, insight="Here are the invoices.",
    )
    defaults.update(overrides)
    return NavigationUnderstanding(**defaults)


def _parsed_response(parsed=None, refusal=None, content=None):
    message = MagicMock(parsed=parsed, refusal=refusal, content=content)
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


class TestRawOpenAIBackendStructuredOutput:
    def test_call_llm_uses_parse_and_returns_validated_dict(self, client):
        understanding = _understanding()
        with patch.object(
            client.client.chat.completions, "parse",
            return_value=_parsed_response(parsed=understanding),
        ) as mock_parse:
            result = client._call_llm("some context block", "some user message")
        mock_parse.assert_called_once()
        assert mock_parse.call_args.kwargs["response_format"] is NavigationUnderstanding
        assert result == understanding.model_dump()

    def test_call_llm_returns_none_on_refusal(self, client):
        with patch.object(
            client.client.chat.completions, "parse",
            return_value=_parsed_response(refusal="I can't help with that."),
        ):
            result = client._call_llm("some context block", "some user message")
        assert result is None

    def test_call_llm_falls_back_to_legacy_on_parse_exception(self, client):
        # Simulates a runtime that doesn't support response_format=json_schema
        # — the structured call raises, and the old json_object + tolerant-
        # parse path takes over rather than surfacing a hard failure.
        legacy_response = MagicMock()
        legacy_response.choices = [
            MagicMock(message=MagicMock(content='{"intent": "guidance", "route_id": null, '
                                                  '"confidence": 0.8, "extracted_entity": null, '
                                                  '"insight": "You are here."}'))
        ]
        with patch.object(
            client.client.chat.completions, "parse", side_effect=RuntimeError("unsupported")
        ), patch.object(
            client.client.chat.completions, "create", return_value=legacy_response
        ) as mock_create:
            result = client._call_llm("some context block", "some user message")
        mock_create.assert_called_once()
        assert result["intent"] == "guidance"

    def test_understand_retries_once_on_transient_failure(self, client):
        understanding = _understanding()
        with patch.object(
            client.client.chat.completions, "parse",
            side_effect=[Exception("timeout"), Exception("timeout")],
        ), patch.object(
            client.client.chat.completions, "create",
            side_effect=Exception("also down"),
        ) as mock_create:
            result = client.understand("take me to invoices", [], {})
        assert result is None
        # Two attempts at the structured call, each of which fell back to
        # one legacy attempt — 2 legacy calls total, not a runaway retry loop.
        assert mock_create.call_count == 2


class TestRephrasePlaceholderGuard:
    def test_rejects_bracket_placeholder_and_keeps_none(self, client):
        # Reproduces the real observed hallucination: rephrasing a draft that
        # named no specific page, the model wrote what looks like a
        # fill-in-the-blank TEMPLATE instead of a finished sentence.
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="I can't provide that number right now, but I can guide "
                    "you to where you'll find it: [insert page name or button location]."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase(
                "how many employees currently clocked in?",
                "I can't answer data questions yet — the Finance and Schedule "
                "agents are coming soon. I can still navigate you anywhere in FIELDCRAFT.",
            )
        assert result is None

    def test_accepts_a_normal_rewrite(self, client):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="I can't crunch that number just yet, but I can take you anywhere in FIELDCRAFT!"
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("how many employees", "draft message")
        assert result == "I can't crunch that number just yet, but I can take you anywhere in FIELDCRAFT!"

    def test_rejects_rewrite_that_alters_a_dollar_figure(self, client):
        # The real risk this guards against: a rewrite silently changing the
        # actual Mongo-sourced number instead of just rewording around it.
        draft = "Riverside: actual cost $64,600 vs. estimate $49,400 (+31%)."
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Riverside came in at $64,000, up 31% from the $49,400 estimate."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("why is cost over budget on riverside", draft)
        assert result is None

    def test_rejects_rewrite_that_drops_a_number_entirely(self, client):
        draft = "Riverside: at risk, 12 days behind."
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Riverside is running behind schedule."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("how's riverside's schedule", draft)
        assert result is None

    def test_accepts_rewrite_that_preserves_all_numbers(self, client):
        draft = "Riverside: actual cost $64,600 vs. estimate $49,400 (+31%)."
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Riverside's actual cost is $64,600, compared to a $49,400 "
                    "estimate — that's +31%."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("why is cost over budget on riverside", draft)
        assert result == (
            "Riverside's actual cost is $64,600, compared to a $49,400 "
            "estimate — that's +31%."
        )

    def test_draft_with_no_numbers_skips_the_guard(self, client):
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Sure thing, here's where you'll find that."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("where's the invoices page", "Here you go.")
        assert result == "Sure thing, here's where you'll find that."

    def test_rejects_rewrite_that_invents_a_new_question(self, client):
        # Real bug, fixed: confirmed live -- despite the prompt instruction
        # against it, this rewrite still sometimes invents its own closing
        # offer/question when the draft has none at all (here, even
        # offering a page that doesn't exist anywhere in the registry). A
        # user confirming that invented offer with "yes" has nothing real
        # to confirm and dead-ends.
        draft = "The project cost is $14,510.07."
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="The project cost is $14,510.07. Would you like to see the cost breakdown page?"
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("what is the project cost", draft)
        assert result is None

    def test_accepts_rewrite_that_preserves_an_existing_question(self, client):
        # The guard must not reject a rewrite that keeps a question ALREADY
        # in the draft -- only originating a NEW one is the problem.
        draft = "Here's the invoice. Would you like to see the Invoices page?"
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Here's the invoice — want me to pull up the Invoices page?"
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("what's my invoice", draft)
        assert result == "Here's the invoice — want me to pull up the Invoices page?"

    def test_rejects_rewrite_that_swaps_the_offered_page(self, client):
        # Real bug, fixed: confirmed live -- a rewrite kept the QUESTION
        # shape (so the "invented question" guard above doesn't catch it)
        # but swapped which page it named -- draft offered Invoices
        # (already permission-checked for this user by
        # _attach_page_suggestion/_enforce_role_scope), rewrite offered
        # "Job Ledger" instead, a page never checked against this user's
        # permissions at all. Exactly the failure the voice prompt's own
        # worked example (prompts/voice.py) already warns against by name.
        draft = "Henderson Roofing has $1,200 outstanding. Would you like to see the Invoices page?"
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Henderson Roofing has $1,200 outstanding. Would you like to see the Job Ledger details?"
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("what do I owe on henderson", draft)
        assert result is None

    def test_accepts_rewrite_that_drops_page_but_keeps_no_question(self, client):
        # A draft with NO suggestion sentence at all must not trip the new
        # page-preservation guard -- it only fires when the draft actually
        # contains _attach_page_suggestion's deterministic phrasing.
        draft = "Henderson Roofing has $1,200 outstanding."
        response = MagicMock()
        response.choices = [MagicMock(message=MagicMock(
            content="Henderson Roofing currently has $1,200 outstanding."
        ))]
        with patch.object(client.client.chat.completions, "create", return_value=response):
            result = client.rephrase("what do I owe on henderson", draft)
        assert result == "Henderson Roofing currently has $1,200 outstanding."


class TestClassifyBillingIntent:
    """Instrumentation-only call for ReportingAgent's BILLING_SIGNALS audit
    (see reporting_agent.py) — never used to route, only to log
    disagreement with the keyword decision on a sampled fraction of
    requests."""

    def test_parses_a_billing_classification(self, client):
        from source.model.navigation_models import BillingIntentCheck

        parsed = BillingIntentCheck(is_billing=True, reasoning="asks what they owe")
        with patch.object(client.client.chat.completions, "parse", return_value=_parsed_response(parsed=parsed)):
            result = client.classify_billing_intent("how much do I owe on this project")
        assert result == {"is_billing": True, "reasoning": "asks what they owe"}

    def test_parses_a_non_billing_classification(self, client):
        from source.model.navigation_models import BillingIntentCheck

        parsed = BillingIntentCheck(is_billing=False, reasoning="asks about progress")
        with patch.object(client.client.chat.completions, "parse", return_value=_parsed_response(parsed=parsed)):
            result = client.classify_billing_intent("how is the project going")
        assert result == {"is_billing": False, "reasoning": "asks about progress"}

    def test_returns_none_on_call_failure(self, client):
        with patch.object(client.client.chat.completions, "parse", side_effect=Exception("timeout")):
            result = client.classify_billing_intent("how much do I owe")
        assert result is None

    def test_returns_none_on_refusal(self, client):
        with patch.object(
            client.client.chat.completions, "parse",
            return_value=_parsed_response(refusal="cannot help"),
        ):
            result = client.classify_billing_intent("how much do I owe")
        assert result is None


class TestFitToBudget:
    """_fit_to_budget measures the REAL constructed prompt (via
    build_user_message) rather than guessing a fixed candidate count —
    the two live context-overflow incidents (SYSTEM_PROMPT growing,
    retrieval backend changing) were both caused by a fixed cap going
    stale. These tests force a tiny budget via monkeypatch so the trimming
    logic is exercised deterministically without needing SYSTEM_PROMPT to
    actually be huge.
    """

    def _candidate(self, i: int, desc_len: int = 200) -> dict:
        return {
            "id": f"route_{i}",
            "label_template": f"Route {i}",
            "description": "x" * desc_len,
        }

    def test_fits_as_is_when_under_budget(self, client, monkeypatch):
        import source.utils.llm_client as llm_client_module

        # Isolate MODEL_CONTEXT_WINDOW_TOKENS behavior from the separate,
        # real GUARDRAILS_MAX_INPUT_TOKENS cap (see its docstring) — this
        # test is about the model-window budget specifically. Real bug,
        # fixed: this test relied on MODEL_CONTEXT_WINDOW_TOKENS's module-
        # level default (previously 32768, effectively unbounded for this
        # fixture) rather than pinning it explicitly like every sibling
        # test in this class already does -- once the real deployed env
        # correctly set MODEL_CONTEXT_WINDOW_TOKENS=2048 (the model's true
        # window, see that env var's docstring), this test started failing
        # in an environment where that's actually set, since 3 candidates
        # no longer fit under the real budget. Pin both caps explicitly so
        # "under budget" means what the test actually wants to assert,
        # regardless of what's configured on the machine running the suite.
        monkeypatch.setattr(llm_client_module, "GUARDRAILS_MAX_INPUT_TOKENS", 999_999)
        monkeypatch.setattr(llm_client_module, "MODEL_CONTEXT_WINDOW_TOKENS", 999_999)
        candidates = [self._candidate(i) for i in range(3)]
        history = [{"utterance": "hi", "type": "navigate", "label": "Invoices"}]
        out_candidates, out_history = client._fit_to_budget(
            "take me to invoices", candidates, {}, history, None
        )
        assert out_candidates == candidates
        assert out_history == history

    def test_drops_history_before_trimming_candidates(self, client, monkeypatch):
        import source.utils.llm_client as llm_client_module

        monkeypatch.setattr(llm_client_module, "GUARDRAILS_MAX_INPUT_TOKENS", 999_999)
        candidates = [self._candidate(i) for i in range(3)]
        history = [{"utterance": "hi", "type": "navigate", "label": "Invoices"}]
        # Budget that fits candidates+no-history but not candidates+history.
        def _msg_tokens(candidates, history):
            context_block, user_content = llm_client_module.build_user_message(
                "q", candidates, {}, history
            )
            return llm_client_module._estimate_tokens(
                context_block
            ) + llm_client_module._estimate_tokens(user_content)

        with_history_tokens = llm_client_module._estimate_tokens(
            llm_client_module.SYSTEM_PROMPT
        ) + _msg_tokens(candidates, history)
        without_history_tokens = llm_client_module._estimate_tokens(
            llm_client_module.SYSTEM_PROMPT
        ) + _msg_tokens(candidates, None)
        assert without_history_tokens < with_history_tokens  # sanity check
        budget_tokens = without_history_tokens + 5
        monkeypatch.setattr(
            llm_client_module, "MODEL_CONTEXT_WINDOW_TOKENS",
            budget_tokens + llm_client_module.RESERVED_OUTPUT_TOKENS,
        )

        out_candidates, out_history = client._fit_to_budget(
            "q", candidates, {}, history, None
        )
        assert out_candidates == candidates  # all candidates kept
        assert out_history is None  # history dropped to make room

    def test_trims_candidates_down_to_floor_when_still_too_big(self, client, monkeypatch):
        import source.utils.llm_client as llm_client_module

        monkeypatch.setattr(llm_client_module, "MODEL_CONTEXT_WINDOW_TOKENS", 250)
        monkeypatch.setattr(llm_client_module, "RESERVED_OUTPUT_TOKENS", 50)
        candidates = [self._candidate(i, desc_len=200) for i in range(8)]

        out_candidates, out_history = client._fit_to_budget(
            "q", candidates, {}, [{"utterance": "hi", "type": "navigate"}], None
        )
        assert out_history is None
        assert len(out_candidates) == llm_client_module.MIN_CANDIDATES_FLOOR
        # Kept the FIRST N (best-scored, since candidates arrive pre-sorted),
        # not an arbitrary subset.
        assert out_candidates == candidates[: llm_client_module.MIN_CANDIDATES_FLOOR]

    def test_guardrails_cap_wins_when_smaller_than_model_window(self, client, monkeypatch):
        """Regression for a real live failure: after the Qwen3-8B-AWQ swap,
        MODEL_CONTEXT_WINDOW_TOKENS defaults to 32768, but the guardrails
        gateway every call actually passes through enforces its own,
        far smaller LLM_MAX_INPUT_TOKENS (2000 in the deployed dev config)
        and silently blocks anything over it before the model ever sees
        it -- no error, just an empty/blocked completion. Budgeting only
        against the model's own window let prompts pass this check while
        still guaranteed to fail at the gateway, 100% of the time, for any
        multi-candidate understand() call. The smaller of the two must
        always win."""
        import source.utils.llm_client as llm_client_module

        monkeypatch.setattr(llm_client_module, "MODEL_CONTEXT_WINDOW_TOKENS", 32_768)
        monkeypatch.setattr(llm_client_module, "GUARDRAILS_MAX_INPUT_TOKENS", 250)
        monkeypatch.setattr(llm_client_module, "RESERVED_OUTPUT_TOKENS", 50)
        candidates = [self._candidate(i, desc_len=200) for i in range(8)]

        out_candidates, out_history = client._fit_to_budget(
            "q", candidates, {}, [{"utterance": "hi", "type": "navigate"}], None
        )
        assert out_history is None
        assert len(out_candidates) == llm_client_module.MIN_CANDIDATES_FLOOR

    def test_never_returns_empty_even_when_hopelessly_over_budget(self, client, monkeypatch):
        import source.utils.llm_client as llm_client_module

        monkeypatch.setattr(llm_client_module, "MODEL_CONTEXT_WINDOW_TOKENS", 10)
        monkeypatch.setattr(llm_client_module, "RESERVED_OUTPUT_TOKENS", 5)
        candidates = [self._candidate(i, desc_len=500) for i in range(8)]

        out_candidates, out_history = client._fit_to_budget("q", candidates, {}, None, None)
        assert len(out_candidates) == llm_client_module.MIN_CANDIDATES_FLOOR


class TestLegacyParsePath:
    def test_legacy_extract_json_still_handles_think_tags_and_fences(self):
        content = (
            "<think>reasoning trace</think>```json\n"
            '{"intent": "navigate", "route_id": "invoice_list", '
            '"confidence": 0.9, "extracted_entity": null, "insight": "ok"}'
            "\n```"
        )
        result = LLMClient._extract_json(content)
        assert result == {
            "intent": "navigate", "route_id": "invoice_list",
            "confidence": 0.9, "extracted_entity": None, "insight": "ok",
        }


# ── Fact-check number tokenisation ───────────────────────────────────────────
# _number_tokens backs the "did the model invent or drop a figure" check in
# rephrase() and in ReportingAgent's RAG answer validation. A unit symbol on
# one side only must never make the same number compare as different.

class TestNumberTokens:
    def test_percent_suffix_matches_bare_number(self):
        # Real bug, fixed: source data holds a bare
        # project_health.completionPct (16.67) while any natural sentence about
        # it writes "16.67%". Confirmed live -- a fully correct reporting
        # answer was rejected as "contains figures not present in source data"
        # with answer_numbers={'16.67%'} the sole offender.
        from source.utils.llm_client import _number_tokens
        assert _number_tokens("16.67%") == _number_tokens("16.67")

    def test_percent_with_trailing_punctuation(self):
        # "...of 16.67%, with 9 tasks..." -- the % strip must survive a clause
        # or sentence break immediately after it.
        from source.utils.llm_client import _number_tokens
        assert _number_tokens("16.67%,") == _number_tokens("16.67")
        assert _number_tokens("16.67%.") == _number_tokens("16.67")

    def test_dollar_prefix_still_matches_bare_number(self):
        # The pre-existing "$" fix must keep working alongside the "%" one.
        from source.utils.llm_client import _number_tokens
        assert _number_tokens("$38,924.32") == _number_tokens("38,924.32")

    def test_the_live_rejected_answer_now_validates(self):
        # Verbatim from the 2026-08-04 13:20:08 log line, against the figures
        # ReportingAgent logged one second later (completionPct=16.67,
        # doneTaskCount=9, totalTaskCount=54). Nothing in the answer is
        # ungrounded, so the difference must be empty.
        from source.utils.llm_client import _number_tokens
        answer = (
            "The project has a completion percentage of 16.67%, with 9 tasks "
            "completed out of 54 total tasks."
        )
        source = "completionPct=16.67 doneTaskCount=9 totalTaskCount=54"
        assert not (_number_tokens(answer) - _number_tokens(source))

    def test_a_genuinely_invented_figure_is_still_caught(self):
        # The guard must still do its job -- this is not a licence to accept
        # any number the model produces.
        from source.utils.llm_client import _number_tokens
        answer = "Completion is 42.5% across 9 of 54 tasks."
        source = "completionPct=16.67 doneTaskCount=9 totalTaskCount=54"
        assert _number_tokens(answer) - _number_tokens(source) == {"42.5"}


# ── PII-redaction exposure: EDEN-side data must not ride in the user role ────
# The guardrails gateway redacts user-role content ONLY ("Redact only user-role
# content; system prompts are trusted and left intact" -- guardrails
# app/pii.py). Real FIELDCRAFT project names ARE person names, so any EDEN-generated
# text placed in the user role comes back to the model as <PERSON>. Observed
# live on every project-named response: the voice rewrite was discarded by its
# own leak guard and the raw template shipped instead.

class TestPresidioExposure:
    def _roles(self, monkeypatch, call, *args, **kwargs):
        """Capture the message roles/content of one LLM call without sending."""
        from source.utils import llm_client as m
        client = m.LLMClient()
        seen = {}

        def fake(**kw):
            seen.update(kw)
            raise RuntimeError("captured")

        monkeypatch.setattr(client.client.chat.completions, "create", fake)
        monkeypatch.setattr(client.client.chat.completions, "parse", fake)
        getattr(client, call)(*args, **kwargs)
        return seen["messages"]

    def test_rephrase_keeps_the_draft_out_of_the_user_role(self, monkeypatch):
        msgs = self._roles(monkeypatch, "rephrase", "what is the status",
                           "Dan Cabush, Racine-Roof Enhancement-646248: on track.")
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        system = " ".join(m["content"] for m in msgs if m["role"] == "system")
        assert "Dan Cabush" in system      # trusted role, not redacted
        assert "Dan Cabush" not in user    # would come back as <PERSON>

    def test_billing_composition_keeps_the_project_name_out_of_the_user_role(self, monkeypatch):
        # No placeholder guard on this path -- the fact-check only verifies the
        # FIGURES survive, so a redacted name would ship straight to the user.
        msgs = self._roles(monkeypatch, "compose_billing_message",
                           "Dan Cabush, Racine-Roof Enhancement-646248", 19656, 0, 19656)
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        assert "Dan Cabush" not in user

    def test_variance_composition_keeps_the_project_name_out_of_the_user_role(self, monkeypatch):
        msgs = self._roles(monkeypatch, "compose_variance_message",
                           "Dan Cabush, Racine-Roof Enhancement-646248", 38924, 19656)
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        assert "Dan Cabush" not in user

    def test_context_switch_decision_keeps_both_names_out_of_the_user_role(self, monkeypatch):
        msgs = self._roles(monkeypatch, "decide_project_context_switch",
                           "what about the cost of that project",
                           "Dan Cabush, Racine-Roof Enhancement-646248",
                           "Al Naber, Burlington-Roof Enhancement-543655")
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        assert "Dan Cabush" not in user and "Al Naber" not in user

    def test_context_switch_reply_resolution_keeps_names_and_question_out_of_user_role(self, monkeypatch):
        # Real bug, fixed: this sibling of decide_project_context_switch was
        # missed when that leak class was fixed -- question/last_project/
        # page_project are all EDEN-owned (the question EDEN itself asked,
        # project names from EDEN's own Mongo reads), yet all rode in the
        # user role, same exposure as the decision call above.
        msgs = self._roles(monkeypatch, "resolve_project_context_switch_reply",
                           "the second one",
                           "Did you mean Dan Cabush or Al Naber?",
                           "Dan Cabush, Racine-Roof Enhancement-646248",
                           "Al Naber, Burlington-Roof Enhancement-543655")
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        assert "Dan Cabush" not in user and "Al Naber" not in user
        assert "Did you mean" not in user
        # The user's own reply must still be genuinely present, in the user role.
        assert "the second one" in user

    def test_the_users_own_utterance_still_goes_in_the_user_role(self, monkeypatch):
        # Redaction of genuine user input is CORRECT and must not be bypassed.
        msgs = self._roles(monkeypatch, "rephrase", "how is Peter Parker doing", "Draft.")
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        assert "Peter Parker" in user

    def test_understand_keeps_current_entity_and_history_project_out_of_user_role(self, monkeypatch):
        # Real bug, fixed: understand()/build_user_message() -- the primary
        # routing call hit on essentially every request -- folded
        # session_context["currentEntity"] and the conversation-history
        # "[project: ...]" bracket into the SAME combined string as the
        # user's own utterance, all sent as one user-role message. Same
        # exposure as every other site in this class, just on the
        # highest-traffic call path.
        route_registry = [{"id": "invoice_list", "label_template": "Invoices", "description": "Billing"}]
        session_context = {
            "currentRoute": "/soldproduction/projectinformation",
            "currentEntity": {"type": "project", "id": "1", "name": "Dan Cabush, Racine-Roof Enhancement-646248"},
        }
        conversation_history = [
            {"utterance": "how is it going", "type": "guidance", "project": "Dan Cabush, Racine-Roof Enhancement-646248"},
        ]
        msgs = self._roles(
            monkeypatch, "understand", "what about the invoice",
            route_registry, session_context, conversation_history,
        )
        user = " ".join(m["content"] for m in msgs if m["role"] == "user")
        system = " ".join(m["content"] for m in msgs if m["role"] == "system")
        assert "Dan Cabush" in system      # trusted role, not redacted
        assert "Dan Cabush" not in user    # would come back as <PERSON>
        # The user's own new utterance must still be genuinely present.
        assert "what about the invoice" in user


# ── rephrase: fabricated causal claim guard ─────────────────────────────────
# Confirmed live: rephrasing a plain STATUS draft (completion %, task counts,
# on-track) that stated no cause at all, the model invented one anyway:
# "...was updated due to changes in scope and resource allocation." Not
# caught by the number guard (all figures survived) or the invented-question
# guard (not phrased as a question).

class TestCausalFabricationGuard:
    def test_fabricated_cause_on_a_causeless_draft_is_rejected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = "Lee Buss, Franklin-Roof Enhancement-102380 is 20% complete (18 of 88 tasks done). On track, no delays."
        rewritten = "The estimation for project 102380 was updated due to changes in scope and resource allocation."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("why did it change", draft) is None

    def test_a_real_cause_reworded_naturally_is_not_rejected(self, monkeypatch):
        # The draft states its cause as "It appears related to:" (this
        # session's own Q1 builder phrasing) -- a rewrite phrasing the SAME
        # cause as "because of X" is a fact-preserving paraphrase, not an
        # invention, and must survive.
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = "Your estimate was updated. It appears related to: Customer Requested To Change."
        rewritten = "Your estimate changed because of a customer-requested update."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("why did it change", draft) == rewritten

    def test_neutral_rewrite_with_no_causal_language_is_not_rejected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = "Al Naber project is 17% complete, on track."
        rewritten = "The Al Naber project is currently 17% complete and running on schedule."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("status", draft) == rewritten


# ── rephrase: fabricated access/permission-denial claim guard ──────────────
# Confirmed live: rephrasing a plain data-availability draft (no access or
# permission problem stated at all) came back with an invented RBAC-denial
# claim -- "I don't have access to view other users' projects." That
# specific live case also happened to end in a question ("Would you like to
# see your own projects instead?"), which is why the pre-existing
# invented-question guard caught it -- but the SAME fabricated claim phrased
# as a flat statement (no trailing "?") would not have been caught by any
# existing guard: no numbers involved, no PII placeholder, no causal
# connector word ("due to"/"because of"/etc.). test_flat_denial_with_no_
# question_is_still_rejected below is the case this guard specifically
# closes.

class TestAccessDenialFabricationGuard:
    def test_flat_denial_with_no_question_is_still_rejected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = ("I don't have that specific detail available for this project. "
                 "I can help with your project's overall estimate or billing "
                 "information instead.")
        rewritten = "I don't have access to view other users' projects."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("show me the projects for TESTLesly", draft) is None

    def test_denial_with_trailing_question_is_also_rejected(self, monkeypatch):
        # The exact live case: caught today only by the invented-question
        # guard, but must independently be caught by this guard too.
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = ("I don't have that specific detail available for this project. "
                 "I can help with your project's overall estimate or billing "
                 "information instead.")
        rewritten = ("I don't have access to view other users' projects. "
                      "Would you like to see your own projects instead?")
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("show me the projects for TESTLesly", draft) is None

    def test_a_real_access_denial_reworded_naturally_is_not_rejected(self, monkeypatch):
        # The draft ITSELF already states the access denial (e.g.
        # navigation_agent.py's "You don't have access to X yet") -- a
        # rewrite phrasing the SAME denial naturally is a fact-preserving
        # paraphrase, not an invention, and must survive.
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = "You don't have access to the Payroll page yet. Ask your admin to grant you access if you need it."
        rewritten = "Looks like you don't have access to Payroll yet — check with your admin if you need it."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("open payroll", draft) == rewritten

    def test_neutral_rewrite_with_no_access_language_is_not_rejected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        draft = "Al Naber project is 17% complete, on track."
        rewritten = "The Al Naber project is currently 17% complete and running on schedule."
        monkeypatch.setattr(
            client.client.chat.completions, "create",
            lambda **kw: type("R", (), {"choices": [type("C", (), {
                "message": type("M", (), {"content": rewritten})()})()]})(),
        )
        assert client.rephrase("status", draft) == rewritten


# Real bug, fixed: confirmed live -- "take me to al naber project" extracted
# project_name="<PERSON>" (the utterance is USER-role content, and the
# gateway's Presidio rail redacts real customer/project names there). Unlike
# the 5 EDEN-owned-data call sites already fixed this session, the utterance
# here can't just move to a system message -- it's genuinely the user's own
# input, not EDEN-owned data. Passing "<PERSON>" through to resolve_project()
# produced "I couldn't find a project matching '<PERSON>'" -- the redaction
# placeholder leaking straight into a customer-facing message.

class TestExtractProjectNamePiiPlaceholderGuard:
    def _parsed_response(self, project_name):
        parsed = type("P", (), {"model_dump": lambda self: {"project_name": project_name}})()
        message = type("M", (), {"refusal": None, "parsed": parsed})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()

    def test_placeholder_extraction_falls_back_like_a_call_failure(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        monkeypatch.setattr(
            client.client.chat.completions, "parse",
            lambda **kw: self._parsed_response("<PERSON>"),
        )
        # None (not {"project_name": None}) -- the caller's contract treats
        # None as "the call itself failed," which is what makes NavigationAgent
        # fall back to _extract_entity_name's deterministic stop-word
        # stripper instead of trusting the corrupted extraction.
        assert client.extract_project_name("take me to al naber project") is None

    def test_a_real_extracted_name_is_unaffected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        monkeypatch.setattr(
            client.client.chat.completions, "parse",
            lambda **kw: self._parsed_response("Henderson Roofing"),
        )
        assert client.extract_project_name("take me to henderson schedule") == {
            "project_name": "Henderson Roofing"
        }

    def test_no_project_named_is_unaffected(self, monkeypatch):
        from source.utils import llm_client as m
        client = m.LLMClient()
        monkeypatch.setattr(
            client.client.chat.completions, "parse",
            lambda **kw: self._parsed_response(None),
        )
        assert client.extract_project_name("what's on my schedule today") == {
            "project_name": None
        }
