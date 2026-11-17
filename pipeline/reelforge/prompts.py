"""Prompts for the language model. Kept in one place so they are easy to tune."""

from __future__ import annotations

from .models import MOODS, WORDS_PER_SECOND


def ideas_prompt(niche: str, keywords: str, count: int) -> str:
    return f"""You are a short-form video strategist for Instagram Reels.
Suggest {count} video ideas for a solo creator in this niche.

NICHE: {niche}
KEYWORDS (optional): {keywords or "none"}

Each idea must work as a faceless Reel of 15-90 seconds: voiceover with visuals and captions.
Make the hooks specific and scroll-stopping. No clickbait that the video can't deliver.

Reply with JSON only, exactly this shape:
{{"ideas": [{{"title": "...", "hook": "first line spoken in the video", "angle": "why it works"}}]}}"""


def script_prompt(topic: str, tone: str, mode: str, target_seconds: int, style: str, character: str) -> str:
    words = int(target_seconds * WORDS_PER_SECOND)
    scenes = max(3, min(12, round(target_seconds / 6)))
    visual_rules = (
        "Each visual_prompt describes one AI-generated video shot: subject, action, setting, "
        "camera movement and lighting. Keep the same character and look in every scene."
        if mode == "full_ai" else
        "Each visual_prompt describes a shot that stock footage or an AI image could show. "
        "stock_query is 2-4 plain words to search a stock video site (for example 'morning coffee window')."
    )
    return f"""You write scripts for vertical Instagram Reels narrated by a voiceover.

TOPIC: {topic}
TONE: {tone}
TARGET LENGTH: {target_seconds} seconds, about {words} spoken words in total (never more than {words + 15})
SCENES: about {scenes}
VISUAL STYLE: {style}
MAIN CHARACTER: {character or "none"}

Rules:
- Scene 1 is the hook: one short line that makes people stop scrolling.
- The last scene is a short call to action (follow, save or comment).
- narration is exactly what the voice says. Plain spoken English, no emojis, no stage directions.
- on_screen_text is a short headline for that scene (max 6 words), or "" for none.
- mood is one of: {", ".join(MOODS)}. Choose it to match the emotion of the scene.
- {visual_rules}
- post_caption is the Instagram caption (max 300 characters). hashtags: 5-10, without the # sign.

Reply with JSON only, exactly this shape:
{{"title": "...", "music_mood": "one mood from the list", "post_caption": "...", "hashtags": ["..."],
  "scenes": [{{"narration": "...", "on_screen_text": "...", "visual_prompt": "...",
               "stock_query": "...", "mood": "..."}}]}}"""


MOOD_VISUAL_CUES = {
    "calm": "soft natural light, gentle slow camera drift, peaceful expression",
    "joyful": "warm golden light, bright colours, smiling, lively movement",
    "excited": "vivid colours, dynamic fast camera push-in, energetic expression",
    "funny": "bright playful lighting, exaggerated expression, comedic timing",
    "inspiring": "sunrise light, wide hopeful framing, slow rising camera",
    "triumphant": "heroic low angle, dramatic backlight, confident expression",
    "serious": "neutral muted tones, steady camera, focused expression",
    "tense": "low-key dramatic lighting, slow push-in, tense worried expression",
    "mysterious": "fog, deep shadows, cool blue tones, slow reveal",
    "sad": "overcast cool light, desaturated colours, slow pull-back, melancholic expression",
}


def visual_prompt(scene_prompt: str, mood: str, style: str, character: str, vertical: bool = True) -> str:
    """Full prompt for an AI image or clip: scene + mood cues + shared style anchor."""
    anchor = f"{style} style"
    if character:
        anchor += f", featuring {character}"
    parts = [scene_prompt, MOOD_VISUAL_CUES.get(mood, ""), anchor]
    if vertical:
        parts.append("vertical 9:16 composition, high detail, no text, no watermark")
    return ", ".join(p for p in parts if p)
