"""MCP server: web search, image, video, 3D and music generation, and typed decisions, for any agent.

Speaks MCP's Streamable HTTP transport in its stateless form, at POST /mcp:
one JSON-RPC request per POST, no session id, no server-initiated stream (GET
answers 405, which the spec defines as "this server offers none"). That is the
whole of the transport an MCP client needs to list and call tools, and it keeps
the daemon free of an SDK and of per-client session state.

Every request is plain JSON except tools/call, which answers as an SSE stream
when the client accepts one (every Streamable HTTP client must). A render takes
minutes, and a silent socket that long trips client read timeouts — Hermes, for
one, gives up after 300s of no bytes. The stream carries a keepalive comment
every few seconds, and progress notifications when the client asked for them.

Authentication is the Hub's own: a client sends the same API key it uses for
the LLM, or an OAuth token (oauth.py) for clients that cannot send a key, such
as Claude's connectors.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from daemon.routers import containers
from daemon.services import (
    favorites_service,
    audio_service, decision_service, image_service, link_service, media_store, mesh_service,
    upload_service, video_service, web_service,
)
from daemon.config import settings
from daemon.services.connect_service import request_origin
from daemon.services.docker_service import (
    available_memory_gb, get_installed_slugs, get_pending, is_ready, is_recipe_running, memory_plan,
    start_health_check,
)
from daemon.services.registry_service import get_recipes

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
# The Hub's mark, so a client that shows server icons (MCP 2025-11-25,
# Implementation.icons) shows ours rather than a generic tile. Inlined as a
# data URI: a LAN or offline client has no https URL it could fetch.
_ICON = Path(__file__).resolve().parents[2] / "frontend" / "public" / "icon-128.png"
SERVER_INFO = {
    "name": "spark-ai-hub", "title": "Spark AI Hub", "version": "1.0.0",
    **({"icons": [{"src": "data:image/png;base64," + base64.b64encode(_ICON.read_bytes()).decode(),
                   "mimeType": "image/png", "sizes": ["128x128"]}]} if _ICON.is_file() else {}),
}
KEEPALIVE_SECONDS = 10
# How long get_image / get_video / get_3d / get_music / start_model hold a call open
# before answering "not done yet". Short enough for any client's tool timeout,
# long enough that polling needs no sleep and a render takes a handful of calls.
# Fixed on purpose: renders have exactly one shape (start, then collect), with
# no knob for a client to get wrong.
CHECK_WAIT = 20

INSTRUCTIONS = (
    "Searches the web privately with web_search (SearXNG on the user's DGX Spark: no "
    "accounts, cookies or API keys) and reads a page with web_fetch (https only, fetched "
    "from the Spark). Use these for anything current or beyond your training data, and "
    "cite the URLs you rely on. If web_search says SearXNG is not running, start it with "
    "start_model (model \"searxng\") and search again.\n\n"
    "Generates and edits images, generates and edits videos, turns pictures into 3D models, and composes "
    "music, with open models running on the user's DGX Spark. Call list_image_models / "
    "list_video_models / list_3d_models to see which are running and how much memory is free "
    "(memory_free_gb; a stopped model's fits_now says whether it would start now). A model "
    "marked `favorite` is the one the Spark's admin recommends: use it unless the user "
    "names another or it cannot do the job. A stopped model can be started with start_model; when memory is short, "
    "stop_model frees a media model that is not rendering. Every result is saved on the Hub and returned as a URL plus a "
    "preview. Show the user the URL. To refine an image, pass its URL to edit_image. "
    "The URLs are private: they open for the user signed in to the Hub, not for you. "
    "Results and uploads stay on the Hub until the user deletes them; "
    "they can see and delete everything they made or uploaded at <Hub address>/files, "
    "and delete_file removes one when they ask you to.\n\n"
    "Files in your own workspace: tools take URLs, never file paths or file contents, "
    "because the models run on the Spark. To use a file you have, call create_upload "
    "and run the curl command it returns; the JSON it prints has the `url` to pass to "
    "edit_image or generate_video. To save a result into your workspace, call "
    "create_download with its URL and run that curl command. Both links are one-off "
    "and short-lived: ask for a new one each time. A picture "
    "attached to the chat is not a file you can upload; ask the user to add it at "
    "<Hub address>/files and paste the link.\n\n"
    "Every render works the same way: generate_image, edit_image, generate_video, generate_3d "
    "and generate_music start a job and return its job_id at once. Then call get_image, "
    "get_video, get_3d or get_music with that job_id. Each get call waits up to "
    f"{CHECK_WAIT} seconds and returns either the result or \"Not done yet\"; call it "
    "again with the same job_id until it returns the result or an error. No sleep is "
    "needed between calls. Keep calling in the same turn instead of telling the user "
    "to wait, and never start the same render again: that queues a second one. "
    "start_model works the same way: call it again until the model is ready.\n\n"
    "Some image and video models take LoRAs: add-ons listed under a model's `loras` in "
    "list_image_models or list_video_models that teach it one trick, such as reshooting a "
    "picture from another camera angle or swapping a character into a clip. Pass the LoRA's "
    "id in `lora` to edit_image, generate_image or generate_video and follow its recommended "
    "notes; the Hub loads it into the model. Several LoRAs of one model run together when "
    "listed together, such as a Turbo (fewer steps) plus an effect; a Turbo's step count "
    "and shift are the ones used. A video model marked `prompt_guide` was trained on prompts "
    "in a set format: call get_prompt_guide for it before writing its prompt.\n\n"
    "Makes decisions with decide: a decision model on the Spark, such as Cloudflare's Clef "
    "(list_decision_models names them), answers typed questions "
    "(a choice among named options, a score, a yes/no) about text, JSON, images and videos "
    "with a probability for every answer, e.g. to sort images and videos into the user's "
    "categories. You gather the inputs and act on the answers; let the model make the call, "
    "and ask the user when its confidence is low."
)

_ASYNC_IMAGE = (
    " Returns a job_id at once, not the image: then call get_image with it until the "
    "image arrives. Do not start the render again."
)

_STEPS_HINT = (
    "Sampling steps. Leave this out: each model then uses the steps its model "
    "card recommends (list_image_models shows them — a step-distilled model "
    "wants 4, a full diffusion model 50). Set it only when the user asks."
)

_MODEL_ARG = {"type": "string",
              "description": ("A model id from list_image_models, list_video_models, "
                              "list_3d_models or list_decision_models, a music model, or "
                              "\"searxng\" for the search engine web_search runs on.")}

_IMAGE_REFS = (
    "Each item is a URL returned by generate_image or edit_image, an upload's URL "
    "(create_upload, or the Hub's My files page), or a public http(s) image URL. "
    "Never a file path or the file's contents: the model runs on the Spark, not "
    "where you are, so upload the file first with create_upload."
)

TOOLS = [
    {
        "name": "web_search",
        "title": "Web search",
        "description": (
            "Search the web privately through SearXNG on the Spark. Returns titles, URLs "
            "and snippets; read a result in full with web_fetch. Use it for current "
            "information and anything beyond your training data, and cite the URLs you use."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for."},
                "max_results": {"type": "integer", "minimum": 1,
                                "maximum": web_service.MAX_RESULTS,
                                "default": web_service.DEFAULT_RESULTS,
                                "description": "How many results to return."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "web_fetch",
        "title": "Fetch web page",
        "description": (
            "Read one web page, fetched from the Spark, as markdown (HTML) or text. HTTPS "
            "only (an admin account may also read http:// pages on its local network); "
            "sends no cookies, so pages that need a login show what an anonymous "
            "visitor sees. Long pages are truncated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "An https:// URL."}},
            "required": ["url"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": True},
    },
    {
        "name": "generate_image",
        "title": "Generate image",
        "description": (
            "Create an image from a text prompt on the Spark. Write a detailed, "
            "visual prompt (subject, setting, lighting, style, composition); put any "
            "text that must appear in the image in quotes." + _ASYNC_IMAGE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What the image should show."},
                "aspect_ratio": {"type": "string", "enum": image_service.ASPECT_RATIOS,
                                 "default": "1:1"},
                "negative_prompt": {"type": "string",
                                    "description": "Things to keep out of the image."},
                "seed": {"type": "integer", "minimum": 0,
                         "description": "Reuse a seed to reproduce an image. Omit for random."},
                "steps": {"type": "integer", "minimum": 1, "maximum": 60,
                          "description": _STEPS_HINT},
                "model": {"type": "string",
                          "description": "A model id from list_image_models. Omit to use whichever is running."},
                "lora": {"type": "array", "items": {"type": "string"},
                         "description": ("LoRA ids from a model's `loras` in list_image_models: "
                                         "add-ons that each teach that model one trick (reshoot the "
                                         "subject from another camera angle, render in fewer "
                                         "steps, ...). List several to run them together. Follow "
                                         "their `recommended` notes. Omit for the model on its own.")},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit_image",
        "title": "Edit image",
        "description": (
            "Edit one to three images with a natural-language instruction on the "
            "Spark, e.g. 'replace the background with a rainy neon street, keep the "
            "person unchanged'." + _ASYNC_IMAGE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The edit to make."},
                "images": {"type": "array", "items": {"type": "string"},
                           "minItems": 1, "maxItems": 3, "description": _IMAGE_REFS},
                "seed": {"type": "integer", "minimum": 0},
                "steps": {"type": "integer", "minimum": 1, "maximum": 60,
                          "description": _STEPS_HINT},
                "model": {"type": "string",
                          "description": "A model id from list_image_models. Omit to use whichever is running."},
                "lora": {"type": "array", "items": {"type": "string"},
                         "description": ("LoRA ids from a model's `loras` in list_image_models: "
                                         "add-ons that each teach that model one trick (reshoot the "
                                         "subject from another camera angle, render in fewer "
                                         "steps, ...). List several to run them together. Follow "
                                         "their `recommended` notes. Omit for the model on its own.")},
            },
            "required": ["prompt", "images"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_image",
        "title": "Get image",
        "description": (
            f"Collect the image job that generate_image or edit_image started. Waits up to "
            f"{CHECK_WAIT} s; returns the image when done, or \"Not done yet\", in which "
            "case call get_image again with the same job_id until it returns the image "
            "or an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "generate_video",
        "title": "Generate video",
        "description": (
            "Start rendering a short video on the Spark from a text prompt; from a first "
            "frame, a last frame or both (image-to-video); from reference pictures, clips "
            "and sounds that set who and what appears (multi-reference, audio-reference); "
            "or from an input video to edit, extend or inpaint (video-to-video). "
            "list_video_models says which inputs, shapes and lengths each model takes. "
            "Returns a job_id at once, not the video: then call get_video with it until the "
            "video arrives (rendering takes minutes). Do not start the render again. "
            "Describe the motion and camera as well as the scene."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What happens in the video."},
                "image": {"type": "string",
                          "description": ("Optional picture: the first frame for image-to-video, "
                                          "or <Picture 1> for a multi-reference model. " + _IMAGE_REFS)},
                "last_image": {"type": "string",
                               "description": ("Optional last frame the clip ends on (models "
                                               "that list last-frame); with `image` too, the "
                                               "clip goes from one to the other. " + _IMAGE_REFS)},
                "images": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                           "description": ("More reference pictures, <Picture 2>, <Picture 3> "
                                           "and so on, for multi-reference models. " + _IMAGE_REFS)},
                "audio": {"type": "string",
                          "description": ("Optional reference sound, <Audio 1>: a voice or "
                                          "soundtrack for audio-reference models. A URL returned by "
                                          "generate_music, an upload's URL (create_upload, or My "
                                          "files), or a public http(s) WAV URL.")},
                "video": {"type": "string",
                          "description": ("Optional input video to edit (models that list "
                                          "video-to-video). A URL returned by get_video, an upload's "
                                          "URL (create_upload, or My files), or a public "
                                          "http(s) video URL.")},
                "seconds": {"type": "integer", "minimum": 1, "maximum": video_service.MAX_SECONDS,
                            "description": ("Length. Omit for the model's default; each model's "
                                            "max_seconds is in list_video_models.")},
                "aspect_ratio": {"type": "string", "enum": video_service.ASPECT_RATIOS,
                                 "description": ("Shape; each model's aspect_ratios are in "
                                                 "list_video_models. Omit for 16:9, or for a "
                                                 "first/last-frame clip to follow the picture.")},
                "seed": {"type": "integer", "minimum": 0},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "Leave this out: the model card's setting is used."},
                "model": {"type": "string",
                          "description": "A model id from list_video_models. Omit to use whichever is running."},
                "lora": {"type": "array", "items": {"type": "string"},
                         "description": ("LoRA ids from a model's `loras` in list_video_models: "
                                         "add-ons that each teach that model one trick (swap a "
                                         "character, orbit a subject, render in fewer steps). List "
                                         "several to run them together, e.g. a Turbo plus an orbit. "
                                         "Follow their `recommended` notes. Omit for the model on "
                                         "its own.")},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_video",
        "title": "Get video",
        "description": (
            f"Collect the video job that generate_video started. Waits up to {CHECK_WAIT} s; "
            "returns the video URL and a still frame when done, or \"Not done yet\" with "
            "its progress, in which case call get_video again with the same job_id until "
            "it returns the video or an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_video_models",
        "title": "List video models",
        "description": ("List the Spark's video models: what each accepts, whether it is "
                        "running, and the settings its model card recommends; plus the "
                        "memory free to start one."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_prompt_guide",
        "title": "Get a model's prompt guide",
        "description": ("How to write a prompt for a video model that list_video_models marks "
                        "with prompt_guide: the format it was trained on (sections, timing, "
                        "speech and sound), condensed from its maker's docs. Read it before "
                        "writing that model's prompt."),
        "inputSchema": {
            "type": "object",
            "properties": {"model": {"type": "string", "description": "A model id from list_video_models."}},
            "required": ["model"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "generate_3d",
        "title": "Generate 3D model",
        "description": (
            "Start turning one picture of an object into a textured 3D model (GLB) on the "
            "Spark. Use a picture of one whole object, centered. list_3d_models says what "
            "background each model takes: models that cut the object out themselves take any "
            "photo; for the others use a plain background, which the Hub cuts away first (a PNG "
            "whose background is already transparent is used as it is). Without a picture, make "
            "one first with generate_image, asking for the object \"centered on a plain white "
            "background\". "
            "With more pictures of the same object from other sides (`views`; models that list "
            "multi-view in list_3d_models), the sides and back are built from them instead of "
            "guessed: real photos work best, as drawn views often disagree with each other. "
            "Returns a job_id at once, not the model: then call get_3d with it until the model "
            "arrives (it takes minutes). Do not start it again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "image": {"type": "string", "description": "The picture to build from: the front "
                                                            "view. " + _IMAGE_REFS},
                "views": {"type": "object",
                          "properties": {v: {"type": "string"} for v in mesh_service.VIEWS},
                          "additionalProperties": False,
                          "description": ("Optional more views of the same object, for models that "
                                          "list multi-view: `left` is the object turned to face the "
                                          "picture's left (its left side to the camera), `back` its "
                                          "back, `right` its right side. Same rules as image: one "
                                          "object, plain background, a URL.")},
                "seed": {"type": "integer", "minimum": 0},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "Leave this out: the model's own setting (50) is used."},
                "model": {"type": "string",
                          "description": "A model id from list_3d_models. Omit to use whichever is running."},
            },
            "required": ["image"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_3d",
        "title": "Get 3D model",
        "description": (
            f"Collect the job that generate_3d started. Waits up to {CHECK_WAIT} s; returns "
            "the GLB's URL and the cut-out picture it was built from when done, or \"Not done "
            "yet\", in which case call get_3d again with the same job_id until it returns the "
            "model or an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_3d_models",
        "title": "List 3D models",
        "description": ("List the Spark's 3D models: what each takes and makes, and whether "
                        "it is running; plus the memory free to start one."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "generate_music",
        "title": "Generate music",
        "description": (
            "Compose a song on the Spark: lyrics plus a style caption in, a stereo WAV out. "
            "Put structure tags ([Verse], [Chorus], [Bridge], [Outro]) each on its own line "
            "in the lyrics. Describe genre, instruments, tempo (BPM), mood and production "
            "in style. Returns a job_id at once, not the song: then call get_music with it "
            "until the song arrives (composing takes minutes). Do not start it again."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lyrics": {"type": "string", "description": "The lyrics, with structure tags on their own lines."},
                "style": {"type": "string", "description": "Genre, instrumentation, tempo, mood, production."},
                "seconds": {"type": "integer", "minimum": 5, "maximum": audio_service.MAX_SECONDS,
                            "description": "Maximum length; the model may end the song sooner. Omit for 30."},
                "seed": {"type": "integer", "minimum": 0},
                "model": {"type": "string", "description": "Omit to use whichever music model is running."},
            },
            "required": ["lyrics", "style"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_music",
        "title": "Get music",
        "description": (
            f"Collect the song job that generate_music started. Waits up to {CHECK_WAIT} s; "
            "returns the song URL when done, or \"Not done yet\", in which case call "
            "get_music again with the same job_id until it returns the song or an error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_upload",
        "title": "Create upload link",
        "description": (
            "Get a one-time link to upload one image, MP4/MOV video or sound file (WAV, MP3, "
            "FLAC, Ogg) from your workspace to the Hub, without any key. Run the returned curl "
            "command with your file; it prints JSON whose `url` you then pass to edit_image "
            "(images) or generate_video (image, video or audio). The link works once and expires in "
            f"{link_service.LINK_TTL // 60} minutes; call this again for each file."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "create_download",
        "title": "Create download link",
        "description": (
            "Get a short-lived link to download one of the user's Hub files (an image, "
            "video, 3D model or song URL from the other tools, or an upload) into your workspace, "
            "without any key. Run the returned curl command. The link expires in "
            f"{link_service.LINK_TTL // 60} minutes; call this again whenever you need it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string",
                                   "description": "The Hub URL of the image, video, 3D model or song."}},
            "required": ["url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_file",
        "title": "Delete file",
        "description": (
            "Delete one of the user's Hub files for good: an image, video, 3D model or song URL from "
            "the other tools, or an upload. Only when the user asks for it; it cannot be undone."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string",
                                   "description": "The Hub URL of the image, video, 3D model, song or upload."}},
            "required": ["url"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": True, "idempotentHint": True},
    },
    {
        "name": "decide",
        "title": "Decide",
        "description": (
            "Ask the Spark's decision model typed questions about a state: text or JSON, "
            "plus up to 4 images and 2 videos. It does not write text; for each question it "
            "returns a probability for every allowed answer, the same every time for the "
            "same input. Use it to categorize, triage, score or check things, e.g. sort "
            "images and videos into the user's categories with one choice question whose "
            "criteria name each category and describe it. Act on confident answers and ask "
            "the user about uncertain ones instead of overriding the model. Answers come "
            "back in the call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "state": {
                    "description": ("What to decide about: text, or a JSON object/array. "
                                    "With media, say what it is and add any context."),
                },
                "questions": {
                    "type": "object",
                    "description": (
                        "Question id -> question. Each has `type`: \"choice\" (criteria maps "
                        "each option id to a one-line description), \"score\" (criteria lists "
                        "ordered levels, lowest first) or \"noul\" (yes/no, no criteria); and "
                        "`instructions`: the question in plain words. Up to 64 questions, "
                        "answered together."),
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string", "enum": ["choice", "score", "noul"]},
                            "instructions": {"type": "string"},
                            "criteria": {"type": ["object", "array"]},
                        },
                        "required": ["type"],
                    },
                },
                "images": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                           "description": "Images to look at. " + _IMAGE_REFS},
                "videos": {"type": "array", "items": {"type": "string"}, "maxItems": 2,
                           "description": (
                               "MP4 videos to watch, sampled at 2 frames a second, up to 64 "
                               "frames. Each item is a URL returned by generate_video, an "
                               "upload's URL (create_upload, or the Hub's My files page), or a "
                               "public http(s) video URL; never a file path.")},
                "model": {"type": "string",
                          "description": ("A model id from list_decision_models. Omit to use "
                                          "whichever is running.")},
            },
            "required": ["state", "questions"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "start_model",
        "title": "Start model",
        "description": (
            "Start one of the Spark's stopped image, video, 3D, music or decision models, or "
            "SearXNG (model \"searxng\"), the search engine web_search runs on, so the other "
            "tools can use it. Loading takes from under a minute to about 15 minutes for "
            f"the largest. Waits up to {CHECK_WAIT} s; returns once the model is ready, or "
            "\"Not done yet\", in which case call start_model again with the same model "
            "until it is ready. Refused when the model needs more memory than is free: the "
            "error names what is running, and stop_model can free a model among them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"model": _MODEL_ARG},
            "required": ["model"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": False, "idempotentHint": True},
    },
    {
        "name": "stop_model",
        "title": "Stop model",
        "description": (
            "Stop a running image, video, 3D, music or decision model, or SearXNG (model "
            "\"searxng\"), to free its memory, e.g. to start another with start_model. Refused "
            "while the model is rendering a job. Only these can be stopped here; LLMs and every "
            "other app are managed in the Hub."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"model": _MODEL_ARG},
            "required": ["model"],
            "additionalProperties": False,
        },
        "annotations": {"destructiveHint": True, "idempotentHint": True},
    },
    {
        "name": "list_decision_models",
        "title": "List decision models",
        "description": ("List the Spark's decision models, such as Cloudflare's Clef: what "
                        "each reads (text, images, videos) and whether it is running; plus "
                        "the memory free to start one."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_image_models",
        "title": "List image models",
        "description": ("List the Spark's image models: what each does, whether it is "
                        "running, and the settings its model card recommends; plus the "
                        "memory free to start one."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
]
_TOOL_NAMES = {t["name"] for t in TOOLS}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _sse(message: dict) -> str:
    return f"event: message\ndata: {json.dumps(message)}\n\n"


# ------------------------------------------------------------------- tools


def _loras(args: dict) -> list[str]:
    """The LoRA ids asked for: a list, or one id as a plain string."""
    value = args.get("lora") or []
    return [str(v).strip() for v in ([value] if isinstance(value, str) else value) if str(v).strip()]


def _int(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise image_service.ImageError(f"'{key}' must be an integer.") from None


async def call_tool(name: str, args: dict, origin: str, on_progress=None,
                    user: dict | None = None) -> dict:
    """Run one tool for `user`; failures come back as an isError result, never raise.

    Media belongs to the account that made it: `user` owns what this call
    creates, and can only use or collect their own images, videos and jobs.
    """
    try:
        if name == "web_search":
            query = str(args.get("query") or "").strip()
            if not query:
                raise web_service.WebError("'query' is required.")
            return _text(await web_service.search(query, _int(args, "max_results")))
        if name == "web_fetch":
            url = str(args.get("url") or "").strip()
            if not url:
                raise web_service.WebError("'url' is required.")
            return _text(await web_service.fetch(url, user))
        if name == "list_image_models":
            return _text(await _with_memory(await image_service.list_models()))
        if name == "list_decision_models":
            return _text(await _with_memory(await decision_service.list_models()))
        if name in ("create_upload", "create_download"):
            return await _link_tool(name, args, origin, user)
        if name == "delete_file":
            file_name = await _own_file(args, user)
            await media_store.delete(file_name, user)
            return {"content": [{"type": "text", "text": f"Deleted {file_name}."}],
                    "structuredContent": {"deleted": file_name}, "isError": False}
        if name == "decide":
            images, videos = args.get("images") or [], args.get("videos") or []
            if isinstance(images, str):
                images = [images]
            if isinstance(videos, str):
                videos = [videos]
            answer = await decision_service.decide(
                args.get("state"), args.get("questions"), [str(i) for i in images],
                [str(v) for v in videos], str(args.get("model") or "").strip() or None, user)
            return {"content": [{"type": "text", "text": json.dumps(answer, indent=2)}],
                    "structuredContent": answer, "isError": False}
        if name == "start_model":
            return await _start_model(args, origin, on_progress, user)
        if name == "stop_model":
            return await _stop_model(args)
        if name == "generate_music":
            lyrics = str(args.get("lyrics") or "").strip()
            style = str(args.get("style") or "").strip()
            if not lyrics or not style:
                raise image_service.ImageError("'lyrics' and 'style' are both required.")
            slug = (await audio_service.pick_backend(str(args.get("model") or "").strip() or None)).slug
            job_id = image_service.start_job("music", lambda: audio_service.generate(
                lyrics=lyrics, style=style, seconds=_int(args, "seconds"), seed=_int(args, "seed"),
                model=slug, user=user), slug, user)
            return _started("get_music", job_id, slug)
        if name in ("get_image", "get_music"):
            info = await image_service.check_job(_job_id(args), CHECK_WAIT, user)
            return _music_result(info, origin) if info["kind"] == "music" else _image_result(info, origin)
        if name == "list_video_models":
            return _text(await _with_memory(await video_service.list_models()))
        if name == "get_prompt_guide":
            return _text(video_service.prompt_guide(str(args.get("model") or "").strip()))
        if name == "list_3d_models":
            return _text(await _with_memory(await mesh_service.list_models()))
        if name == "generate_3d":
            image = str(args.get("image") or "").strip()
            if not image:
                raise image_service.ImageError("'image' is required: the picture to build the model from.")
            views = args.get("views") or {}
            if not isinstance(views, dict):
                raise image_service.ImageError("'views' must be an object: {\"left\": url, ...}.")
            views = {str(k): str(v).strip() for k, v in views.items() if str(v).strip()}
            slug = (await mesh_service.pick_backend(str(args.get("model") or "").strip() or None,
                                                    multi_view=bool(views))).slug
            job_id = image_service.start_job("mesh", lambda: mesh_service.generate(
                image=image, views=views, seed=_int(args, "seed"), steps=_int(args, "steps"),
                model=slug, user=user), slug, user)
            return _started("get_3d", job_id, slug)
        if name == "get_3d":
            return _mesh_result(await image_service.check_job(_job_id(args), CHECK_WAIT, user), origin)
        if name == "get_video":
            info = await video_service.check(_job_id(args), CHECK_WAIT, on_progress, user)
            return _video_result(info, origin)
        if name == "generate_video":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                raise image_service.ImageError("'prompt' is required.")
            info = await video_service.start(
                prompt=prompt, image=str(args.get("image") or "").strip() or None,
                video=str(args.get("video") or "").strip() or None,
                last_image=str(args.get("last_image") or "").strip() or None,
                images=[str(i).strip() for i in (args.get("images") or []) if str(i).strip()],
                audio=str(args.get("audio") or "").strip() or None,
                seconds=_int(args, "seconds"), aspect_ratio=str(args.get("aspect_ratio") or "") or None,
                seed=_int(args, "seed"), steps=_int(args, "steps"),
                model=str(args.get("model") or "").strip() or None,
                lora=_loras(args), user=user,
            )
            return _started("get_video", info["job_id"], info["model"], info)

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise image_service.ImageError("'prompt' is required.")
        params = image_service.Params(
            prompt=prompt,
            negative_prompt=str(args.get("negative_prompt") or ""),
            aspect_ratio=str(args.get("aspect_ratio") or "1:1"),
            seed=_int(args, "seed"),
            steps=_int(args, "steps"),
        )
        kind, images = "generate", []
        if name == "edit_image":
            kind, images = "edit", args.get("images")
            if isinstance(images, str):
                images = [images]
            if not isinstance(images, list) or not 1 <= len(images) <= 3:
                raise image_service.ImageError("'images' must list one to three images.")
            images = [str(i) for i in images]
        lora = _loras(args)
        # Picked now, so "no model is running" comes back from this call, not the first get.
        slug = (await image_service.pick_backend(
            kind, str(args.get("model") or "").strip() or None,
            await image_service.installed_lora(lora))).slug
        job_id = image_service.start_job(
            kind, lambda: image_service.run(kind, params, images, slug, user=user, lora=lora), slug, user)
        return _started("get_image", job_id, slug)
    except (image_service.ImageError, web_service.WebError) as exc:
        return _text(str(exc), is_error=True)
    except Exception as exc:  # noqa: BLE001 - surface anything else to the agent
        return _text(f"{name} failed: {type(exc).__name__}: {exc}", is_error=True)


def _job_id(args: dict) -> str:
    job_id = str(args.get("job_id") or "").strip()
    if not job_id:
        raise image_service.ImageError("'job_id' is required.")
    return job_id


def _next_call(tool: str, job_id: str) -> dict:
    """What an unfinished job's result tells a client that reads structuredContent only."""
    return {"done": False, "next_call": {"tool": tool, "arguments": {"job_id": job_id}}}


