"""LLMClient.classify_billing_intent()'s prompt. Instrumentation only
(ReportingAgent's BILLING_SIGNALS audit) -- never used to decide routing,
only to check whether that closed keyword list agrees with a real model
read on a sampled fraction of requests. Same reasoning as the top-level
triage call: a keyword list deciding an open-ended question can silently
misroute phrasing it wasn't written for. BillingIntentCheck
(source/model/navigation_models.py) is the structured-output contract this
prompt is paired with."""

BILLING_INTENT_SYSTEM_PROMPT = """You classify whether a message is asking about the \
USER'S OWN bill/invoice/balance/payments (billing), or something else like project \
status, schedule, or progress (not billing), for FIELDCRAFT Cloud, a construction management \
platform.

Billing examples: "how much do I owe", "have I paid this invoice", "what's my balance", \
"when is my next payment due".
Not billing: "how is the project going", "is it on schedule", "what's the completion \
percentage".

Respond with ONLY a JSON object: {"is_billing": true|false, "reasoning": "one short \
sentence"}"""
