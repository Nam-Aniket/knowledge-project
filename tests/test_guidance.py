import unittest
import unittest.mock as mock
import os
import sqlite3
import tempfile
import json
from datetime import datetime, timezone

# Ensure project root is in path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import guidance


def _valid_plan_json(goal, domain):
    """A schema-conformant action plan used by the generation tests."""
    return {
        "domain": domain,
        "goal": goal,
        "diagnosis": "Grounded read of the situation.",
        "actions": [
            {
                "action": "A/B test two subject lines on 50 emails",
                "horizon": "this_week",
                "time_estimate_min": 45,
                "success_criterion": "Both variants sent to 25 recipients each",
                "due_offset_days": 3,
                "metric": {"name": "reply_rate", "type": "objective", "unit": "%"},
            },
            {
                "action": "Rewrite the first line of the template with personalization",
                "horizon": "today",
                "time_estimate_min": 20,
                "success_criterion": "New template saved and used for next batch",
                "due_offset_days": 1,
            },
        ],
        "first_action_index": 1,
        "relevant_principles": [{"principle": "Personalization is key", "source": "The Cold Email Manifesto, Ch. 4"}],
        "rule_suggestions": ["Keep emails short"],
        "review_in_days": 7,
    }


class TestGuidanceLayer(unittest.TestCase):
    def setUp(self):
        # Create a temp file for database
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)
        self.conn = db.get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def test_database_schema(self):
        """Verify that guidance layer tables are created by init_db()."""
        cursor = self.conn.cursor()
        tables = ['goals', 'experiments', 'metric_logs', 'reviews', 'rules']
        for t in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            self.assertIsNotNone(cursor.fetchone(), f"Table {t} should exist")

    def test_goals_crud(self):
        """Test goals creation, retrieval, and updating."""
        # 1. Add goal
        goal_id = db.add_goal(self.conn, domain="business", title="Increase outreach reply rate", description="Target 15% reply rate", stage="planning")
        self.assertEqual(goal_id, 1)

        # 2. Get active goals
        goals = db.get_goals(self.conn, domain="business", status="active")
        self.assertEqual(len(goals), 1)
        self.assertEqual(goals[0]["title"], "Increase outreach reply rate")
        self.assertEqual(goals[0]["stage"], "planning")

        # 3. Update goal
        db.update_goal(self.conn, goal_id, stage="executing", status="completed")
        goals_active = db.get_goals(self.conn, domain="business", status="active")
        self.assertEqual(len(goals_active), 0)

        goals_all = db.get_goals(self.conn, domain="business", status="completed")
        self.assertEqual(len(goals_all), 1)
        self.assertEqual(goals_all[0]["stage"], "executing")

    def test_experiments_crud(self):
        """Test experiments creation, retrieval, and updating."""
        goal_id = db.add_goal(self.conn, domain="health", title="Lose 5kg weight")
        
        # 1. Add experiment
        exp_id = db.add_experiment(
            self.conn, goal_id=goal_id, title="Keto diet",
            hypothesis="Keto diet will lose 2kg in 2 weeks",
            metric_name="weight", success_condition="<80kg",
            failure_condition=">82kg", review_date="2026-06-22"
        )
        self.assertEqual(exp_id, 1)

        # 2. Get active experiments
        exps = db.get_experiments(self.conn, goal_id=goal_id, status="active")
        self.assertEqual(len(exps), 1)
        self.assertEqual(exps[0]["title"], "Keto diet")
        self.assertEqual(exps[0]["metric_name"], "weight")
        self.assertEqual(exps[0]["success_condition"], "<80kg")

        # 3. Update experiment
        db.update_experiment(self.conn, exp_id, status="completed", outcome="Success, weight is 79kg")
        exps_active = db.get_experiments(self.conn, goal_id=goal_id, status="active")
        self.assertEqual(len(exps_active), 0)

        exps_completed = db.get_experiments(self.conn, goal_id=goal_id, status="completed")
        self.assertEqual(len(exps_completed), 1)
        self.assertEqual(exps_completed[0]["outcome"], "Success, weight is 79kg")

    def test_metric_logs_crud(self):
        """Test logging metrics and retrieving them."""
        goal_id = db.add_goal(self.conn, domain="business", title="Improve outreach reply rate")
        exp_id = db.add_experiment(self.conn, goal_id=goal_id, title="Personalized first lines")

        # Log a goal metric
        db.add_metric_log(self.conn, metric_name="reply_rate", value=12.5, unit="%", note="First batch", goal_id=goal_id)
        # Log an experiment metric
        db.add_metric_log(self.conn, metric_name="reply_rate", value=14.0, unit="%", note="Second batch", experiment_id=exp_id)

        logs = db.get_metric_logs(self.conn, metric_name="reply_rate")
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["value"], 14.0)
        self.assertEqual(logs[1]["value"], 12.5)

        logs_by_goal = db.get_metric_logs(self.conn, goal_id=goal_id)
        self.assertEqual(len(logs_by_goal), 1)
        self.assertEqual(logs_by_goal[0]["value"], 12.5)

    def test_reviews_crud(self):
        """Test reviews creation and retrieval."""
        goal_id = db.add_goal(self.conn, domain="business", title="Improve outreach reply rate")
        review_id = db.add_review(
            self.conn, what_happened="Outreach rate went up",
            what_worked="Personalization", what_didnt="Generic templates",
            lesson="Always personalize first line", next_action="Scale personalization",
            goal_id=goal_id
        )
        self.assertEqual(review_id, 1)

        reviews = db.get_reviews(self.conn, goal_id=goal_id)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["what_happened"], "Outreach rate went up")
        self.assertEqual(reviews[0]["lesson"], "Always personalize first line")

    def test_rules_crud(self):
        """Test personal rules creation, retrieval, and updating."""
        rule_id = db.add_rule(self.conn, domain="business", rule_text="Never send generic cold emails", source="review:1", confidence="tested")
        self.assertEqual(rule_id, 1)

        rules = db.get_rules(self.conn, domain="business")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["rule_text"], "Never send generic cold emails")
        self.assertEqual(rules[0]["confidence"], "tested")

        # Update rule
        db.update_rule(self.conn, rule_id, confidence="proven", active=0)
        rules_active = db.get_rules(self.conn, domain="business", active=True)
        self.assertEqual(len(rules_active), 0)

        rules_all = db.get_rules(self.conn, domain="business", active=False)
        self.assertEqual(len(rules_all), 1)
        self.assertEqual(rules_all[0]["confidence"], "proven")

    def test_domain_pack_loading(self):
        """Verify that seed domain packs are created and loadable."""
        pack = guidance.load_domain_pack("business")
        self.assertEqual(pack["domain"], "business")
        self.assertIn("diagnostic_questions", pack)
        self.assertIn("metrics", pack)
        
        # Fallback test
        general_pack = guidance.load_domain_pack("nonexistent_domain")
        self.assertEqual(general_pack["domain"], "general")

    def test_domain_detection(self):
        """Verify domain detection based on keyword match rules."""
        domain1 = guidance.detect_domain("I want to start a business or startup")
        self.assertEqual(domain1, "business")

        domain2 = guidance.detect_domain("How should I budget my compound interest savings?")
        self.assertEqual(domain2, "wealth")

        domain3 = guidance.detect_domain("My sleep hours are low")
        self.assertEqual(domain3, "health")

        domain4 = guidance.detect_domain("I want to brainstorm and prototype an idea")
        self.assertEqual(domain4, "ideation")

        domain5 = guidance.detect_domain("Some completely random text")
        self.assertEqual(domain5, "general")

    @mock.patch('query.perform_hybrid_search')
    @mock.patch('query.retrieve_concept_context')
    def test_generate_guidance_brief(self, mock_retrieve_concept, mock_hybrid_search):
        """Test generating guidance brief with mocked LLM Client and search results."""
        mock_hybrid_search.return_value = [
            ({"chunk_id": 1, "text": "Personalization increases response rate.", "location": "Ch. 4", "source_title": "The Cold Email Manifesto", "source_author": "Expert"}, 0.9)
        ]
        mock_retrieve_concept.return_value = "Concept: Outreach -> Email templates"

        # Mock LLMClient
        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        
        brief_json = _valid_plan_json("Improve outreach reply rate", "business")
        mock_llm.generate_completion.return_value = json.dumps(brief_json)

        brief = guidance.generate_guidance_brief(
            goal_text="Improve outreach reply rate",
            domain="business",
            db_path=self.db_path,
            llm=mock_llm
        )

        self.assertEqual(brief["domain"], "business")
        self.assertEqual(brief["goal"], "Improve outreach reply rate")
        self.assertTrue(brief["actions"])
        self.assertEqual(brief["rule_suggestions"], ["Keep emails short"])
        mock_hybrid_search.assert_called_once()
        mock_retrieve_concept.assert_called_once()

    @mock.patch('query.perform_hybrid_search')
    @mock.patch('query.retrieve_concept_context')
    def test_brief_retry_on_bad_json(self, mock_retrieve_concept, mock_hybrid_search):
        """Garbage first response triggers exactly one retry, which succeeds."""
        mock_hybrid_search.return_value = []
        mock_retrieve_concept.return_value = ""
        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        mock_llm.generate_completion.side_effect = [
            "Sure! Here is some prose, not JSON.",
            json.dumps(_valid_plan_json("Test goal", "general")),
        ]

        brief = guidance.generate_guidance_brief("Test goal", "general", self.db_path, mock_llm)
        self.assertTrue(brief["actions"])
        self.assertEqual(mock_llm.generate_completion.call_count, 2)
        self.assertNotIn("parse_error", brief)
        self.assertNotIn("raw_response", brief)

    @mock.patch('query.perform_hybrid_search')
    @mock.patch('query.retrieve_concept_context')
    def test_brief_validates_actions(self, mock_retrieve_concept, mock_hybrid_search):
        """Every returned action conforms to the plan schema."""
        from plan_schema import VALID_HORIZONS
        mock_hybrid_search.return_value = []
        mock_retrieve_concept.return_value = ""
        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        mock_llm.generate_completion.return_value = json.dumps(_valid_plan_json("Test goal", "general"))

        brief = guidance.generate_guidance_brief("Test goal", "general", self.db_path, mock_llm)
        self.assertTrue(brief["actions"])
        for action in brief["actions"]:
            self.assertIn(action["horizon"], VALID_HORIZONS)
            self.assertIsInstance(action["due_offset_days"], int)

    def test_materialize_creates_records(self):
        """materialize_plan persists one goal + one experiment per action under a shared plan_id."""
        plan = _valid_plan_json("Improve outreach reply rate", "business")
        plan["actions"].append({
            "action": "Clean the prospect list of bounced addresses",
            "horizon": "this_month",
            "time_estimate_min": 60,
            "success_criterion": "Bounce rate below 1%",
            "due_offset_days": 14,
        })
        result = guidance.materialize_plan(plan, self.db_path)

        goals = self.conn.execute(
            "SELECT id, plan_id FROM goals WHERE plan_id = ?", (result["plan_id"],)
        ).fetchall()
        self.assertEqual(len(goals), 1)

        experiments = self.conn.execute(
            "SELECT title, success_condition, metric_name, review_date FROM experiments WHERE plan_id = ?",
            (result["plan_id"],),
        ).fetchall()
        self.assertEqual(len(experiments), 3)
        by_title = {e[0]: e for e in experiments}
        first = by_title["A/B test two subject lines on 50 emails"]
        self.assertEqual(first[1], "Both variants sent to 25 recipients each")
        self.assertEqual(first[2], "reply_rate")
        for e in experiments:
            datetime.strptime(e[3], "%Y-%m-%d")

    def test_checkin_no_chat_logs_review(self):
        """No chat model: the update is logged as one review, no LLM call."""
        goal_id = db.add_goal(self.conn, domain="wealth", title="Save $2k")
        db.add_experiment(self.conn, goal_id=goal_id, title="Cancel 2 subscriptions",
                          success_condition="2 cancelled")

        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm.chat_model = "none"
        mock_llm.generate_completion.side_effect = AssertionError("must not be called")

        result = guidance.checkin_plan(goal_id, "I cancelled one sub", self.db_path, mock_llm)
        mock_llm.generate_completion.assert_not_called()
        self.assertEqual(len(result["reviews"]), 1)
        reviews = db.get_reviews(self.conn, goal_id=goal_id)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0]["what_happened"], "I cancelled one sub")

    def test_checkin_chat_completes_and_stores_decision(self):
        """Chat path: experiment completed, review written, decision stored as atomic fact."""
        goal_id = db.add_goal(self.conn, domain="wealth", title="Save $2k")
        exp_id = db.add_experiment(self.conn, goal_id=goal_id, title="Cancel 2 subscriptions",
                                   success_condition="2 cancelled")

        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        mock_llm.get_embedding.return_value = [0.1] * 8
        mock_llm.generate_completion.return_value = json.dumps({
            "summary": "Both subscriptions cancelled. Goal on track.",
            "experiment_updates": [
                {"experiment_id": exp_id, "decision": "complete", "reason": "Both subs cancelled"}
            ],
            "key_decisions": ["User cancels unused subscriptions quarterly to control spending"],
        })

        result = guidance.checkin_plan(goal_id, "Cancelled both subscriptions today", self.db_path, mock_llm)
        self.assertIn(exp_id, result["completed"])
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(len(result["facts_stored"]), 1)

        exp = db.get_experiments(self.conn, goal_id=goal_id, status="completed")
        self.assertEqual(len(exp), 1)
        fact_row = self.conn.execute(
            "SELECT category, fact FROM atomic_memories WHERE id = ?", (result["facts_stored"][0],)
        ).fetchone()
        self.assertEqual(fact_row[0], "decision")
        self.assertIn("subscriptions", fact_row[1])

    def test_retrieval_only_when_no_chat(self):
        """No chat model: retrieval-only brief, generate_completion never called."""
        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm.chat_model = "none"
        mock_llm.generate_completion.side_effect = AssertionError("must not be called")

        brief = guidance.generate_guidance_brief("Test goal", "general", self.db_path, mock_llm)
        self.assertIsInstance(brief, dict)
        self.assertEqual(brief["actions"], [])
        mock_llm.generate_completion.assert_not_called()

    @mock.patch('guidance.LLMClient')
    @mock.patch('guidance.generate_guidance_brief')
    def test_mcp_generate_guidance_tool(self, mock_gen_brief, mock_llm_class):
        """Test the MCP generate_guidance tool handler wrapper."""
        # Set database path env var to temp db
        os.environ["DATABASE_PATH"] = self.db_path

        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        mock_llm_class.return_value = mock_llm

        brief_data = {
            "domain": "business",
            "goal": "Test Goal"
        }
        mock_gen_brief.return_value = brief_data

        result = guidance.generate_guidance_tool("Test Goal", domain="business")
        parsed = json.loads(result)
        self.assertEqual(parsed["domain"], "business")
        self.assertEqual(parsed["goal"], "Test Goal")

    def test_mcp_list_goals_experiments_tool(self):
        """Test the MCP list_goals_and_experiments tool handler wrapper."""
        # Add a goal
        db.add_goal(self.conn, domain="business", title="MCP active goal")
        db.add_experiment(self.conn, goal_id=1, title="MCP active experiment", metric_name="replies")
        db.add_rule(self.conn, domain="business", rule_text="MCP active rule")

        # Call tool
        result = guidance.list_goals_experiments_tool(domain="business", topic=None)
        
        # We need to temporarily force the database path in the tool
        with mock.patch('guidance.resolve_db_path', return_value=self.db_path):
            result = guidance.list_goals_experiments_tool(domain="business", topic=None)

        self.assertIn("MCP active goal", result)
        self.assertIn("MCP active experiment", result)
        self.assertIn("MCP active rule", result)