def _not_done(tool: str, job_id: str, status: str, info: dict) -> dict:
    """The one "keep collecting" answer every job gives, started or still running."""
    return {"content": [{"type": "text", "text": (
                f"Not done yet: {status}. This is a job, not the result. Now call {tool} "
                f"with job_id {job_id}, and keep calling it until it returns the result "
                "or an error. Do not start the render again.")}],
            "structuredContent": {"status": "rendering", **info, "job_id": job_id,
                                  **_next_call(tool, job_id)},
            "isError": False}


def _started(tool: str, job_id: str, model: str, info: dict | None = None) -> dict:
    return _not_done(tool, job_id, f"job {job_id} started on {model}",
                     {**(info or {}), "model": model})


# ------------------------------------------------------------ start / stop
# Agents may start and stop the media models the tools above list, and one
# app: SearXNG, the search engine web_search runs on. LLMs and every other app
# stay the Hub's. Both go through the same launch and stop the Hub's buttons use.

READY_POLL_SECONDS = 2
# One start at a time, so two agents cannot both see the same free memory and
# launch into it together.
_start_lock = asyncio.Lock()


MCP_APPS = {web_service.SEARXNG}


def _startable_slugs() -> set[str]:
    return (set(image_service.backends()) | set(video_service.backends())
            | set(mesh_service.backends()) | set(audio_service.backends())
            | decision_service.backends() | MCP_APPS)


