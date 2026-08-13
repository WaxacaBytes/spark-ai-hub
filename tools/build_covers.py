#!/usr/bin/env python3
"""
Render the catalog's cover art.

Which picture a recipe uses is *not* decided here — it is declared by the
recipe itself, in registry/recipes/<slug>/recipe.yaml:

    cover:
      image: qwen-27b.jpg      # a file in registry/covers/
      caption: "..."           # what it shows and why
      fit: contain             # optional: "cover" (default) crops to fill
      grade: false             # optional: skip the cinematic colour grade
      credit: "..."            # optional: source · author · licence
      source: "https://..."    # optional

Recipes share an image by naming the same file, which is how every build of
a model at a given parameter size ends up looking alike. A recipe from
outside this repo ships its own image in registry/covers/ and needs no code
change at all.

This tool reads those declarations and renders each referenced image into
the two shapes the UI needs:

    frontend/public/covers/<stem>-poster.jpg     600x900    (card)
    frontend/public/covers/<stem>-backdrop.jpg   2560x1000  (hero)

Both come from the same source, so a card and its hero always match.

Usage:
    python3 tools/build_covers.py             # render everything declared
    python3 tools/build_covers.py qwen-27b    # re-render one image
    python3 tools/build_covers.py --list      # show declarations and users
"""
from __future__ import annotations

import json
import math
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "frontend" / "public"
OUT = PUBLIC / "covers"
SOURCES = ROOT / "registry" / "covers"
RECIPES = ROOT / "registry" / "recipes"
CACHE = Path(__file__).resolve().parent / ".cover-cache"
UA = "SparkAIHub-CoverArt/1.0 (https://github.com/WaxacaBytes/spark-ai-hub)"

# The hero is a wide, short band (~2.8:1 at 1600px). At 16:9 the browser
# cropped away half the height and beheaded the subject.
BACKDROP = (2560, 1000)
POSTER = (600, 900)

# Fallback tint for letterboxing and grading when a cover has no palette.
DEFAULT_TINT = "#6C5CE7"

# Only these license families may ship in the repo.
FREE_LICENSE = ("cc0", "cc by", "cc-by", "public domain", "pd-", "gfdl")


@dataclass
class Declared:
    """A cover as declared by one or more recipes."""
    image: str
    caption: str = ""
    fit: str = "cover"
    backdrop_fit: str = ""
    focus_x: float = 0.5
    focus_y: float = 0.5
    grade: bool = True
    credit: str = ""
    source: str = ""
    tint: str = DEFAULT_TINT
    used_by: list = None

    @property
    def stem(self) -> str:
        return Path(self.image).stem
# ─────────────────────────── commons fetch ───────────────────────────

