"""How much a model thinks: the effort levels its chat template accepts.

OpenAI's `reasoning_effort` (and Anthropic's thinking/effort) is one knob, but
open models each read it differently -- or not at all -- through their chat
templates: Qwen3.8 takes reasoning_effort low/medium/xhigh and raises an error
on anything else, Nemotron Super flips a `low_effort` flag, Seed-OSS takes a
token budget, Qwen3.5 can only switch thinking on and off. An agent cannot
know which, so a recipe names its template's style (Recipe.reasoning) and the
Hub does the rest: /v1/models lists each model's levels, and a request that
asks for any effort gets the nearest level the model has, written the way its
template reads it (chat_template_kwargs), so no request fails on a word.

Every style below was read off the template its weights ship with.
"""
from __future__ import annotations

# The shared scale, least thinking first. "none" means no thinking at all.
SCALE = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

# style -> (default level, {level: chat_template_kwargs that select it}).
# A level's kwargs are what the template reads; {} is the template's own
# default. Levels are listed in SCALE order.
STYLES: dict[str, tuple[str, dict[str, dict]]] = {
    # Qwen3.8 (27B, Flash-Next and their finetunes): reasoning_effort in
    # (xhigh, medium, low), anything else raises; enable_thinking=false skips it.
    "qwen3.8": ("xhigh", {
        "none": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "xhigh": {"reasoning_effort": "xhigh"},
    }),
    # Thinking on or off, nothing between (Qwen3.5/3.6, Ornith, Gemma 4,
    # Laguna, GLM-4.7, MiMo, MiniCPM-V, Nemotron Nano/Lightning, Ling 3).
    "switch": ("high", {
        "none": {"enable_thinking": False},
        "high": {"enable_thinking": True},
    }),
    # Nemotron 3 Super / Puzzle: low_effort appends "{reasoning effort: low}"
    # to the last user turn.
    "nemotron-low": ("high", {
        "none": {"enable_thinking": False},
        "low": {"low_effort": True},
        "high": {},
    }),
    # Nemotron 3.5 Bolt / Super VL, Comet: medium_effort appends
    # "{reasoning effort: efficient}".
    "nemotron-medium": ("high", {
        "none": {"enable_thinking": False},
        "medium": {"medium_effort": True},
        "high": {},
    }),
    # Nemotron Cascade 2 / Elastic / Nano Omni: reasoning_budget appends
    # "{thinking token budget: N}" to the last user turn.
    "nemotron-budget": ("high", {
        "none": {"enable_thinking": False},
        "low": {"reasoning_budget": 1024},
        "medium": {"reasoning_budget": 4096},
        "high": {},
    }),
    # Seed-OSS: thinking_budget in tokens, 0 skips thinking, -1 (default) is
    # unlimited; its template's reflection gears run 512 to 16384.
    "seed-oss": ("high", {
        "none": {"thinking_budget": 0},
        "low": {"thinking_budget": 512},
        "medium": {"thinking_budget": 4096},
        "high": {"thinking_budget": -1},
    }),
    # gpt-oss (harmony): "Reasoning: low|medium|high" in the system turn.
    "gpt-oss": ("medium", {
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high": {"reasoning_effort": "high"},
    }),
    # DeepSeek-V4.x: thinking_mode chat|thinking, reasoning_effort
    # low/medium/high/max (default high).
    "deepseek-v4": ("high", {
        "none": {"thinking_mode": "chat"},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    }),
    # DeepSeek-V4.1 (vLLM's deepseek_v41 tokenizer): `thinking` on/off and
    # reasoning_effort minimal..max (default high), which it turns into a
    # thinking budget: minimal/low 50, medium/high/xhigh 75, max 100.
    "deepseek-v4.1": ("high", {
        "none": {"thinking": False},
        "minimal": {"reasoning_effort": "minimal"},
        "low": {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high": {"reasoning_effort": "high"},
        "xhigh": {"reasoning_effort": "xhigh"},
        "max": {"reasoning_effort": "max"},
    }),
    # GLM-5.3: reasoning_effort low|high, anything else is max; the Hub's
    # recipe patches its template so enable_thinking=false skips thinking.
    "glm-5.3": ("max", {
        "none": {"enable_thinking": False},
        "low": {"reasoning_effort": "low"},
        "high": {"reasoning_effort": "high"},
        "max": {"reasoning_effort": "max"},
    }),
    # Hy3: reasoning_effort no_think (its default) | low | high.
    "hy3": ("none", {
        "none": {"reasoning_effort": "no_think"},
        "low": {"reasoning_effort": "low"},
        "high": {"reasoning_effort": "high"},
    }),
    # Muse Glimmer: "Reasoning strength: <x>." in the system turn, default high.
    "muse": ("high", {
        "low": {"reasoning_strength": "low"},
        "medium": {"reasoning_strength": "medium"},
        "high": {"reasoning_strength": "high"},
    }),
}

# Words agents send that are not on the scale.
_ALIASES = {"off": "none", "disabled": "none", "no_think": "none", "on": "high",
            "enabled": "high", "minimum": "minimal", "maximum": "max", "xlow": "minimal"}


def levels(style: str | None) -> list[str]:
    return list(STYLES[style][1]) if style in STYLES else []


def default(style: str | None) -> str | None:
    return STYLES[style][0] if style in STYLES else None


def nearest(style: str, asked: str) -> str | None:
    """The model's level closest to the one asked for; on a tie, the larger
    (an agent that asked for high gets xhigh rather than medium). None for a
    word that is not an effort at all."""
    asked = _ALIASES.get(str(asked).strip().lower(), str(asked).strip().lower())
    if asked not in SCALE or style not in STYLES:
        return None
    have = STYLES[style][1]
    if asked in have:
        return asked
    if asked == "none":
        # A model that cannot stop thinking thinks as little as it can.
        return next(iter(have))
    want = SCALE.index(asked)
    return min((lvl for lvl in have if lvl != "none"),
               key=lambda lvl: (abs(SCALE.index(lvl) - want), -SCALE.index(lvl)))


def kwargs_for(style: str, level: str) -> dict:
    return dict(STYLES[style][1][level])


def summary(style: str | None) -> dict | None:
    """What /v1/models reports: the levels, least thinking first, and the
    one used when a request names none."""
    if style not in STYLES:
        return None
    return {"efforts": levels(style), "default": default(style)}