class TestSynthesisPack(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp()
        db.init_db(self.db_path)

    def tearDown(self):
        os.close(self.db_fd)
        os.unlink(self.db_path)

    def _no_chat_llm(self):
        mock_llm = mock.Mock()
        mock_llm.provider = "none"
        mock_llm.chat_model = "none"
        mock_llm.get_embedding.return_value = None
        return mock_llm

    @mock.patch('query.perform_hybrid_search')
    @mock.patch('query.retrieve_concept_context')
    def test_build_synthesis_pack_structure(self, mock_concept, mock_search):
        """build_synthesis_pack returns mode==synthesis_pack with all required keys."""
        mock_search.return_value = []
        mock_concept.return_value = ""
        from plan_schema import PLAN_SCHEMA_DESCRIPTION
        pack = guidance.build_synthesis_pack("Save money", "wealth", self.db_path, self._no_chat_llm())
        self.assertEqual(pack["mode"], "synthesis_pack")
        self.assertTrue(pack["instruction"])
        self.assertEqual(pack["schema"], PLAN_SCHEMA_DESCRIPTION)
        ctx = pack["context"]
        for key in ("retrieved_knowledge", "graph_context", "known_facts", "active_goals",
                    "active_experiments", "personal_rules", "diagnostic_questions", "available_metrics"):
            self.assertIn(key, ctx)

    @mock.patch('query.perform_hybrid_search')
    @mock.patch('query.retrieve_concept_context')
    def test_generate_guidance_tool_no_chat_returns_synthesis_pack(self, mock_concept, mock_search):
        """generate_guidance_tool with no-chat LLM returns JSON with mode==synthesis_pack."""
        mock_search.return_value = []
        mock_concept.return_value = ""
        mock_llm = self._no_chat_llm()
        with mock.patch('guidance.LLMClient', return_value=mock_llm), \
             mock.patch('guidance.resolve_db_path', return_value=self.db_path):
            result = guidance.generate_guidance_tool("Save money", domain="wealth")
        parsed = json.loads(result)
        self.assertEqual(parsed["mode"], "synthesis_pack")

    def test_submit_guidance_plan_materializes(self):
        """submit_guidance_plan_tool with a valid 2-action plan creates goal + experiments sharing plan_id."""
        plan = _valid_plan_json("Improve outreach", "business")
        plan_str = json.dumps(plan)
        with mock.patch('guidance.resolve_db_path', return_value=self.db_path):
            result_str = guidance.submit_guidance_plan_tool(plan_str)
        result = json.loads(result_str)
        self.assertEqual(result["status"], "materialized")
        self.assertIn("goal_id", result)
        self.assertIn("experiment_ids", result)
        self.assertEqual(len(result["experiment_ids"]), 2)
        self.assertEqual(result["synthesized_by"], "host-agent")

        conn = db.get_connection(self.db_path)
        goals = conn.execute("SELECT id FROM goals WHERE plan_id = ?", (result["plan_id"],)).fetchall()
        self.assertEqual(len(goals), 1)
        exps = conn.execute("SELECT id FROM experiments WHERE plan_id = ?", (result["plan_id"],)).fetchall()
        self.assertEqual(len(exps), 2)
        conn.close()

    def test_submit_guidance_plan_rejects_garbage(self):
        """submit_guidance_plan_tool with garbage input returns error, no goal created."""
        with mock.patch('guidance.resolve_db_path', return_value=self.db_path):
            result_str = guidance.submit_guidance_plan_tool("not json at all")
        result = json.loads(result_str)
        self.assertIn("error", result)
        conn = db.get_connection(self.db_path)
        goals = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        conn.close()
        self.assertEqual(goals, 0)

    def test_submit_guidance_plan_dedup(self):
        """Submitting the same plan twice returns duplicate on the second call, goal count stays 1."""
        plan = _valid_plan_json("Improve outreach", "business")
        plan_str = json.dumps(plan)
        with mock.patch('guidance.resolve_db_path', return_value=self.db_path):
            r1 = json.loads(guidance.submit_guidance_plan_tool(plan_str))
            r2 = json.loads(guidance.submit_guidance_plan_tool(plan_str))
        self.assertEqual(r1["status"], "materialized")
        self.assertEqual(r2["status"], "duplicate")
        self.assertEqual(r2["goal_id"], r1["goal_id"])
        conn = db.get_connection(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