async def _startable(args: dict) -> str:
    """The installed media model, or SearXNG, that `args` names."""
    slug = str(args.get("model") or "").strip()
    installed = _startable_slugs() & await get_installed_slugs()
    if slug not in installed:
        raise image_service.ImageError(
            f"'{slug}' is not an installed image, video, 3D, music or decision model, or "
            f"SearXNG. Installed: {', '.join(sorted(installed)) or 'none'}.")
    return slug


async def _with_memory(models: list[dict]) -> str:
    """A list_*_models answer: the models, and the memory free for starting
    one right now (CPU and GPU share it on the Spark), so an agent can tell
    which stopped model start_model would accept without trying."""
    _, free, _ = await memory_plan()
    free = max(free, 0)
    for model in models:
        if model.get("state") == "stopped" and model.get("min_memory_gb") is not None:
            model["fits_now"] = model["min_memory_gb"] <= free
        if favorites_service.is_favorite(model["model"]):
            model["favorite"] = True
    # The ones the admin starred first: the order is a hint agents follow.
    models.sort(key=lambda m: not m.get("favorite"))
    return json.dumps({"memory_free_gb": round(free), "memory_total_gb": round(available_memory_gb("MemTotal")),
                       "models": models}, indent=2)


async def _check_memory(slug: str) -> None:
    """Refuse a start that would not fit, and say what holds the memory."""
    need, free, running = await memory_plan(slug)
    if free >= need:
        return
    startable = _startable_slugs()
    running_media = [o for o in running if o in startable]
    running_other = [o for o in running if o not in startable]
    recipes = get_recipes()
    lines = [f"{slug} needs about {need:.0f} GB and only {max(free, 0):.0f} GB is free."]
    if running_media:
        lines.append("Running models you can stop with stop_model: "
                     + ", ".join(running_media) + ".")
    if running_other:
        lines.append("Also running (only the Hub can stop these): "
                     + ", ".join(recipes[o].name for o in running_other) + ".")
    raise image_service.ImageError(" ".join(lines))


