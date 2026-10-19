#!/usr/bin/env python3
"""
Measure media models' Time (render_seconds) and Ready in (load_seconds) the
same way every time, through the Hub's /mcp -- the path agents take.

    tools/benchmark_media.py SLUG [SLUG ...]            measure and print
    tools/benchmark_media.py --write SLUG [SLUG ...]    also store in recipe.yaml
    tools/benchmark_media.py --cold SLUG                restart a running model to time its load

One fixed workload per kind, with the inputs kept in tools/media_bench/ so a
figure can be reproduced months later (they are the 2026-10-15 benchmark's):

  image  IMAGE_PROMPT, 1:1, seed 7, the card's settings; run twice, the
         second (warm) run is the figure.
  video  frame.png as the first frame + VIDEO_PROMPT, 5 s, 16:9, seed 7, the
         card's settings; one run. Orbit LoRAs get the same picture first and
         last with their own prompt; reference-only models (no text-to-video)
         get "<Picture 1> " + the prompt; face swap gets clip.mp4 + face.png.
         A LoRA (lora-* recipe) is measured on its first installed base
         model with its own workload; it gets a Time, never a Ready in,
         which is its base model's.
  3D     object.png, seed 7, default steps; run twice, the second counts.
  music  LYRICS + STYLE, 60 s, seed 7; one run.

Time is wall clock from the generate_* call until get_* returns the result,
as an agent sees it. Ready in is start_model from stopped until ready.

Each model is measured alone among the media models: running image, video,
3D and music models are stopped first (LLMs and apps are left alone; if one
holds the memory, start_model's refusal says so). The last model measured is
left running. Every result is appended to data/benchmarks/media.jsonl.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES_DIR = ROOT / "registry" / "recipes"
INPUTS = ROOT / "tools" / "media_bench"
DATA = ROOT / "data"
LOG = DATA / "benchmarks" / "media.jsonl"
UPLOADED = DATA / "benchmarks" / "inputs.json"   # sha256 -> Hub URL, so inputs upload once

HUB = os.environ.get("SAH_HUB_LOCAL", "http://127.0.0.1:9010")
KEY_FILE = Path.home() / ".config" / "sah" / "key"

SEED = 7
VIDEO_SECONDS = 5
# Describes frame.png, the first frame of clip.mp4: real daylight footage
# (Coverr "Girl smiling and petting dogs", seconds 1.5-6.5) with a face, three
# dogs, objects and textures, so every video test checks the same scene.
VIDEO_PROMPT = (
    "On a sunny hillside terrace, the smiling blonde woman kneeling on the gravel strokes the "
    "long-haired ginger dog; the dog pants happily and turns its head to look at her, she laughs, "
    "a black dog trots past behind them, the breeze lifts her hair and the palm fronds, and the "
    "white town on the hills shimmers in the distance. Steady handheld camera, natural daylight. "
    "The dogs panting, her soft laughter, birdsong and a light breeze.")
ORBIT_PROMPT = (
    "One frozen instant. Only the camera moves. In a continuous 360 orbit. Preserve every person and "
    "object in exactly the same world position, orientation, shape and pose throughout the shot. "
    "Airborne objects remain suspended at the captured height and angle: no wobbling, shaking, "
    "spinning, drifting, falling or continued action. Keep faces, hands, clothing, liquids and the "
    "background motionless while retaining their natural appearance. Camera parallax is the only "
    "source of apparent movement. No cuts, zoom, morphing or added objects.")
FACESWAP_PROMPT = "Faceswap: replace the woman's face in <Video 1> with the face of <Picture 1>."
# The Day-to-Night IC-LoRA card's prompt shape, for clip.mp4's sunny terrace.
NIGHT_PROMPT = (
    "A realistic nighttime scene. The blonde woman kneels on the terrace stroking the long-haired "
    "ginger dog under warm string lights and a dim streetlamp, the hillside town below glitters with "
    "window lights under a dark starry sky, deep natural shadows on the gravel. Only the lighting "
    "changes from day to night; identical composition, framing, camera movement and motion.")
# The Character Swap card's own example prompt.
CHARSWAP_PROMPT = ("Replace the selected person in <Video 1> with the character in <Picture 1>. "
                   "Keep the reference character's face, hair and clothing. Follow the source "
                   "performance's pose, gaze, expression and movement. Preserve the scene and "
                   "the other people.")
IMAGE_PROMPT = (
    "A cozy corner bookshop on a rainy evening, seen from the street: warm golden light spilling from "
    "tall windows onto wet cobblestones, a ginger cat asleep on a stack of books in the window, a "
    "hand-painted wooden sign above the door that reads \"THE LAST CHAPTER\", reflections in puddles, "
    "a woman with a red umbrella walking past, photorealistic, 35mm photograph, shallow depth of field")
SONG_SECONDS = 60
STYLE = ("warm indie folk ballad, acoustic guitar, soft piano, brushed drums, female vocal, 84 BPM, "
         "intimate, cozy, analog production")
LYRICS = """[Verse]
Rain on the window, lamplight on the floor
A ginger cat is sleeping by the bookshop door
Pages full of stories nobody has read
Waiting for the morning, waiting in their bed

