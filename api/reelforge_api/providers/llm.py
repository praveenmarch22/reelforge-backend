"""Language models: ideas, scripts, scene rewrites and hooks (technical design 9.0).

Gemini first, Groq as the fallback. Each returns one parsed JSON object; the caller checks
its shape. "mock" is an offline stand-in for development and tests.
"""

from __future__ import annotations

import json
import re

from ..config import Settings
from .base import NotConfigured, ProviderError, http, json_of

# Three tries per model at most 30 s each keeps two models inside the job's 4-minute limit.
TIMEOUT = 30


def parse_json(text: str) -> dict:
    text = text.strip()
    # Some models wrap JSON in ```json fences.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Unusable output is often fixed by asking again (S-1).
        raise ProviderError("the model's answer was not valid JSON", retryable=True) from None
    if not isinstance(data, dict):
        raise ProviderError("the model's answer was not a JSON object", retryable=True)
    return data


class GeminiLLM:
    name = "gemini"

    def __init__(self, s: Settings):
        self.key, self.model = s.gemini_api_key, s.gemini_model

    def generate_json(self, prompt: str, kind: str, context: dict) -> dict:  # noqa: ARG002
        if not self.key:
            raise NotConfigured("GEMINI_API_KEY is not set")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
        }
        data = json_of(http("POST", url, json=body, headers={"x-goog-api-key": self.key}, timeout=TIMEOUT))
        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            if reason:
                raise ProviderError(f"Gemini refused the request ({reason})", refused=True)
            raise ProviderError("Gemini returned no answer", retryable=True)
        candidate = candidates[0]
        if candidate.get("finishReason") in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
            raise ProviderError("Gemini refused for safety reasons", refused=True)
        parts = (candidate.get("content") or {}).get("parts") or []
        return parse_json("".join(p.get("text", "") for p in parts))


class GroqLLM:
    name = "groq"

    def __init__(self, s: Settings):
        self.key, self.model = s.groq_api_key, s.groq_model

    def generate_json(self, prompt: str, kind: str, context: dict) -> dict:  # noqa: ARG002
        if not self.key:
            raise NotConfigured("GROQ_API_KEY is not set")
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
            "POST",
            "https://api.groq.com/openai/v1/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {self.key}"},
            timeout=TIMEOUT,
        )
        data = json_of(data)
        try:
            return parse_json(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise ProviderError("Groq returned an unexpected answer", retryable=True) from None


class MockLLM:
    """Offline stand-in with fixed, valid answers. Never used in production.

    For trying failure paths by hand, a topic, script or instruction containing one of these
    words makes it misbehave: [refuse] (safety refusal), [fail] (provider error),
    [invalid] (unusable output).
    """

    name = "mock"

    def __init__(self, s: Settings):  # noqa: ARG002
        pass

    def generate_json(self, prompt: str, kind: str, context: dict) -> dict:  # noqa: ARG002
        text = json.dumps(context)
        if "[refuse]" in text:
            raise ProviderError("mock refusal", refused=True)
        if "[fail]" in text:
            raise ProviderError("mock failure", retryable=False)
        if "[invalid]" in text:
            return {"unexpected": True}
        return getattr(self, f"_{kind}")(context)

    def _ideas(self, c: dict) -> dict:
        niche = c["niche"]
        return {
            "ideas": [
                {
                    "title": f"{niche} idea {i}",
                    "hook": f"The one {niche} mistake almost everyone makes, number {i}.",
                    "angle": "A common mistake with a quick fix",
                }
                for i in range(1, c["count"] + 1)
            ]
        }

    def _script(self, c: dict) -> dict:
        topic = c["topic"]
        narrations = [
            f"Here is something surprising about {topic}.",
            "Most people never notice it, but it changes everything.",
            "Start small, stay consistent, and watch it grow.",
            "Follow for more tips like this one.",
        ]
        moods = ["excited", "calm", "inspiring", "triumphant"]
        return {
            "title": f"{topic}"[:60],
            "music_mood": "inspiring",
            "post_caption": f"A quick look at {topic}. Save this for later!",
            "hashtags": ["reelforge", "tips", "learnontiktok"],
            "scenes": [
                {
                    "narration": n,
                    "on_screen_text": " ".join(n.split()[:4]),
                    "visual_prompt": f"{topic}, scene {i + 1}",
                    "stock_query": " ".join(topic.split()[:3]),
                    "mood": moods[i % len(moods)],
                }
                for i, n in enumerate(narrations)
            ],
        }

    def _annotate_script(self, c: dict) -> dict:
        narrations = c["narrations"]
        first = narrations[0]
        return {
            "title": " ".join(first.split()[:6])[:60],
            "music_mood": "calm",
            "post_caption": f"{first[:120]} Save this for later!",
            "hashtags": ["reelforge", "tips"],
            "scenes": [
                {
                    "on_screen_text": " ".join(n.split()[:4]),
                    "visual_prompt": f"a shot showing: {n[:80]}",
                    "stock_query": " ".join(n.split()[:3]),
                    "mood": "calm",
                }
                for n in narrations
            ],
        }

    def _rewrite_scene(self, c: dict) -> dict:
        return {
            "narration": f"{c['narration'].rstrip('.!?')}, and that is the point.",
            "on_screen_text": c.get("on_screen_text") or "The point",
        }

    def _autopilot_topics(self, c: dict) -> dict:
        return {
            "topics": [
                {
                    "category": category,
                    "subject": f"{category.title()} Legend {i}",
                    "angle": f"The day story {i} happened",
                }
                for category, count in c["mix"].items()
                for i in range(1, count + 1)
            ]
        }

    def _caption(self, c: dict) -> dict:
        topic = c.get("topic") or c.get("title") or "this"
        return {
            "title": f"{topic}: what nobody tells you"[:90],
            "caption": f"Everything you need to know about {topic} in under a minute. Follow for more.",
            "hashtags": ["shorts", "learn", "facts"],
        }

    def _hooks(self, c: dict) -> dict:
        topic = c.get("topic") or "this"
        return {
            "hooks": [
                f"Stop scrolling: {topic} is not what you think.",
                f"I wish I knew this about {topic} sooner.",
                f"Three seconds to change how you see {topic}.",
            ]
        }


PROVIDERS = {"gemini": GeminiLLM, "groq": GroqLLM, "mock": MockLLM}
