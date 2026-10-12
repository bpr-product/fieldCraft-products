"""LLMClient.suggest_related_routes()'s prompt. Replaces DATA_TOPIC_ROUTES
(navigation_agent.py) -- a fixed phrase->route table (e.g.
"margin"/"profit"/"revenue" -> tenant_finance) -- as the PRIMARY mechanism
for picking which page(s) to suggest when EDEN can't actually compute an
answer to a data question. Same reasoning as extract_project.
EXTRACT_PROJECT_SYSTEM_PROMPT: reading the question and the candidates' real
descriptions beats a maintained keyword list that silently misses any
phrasing not already in it. The keyword table survives only as the degraded
fallback when this call fails. RelatedRoutesSuggestion
(source/model/navigation_models.py) is the structured-output contract this
prompt is paired with."""

RELATED_ROUTES_SYSTEM_PROMPT = """You help route a construction-management \
assistant (FIELDCRAFT Cloud) user to the right page when the assistant can't directly \
compute the answer to their question. You are given the user's question and a list \
of candidate pages, each with an id, label, and description.

Pick the page id(s) — from the candidate list ONLY, never invented — where the \
answer to the question most plausibly lives. Order them most-relevant first. Return \
at most 3. Return an EMPTY list if none of the candidates genuinely relate to the \
question — an irrelevant suggestion is worse than none.

Respond with ONLY a JSON object: {"route_ids": ["...", ...]}"""