async def _start_model(args: dict, origin: str, on_progress=None, user: dict | None = None) -> dict:
    slug = await _startable(args)
    async with _start_lock:
        if not await is_recipe_running(slug) and get_pending(slug) != "launching":
            await _check_memory(slug)
            if on_progress:
                on_progress(f"starting {slug}")
            try:
                await containers.launch_app(slug, user or {}, origin)
            except HTTPException as exc:
                raise image_service.ImageError(
                    f"{slug} failed to launch: {str(exc.detail)[-500:]}") from None

    deadline = asyncio.get_running_loop().time() + CHECK_WAIT
    while not is_ready(slug):
        if not await is_recipe_running(slug) and get_pending(slug) != "launching":
            raise image_service.ImageError(
                f"{slug} stopped before it finished loading. Its log is on its page in the Hub.")
        if asyncio.get_running_loop().time() >= deadline:
            return {"content": [{"type": "text", "text": (
                        f"Not done yet: {slug} is still starting up. Now call start_model "
                        f"with model {slug} again, and keep calling it until it is ready.")}],
                    "structuredContent": {"model": slug, "status": "starting", "done": False,
                                          "next_call": {"tool": "start_model",
                                                        "arguments": {"model": slug}}},
                    "isError": False}
        # The Hub's own check gives up after five minutes; the largest models
        # take longer. It no-ops while one is already polling.
        await start_health_check(slug)
        await asyncio.sleep(READY_POLL_SECONDS)
    return {"content": [{"type": "text", "text": f"{slug} is ready."}],
            "structuredContent": {"model": slug, "status": "ready", "done": True},
            "isError": False}


