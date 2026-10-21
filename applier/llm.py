"""Small Gemini wrapper returning validated JSON, shared by CV tailoring and form answers."""

import json
import os
import time

from utils.analyzer import DEFAULT_MODEL, MAX_ATTEMPTS, REQUEST_TIMEOUT_MS, pace, retry_delay


class LLM:
    def __init__(self, client=None, model=None):
        self.model = model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL
        self.owned = client is None
        if client is None:
            key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not key:
                raise RuntimeError("Set GEMINI_API_KEY in .env before applying")
            try:
                from google import genai
            except ImportError:
                raise RuntimeError("Install requirements-ai.txt to use Gemini") from None
            client = genai.Client(api_key=key, http_options={"timeout": REQUEST_TIMEOUT_MS,
                                                             "retry_options": {"attempts": 1}})
        self.client = client

    def json(self, prompt, schema):
        config = {"response_mime_type": "application/json", "response_json_schema": schema}
        for attempt in range(MAX_ATTEMPTS):
            try:
                if self.owned:
                    pace()
                response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                result = json.loads(response.text)
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
                return result
            except Exception as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                time.sleep(retry_delay(error, attempt))

    def close(self):
        if self.owned:
            try:
                self.client.close()
            except Exception:
                pass
