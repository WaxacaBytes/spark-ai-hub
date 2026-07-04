"""Deep-Live-Cam — Gradio Web UI for DGX Spark."""

import os
import sys
import types
import tempfile
import shutil
import time

# Set up Deep-Live-Cam modules
os.environ.setdefault("GRADIO_SERVER_NAME", "0.0.0.0")

# Stub out desktop GUI dependencies before importing Deep-Live-Cam modules.
# The import chain: face_swapper -> modules.core -> modules.ui -> customtkinter
# We run headless with Gradio, so none of the Tkinter UI code is needed.
# Note: we must NOT stub tkinter itself — matplotlib needs the real one (or none).
# Instead we set MPLBACKEND=Agg to avoid any tkinter probing.
os.environ["MPLBACKEND"] = "Agg"

for mod_name in ("customtkinter", "tkinter_fix", "pygrabber",
                 "cv2_enumerate_cameras"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Stub modules.ui so modules.core can import it without Tkinter
ui_stub = types.ModuleType("modules.ui")
ui_stub.update_status = lambda message: None
sys.modules["modules.ui"] = ui_stub

import cv2
import numpy as np
import gradio as gr

import modules.globals as globals

# Force headless mode so update_status() only prints, never touches UI
globals.headless = True

from modules.face_analyser import get_one_face, get_many_faces
from modules.processors.frame.face_swapper import (
    pre_check as swapper_pre_check,
    pre_start as swapper_pre_start,
    process_frame,
    process_image,
)
from modules.processors.frame.face_enhancer import (
    pre_check as enhancer_pre_check,
    process_frame as enhance_frame,
)
from modules.utilities import is_video


def init_pipeline():
    """Initialize the face swap pipeline with CUDA."""
    globals.execution_providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    globals.execution_threads = 4
    globals.keep_fps = True
    globals.keep_audio = True
    globals.video_encoder = "libx264"
    globals.video_quality = 18

    print("[deep-live-cam] Initializing face swapper...", flush=True)
    swapper_pre_check()
    print("[deep-live-cam] Initializing face enhancer...", flush=True)
    enhancer_pre_check()
    print("[deep-live-cam] Pipeline ready.", flush=True)


def swap_image(source_img, target_img, many_faces, enhance, mouth_mask):
    """Swap face(s) in a target image using the source face."""
    if source_img is None or target_img is None:
        return None, "Please upload both a source face and a target image."

    globals.many_faces = many_faces
    globals.mouth_mask = mouth_mask
    globals.color_correction = True
    globals.face_swapper_enabled = True

    # Save inputs to temp files
    tmp_dir = tempfile.mkdtemp()
    try:
        src_path = os.path.join(tmp_dir, "source.png")
        tgt_path = os.path.join(tmp_dir, "target.png")
        out_path = os.path.join(tmp_dir, "output.png")

        cv2.imwrite(src_path, cv2.cvtColor(source_img, cv2.COLOR_RGB2BGR))
        cv2.imwrite(tgt_path, cv2.cvtColor(target_img, cv2.COLOR_RGB2BGR))

        globals.source_path = src_path
        globals.target_path = tgt_path
        globals.output_path = out_path

        # Detect source face
        source_bgr = cv2.imread(src_path)
        source_face = get_one_face(source_bgr)
        if source_face is None:
            return None, "No face detected in the source image."

        # Process target
        target_bgr = cv2.imread(tgt_path)
        if many_faces:
            many = get_many_faces(target_bgr)
            if many:
                for face in many:
                    target_bgr = process_frame(source_face, target_bgr)
            else:
                return None, "No faces detected in the target image."
        else:
            target_face = get_one_face(target_bgr)
            if target_face is None:
                return None, "No face detected in the target image."
            target_bgr = process_frame(source_face, target_bgr)

        # Optionally enhance
        if enhance:
            target_bgr = enhance_frame(source_face, target_bgr)

        result_rgb = cv2.cvtColor(target_bgr, cv2.COLOR_BGR2RGB)
        return result_rgb, "Face swap complete!"
    except Exception as e:
        return None, f"Error: {e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def swap_video(source_img, target_video, many_faces, enhance, mouth_mask, progress=gr.Progress()):
    """Swap face(s) in a target video using the source face."""
    if source_img is None or target_video is None:
        return None, "Please upload both a source face and a target video."

    globals.many_faces = many_faces
    globals.mouth_mask = mouth_mask
    globals.color_correction = True
    globals.face_swapper_enabled = True

    tmp_dir = tempfile.mkdtemp()
    try:
        src_path = os.path.join(tmp_dir, "source.png")
        out_path = os.path.join(tmp_dir, "output.mp4")

        cv2.imwrite(src_path, cv2.cvtColor(source_img, cv2.COLOR_RGB2BGR))

        globals.source_path = src_path

        # Read source face
        source_bgr = cv2.imread(src_path)
        source_face = get_one_face(source_bgr)
        if source_face is None:
            return None, "No face detected in the source image."

        # Process video frame by frame
        cap = cv2.VideoCapture(target_video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Face swap
            result = process_frame(source_face, frame)

            # Enhance if requested
            if enhance:
                result = enhance_frame(source_face, result)

            writer.write(result)
            frame_idx += 1
            if total > 0:
                progress(frame_idx / total, desc=f"Processing frame {frame_idx}/{total}")

        cap.release()
        writer.release()

        # Re-encode with ffmpeg for browser compatibility + copy audio
        final_path = os.path.join(tmp_dir, "final.mp4")
        audio_flag = f'-i "{target_video}" -map 0:v -map 1:a? -c:a copy' if globals.keep_audio else ""
        cmd = f'ffmpeg -y -i "{out_path}" {audio_flag} -c:v libx264 -preset fast -crf {globals.video_quality} -movflags +faststart "{final_path}" 2>/dev/null'
        os.system(cmd)

        if os.path.exists(final_path):
            return final_path, f"Video processed! {frame_idx} frames."
        return out_path, f"Video processed! {frame_idx} frames."
    except Exception as e:
        return None, f"Error: {e}"


# --- Live-mode source-face cache ---------------------------------------
# The source face never changes between webcam frames, yet the original code
# re-ran full detection + ArcFace embedding on the source image for EVERY
# frame. That roughly doubled per-frame detection cost and was the main cause
# of choppy live output. We compute the source face once and reuse it until
# the uploaded source image actually changes (cheap content hash check).
_SOURCE_FACE_CACHE = {"key": None, "face": None}


def _get_source_face_cached(source_img):
    """Return the detected source face, recomputing only when the image changes."""
    key = hash(source_img.tobytes())
    if key != _SOURCE_FACE_CACHE["key"]:
        source_bgr = cv2.cvtColor(source_img, cv2.COLOR_RGB2BGR)
        _SOURCE_FACE_CACHE["face"] = get_one_face(source_bgr)
        _SOURCE_FACE_CACHE["key"] = key
        print("[deep-live-cam] source face (re)detected and cached", flush=True)
    return _SOURCE_FACE_CACHE["face"]


def swap_webcam(source_img, webcam_frame, many_faces, enhance, mouth_mask):
    """Process a single webcam frame for live face swap."""
    if source_img is None or webcam_frame is None:
        return webcam_frame

    globals.many_faces = many_faces
    globals.mouth_mask = mouth_mask
    globals.color_correction = True
    globals.face_swapper_enabled = True

    # Get source face (cached across frames — only re-detected when it changes)
    source_face = _get_source_face_cached(source_img)
    if source_face is None:
        return webcam_frame

    # Process frame
    frame_bgr = cv2.cvtColor(webcam_frame, cv2.COLOR_RGB2BGR)
    result = process_frame(source_face, frame_bgr)
    if enhance:
        result = enhance_frame(source_face, result)

    return cv2.cvtColor(result, cv2.COLOR_BGR2RGB)


def build_ui():
    """Build the Gradio interface.

    Plain, default Gradio — no custom CSS, no custom theme, no fonts. Only the
    *structure* is improved: a big output stage on the left, and a simple
    stepped control panel on the right (1. reference face, 2. options, 3. run).
    Live Webcam is the primary tab.
    """
    with gr.Blocks(title="Deep-Live-Cam") as demo:
        gr.Markdown("# Deep-Live-Cam\nReal-time face swap · InsightFace + ONNX Runtime on DGX Spark")

        with gr.Row():
            # ---------------- LEFT: big output stage ----------------
            with gr.Column(scale=3):
                with gr.Tabs():
                    with gr.TabItem("Live Webcam"):
                        gr.Markdown("Drop a reference face on the right, then start your camera below.")
                        webcam_output = gr.Image(label="Live Result", streaming=True)
                        webcam_input = gr.Image(label="Your Camera", sources=["webcam"], streaming=True)

                    with gr.TabItem("Image"):
                        with gr.Row():
                            target_img = gr.Image(label="Target Image", type="numpy")
                            output_img = gr.Image(label="Result", type="numpy")
                        img_btn = gr.Button("Swap Face", variant="primary")
                        img_status = gr.Textbox(label="Status", interactive=False)

                    with gr.TabItem("Video"):
                        with gr.Row():
                            target_video = gr.Video(label="Target Video")
                            output_video = gr.Video(label="Result")
                        vid_btn = gr.Button("Process Video", variant="primary")
                        vid_status = gr.Textbox(label="Status", interactive=False)

            # ---------------- RIGHT: stepped control panel ----------------
            with gr.Column(scale=1):
                gr.Markdown("### Step 1 · Reference face")
                source_img = gr.Image(label="Source Face", type="numpy")

                gr.Markdown("### Step 2 · Options")
                many_faces = gr.Checkbox(label="Swap all faces", value=False)
                enhance = gr.Checkbox(label="Face enhancer (GFPGAN)", value=False)
                mouth_mask = gr.Checkbox(label="Keep original mouth (mask)", value=False)

                gr.Markdown(
                    "### Step 3 · Run\n"
                    "- **Live**: start your camera on the left — it swaps automatically.\n"
                    "- **Image / Video**: load a target on the left, then press the swap button."
                )

        # ---------------- event wiring (after all components exist) ----------------
        webcam_input.stream(
            fn=swap_webcam,
            inputs=[source_img, webcam_input, many_faces, enhance, mouth_mask],
            outputs=webcam_output,
            stream_every=0.1,
        )
        img_btn.click(
            fn=swap_image,
            inputs=[source_img, target_img, many_faces, enhance, mouth_mask],
            outputs=[output_img, img_status],
        )
        vid_btn.click(
            fn=swap_video,
            inputs=[source_img, target_video, many_faces, enhance, mouth_mask],
            outputs=[output_video, vid_status],
        )

    return demo


if __name__ == "__main__":
    init_pipeline()
    demo = build_ui()
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
        share=False,
    )
