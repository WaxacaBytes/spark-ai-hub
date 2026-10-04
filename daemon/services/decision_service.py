"""Typed decisions behind the Hub's MCP `decide` tool.

A decision model does not write text: it reads a state (text or JSON, plus
images and videos) and typed questions -- a choice among named options, a
score on an ordered scale, a yes/no -- and returns a probability for every
allowed answer. Every such model speaks the Jev / SystemOne API
(POST /v1/systemone); a recipe opts in with the `systemone-api` tag, so adding
a model is a recipe, never code.

Images and videos arrive as URLs, exactly as the image and video tools take
them, and are handed to the model as base64. A decision takes well under a
second to a few seconds, so it is answered in the call, not as a job.
"""
from __future__ import annotations

import base64
import json

import aiohttp

from daemon.services import proxy_service
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services.image_service import ImageError, load_input
from daemon.services.registry_service import get_recipes
from daemon.services.video_service import MAX_VIDEO_INPUT_BYTES

SYSTEMONE_TAG = "systemone-api"
QUESTION_TYPES = ("choice", "score", "noul")
TIMEOUT = aiohttp.ClientTimeout(total=300, sock_connect=10)


def backends() -> set[str]:
    """Every decision model the catalog offers."""
    return {slug for slug, recipe in get_recipes().items() if SYSTEMONE_TAG in recipe.tags}


async def pick_backend(model: str | None) -> str:
    """The model to ask: the one named, else one that is running."""
    installed = sorted(backends() & await get_installed_slugs())
    if model:
        if model not in installed:
            raise ImageError(f"'{model}' is not an installed decision model. "
                             f"Installed: {', '.join(installed) or 'none'}.")
        installed = [model]
    starting = []
    for slug in installed:
        if await is_recipe_running(slug):
            if is_ready(slug):
                return slug
            starting.append(slug)
    if starting:
        raise ImageError(f"{starting[0]} is still starting (loading weights). Call start_model "
                         f"with model {starting[0]} to wait until it is ready.")
    if not installed:
        raise ImageError("No decision model is installed on the Spark. "
                         "Install one (e.g. Clef-Flash) from the Spark AI Hub first.")
    raise ImageError(f"No decision model is running on the Spark. Start {' or '.join(installed)} "
                     "with start_model first.")


def _check(questions: object) -> dict:
    # Models often send a nested object as its JSON text, sometimes with a stray
    # closing brace after it; take the first whole JSON value.
    if isinstance(questions, str):
        try:
            questions = json.JSONDecoder().raw_decode(questions.strip())[0]
        except ValueError:
            pass
    if not isinstance(questions, dict) or not questions:
        raise ImageError("'questions' must map each question id to a question.")
    for qid, q in questions.items():
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES:
            raise ImageError(f"Question '{qid}': 'type' must be choice, score or noul.")
        criteria = q.get("criteria")
        if q["type"] == "choice" and not (isinstance(criteria, dict) and len(criteria) >= 2):
            raise ImageError(f"Question '{qid}': a choice needs 'criteria' mapping at least two "
                             "option ids to their descriptions.")
        if q["type"] == "score" and not (isinstance(criteria, list) and len(criteria) >= 2):
            raise ImageError(f"Question '{qid}': a score needs 'criteria' listing at least two "
                             "levels, lowest first.")
    return questions


async def decide(state, questions, images: list[str], videos: list[str],
                 model: str | None, user: dict | None = None) -> dict:
    """The model's answers: {question id: answer with probabilities}."""
    if state is None or state == "":
        raise ImageError("'state' is required: the text or JSON the decision is about "
                         "(for media alone, say what the images or videos are).")
    body = {"state": state, "questions": _check(questions)}
    slug = await pick_backend(model)
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        if images:
            body["images"] = [base64.b64encode(await load_input(ref, session, user)).decode()
                              for ref in images]
        if videos:
            body["videos"] = [base64.b64encode(await load_input(
                ref, session, user, suffix=".mp4", max_bytes=MAX_VIDEO_INPUT_BYTES)).decode()
                for ref in videos]
        async with session.post(f"{proxy_service.internal_url(slug)}/v1/systemone",
                                headers=proxy_service.probe_headers(), json=body) as r:
            reply = await r.json(content_type=None)
    if r.status != 200:
        message = (reply.get("error") or {}).get("message") if isinstance(reply, dict) else None
        raise ImageError(f"{slug} refused the request: {message or f'HTTP {r.status}'}")
    return {"model": slug, "answers": reply["answers"]}
