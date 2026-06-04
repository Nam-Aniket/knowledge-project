import os
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class LLMClient:
    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "gemini").lower()
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.openai_key = os.getenv("OPENAI_API_KEY")
        
        # Configure models
        if self.provider == "openai":
            self.embed_model = os.getenv("EMBED_MODEL") or "text-embedding-3-small"
            self.chat_model = os.getenv("CHAT_MODEL") or "gpt-4o-mini"
        else: # Default is gemini
            self.embed_model = os.getenv("EMBED_MODEL") or "text-embedding-004"
            self.chat_model = os.getenv("CHAT_MODEL") or "gemini-1.5-flash"
            
        # Verify credentials
        if self.provider == "openai" and not self.openai_key:
            raise ValueError("LLM_PROVIDER is set to 'openai' but OPENAI_API_KEY is not configured in .env.")
        elif self.provider == "gemini" and not self.gemini_key:
            raise ValueError("LLM_PROVIDER is set to 'gemini' but GEMINI_API_KEY is not configured in .env.")

    def get_embedding(self, text: str) -> list[float]:
        """Generates a single text embedding vector."""
        if self.provider == "openai":
            return self._get_openai_embedding(text)
        else:
            return self._get_gemini_embedding(text)

    def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generates embeddings for a list of texts in batch for performance."""
        if not texts:
            return []
            
        if self.provider == "openai":
            return self._get_openai_embeddings_batch(texts)
        else:
            return self._get_gemini_embeddings_batch(texts)

    def generate_completion(self, system_instruction: str, prompt: str) -> str:
        """Generates a chat completion response from the configured LLM."""
        if self.provider == "openai":
            return self._generate_openai_completion(system_instruction, prompt)
        else:
            return self._generate_gemini_completion(system_instruction, prompt)

    # --- GEMINI IMPLEMENTATIONS ---
    
    def _get_gemini_embedding(self, text: str) -> list[float]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.embed_model}:embedContent?key={self.gemini_key}"
        payload = {
            "content": {
                "parts": [{"text": text}]
            }
        }
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"Gemini embedding API error ({res.status_code}): {res.text}")
        
        data = res.json()
        return data["embedding"]["values"]

    def _get_gemini_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        # Gemini allows batching up to 100 requests in a batchEmbedContents call
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.embed_model}:batchEmbedContents?key={self.gemini_key}"
        
        # Batch sizes of 100
        batch_size = 100
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            chunk_texts = texts[i:i+batch_size]
            requests_list = [
                {
                    "model": f"models/{self.embed_model}",
                    "content": {"parts": [{"text": t}]}
                }
                for t in chunk_texts
            ]
            
            payload = {"requests": requests_list}
            res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if res.status_code != 200:
                # Fallback to single requests if batch fails
                for t in chunk_texts:
                    embeddings.append(self._get_gemini_embedding(t))
                continue
                
            data = res.json()
            for item in data.get("embeddings", []):
                embeddings.append(item["values"])
                
        return embeddings

    def _generate_gemini_completion(self, system_instruction: str, prompt: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.chat_model}:generateContent?key={self.gemini_key}"
        
        # Prepare system instruction and contents
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"Instructions:\n{system_instruction}\n\nQuery:\n{prompt}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2
            }
        }
        
        res = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
        if res.status_code != 200:
            raise RuntimeError(f"Gemini completion API error ({res.status_code}): {res.text}")
            
        data = res.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from Gemini: {data}")

    # --- OPENAI IMPLEMENTATIONS ---
    
    def _get_openai_embedding(self, text: str) -> list[float]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "input": text,
            "model": self.embed_model
        }
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        if res.status_code != 200:
            raise RuntimeError(f"OpenAI embedding API error ({res.status_code}): {res.text}")
            
        data = res.json()
        return data["data"][0]["embedding"]

    def _get_openai_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        url = "https://api.openai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        
        # Batch size of 2008 for OpenAI
        batch_size = 500
        embeddings = []
        
        for i in range(0, len(texts), batch_size):
            chunk_texts = texts[i:i+batch_size]
            payload = {
                "input": chunk_texts,
                "model": self.embed_model
            }
            res = requests.post(url, json=payload, headers=headers, timeout=30)
            if res.status_code != 200:
                # Fallback to single requests if batch fails
                for t in chunk_texts:
                    embeddings.append(self._get_openai_embedding(t))
                continue
                
            data = res.json()
            # Sort data objects by index to keep alignments
            sorted_data = sorted(data.get("data", []), key=lambda x: x["index"])
            for item in sorted_data:
                embeddings.append(item["embedding"])
                
        return embeddings

    def _generate_openai_completion(self, system_instruction: str, prompt: str) -> str:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.chat_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2
        }
        res = requests.post(url, json=payload, headers=headers, timeout=45)
        if res.status_code != 200:
            raise RuntimeError(f"OpenAI completion API error ({res.status_code}): {res.text}")
            
        data = res.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected response format from OpenAI: {data}")