async def _stop_model(args: dict) -> dict:
    slug = await _startable(args)
    if not await is_recipe_running(slug):
        return {"content": [{"type": "text", "text": f"{slug} is already stopped."}],
                "structuredContent": {"model": slug, "status": "stopped"}, "isError": False}
    busy = image_service.renders_on(slug) + await video_service.rendering_on(slug)
    if busy:
        raise image_service.ImageError(
            f"{slug} is rendering {busy} job{'s' * (busy > 1)} right now; stopping it would lose "
            f"{'them' if busy > 1 else 'it'}. Collect the results first, then stop it.")
    result = await containers.stop(slug)
    if result["status"] != "stopped":
        raise image_service.ImageError(f"Stopping {slug} failed; try again or stop it in the Hub.")
    return {"content": [{"type": "text", "text": f"Stopped {slug}; its memory is free."}],
            "structuredContent": {"model": slug, "status": "stopped"}, "isError": False}


def _image_result(info: dict, origin: str) -> dict:
    """One shape for both paths: the finished image, or the job to collect."""
    if info["status"] == "failed":
        return _text(info.get("error") or f"Image job {info['job_id']} failed.", is_error=True)
    if info["status"] != "completed":
        return _not_done("get_image", info["job_id"],
                         f"still rendering on {info['model']} ({info['elapsed']}s so far)",
                         {k: v for k, v in info.items() if k != "result"})
    result = info["result"]
    url = f"{origin}{result.path}"
    lines = [
        f"Image: {url}",
        f"Model: {result.model}",
        f"Size: {result.width}x{result.height}",
    ]
    if result.seed is not None:
        lines.append(f"Seed: {result.seed}")
    if result.steps is not None:
        lines.append(f"Steps: {result.steps}")
    if result.preset:
        lines.append(f"Preset: {result.preset}")
    lines.append("Pass this URL to edit_image to change it further.")
    lines.append("To save it into your workspace, call create_download with this URL; "
                 "the attached JPEG is a small preview, not the file.")
    return {
        "content": [
            {"type": "text", "text": "\n".join(lines)},
            {"type": "image", "data": result.preview_jpeg_b64, "mimeType": "image/jpeg"},
        ],
        "structuredContent": {
            "url": url, "model": result.model, "width": result.width,
            "height": result.height, "seed": result.seed, "steps": result.steps,
        },
        "isError": False,
    }


