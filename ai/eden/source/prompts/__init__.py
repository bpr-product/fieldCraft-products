"""Every system prompt LLMClient sends to the model, one file per prompt
(see each module's docstring for what it's for and which structured-output
contract it pairs with). Centralized here, instead of inline in
llm_client.py, so a prompt can be found, reviewed, or tuned without reading
the request/response plumbing around it."""

from source.prompts.billing_compose import BILLING_COMPOSE_SYSTEM_PROMPT
from source.prompts.billing_intent import BILLING_INTENT_SYSTEM_PROMPT
from source.prompts.date_intent import DATE_INTENT_SYSTEM_PROMPT
from source.prompts.extract_project import EXTRACT_PROJECT_SYSTEM_PROMPT
from source.prompts.goal_continuation import GOAL_CONTINUATION_SYSTEM_PROMPT
from source.prompts.project_data_answer import PROJECT_DATA_ANSWER_SYSTEM_PROMPT
from source.prompts.project_context_switch import (
    PROJECT_CONTEXT_SWITCH_REPLY_SYSTEM_PROMPT,
    PROJECT_CONTEXT_SWITCH_SYSTEM_PROMPT,
)
from source.prompts.related_routes import RELATED_ROUTES_SYSTEM_PROMPT
from source.prompts.triage import TRIAGE_PAGE_CONTEXT_TEMPLATE, TRIAGE_SYSTEM_PROMPT
from source.prompts.understanding import STRICT_SUFFIX, SYSTEM_PROMPT
from source.prompts.variance_compose import VARIANCE_COMPOSE_SYSTEM_PROMPT
from source.prompts.verify_ambiguity import VERIFY_AMBIGUITY_SYSTEM_PROMPT
from source.prompts.voice import VOICE_SYSTEM_PROMPT

__all__ = [
    "BILLING_COMPOSE_SYSTEM_PROMPT",
    "BILLING_INTENT_SYSTEM_PROMPT",
    "DATE_INTENT_SYSTEM_PROMPT",
    "EXTRACT_PROJECT_SYSTEM_PROMPT",
    "GOAL_CONTINUATION_SYSTEM_PROMPT",
    "PROJECT_DATA_ANSWER_SYSTEM_PROMPT",
    "PROJECT_CONTEXT_SWITCH_REPLY_SYSTEM_PROMPT",
    "PROJECT_CONTEXT_SWITCH_SYSTEM_PROMPT",
    "RELATED_ROUTES_SYSTEM_PROMPT",
    "STRICT_SUFFIX",
    "SYSTEM_PROMPT",
    "TRIAGE_PAGE_CONTEXT_TEMPLATE",
    "TRIAGE_SYSTEM_PROMPT",
    "VARIANCE_COMPOSE_SYSTEM_PROMPT",
    "VERIFY_AMBIGUITY_SYSTEM_PROMPT",
    "VOICE_SYSTEM_PROMPT",
]
