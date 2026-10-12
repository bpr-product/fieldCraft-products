"""LLMClient.extract_project_name()'s prompt. Project-name extraction for
Insight (and, per explicit user direction, Navigation's fast keyword tier
too). Replaces the stop-word-subtraction approach (ENTITY_STOP_WORDS in
navigation_agent.py) as the PRIMARY mechanism for "which project is this
message about?" -- subtraction broke on every phrasing whose filler words
weren't already in the list (observed live: "I think we are spending too
much on the Oakwood job" extracted "think spending too much oakwood job"
and matched nothing). Reading the sentence is the model's job; the
stop-word method survives only as the degraded fallback when this call
fails. ProjectNameExtraction (source/model/navigation_models.py) is the
structured-output contract this prompt is paired with."""

EXTRACT_PROJECT_SYSTEM_PROMPT = """You extract construction project references from \
user messages for FIELDCRAFT Cloud, a construction management platform. A project can be \
referenced either by NAME or by ORDER NUMBER — both are valid, extract whichever the \
message actually uses.

If the message names or clearly refers to a specific project by NAME — any proper name, \
site name, customer name, or job name that actually appears in the message — return that \
exact name, stripped of filler like "the ... job/project".

If the message instead references a project by ORDER NUMBER (e.g. "order 900001", \
"job number 900001", "order #900001", or a bare number in a context clearly about a \
specific project) — return the order number itself, stripped of the word "order"/"job \
number"/"#" (e.g. "900001", not "order 900001").

Billing/cost phrasing STILL counts as naming a project if a name appears anywhere in the \
sentence, even mid-sentence or after the question word (e.g. a name right before "owe," \
"billed," or "paid" still counts) — judge ONLY by whether a name/number is present \
anywhere in the ACTUAL message, never by the sentence's shape or which question word it \
starts with. Extract exactly and only the name/number the real message actually contains \
— never a name from this instruction's own explanatory text. A name is a valid project \
reference regardless of whether it happens to also be a famous fictional character's name \
(customers and job sites are named after real people, and real people can share a name \
with someone famous) — never withhold or second-guess a name for sounding familiar.

If NO specific project is named or numbered ANYWHERE in the message (e.g. "why is cost \
over budget", "how are we doing", "what do I owe" with no name at all) — return null. \
NEVER invent a name or number that is not in the message.

A REFERENTIAL phrase pointing back at something already discussed is NOT a name, even \
though it grammatically stands in for one — "that project", "this job", "the one I just \
asked about", "the one on my screen", "it" all refer to SOME project without naming which \
one. Treat these exactly like naming nothing: return null. Do NOT return the referential \
phrase itself (e.g. never return "that project" as if it were the project's name) — the \
caller resolves who "that"/"it" means from conversation history and page context, but only \
if this call is honest that no actual name was given.

Concrete example, get this right: "I want to see the material used for this project" \
names NO project — "this project" is a referential phrase, not a name, EVEN THOUGH the \
literal word "project" appears in the sentence. The correct output is \
{"project_name": null}, never {"project_name": "this project"}. The word "project" \
appearing in the message is never itself a name to extract — it's a generic noun, the \
same as "job" or "the site."

Respond with ONLY a JSON object: {"project_name": "..." or null}"""