def _video_result(info: dict, origin: str) -> dict:
    details = [f"{k.capitalize()}: {info[k]}" for k in ("size", "seconds", "seed", "steps")
               if info.get(k) is not None]
    if info["status"] == "failed":
        return _text(f"Video job {info['job_id']} failed on {info['model']}: {info.get('error')}",
                     is_error=True)
    if info["status"] != "completed":
        return _not_done("get_video", info["job_id"],
                         f"still rendering on {info['model']} ({info['status']}, "
                         f"{info.get('progress', 0)}% done)",
                         {k: v for k, v in info.items() if k != "poster"})
    url = f"{origin}{video_service.PUBLIC_PREFIX}/{info['video_id']}.mp4"
    lines = [f"Video: {url}", f"Model: {info['model']}", *details]
    if info.get("inference_time_s"):
        lines.append(f"Render time: {round(info['inference_time_s'])}s")
    lines.append("To save it into your workspace, call create_download with this URL"
                 + ("; the attached JPEG is only a poster frame, not the video." if info.get("poster") else "."))
    content = [{"type": "text", "text": "\n".join(lines)}]
    if info.get("poster"):
        content.append({"type": "image", "data": info["poster"], "mimeType": "image/jpeg"})
    structured = {k: v for k, v in info.items() if k != "poster"}
    return {"content": content, "structuredContent": {**structured, "url": url}, "isError": False}


