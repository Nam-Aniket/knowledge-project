"""Tests for LLMClient chat provider decoupling (CHAT_PROVIDER env var)."""
import os
import sys
import unittest
from unittest import mock

# Ensure repo root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_ENV = {"TESTING": "true"}


def make_client(env_overrides):
    """Build an LLMClient with a clean env overlay."""
    # We need to reload the module to pick up env changes because LLMClient
    # reads env at __init__ time (no module-level caching), so direct patching works.
    from llm_client import LLMClient
    with mock.patch.dict(os.environ, {**BASE_ENV, **env_overrides}, clear=True):
        return LLMClient()


class TestChatProviderDefault(unittest.TestCase):
    """CHAT_PROVIDER unset: chat_provider mirrors LLM_PROVIDER."""

    def test_local_provider_gives_none_chat(self):
        client = make_client({"LLM_PROVIDER": "local"})
        self.assertEqual(client.chat_provider, "local")
        self.assertEqual(client.chat_model, "none")

    def test_gemini_backecompat(self):
        """LLM_PROVIDER=gemini + key set, no CHAT_PROVIDER → chat_model=gemini-1.5-flash."""
        client = make_client({
            "LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "fake-key",
        })
        self.assertEqual(client.chat_provider, "gemini")
        self.assertEqual(client.chat_model, "gemini-1.5-flash")

    def test_none_provider_gives_none_chat(self):
        client = make_client({"LLM_PROVIDER": "none"})
        self.assertEqual(client.chat_provider, "none")
        self.assertEqual(client.chat_model, "none")


class TestChatProviderOllama(unittest.TestCase):
    """LLM_PROVIDER=local + CHAT_PROVIDER=ollama routes completions to Ollama."""

    @mock.patch("requests.post")
    def test_ollama_chat_dispatch(self, mock_post):
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"role": "assistant", "content": "hi"}}
        mock_post.return_value = mock_resp

        client = make_client({
            "LLM_PROVIDER": "local",
            "CHAT_PROVIDER": "ollama",
            "CHAT_MODEL": "llama3",
            "OLLAMA_HOST": "http://localhost:11434",
        })
        self.assertEqual(client.chat_provider, "ollama")
        self.assertEqual(client.chat_model, "llama3")

        result = client.generate_completion("sys", "prompt")
        self.assertEqual(result, "hi")
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertIn("/api/chat", call_url)

    def test_ollama_default_model(self):
        client = make_client({
            "LLM_PROVIDER": "local",
            "CHAT_PROVIDER": "ollama",
        })
        self.assertEqual(client.chat_model, "llama3")


class TestChatProviderOpenAIError(unittest.TestCase):
    """CHAT_PROVIDER=openai without key raises ValueError."""

    def test_missing_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            make_client({"LLM_PROVIDER": "local", "CHAT_PROVIDER": "openai"})
        self.assertIn("OPENAI_API_KEY", str(ctx.exception))


class TestChatProviderGeminiBackcompat(unittest.TestCase):
    """LLM_PROVIDER=gemini, key set, no CHAT_PROVIDER → unchanged behavior."""

    def test_gemini_chat_model_default(self):
        client = make_client({
            "LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "fake-key",
        })
        self.assertEqual(client.chat_provider, "gemini")
        self.assertEqual(client.chat_model, "gemini-1.5-flash")


if __name__ == "__main__":
    unittest.main()
