"""Language-model providers. Each returns a parsed JSON object for a prompt."""

from __future__ import annotations

import json
import re

from ..config import Settings
from ..errors import NotConfigured, ProviderError, http


def _parse_json(text: str) -> dict:
    text = text.strip()
    # Some models wrap JSON in ```json fences.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Invalid output is often fixed by asking again, so it is retryable.
        raise ProviderError(f"model returned invalid JSON: {e}", retryable=True) from e
    if not isinstance(data, dict):
        raise ProviderError("model returned JSON that is not an object", retryable=True)
    return data


class GeminiLLM:
    name = "gemini"

    def __init__(self, s: Settings):
        self.key, self.model = s.gemini_api_key, s.gemini_model

    def generate_json(self, prompt: str) -> dict:
        if not self.key:
            raise NotConfigured("GEMINI_API_KEY is empty")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
        }
        data = http("POST", url, json=body, headers={"x-goog-api-key": self.key}).json()
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ProviderError(f"Gemini returned no answer (block reason: {reason})", refused=bool(reason))
        cand = candidates[0]
        if cand.get("finishReason") == "SAFETY":
            raise ProviderError("Gemini refused for safety reasons", refused=True)
        parts = (cand.get("content") or {}).get("parts") or []
        return _parse_json("".join(p.get("text", "") for p in parts))


class GroqLLM:
    name = "groq"

    def __init__(self, s: Settings):
        self.key, self.model = s.groq_api_key, s.groq_model

    def generate_json(self, prompt: str) -> dict:
        if not self.key:
            raise NotConfigured("GROQ_API_KEY is empty")
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You reply with one JSON object only."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.9,
        }
        data = http(
            "POST", "https://api.groq.com/openai/v1/chat/completions",
            json=body, headers={"Authorization": f"Bearer {self.key}"},
        ).json()
        return _parse_json(data["choices"][0]["message"]["content"])


class MockLLM:
    """Offline stand-in. Returns a fixed, valid answer based on the prompt type."""

    name = "mock"

    def __init__(self, s: Settings):
        pass

    def generate_json(self, prompt: str) -> dict:
        if '"ideas"' in prompt:
            return {"ideas": [
                {"title": f"Idea {i}", "hook": f"Placeholder hook number {i}", "angle": "mock"}
                for i in range(1, 6)
            ]}
        topic_match = re.search(r"TOPIC: (.+)", prompt)
        topic = topic_match.group(1).strip() if topic_match else "a topic"
        moods = ["excited", "calm", "inspiring", "triumphant"]
        return {
            "title": f"Test Reel: {topic}"[:60],
            "music_mood": "inspiring",
            "post_caption": f"A quick look at {topic}. Save this for later!",
            "hashtags": ["reelforge", "test", "shorts"],
            "scenes": [
                {
                    "narration": text,
                    "on_screen_text": ots,
                    "visual_prompt": f"{topic}, scene {i + 1}",
                    "stock_query": topic,
                    "mood": moods[i % len(moods)],
                }
                for i, (text, ots) in enumerate([
                    (f"Here is something surprising about {topic}.", "Did you know?"),
                    ("Most people never notice it, but it changes everything.", "Most people miss this"),
                    ("Start small, stay consistent, and watch it grow.", "Start small"),
                    ("Follow for more tips like this one.", "Follow for more"),
                ])
            ],
        }