def _mesh_result(info: dict, origin: str) -> dict:
    if info["status"] == "failed":
        return _text(info.get("error") or f"3D job {info['job_id']} failed.", is_error=True)
    if info["status"] != "completed":
        return _not_done("get_3d", info["job_id"],
                         f"still building on {info['model']} ({info['elapsed']}s so far)",
                         {k: v for k, v in info.items() if k != "result"})
    mesh = {k: v for k, v in info["result"].items() if k != "preview"}
    url = f"{origin}{mesh_service.PUBLIC_PREFIX}/{mesh['mesh_id']}.glb"
    lines = [f"3D model (GLB): {url}", f"Model: {mesh['model']}", f"Seed: {mesh['seed']}",
             f"File size: {mesh['bytes'] / 1e6:.1f} MB"]
    if mesh.get("inference_time_s"):
        lines.append(f"Build time: {round(mesh['inference_time_s'])}s")
    lines.append("The user can turn it around in the Hub's My files. To save it into your "
                 "workspace, call create_download with this URL; the attached JPEG is the cut-out "
                 "picture it was built from, not a render of the model.")
    return {"content": [{"type": "text", "text": "\n".join(lines)},
                        {"type": "image", "data": info["result"]["preview"], "mimeType": "image/jpeg"}],
            "structuredContent": {**mesh, "url": url}, "isError": False}


def _music_result(info: dict, origin: str) -> dict:
    if info["status"] == "failed":
        return _text(info.get("error") or f"Music job {info['job_id']} failed.", is_error=True)
    if info["status"] != "completed":
        return _not_done("get_music", info["job_id"],
                         f"still composing on {info['model']} ({info['elapsed']}s so far)",
                         {k: v for k, v in info.items() if k != "result"})
    song = info["result"]
    url = f"{origin}{audio_service.PUBLIC_PREFIX}/{song['audio_id']}.wav"
    lines = [f"Music: {url}", f"Model: {song['model']}",
             f"Length: {song['seconds']}s", f"Seed: {song['seed']}"]
    return {"content": [{"type": "text", "text": "\n".join(lines)}],
            "structuredContent": {**song, "url": url}, "isError": False}


async def _own_file(args: dict, user: dict | None) -> str:
    """The name of the caller's Hub file behind args['url'], or an ImageError."""
    if user is None:
        raise image_service.ImageError("Files need a signed-in Hub account behind this connection.")
    file_name = media_store.name_in_url(str(args.get("url") or ""))
    if not file_name:
        raise image_service.ImageError("'url' must be a Hub image, video, 3D model or song URL "
                                       "(.../images/<id>.png, /videos/<id>.mp4, /meshes/<id>.glb "
                                       "or /audio/<id>.wav).")
    if media_store.find(file_name) is None or not await media_store.can_read(file_name, user):
        raise image_service.ImageError(f"No Hub file {file_name} for this account "
                                       "(it may have expired or been deleted).")
    return file_name