def _api(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def fetch_commons(file_title: str) -> tuple[Path, dict]:
    """Download a Commons file (max 2600px wide) and return (path, metadata)."""
    CACHE.mkdir(exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", file_title)[:120]
    img_path = CACHE / safe
    meta_path = CACHE / (safe + ".meta.json")

    if img_path.exists() and meta_path.exists():
        return img_path, json.loads(meta_path.read_text())

    q = urllib.parse.quote("File:" + file_title, safe="")
    data = _api(
        "https://commons.wikimedia.org/w/api.php?action=query&format=json"
        f"&titles={q}&prop=imageinfo&iiprop=url|size|extmetadata&iiurlwidth=2600"
        "&iiextmetadatafilter=LicenseShortName|LicenseUrl|Artist|Credit"
    )
    pages = (data.get("query") or {}).get("pages") or {}
    info = None
    for _, p in pages.items():
        if p.get("imageinfo"):
            info = p["imageinfo"][0]
    if not info:
        raise RuntimeError(f"not found on Commons: {file_title}")

    em = info.get("extmetadata", {})
    lic = em.get("LicenseShortName", {}).get("value", "?")
    if not any(f in lic.lower() for f in FREE_LICENSE):
        raise RuntimeError(f"refusing non-free license {lic!r} for {file_title}")

    artist = re.sub(r"<[^>]+>", "", em.get("Artist", {}).get("value", "") or "")
    # Some files bury camera GPS text and newlines in the artist field;
    # collapse it to one line or it breaks the yaml it gets written into.
    artist = re.sub(r"\s+", " ", artist).split("Camera location")[0].strip(" ,;")
    meta = {
        "file": file_title,
        "license": lic,
        "license_url": em.get("LicenseUrl", {}).get("value", ""),
        "artist": artist,
        "page": "https://commons.wikimedia.org/wiki/" + urllib.parse.quote("File:" + file_title),
    }

    src = info.get("thumburl") or info["url"]
    req = urllib.request.Request(src, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        img_path.write_bytes(r.read())
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return img_path, meta


# ─────────────────────────── image helpers ───────────────────────────

def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cover_crop(im: Image.Image, size: tuple[int, int], focus="center", zoom=1.0,
               shift=(0.0, 0.0)) -> Image.Image:
    """Scale-and-crop so `im` exactly fills `size`, biased toward `focus`.

    `shift` nudges the crop window as a fraction of the slack in each axis,
    which is what makes the per-variant framings differ.
    """
    tw, th = size
    sw, sh = im.size
    scale = max(tw / sw, th / sh) * zoom
    nw, nh = max(tw, int(sw * scale + 0.5)), max(th, int(sh * scale + 0.5))
    im = im.resize((nw, nh), Image.LANCZOS)

    if focus == "top":
        ty = 0.0
    elif focus == "upper":            # keep faces off the very top edge
        ty = 0.18
    elif focus == "bottom":
        ty = 1.0
    else:
        ty = 0.5

    left = int(max(0, min(nw - tw, (nw - tw) * (0.5 + shift[0]))))
    top = int(max(0, min(nh - th, (nh - th) * (ty + shift[1]))))
    return im.crop((left, top, left + tw, top + th))


def split_tone(arr: np.ndarray, shadow: tuple[int, int, int],
               highlight: tuple[int, int, int], strength=0.35) -> np.ndarray:
    """Tint shadows toward `shadow` and highlights toward `highlight`."""
    lum = (arr * np.array([0.2126, 0.7152, 0.0722])).sum(axis=2, keepdims=True) / 255.0
    sh = np.array(shadow, dtype=np.float32).reshape(1, 1, 3)
    hi = np.array(highlight, dtype=np.float32).reshape(1, 1, 3)
    target = sh * (1.0 - lum) + hi * lum
    # Weight the tint toward the mid/shadow range so highlights stay clean.
    w = strength * (1.0 - 0.55 * lum)
    return arr * (1.0 - w) + target * w


def vignette(arr: np.ndarray, amount=0.55, radius=0.75) -> np.ndarray:
    h, w = arr.shape[:2]
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h / 2.0
    d = np.sqrt(((x - cx) / cx) ** 2 + ((y - cy) / cy) ** 2) / math.sqrt(2)
    mask = np.clip((d - radius) / (1.0 - radius), 0, 1) ** 1.6
    return arr * (1.0 - amount * mask[..., None])


def grain(arr: np.ndarray, amount=5.0, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = rng.normal(0.0, amount, arr.shape[:2]).astype(np.float32)
    return arr + n[..., None]


def linear_scrim(im: Image.Image, direction: str, color=(6, 6, 14),
                 start=0.0, end=1.0, extent=0.6) -> Image.Image:
    """Overlay a one-directional darkening gradient for text legibility."""
    w, h = im.size
    if direction == "bottom":
        ramp = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        ramp = np.clip((ramp - (1 - extent)) / extent, 0, 1) ** 1.35
        alpha = start + (end - start) * ramp
        alpha = np.repeat(alpha, w, axis=1)
    elif direction == "left":
        ramp = np.linspace(1, 0, w, dtype=np.float32)[None, :]
        ramp = np.clip((ramp - (1 - extent)) / extent, 0, 1) ** 1.2
        alpha = start + (end - start) * ramp
        alpha = np.repeat(alpha, h, axis=0)
    else:
        raise ValueError(direction)

    arr = np.asarray(im, dtype=np.float32)
    col = np.array(color, dtype=np.float32).reshape(1, 1, 3)
    out = arr * (1 - alpha[..., None]) + col * alpha[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def load_logo(name: str | None) -> Image.Image | None:
    if not name:
        return None
    for cand in (f"{name}-dark.png", f"{name}.png", f"{name}-light.png"):
        p = LOGOS / cand
        if p.exists():
            return Image.open(p).convert("RGBA")
    return None


# ─────────────────────────── registry ───────────────────────────

def read_declarations() -> dict[str, Declared]:
    """Collect every `cover:` block in the registry, keyed by image stem."""
    found: dict[str, Declared] = {}
    for path in sorted(RECIPES.glob("*/recipe.yaml")):
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception as e:
            print(f"  ! {path.parent.name}: {e}")
            continue
        slug = data.get("slug", path.parent.name)
        cover = data.get("cover") or {}
        image = cover.get("image")
        if not image:
            print(f"  ! {slug}: no cover declared")
            continue

        stem = Path(image).stem
        if stem in found:
            found[stem].used_by.append(slug)
            continue
        found[stem] = Declared(
            image=image,
            caption=cover.get("caption", ""),
            fit=cover.get("fit", "cover"),
            backdrop_fit=cover.get("backdrop_fit", ""),
            focus_x=float(cover.get("focus_x", 0.5)),
            focus_y=float(cover.get("focus_y", 0.5)),
            grade=bool(cover.get("grade", True)),
            credit=cover.get("credit", ""),
            source=cover.get("source", ""),
            tint=cover.get("tint", DEFAULT_TINT),
            used_by=[slug],
        )
    return found


# ─────────────────────────── rendering ───────────────────────────

def render(dec: Declared, size, scrim: str) -> Image.Image:
    """Render one declared source into `size`, graded and scrimmed for text."""
    im = Image.open(SOURCES / dec.image).convert("RGB")
    w, h = size
    fit = dec.fit if scrim == "bottom" else (dec.backdrop_fit or dec.fit)

    if fit == "contain":
        # Letterbox against a blurred, tinted copy of the picture itself, so
        # wide artwork keeps its text instead of being sliced mid-word.
        back = cover_crop(im, size, "center", 1.35)
        back = back.filter(ImageFilter.GaussianBlur(radius=max(w, h) * 0.05))
        barr = np.asarray(back, dtype=np.float32) * 0.42
        tint = np.array(hex_rgb(dec.tint), dtype=np.float32).reshape(1, 1, 3)
        plate = Image.fromarray(np.clip(barr * 0.78 + tint * 0.22, 0, 255).astype(np.uint8))
        avail_h = int(h * (0.80 if scrim == "bottom" else 0.94))
        fg = im.copy()
        fg.thumbnail((int(w * 0.96), avail_h), Image.LANCZOS)
        plate.paste(fg, ((w - fg.width) // 2,
                         int((h - fg.height) * (0.34 if scrim == "bottom" else 0.5))))
        im = plate
    else:
        im = cover_crop(im, size, "center", 1.0,
                        shift=(dec.focus_x - 0.5, dec.focus_y - 0.5))

    seed = abs(hash(dec.stem)) % 9999
    if dec.grade:
        im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=55, threshold=3))
        im = ImageEnhance.Color(im).enhance(0.62)
        im = ImageEnhance.Contrast(im).enhance(1.18)
        im = ImageEnhance.Brightness(im).enhance(0.90)
        arr = np.asarray(im, dtype=np.float32)
        brand = hex_rgb(dec.tint)
        shadow = tuple(int(0.22 * c + 0.78 * b) for c, b in zip(brand, (10, 12, 26)))
        arr = split_tone(arr, shadow, tuple(min(255, int(c * 0.9 + 90)) for c in brand), 0.38)
        arr = vignette(arr, amount=0.50, radius=0.62)
        arr = grain(arr, amount=4.0, seed=seed)
    else:
        arr = np.asarray(im, dtype=np.float32)
        arr = vignette(arr, amount=0.34, radius=0.72)
        arr = grain(arr, amount=2.5, seed=seed)
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    if scrim == "bottom":
        return linear_scrim(im, "bottom", start=0.0, end=0.90,
                            extent=0.34 if fit == "contain" else 0.60)
    im = linear_scrim(im, "left", start=0.0, end=0.88, extent=0.62)
    return linear_scrim(im, "bottom", start=0.0, end=0.45, extent=0.34)


def build(dec: Declared) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for kind, size, scrim in (("poster", POSTER, "bottom"), ("backdrop", BACKDROP, "left")):
        dest = OUT / f"{dec.stem}-{kind}.jpg"
        render(dec, size, scrim).save(dest, "JPEG", quality=87, optimize=True,
                                      progressive=True)
        total += dest.stat().st_size
    print(f"  {dec.stem:28s} {len(dec.used_by):3d} recipes  {total // 1024:5d} KB")


def write_attribution(decs: dict[str, Declared]) -> None:
    lines = [
        "# Cover art attribution",
        "",
        "Generated by `tools/build_covers.py` from the `cover:` blocks in",
        "`registry/recipes/*/recipe.yaml`. Do not edit by hand.",
        "",
        "Sources live in `registry/covers/`. Photographic covers are derived",
        "from freely licensed Wikimedia Commons files (colour-graded, cropped",
        "and darkened); the rest is artwork published by the projects the",
        "covers stand for.",
        "",
    ]
    credited = [d for d in decs.values() if d.credit]
    for d in sorted(credited, key=lambda d: d.stem):
        lines.append(f"- **{d.stem}.jpg** — {d.credit}"
                     + (f"  \n  {d.source}" if d.source else ""))
    lines += ["", f"_{len(credited)} of {len(decs)} covers carry a third-party credit._"]
    (OUT / "ATTRIBUTION.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {(OUT / 'ATTRIBUTION.md').relative_to(ROOT)} ({len(credited)} credited)")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    decs = read_declarations()

    if "--list" in sys.argv[1:]:
        for stem, d in sorted(decs.items()):
            print(f"{stem:28s} {d.fit:8s} {len(d.used_by):3d} recipes   {d.caption[:60]}")
        print(f"\n{len(decs)} covers declared by "
              f"{sum(len(d.used_by) for d in decs.values())} recipes")
        return

    unknown = [a for a in args if a not in decs]
    if unknown:
        sys.exit(f"not declared by any recipe: {', '.join(unknown)}\n"
                 "run with --list to see valid names")

    missing_src = [d.image for d in decs.values() if not (SOURCES / d.image).is_file()]
    if missing_src:
        sys.exit(f"missing source images in {SOURCES.relative_to(ROOT)}: "
                 f"{', '.join(sorted(missing_src))}")

    for stem in (args or sorted(decs)):
        build(decs[stem])

    write_attribution(decs)

    gaps = [d.stem for d in decs.values()
            if not (OUT / f"{d.stem}-poster.jpg").exists()
            or not (OUT / f"{d.stem}-backdrop.jpg").exists()]
    if gaps:
        sys.exit(f"\nERROR: {len(gaps)} covers did not render: {', '.join(gaps)}")
    print(f"\nall {len(decs)} covers present, "
          f"used by {sum(len(d.used_by) for d in decs.values())} recipes")


if __name__ == "__main__":
    main()
