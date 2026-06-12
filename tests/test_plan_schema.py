import unittest

import plan_schema


def _conformant_plan():
    return {
        "domain": "health",
        "goal": "Sleep 8 hours a night",
        "diagnosis": "Sleep is inconsistent due to late screen time.",
        "actions": [
            {
                "action": "Set a 10pm phone curfew",
                "horizon": "today",
                "time_estimate_min": 5,
                "success_criterion": "Phone on charger outside bedroom by 10pm",
                "due_offset_days": 0,
                "metric": {"name": "phone_curfew_kept", "type": "objective", "unit": "days"},
            },
            {
                "action": "Track wake-up time for a week",
                "horizon": "this_week",
                "time_estimate_min": 2,
                "success_criterion": "Seven consecutive wake-up times logged",
                "due_offset_days": 7,
            },
        ],
        "first_action_index": 0,
        "relevant_principles": [{"principle": "Consistency beats duration", "source": "Why We Sleep, Ch. 2"}],
        "rule_suggestions": ["No screens after 10pm"],
        "review_in_days": 7,
    }


class TestPlanSchema(unittest.TestCase):
    def test_valid_plan_passes(self):
        ok, reason = plan_schema.validate_plan(_conformant_plan())
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")

    def test_missing_actions_fails(self):
        plan = _conformant_plan()
        del plan["actions"]
        ok, reason = plan_schema.validate_plan(plan)
        self.assertFalse(ok)
        self.assertIn("actions", reason)

    def test_bad_horizon_dropped_by_coerce(self):
        plan = _conformant_plan()
        plan["actions"][0]["horizon"] = "someday"
        coerced = plan_schema.coerce_plan(plan, "Sleep 8 hours a night", "health")
        kept_actions = [a["action"] for a in coerced["actions"]]
        self.assertNotIn("Set a 10pm phone curfew", kept_actions)
        self.assertIn("Track wake-up time for a week", kept_actions)

    def test_parse_strips_fences(self):
        import json
        raw = "```json\n" + json.dumps(_conformant_plan()) + "\n```"
        plan, reason = plan_schema.parse_plan_response(raw, "Sleep 8 hours a night", "health")
        self.assertIsNotNone(plan, reason)
        self.assertEqual(len(plan["actions"]), 2)

    def test_parse_garbage_returns_none(self):
        plan, reason = plan_schema.parse_plan_response("not json", "g", "general")
        self.assertIsNone(plan)
        self.assertTrue(reason)

    def test_empty_plan_is_valid(self):
        ok, reason = plan_schema.validate_plan(plan_schema.empty_plan("g", "general"))
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
