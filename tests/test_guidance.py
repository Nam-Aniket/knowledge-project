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
        
        brief_json = {
            "domain": "business",
            "stage": "planning",
            "goal": "Improve outreach reply rate",
            "missing_information": ["List quality details"],
            "relevant_principles": [{"principle": "Personalization is key", "source": "The Cold Email Manifesto"}],
            "key_assumptions": ["List is valid"],
            "risks_and_traps": ["Spam filters"],
            "suggested_metrics": [{"name": "reply_rate", "type": "objective", "unit": "%"}],
            "next_action": "A/B test subject lines",
            "success_condition": "Reply rate > 10%",
            "failure_condition": "Spam rate > 2%",
            "review_date": "2026-06-22",
            "rule_suggestions": ["Keep emails short"]
        }
        mock_llm.generate_completion.return_value = json.dumps(brief_json)

        brief = guidance.generate_guidance_brief(
            goal_text="Improve outreach reply rate",
            domain="business",
            db_path=self.db_path,
            llm=mock_llm
        )

        self.assertEqual(brief["domain"], "business")
        self.assertEqual(brief["goal"], "Improve outreach reply rate")
        self.assertEqual(brief["next_action"], "A/B test subject lines")
        self.assertEqual(brief["rule_suggestions"], ["Keep emails short"])
        mock_hybrid_search.assert_called_once()
        mock_retrieve_concept.assert_called_once()

    @mock.patch('guidance.LLMClient')
    def test_generate_guidance_fallback_parsing(self, mock_llm_class):
        """Test fallback parser if LLM outputs poorly formatted JSON."""
        mock_llm = mock.Mock()
        mock_llm.provider = "mock"
        mock_llm.chat_model = "mock-model"
        
        # Simulate LLM returning conversational text wrapped around a fake schema
        bad_response = '''
        Sure! Here is your guidance:
        Next Action: Build a prototype
        Success Condition: It works
        Review Date: 2026-06-20
        '''
        mock_llm.generate_completion.return_value = bad_response
        
        brief = guidance.generate_guidance_brief(
            goal_text="Test idea",
            domain="ideation",
            db_path=self.db_path,
            llm=mock_llm
        )
        self.assertEqual(brief["domain"], "ideation")
        self.assertEqual(brief["next_action"], "Build a prototype")
        self.assertEqual(brief["success_condition"], "It works")
        self.assertEqual(brief["review_date"], "2026-06-20")
        self.assertIn("Could not strictly parse", brief["parse_error"])

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

if __name__ == "__main__":
    unittest.main()
