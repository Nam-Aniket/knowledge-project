"""Actionable guidance-plan schema, validation, and resilient JSON parsing.

The v0.6 guidance layer produces an ACTION PLAN, not advice. This module owns
the schema contract shared by generation (guidance.py) and materialization
(records written to goals/experiments/metric_logs).

Empty-actions contract: a plan with ``actions: []`` is structurally VALID
(``validate_plan`` returns ``(True, "")``) — it is the graceful-degradation
and parse-failure fallback shape produced by ``empty_plan``. The *generation
caller* separately checks ``len(plan["actions"]) > 0`` to decide whether to
retry; emptiness is a soft failure, not a schema violation.
"""
import json
import re

# The canonical schema, embedded verbatim into the LLM system prompt.
PLAN_SCHEMA_DESCRIPTION = """{
  "domain": "<string>",
  "goal": "<one-line restatement of the user's goal>",
  "diagnosis": "<2-3 sentence read of the situation, grounded in retrieved knowledge/facts>",
  "actions": [
    {
      "action": "<concrete imperative step the user does themselves>",
      "horizon": "today|this_week|this_month",
      "time_estimate_min": <integer minutes>,
      "success_criterion": "<observable, checkable outcome>",
      "due_offset_days": <integer days from today>,
      "metric": {"name": "<snake_case>", "type": "objective|subjective", "unit": "<unit>"}
    }
  ],
  "first_action_index": <integer index into actions of the single thing to do first>,
  "relevant_principles": [{"principle": "<insight>", "source": "<Title, Location>"}],
  "rule_suggestions": ["<personal rule to consider adopting>"],
  "review_in_days": <integer, default 7>
}"""

VALID_HORIZONS = {"today", "this_week", "this_month"}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _non_empty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_action(action) -> bool:
    """Per-action check shared by validate_plan and coerce_plan."""
    if not isinstance(action, dict):
        return False
    if not _non_empty_str(action.get("action")):
        return False
    if action.get("horizon") not in VALID_HORIZONS:
        return False
    if not _is_int(action.get("time_estimate_min")) or action["time_estimate_min"] < 1:
        return False
    if not _non_empty_str(action.get("success_criterion")):
        return False
    if not _is_int(action.get("due_offset_days")) or action["due_offset_days"] < 0:
        return False
    metric = action.get("metric")
    if metric is not None:
        if not isinstance(metric, dict):
            return False
        for key in ("name", "type", "unit"):
            if not _non_empty_str(metric.get(key)):
                return False
    return True


def empty_plan(goal_text: str, domain: str) -> dict:
    """Returns a minimal valid plan dict (no actions) for graceful-degradation
    and parse-failure fallback paths."""
    return {
        "domain": domain,
        "goal": goal_text,
        "diagnosis": "",
        "actions": [],
        "first_action_index": 0,
        "relevant_principles": [],
        "rule_suggestions": [],
        "review_in_days": 7,
    }


def validate_plan(obj) -> tuple[bool, str]:
    """Returns (True, "") if obj matches the plan schema, else (False, reason).
    Strict on required keys/types; tolerant of extra keys (ignored downstream).

    Empty ``actions`` is valid-but-empty (see module docstring); in that case
    ``first_action_index`` must be 0.
    """
    if not isinstance(obj, dict):
        return False, "plan is not a dict"
    for key in ("domain", "goal", "actions", "first_action_index", "review_in_days"):
        if key not in obj:
            return False, f"missing required key: {key}"
    if not _non_empty_str(obj["domain"]):
        return False, "domain must be a non-empty string"
    if not _non_empty_str(obj["goal"]):
        return False, "goal must be a non-empty string"
    if not isinstance(obj["actions"], list):
        return False, "actions must be a list"
    for i, action in enumerate(obj["actions"]):
        if not _valid_action(action):
            return False, f"actions[{i}] is malformed"
    if not _is_int(obj["first_action_index"]):
        return False, "first_action_index must be an integer"
    if obj["actions"]:
        if obj["first_action_index"] not in range(len(obj["actions"])):
            return False, "first_action_index out of range"
    elif obj["first_action_index"] != 0:
        return False, "first_action_index must be 0 when actions is empty"
    if not _is_int(obj["review_in_days"]) or obj["review_in_days"] < 1:
        return False, "review_in_days must be a positive integer"
    return True, ""


def coerce_plan(obj, goal_text: str, domain: str) -> dict:
    """Best-effort normalize a loosely-valid object into a schema-conformant
    plan: fills missing optional lists with [], clamps actions to <= 5, drops
    malformed action entries, defaults horizon to 'this_week'. Raises ValueError
    only if obj is not a dict.

    A *missing* horizon defaults to 'this_week'; an explicitly invalid horizon
    fails the per-action check and the entry is dropped. If every action is
    dropped the result has ``actions: []`` (caller treats empty actions as a
    soft failure to trigger retry).
    """
    if not isinstance(obj, dict):
        raise ValueError("plan response is not a JSON object")

    actions = []
    raw_actions = obj.get("actions")
    if isinstance(raw_actions, list):
        for action in raw_actions:
            if isinstance(action, dict) and "horizon" not in action:
                action = dict(action, horizon="this_week")
            if _valid_action(action):
                actions.append(action)
            if len(actions) == 5:
                break

    first_index = obj.get("first_action_index")
    if not _is_int(first_index) or first_index not in range(len(actions) or 1):
        first_index = 0

    review_in_days = obj.get("review_in_days")
    if not _is_int(review_in_days) or review_in_days < 1:
        review_in_days = 7

    principles = obj.get("relevant_principles")
    rules = obj.get("rule_suggestions")
    return {
        "domain": str(obj.get("domain") or domain),
        "goal": str(obj.get("goal") or goal_text),
        "diagnosis": str(obj.get("diagnosis") or ""),
        "actions": actions,
        "first_action_index": first_index,
        "relevant_principles": principles if isinstance(principles, list) else [],
        "rule_suggestions": rules if isinstance(rules, list) else [],
        "review_in_days": review_in_days,
    }


def parse_plan_response(raw: str, goal_text: str, domain: str) -> tuple[dict | None, str]:
    """Strips ``` fences, json.loads, validates. Returns (plan, "") on success
    or (None, reason) on failure — caller decides whether to retry."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty response"
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"
    try:
        plan = coerce_plan(obj, goal_text, domain)
    except ValueError as e:
        return None, str(e)
    ok, reason = validate_plan(plan)
    if not ok:
        return None, reason
    return plan, ""