async def _link_tool(name: str, args: dict, origin: str, user: dict | None) -> dict:
    """create_upload / create_download: a key-less link for the caller's account."""
    if user is None:
        raise image_service.ImageError("Links need a signed-in Hub account behind this connection.")
    minutes = link_service.LINK_TTL // 60
    if name == "create_upload":
        token, expires = await link_service.create("upload", user["id"])
        link = f"{origin}{link_service.UPLOAD_PREFIX}{token}"
        curl = f"curl -sS --data-binary @<your file> '{link}'"
        text = (f"Upload link (one file, expires in {minutes} minutes):\n{link}\n\n"
                f"Run: {curl}\n"
                "It prints JSON; pass its `url` to edit_image or generate_video. Images up to "
                f"{upload_service.MAX_IMAGE_BYTES // 2**20} MB, MP4/MOV videos up to "
                f"{upload_service.MAX_VIDEO_BYTES // 2**20} MB, sounds up to "
                f"{upload_service.MAX_AUDIO_BYTES // 2**20} MB. A refused file leaves the link usable.")
        return {"content": [{"type": "text", "text": text}], "isError": False,
                "structuredContent": {"upload_url": link, "method": "POST", "curl": curl,
                                      "expires_at": expires}}

    file_name = await _own_file(args, user)
    token, expires = await link_service.create("download", user["id"], file_name)
    link = f"{origin}{link_service.DOWNLOAD_PREFIX}{token}"
    curl = f"curl -sSfL -o {file_name} '{link}'"
    text = (f"Download link for {file_name} (expires in {minutes} minutes):\n{link}\n\n"
            f"Run: {curl}")
    return {"content": [{"type": "text", "text": text}], "isError": False,
            "structuredContent": {"download_url": link, "file": file_name, "curl": curl,
                                  "expires_at": expires}}


def _origin(request: Request) -> str:
    return request_origin(request) or f"{request.url.scheme}://{request.url.netloc}"


async def _tools_call(request: Request, msg_id: Any, params: dict) -> Response:
    name = params.get("name")
    if name not in _TOOL_NAMES:
        return JSONResponse(_error(msg_id, -32602, f"Unknown tool: {name}"))
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return JSONResponse(_error(msg_id, -32602, "arguments must be an object"))
    origin = _origin(request)
    user = getattr(request.state, "user", None)
    token = (params.get("_meta") or {}).get("progressToken")

    if "text/event-stream" not in request.headers.get("accept", ""):
        return JSONResponse(_result(msg_id, await call_tool(name, args, origin, user=user)))

    progress: asyncio.Queue[str] = asyncio.Queue()
    task = asyncio.create_task(call_tool(name, args, origin, progress.put_nowait, user))

    async def stream():
        count = 0
        try:
            while not task.done():
                getter = asyncio.ensure_future(progress.get())
                done, _ = await asyncio.wait({task, getter}, timeout=KEEPALIVE_SECONDS,
                                             return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    if token is not None:
                        count += 1
                        yield _sse({"jsonrpc": "2.0", "method": "notifications/progress",
                                    "params": {"progressToken": token, "progress": count,
                                               "message": getter.result()}})
                else:
                    getter.cancel()
                    if not done:
                        yield ": keepalive\n\n"
            yield _sse(_result(msg_id, task.result()))
        finally:
            # The client hung up: stop waiting. The render already queued in the
            # app finishes on its own; there is no way to recall it.
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ routes


@router.post("/mcp")
async def mcp_post(request: Request):
    try:
        msg = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return JSONResponse(_error(None, -32600, "Expected one JSON-RPC 2.0 message"),
                            status_code=400)

    # Notifications and responses expect no reply beyond the acknowledgement.
    if "id" not in msg or "method" not in msg:
        return Response(status_code=202)

    msg_id, method = msg["id"], msg["method"]
    params = msg.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return JSONResponse(_result(msg_id, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }))
    if method == "ping":
        return JSONResponse(_result(msg_id, {}))
    if method == "tools/list":
        return JSONResponse(_result(msg_id, {"tools": TOOLS}))
    if method == "tools/call":
        return await _tools_call(request, msg_id, params)
    return JSONResponse(_error(msg_id, -32601, f"Method not found: {method}"))


@router.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_no_stream():
    return Response(status_code=405, headers={"Allow": "POST"})


def _cache_headers(path) -> dict:
    # Private to one account, who can delete it at any time: no
    # shared cache such as Cloudflare's may keep a copy.
    if upload_service.is_upload(path):
        return {"Cache-Control": "private, max-age=3600"}
    return {"Cache-Control": "private, max-age=86400, immutable"}


_SIGN_IN_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · Spark AI Hub</title>
<body style="font:15px system-ui,sans-serif;max-width:480px;margin:15vh auto;padding:0 16px">
<p>This file is private. <a href="/">Sign in to Spark AI Hub</a> with the account that
made it, then open the link again.</p>"""


async def _media_file(request: Request, name: str, suffix: str) -> Response:
    """A generated or uploaded file, for its owner only (media_store)."""
    user = getattr(request.state, "user", None)
    if settings.auth_enabled and user is None:
        if "text/html" in request.headers.get("accept", ""):
            return HTMLResponse(_SIGN_IN_PAGE, status_code=401)
        return JSONResponse({"detail": "Authentication required: sign in to the Hub, "
                             "or ask the MCP tools for a create_download link."}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="Spark AI Hub"'})
    path = media_store.find(name) if name.endswith(suffix) else None
    # Someone else's file answers exactly like a missing one.
    if path is None or not await media_store.can_read(name, user):
        return Response(status_code=404)
    return FileResponse(path, media_type=media_store.MEDIA_TYPES[suffix], headers=_cache_headers(path))


@router.get("/meshes/{name}")
async def mesh_file(request: Request, name: str):
    return await _media_file(request, name, ".glb")


@router.get("/audio/{name}")
async def audio_file(request: Request, name: str):
    return await _media_file(request, name, ".wav")


@router.get("/videos/{name}")
async def video_file(request: Request, name: str):
    return await _media_file(request, name, ".mp4")


@router.get("/images/{name}")
async def image_file(request: Request, name: str):
    return await _media_file(request, name, ".png")