[Chorus]
Stay a little longer, the night is soft and slow
Every word we're keeping is a place for us to go
Stay a little longer, under amber light
The last chapter's open, and we'll read it through the night

[Verse]
Footsteps on the cobbles, a red umbrella sways
Somebody is passing on a rainy Sunday's way
The kettle in the back room hums a quiet tune
We'll close the shop at midnight underneath the moon

[Chorus]
Stay a little longer, the night is soft and slow
Every word we're keeping is a place for us to go
Stay a little longer, under amber light
The last chapter's open, and we'll read it through the night

[Outro]
Through the night"""

LISTS = {"image": "list_image_models", "video": "list_video_models", "3d": "list_3d_models"}
TOOLS = {"image": ("generate_image", "get_image"), "video": ("generate_video", "get_video"),
         "3d": ("generate_3d", "get_3d"), "music": ("generate_music", "get_music")}


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ------------------------------------------------------------------ the Hub

_id = 0


def call(name, **args):
    """One /mcp tools/call: (is_error, text, structuredContent)."""
    global _id
    _id += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _id, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(f"{HUB}/mcp", data=body, method="POST", headers={
        "Authorization": f"Bearer {KEY_FILE.read_text().strip()}",
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read().decode()
    msg = None
    for line in raw.splitlines():
        if line.startswith("data:"):
            msg = json.loads(line[5:])
    msg = msg or json.loads(raw)
    if "error" in msg:
        return True, json.dumps(msg["error"]), {}
    res = msg["result"]
    text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
    return bool(res.get("isError")), text, res.get("structuredContent") or {}


def upload_path(url: str) -> Path:
    """Where the Hub keeps an upload served at /images/<id>.png or /videos/<id>.mp4."""
    kind, name = url.rstrip("/").split("/")[-2:]
    return DATA / kind / "uploads" / name


def upload(path: Path) -> str:
    """The Hub URL of one input, uploaded once and reused while it exists."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    known = json.loads(UPLOADED.read_text()) if UPLOADED.exists() else {}
    url = known.get(digest)
    # Still there unless someone deleted it in My files. Checked on disk: the
    # API key opens /mcp only, so asking for the URL itself is always refused,
    # and that refusal used to re-upload every input on every run.
    if url and upload_path(url).is_file():
        return url
    # The way an agent uploads: a one-time link from create_upload.
    err, text, link = call("create_upload")
    if err:
        raise SystemExit(f"create_upload: {text}")
    req = urllib.request.Request(link["upload_url"], data=path.read_bytes(), method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        url = json.loads(r.read())["url"]
    known[digest] = url
    UPLOADED.parent.mkdir(parents=True, exist_ok=True)
    UPLOADED.write_text(json.dumps(known, indent=2))
    return url


def listing():
    """{slug: (kind, entry)} for every installed media model, and the music models."""
    out = {}
    for kind, tool in LISTS.items():
        err, text, _ = call(tool)
        if err:
            raise SystemExit(f"{tool}: {text}")
        for entry in json.loads(text)["models"]:
            out[entry["model"]] = (kind, entry)
    return out


def recipe(slug):
    import yaml
    return yaml.safe_load((RECIPES_DIR / slug / "recipe.yaml").read_text())


def kind_of(slug, models):
    if slug in models:
        return models[slug]
    if is_music(slug):
        return "music", {"model": slug, "state": "ready" if slug in running_containers() else "stopped"}
    raise SystemExit(f"{slug} is not an installed image, video, 3D or music model.")


def is_music(slug):
    """A music model, as the Hub's audio_service finds one (it has no list tool)."""
    tags = set(recipe(slug).get("tags") or []) if (RECIPES_DIR / slug).is_dir() else set()
    return {"openai-music", "text-to-music"} <= tags


def running_containers():
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    return {n.removeprefix("spark-ai-hub-") for n in out.split()}


def running_media(models):
    """Image, video, 3D and music models that are up: what stop_model may stop."""
    up = [s for s, (_, e) in models.items() if e.get("state") in ("ready", "starting")]
    return up + [s for s in running_containers() if s not in models and is_music(s)]


def start(slug):
    """Seconds from start_model to ready, or (None, why)."""
    t0 = time.time()
    while True:
        err, text, _ = call("start_model", model=slug)
        if err:
            return None, text
        if "not done" not in text.lower():
            return time.time() - t0, text
        if time.time() - t0 > 3600:
            return None, "not ready after an hour"


def render(kind, args):
    """(seconds, structured result) for one job, timed as an agent sees it."""
    gen, get = TOOLS[kind]
    t0 = time.time()
    err, text, data = call(gen, **args)
    if err:
        raise RuntimeError(text)
    job = data.get("job_id") or re.search(r"job[_ ]id\W+([A-Za-z0-9_-]{6,})", text).group(1)
    # Logged at once: if this script is cut off, the job can still be collected
    # with get_* (the Hub keeps it in memory until then).
    log(f"    job {job}")
    last = ""
    while True:
        err, text, data = call(get, job_id=job)
        if err:
            raise RuntimeError(text)
        if not text.lower().startswith("not done"):
            return time.time() - t0, data
        if text[:80] != last:
            log(f"    {text[:120]}")
            last = text[:80]


# ------------------------------------------------------------- workloads

def plan(slug, kind, entry, inputs, model=None):
    """(arguments, number of runs, describe(result) -> render_workload).
    `model` is the base a LoRA `slug` runs on; None for a model itself."""
    if model:
        args, runs, describe = plan(slug, kind, entry, inputs)
        args.update(model=model, lora=slug)
        base = recipe(model)["name"]
        return args, runs, lambda r: f"{describe(r)}, on {base}"
    tags = set(recipe(slug).get("tags") or [])
    if kind == "image":
        def describe(r):
            return f"{r['width']}x{r['height']}, {r['steps']} steps" if r.get("steps") else \
                   f"{r['width']}x{r['height']}, default steps"
        return {"model": slug, "prompt": IMAGE_PROMPT, "seed": SEED, "aspect_ratio": "1:1"}, 2, describe
    if kind == "3d":
        def describe(r):
            return f"one 1024x1024 picture, default steps, textured GLB ({r['bytes'] / 1e6:.1f} MB)"
        return {"model": slug, "image": inputs["object"], "seed": SEED}, 2, describe
    if kind == "music":
        def describe(r):
            return f"one {SONG_SECONDS} s song with vocals"
        return {"model": slug, "lyrics": LYRICS, "style": STYLE, "seconds": SONG_SECONDS,
                "seed": SEED}, 1, describe

    args = {"model": slug, "seconds": VIDEO_SECONDS, "seed": SEED, "aspect_ratio": "16:9",
            "prompt": VIDEO_PROMPT, "image": inputs["frame"]}
    what = "clip from a first frame"
    if "orbit-360" in tags:
        args.update(prompt=ORBIT_PROMPT, last_image=inputs["frame"])
        what = "orbit, same picture first and last"
    elif "faceswap" in tags or "character-swap" in tags:
        prompt = FACESWAP_PROMPT if "faceswap" in tags else CHARSWAP_PROMPT
        args.update(prompt=prompt, video=inputs["clip"], image=inputs["face"])
        what = None
    elif "relighting" in tags:
        # An IC-LoRA edits the clip itself: no picture.
        # 4 s: the card's training length (97 frames); clip.mp4 runs 5.
        del args["image"]
        args.update(prompt=NIGHT_PROMPT, video=inputs["clip"], seconds=4)
        what = "clip relit to night"
    elif "text-to-video" not in entry.get("accepts", []):
        args["prompt"] = "<Picture 1> " + VIDEO_PROMPT
        what = "clip from one reference picture"

    def describe(r):
        size = r.get("size") or ""
        steps = f"{r['steps']} steps" if r.get("steps") else "server default steps"
        if what is None:
            swapped = "face" if "faceswap" in tags else "character"
            return f"new {swapped} on a {VIDEO_SECONDS} s {size} clip, {steps}"
        return f"{clip_seconds(r):g} s 16:9 {what}, {size}, {steps}"
    return args, 1, describe


def clip_seconds(result):
    """The clip's real length, read from the file the Hub saved."""
    name = result.get("url", "").rsplit("/", 1)[-1]
    path = DATA / "videos" / name
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
        return round(float(out.stdout), 1)
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        return VIDEO_SECONDS


# ---------------------------------------------------------- recipe.yaml

def write_recipe(slug, render_s, workload, load_s):
    """Set render_seconds / render_workload (and load_seconds when measured),
    replacing old values in place or adding them after weights_gb."""
    p = RECIPES_DIR / slug / "recipe.yaml"
    text = p.read_text()
    fields = {"render_seconds": f"{render_s:.1f}", "render_workload": json.dumps(workload)}
    if load_s is not None:
        fields["load_seconds"] = f"{round(load_s)}"
    missing = []
    for key, value in fields.items():
        text, n = re.subn(rf"^{key}:.*$", f"{key}: {value}", text, count=1, flags=re.M)
        if not n:
            missing.append(f"{key}: {value}")
    if missing:
        anchor = re.search(r"^weights_gb:.*$", text, re.M) or re.search(r"^quantization:.*$", text, re.M)
        if not anchor:
            raise RuntimeError(f"no weights_gb/quantization line to put the figures after in {p}")
        text = text[:anchor.end()] + "\n" + "\n".join(missing) + text[anchor.end():]
    p.write_text(text)


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slugs", nargs="+")
    ap.add_argument("--write", action="store_true", help="store the figures in each recipe.yaml")
    ap.add_argument("--cold", action="store_true", help="stop a running model first to time its load")
    opts = ap.parse_args()

    inputs = {k: upload(INPUTS / f) for k, f in
              (("frame", "frame.png"), ("face", "face.png"), ("object", "object.png"), ("clip", "clip.mp4"))}
    LOG.parent.mkdir(parents=True, exist_ok=True)

    for slug in opts.slugs:
        models = listing()
        lora = recipe(slug).get("lora") if (RECIPES_DIR / slug).is_dir() else None
        model = slug
        if lora:
            model = next((b for b in lora["bases"] if b in models), None)
            if model is None:
                raise SystemExit(f"{slug}: none of its base models is installed ({', '.join(lora['bases'])}).")
        kind, entry = kind_of(model, models)
        others = [s for s in running_media(models) if s != model]
        for other in others:
            err, text, _ = call("stop_model", model=other)
            log(f"stop {other}: {text[:100]}")
            if err:
                raise SystemExit(f"cannot measure {slug} alone: {text}")
        if opts.cold and entry.get("state") in ("ready", "starting"):
            log(f"stop {model} to time its load: {call('stop_model', model=model)[1][:80]}")
            entry["state"] = "stopped"

        load_s = None
        if entry.get("state") in ("ready", "starting"):
            log(f"{model} is already running: Ready in is not measured (use --cold)")
            start(model)
        else:
            log(f"start {model}")
            load_s, text = start(model)
            if load_s is None:
                log(f"  {model} did not start: {text}")
                continue
            log(f"  ready in {load_s:.0f} s")
        if lora:
            load_s = None   # the base model's figure, not the LoRA's

        args, runs, describe = plan(slug, kind, entry, inputs, model if lora else None)
        times, result = [], {}
        for run in range(1, runs + 1):
            secs, result = render(kind, args)
            times.append(secs)
            log(f"  run {run}: {secs:.1f} s  {result.get('url', '')}")
        render_s = times[-1]
        workload = describe(result)
        log(f"{slug}: Time {render_s:.1f} s ({workload})"
            + (f", Ready in {load_s:.0f} s" if load_s is not None else ""))
        with LOG.open("a") as f:
            f.write(json.dumps({"slug": slug, "kind": kind, "date": time.strftime("%Y-%m-%d %H:%M"),
                                "render_seconds": round(render_s, 1), "runs": [round(t, 1) for t in times],
                                "render_workload": workload, "load_seconds": load_s and round(load_s),
                                "args": args, "result": result}) + "\n")
        if opts.write:
            write_recipe(slug, render_s, workload, load_s)
            log(f"  wrote {RECIPES_DIR / slug / 'recipe.yaml'}")
    # The last model measured stays running.


if __name__ == "__main__":
    sys.exit(main())
