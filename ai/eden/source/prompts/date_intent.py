"""LLMClient.classify_date_intent()'s prompt. PRIMARY decision mechanism for
ReportingAgent.respond()'s date-vs-status fork (see DATE_SIGNALS' docstring
for why this replaced a keyword list rather than keeping it as the only
check): whether the message is asking specifically about project
start/end/schedule DATES, or a general status/progress question. Falls back
to DATE_SIGNALS only when this call itself fails (model unreachable),
same pattern as every other LLM-primary/keyword-fallback pair in this
codebase (triage, understanding, extract_project_name). DateIntentCheck
(source/model/navigation_models.py) is the structured-output contract this
prompt is paired with."""

DATE_INTENT_SYSTEM_PROMPT = """You classify whether a message is asking specifically \
about a construction project's SCHEDULE DATES (start date, end date, kickoff, target \
completion date, how long it will take), or something else like overall status, task \
completion, or budget, for FIELDCRAFT Cloud, a construction management platform.

Date examples: "when does this project start", "what's the target completion date", \
"when did it kick off", "how long until it's done".
Not date: "how is the project going", "what's the completion percentage", "is it over \
budget", "how much do I owe".

Respond with ONLY a JSON object: {"is_date_question": true|false, "reasoning": "one \
short sentence"}"""
