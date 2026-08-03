import os

# ZeroGPU packs the whole module-scope resident set to disk at startup, in a
# single pre-allocated file at ZEROGPU_OFFLOAD_DIR (default ~/.zerogpu/tensors).
# The default overlay layer and the RAM-backed tmpfs (/dev/shm) both run out of
# room at pack time (OSError(28)); the dedicated NVMe volume /data-nvme is a
# real block device with predictable free space, so point the offload there.
os.environ.setdefault("ZEROGPU_OFFLOAD_DIR", "/data-nvme/zerogpu_tensors")

# Allocator config to survive transient memory spikes from the large DiT.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
# DiffSynth defaults to ModelScope; force Hugging Face so all weights come from the Hub.
os.environ.setdefault("DIFFSYNTH_DOWNLOAD_SOURCE", "huggingface")

import gc
import glob
import math
import sys
import time

import spaces  # must come before torch / any CUDA-touching import
import numpy as np
import torch
import torch.nn.functional as F
import gradio as gr
from PIL import Image
from huggingface_hub import hf_hub_download, snapshot_download

# Local vendored packages (MetaView `src/` + `diffsynth/` and Depth-Anything-3).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from depth_anything_3.api import DepthAnything3
from diffsynth.core import ModelConfig
from diffsynth import load_state_dict
from diffsynth.utils.lora import GeneralLoRALoader
from src.MetaView_pipeline import MetaViewPipeline

# ---------------------------------------------------------------------------
# Model ids
# ---------------------------------------------------------------------------
METAVIEW_REPO = "Kwai-Kolors/MetaView"
DA3_GIANT_REPO = "depth-anything/DA3-GIANT-1.1"
DA3_NESTED_REPO = "depth-anything/DA3NESTED-GIANT-LARGE-1.1"
QWEN_EDIT_REPO = "Qwen/Qwen-Image-Edit"

# Qwen-Image-Edit Lightning 8-step distillation LoRA (rank 64 / per-layer
# alpha 8 → fusion scale alpha/rank = 0.125). Fused into the DiT at module
# scope below so the whole pipeline runs in 8 steps with no CFG. All 720 LoRA
# layers map onto transformer_blocks.*.attn / img_mlp / txt_mlp modules.
LORA_REPO = "lightx2v/Qwen-Image-Lightning"
LORA_FILE = "Qwen-Image-Edit-Lightning-8steps-V1.0.safetensors"
LORA_SCALE = 8.0 / 64.0

# MetaView global config (matches the reference inference script).
EXPORT_3D_FEAT_LAYERS = [19, 27, 33, 39]
PROPE_DIM_ARRANGE = [64, 20, 20, 24]
ADD_DEPTH = len(PROPE_DIM_ARRANGE) == 4
MERGE_3D = True
PROMPT = ["镜头视角转到指定位置"]  # "move the camera view to the target position"
GEN_W, GEN_H = 960, 528
# Lightning 8-step distillation: 8 steps, no classifier-free guidance.
NUM_STEPS = 8
CFG_SCALE = 1.0
# Camera orbit radius = central-subject depth * this factor. The camera orbits a
# pivot on its optical axis, so whatever sits at `radius` stays pinned to the
# image center through the rotation. Factor 1.0 puts the pivot AT the central
# subject → the subject is the center of rotation and stays framed even at large
# yaw/pitch. A factor <1 moves the pivot IN FRONT of the subject, which swings
# the subject out of frame as you rotate — do NOT lower this below 1.0.
AUTO_DIST_FACTOR = 1.0

device = "cuda"
dtype = torch.bfloat16

# ---------------------------------------------------------------------------
# Load everything on CPU at module scope. DiffSynth's loader reads safetensors
# directly onto the requested device (bypassing the `spaces` CUDA hijack), so
# loading with device="cuda" here fails on ZeroGPU with "No CUDA GPUs are
# available" — no GPU is attached to the main process. We therefore load on
# CPU and then call `.to("cuda")` at module scope (below). That explicit
# module-scope `.to("cuda")` IS intercepted by the `spaces` hijack and is what
# ZeroGPU packs for the GPU worker — the standard ZeroGPU recipe.
# ---------------------------------------------------------------------------
print("[*] Downloading MetaView checkpoint...")
metaview_dir = snapshot_download(METAVIEW_REPO)
metaview_ckpt = glob.glob(os.path.join(metaview_dir, "*.safetensors"))[0]

print("[*] Downloading Depth-Anything-3 models...")
da3_giant_dir = snapshot_download(DA3_GIANT_REPO)
da3_nested_dir = snapshot_download(DA3_NESTED_REPO)

# Download the Qwen-Image-Edit backbone components from the Hub explicitly so we
# can hand DiffSynth concrete local file paths (avoids the ModelScope default
# download path and the processor-dir globbing quirk).
print("[*] Downloading Qwen-Image-Edit backbone...")
qwen_edit_dir = snapshot_download(
    QWEN_EDIT_REPO,
    allow_patterns=["transformer/diffusion_pytorch_model*.safetensors",
                    "transformer/*.json", "processor/*"],
)
qwen_image_dir = snapshot_download(
    "Qwen/Qwen-Image",
    allow_patterns=["text_encoder/model*.safetensors", "text_encoder/*.json",
                    "vae/diffusion_pytorch_model.safetensors", "vae/*.json",
                    "tokenizer/*"],
)

transformer_files = sorted(glob.glob(
    os.path.join(qwen_edit_dir, "transformer", "diffusion_pytorch_model*.safetensors")))
text_encoder_files = sorted(glob.glob(
    os.path.join(qwen_image_dir, "text_encoder", "model*.safetensors")))
vae_file = os.path.join(qwen_image_dir, "vae", "diffusion_pytorch_model.safetensors")
processor_path = os.path.join(qwen_edit_dir, "processor")
tokenizer_path = os.path.join(qwen_image_dir, "tokenizer")

# The MetaView / Qwen-Image-Edit pipeline is the dominant memory consumer
# (~58 GB of weights). Load it at module scope so `import spaces` can pack it.
# The two Depth-Anything-3 priors (~12 GB) are loaded lazily on the first GPU
# request instead — keeping them out of the module-scope resident set gives the
# large transformer enough headroom to load within the 104 GB host RAM limit.
print("[*] Loading MetaView / Qwen-Image-Edit pipeline on CPU...")
pipe = MetaViewPipeline.from_pretrained(
    torch_dtype=dtype,
    device="cpu",
    model_configs=[
        ModelConfig(path=transformer_files),
        ModelConfig(path=text_encoder_files),
        ModelConfig(path=vae_file),
    ],
    tokenizer_config=ModelConfig(path=tokenizer_path),
    processor_config=ModelConfig(path=processor_path),
)

print(f"[*] Applying MetaView weights from {metaview_ckpt}...")
state_dict = load_state_dict(metaview_ckpt)
pipe.dit.load_state_dict(state_dict, strict=False)
del state_dict
gc.collect()

# Fuse the Lightning 8-step LoRA into the DiT on CPU, before the module-scope
# `.to("cuda")` that ZeroGPU packs. The LoRA touches only the repeated
# transformer blocks, whose forward is replaced by the AoTI artifact below (also
# compiled LoRA-fused); fusing here keeps the eager fallback path correct too.
print(f"[*] Fusing Lightning LoRA {LORA_FILE} (scale={LORA_SCALE})...")
lora_sd = load_state_dict(hf_hub_download(LORA_REPO, LORA_FILE))
GeneralLoRALoader(device="cpu", torch_dtype=torch.float32).fuse_lora_to_base_model(
    pipe.dit, lora_sd, alpha=LORA_SCALE)
del lora_sd
gc.collect()

# Load the two Depth-Anything-3 priors on CPU too. The full module-scope
# resident set (pipe ~66 GB + priors ~12 GB in fp32) is packed to disk by
# ZeroGPU at startup and must fit the ~76 GB NVMe offload volume (below); fp32
# priors overflow it by ~2 GB. DA3's Transformer *backbone* is the multi-GB
# bulk and already runs under bf16 autocast, so we store its weights in bf16
# (lossless in practice). Its downstream heads (depth DPT, camera decoder,
# gaussian) run ops that require weights and activations to share a dtype, so
# they stay fp32; a forward hook re-casts the bf16 backbone features to fp32 at
# the boundary so the fp32 heads receive fp32 inputs. This trims each prior
# enough to fit while keeping the geometry outputs numerically fp32.
def _cast_tree_to_float(x):
    if isinstance(x, torch.Tensor):
        return x.float() if x.is_floating_point() else x
    if isinstance(x, (list, tuple)):
        return type(x)(_cast_tree_to_float(v) for v in x)
    if isinstance(x, dict):
        return {k: _cast_tree_to_float(v) for k, v in x.items()}
    return x


def _shrink_backbone_to_bf16(api):
    model = getattr(api, "model", api)
    backbone = getattr(model, "backbone", None)
    if isinstance(backbone, torch.nn.Module):
        backbone.to(dtype=dtype)
        # Re-cast backbone outputs to fp32 so the fp32 heads stay dtype-consistent.
        backbone.register_forward_hook(lambda _m, _in, out: _cast_tree_to_float(out))
    return api


print("[*] Loading Depth-Anything-3 GIANT (3D feature extractor)...")
da3_giant = _shrink_backbone_to_bf16(DepthAnything3.from_pretrained(da3_giant_dir).eval())
print("[*] Loading Depth-Anything-3 NESTED (dense depth)...")
da3_nested = _shrink_backbone_to_bf16(DepthAnything3.from_pretrained(da3_nested_dir).eval())
print("[*] All models loaded on CPU.")

# ---------------------------------------------------------------------------
# Standard ZeroGPU recipe: move everything to CUDA at module/global scope.
# `import spaces` monkey-patches `torch.cuda.*`, so these `.to("cuda")` calls
# are intercepted in the main (GPU-less) process — the backend packs the
# weights to disk and streams them into VRAM inside the GPU worker on the first
# @spaces.GPU call. This must NOT be deferred into the decorated function.
# ---------------------------------------------------------------------------
print("[*] Moving models to CUDA (module scope)...")
pipe.to(device=device)
da3_giant.to(device)
da3_nested.to(device)
print("[*] Models placed on CUDA.")

# ---------------------------------------------------------------------------
# AoTI (ahead-of-time inductor): load the precompiled repeated-block graph.
# The artifact is produced offline by the companion compile Space
# (hugging-apps/metaview-lightning-aoti-compile) and published to an HF Dataset repo. We
# load it once at module scope via the `spaces` package's AoTI helpers,
# following the block-loading pattern (module exposes `_repeated_blocks`). If
# the artifact is missing or incompatible we fall back to eager execution.
# ---------------------------------------------------------------------------
AOTI_REPO = "hugging-apps/metaview-lightning-aoti"  # HF Dataset repo (artifact store)
AOTI_BLOCK = "MetaViewTransformerBlock"
# Expose the repeated block so the standard `spaces` AoTI blocks pattern applies.
pipe.dit._repeated_blocks = [AOTI_BLOCK]


def _load_aoti():
    """Download the precompiled block from the dataset and patch it onto the DiT.

    Uses the `spaces` package AoTI machinery. `aoti_blocks_load` defaults to a
    model repo, so we resolve the dataset artifact manually and reuse the same
    LazyAOTIModel + aoti_patch primitives it uses internally.
    """
    from huggingface_hub import hf_hub_download
    from spaces.zero.torch.aoti import LazyAOTIModel, aoti_patch

    pt2 = hf_hub_download(
        repo_id=AOTI_REPO,
        filename="package.pt2",
        subfolder=AOTI_BLOCK,
        repo_type="dataset",
        token=os.environ.get("HF_TOKEN"),
    )
    lazy = LazyAOTIModel(pt2)
    patched = 0
    for module in pipe.dit.modules():
        if type(module).__name__ == AOTI_BLOCK:
            aoti_patch(module, lazy)
            patched += 1
    print(f"[*] AoTI: patched {patched} '{AOTI_BLOCK}' blocks from {AOTI_REPO}.")


try:
    _load_aoti()
except Exception as e:  # noqa: BLE001 - never break serving on AoTI issues
    print(f"[*] AoTI load failed ({e!r}); running eager.")


def compute_target_extrinsic(yaw_deg, pitch_deg, radius):
    """Camera World-to-Camera extrinsic for a rotation around a sphere center
    in front of the camera (yaw = left/right, pitch = up/down)."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    R_y = np.array([[np.cos(yaw), 0, np.sin(yaw)],
                    [0, 1, 0],
                    [-np.sin(yaw), 0, np.cos(yaw)]])
    R_x = np.array([[1, 0, 0],
                    [0, np.cos(pitch), -np.sin(pitch)],
                    [0, np.sin(pitch), np.cos(pitch)]])
    R = R_y @ R_x
    C = np.array([0.0, 0.0, radius])
    t = C - R @ C
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def fit_to_model_frame(image):
    """Aspect-preserving resize to ~GEN_W*GEN_H pixels, both sides a multiple of
    16. This is the DA3 *input*; the actual generation size is derived from the
    DA3 feature grid (see synthesize) so the whole image is kept at its own aspect
    ratio — no stretching (deform), no center-crop (zoom-in)."""
    img = image.convert("RGB")
    w, h = img.size
    scale = ((GEN_W * GEN_H) / (w * h)) ** 0.5

    def _round16(v):
        return max(256, min(1280, int(round(v / 16)) * 16))

    return img.resize((_round16(w * scale), _round16(h * scale)), Image.LANCZOS)


@spaces.GPU(duration=240, size="xlarge")
def synthesize(image, yaw, pitch, zoom=1.0, *args, progress=gr.Progress(track_tqdm=True)):
    """Synthesize a novel view of a single input image at a target camera pose.

    Args:
        image: Source image (a single monocular view).
        yaw: Horizontal camera rotation in degrees (positive = right, negative = left).
        pitch: Vertical camera rotation in degrees (positive = up, negative = down).
        zoom: Dolly along the optical axis. 1.0 = original framing; >1 pulls the
            camera back (subject smaller, more surroundings — the model extrapolates
            the newly-revealed borders); <1 pushes in (subject larger). The subject
            stays centered either way.

    Returns:
        The synthesized novel-view image at the requested camera pose.
    """
    # The orbit radius is auto-derived from the central-subject depth so rotation
    # circles the subject; `zoom` then dollies the camera in/out along its axis.
    # `*args` swallows any extra positional Gradio injects (and any stale input a
    # cached frontend still sends), so the result is stable regardless.
    if image is None:
        raise gr.Error("Please provide an input image.")

    original_image = image.convert("RGB")
    # Aspect-preserving resize (whole image kept) used as the DA3 input.
    edit_image = fit_to_model_frame(original_image)

    t0 = time.perf_counter()

    with torch.inference_mode():
        # --- 1. 3D feature extraction (DA3 GIANT) + intrinsics ---
        feat_out = da3_giant.inference(
            [edit_image], export_feat_layers=EXPORT_3D_FEAT_LAYERS, process_res=840
        )
        intri = feat_out.intrinsics[0]
        width = intri[0, 2] * 2
        height = intri[1, 2] * 2
        Ks_matrix = [
            [intri[0, 0] / width, 0.0, 0.0],
            [0.0, intri[1, 1] / height, 0.0],
            [0.0, 0.0, 1.0],
        ]
        Ks = torch.Tensor(Ks_matrix)
        Ks = torch.stack([Ks, Ks], dim=0).unsqueeze(0)  # (1, 2, 3, 3)

        feats = [torch.from_numpy(feat_out.aux[f"feat_layer_{layer}"])
                 for layer in EXPORT_3D_FEAT_LAYERS]
        feat_3D = torch.cat(feats, dim=-1).to(dtype=dtype, device=device)

        # MetaView merges the 3D feature tokens 1:1 with the image latent tokens,
        # so the generation grid MUST equal the DA3 feature grid. Derive the gen
        # size from the feature grid (latent patch = 16) rather than picking it
        # freely — otherwise the token counts mismatch (AssertionError in the DiT).
        h_3D, w_3D = feat_3D.shape[1], feat_3D.shape[2]
        gen_h, gen_w = h_3D * 16, w_3D * 16
        # The VAE reference image must be at the generation size (same aspect).
        edit_image = original_image.resize((gen_w, gen_h), Image.LANCZOS)

        # --- 2. Dense depth estimation (DA3 NESTED) ---
        prediction = da3_nested.inference([edit_image], process_res=840)
        depth_edit = torch.Tensor(prediction.depth).unsqueeze(0)
        depth_edit = F.interpolate(depth_edit, size=(gen_h, gen_w),
                                   mode="bilinear", align_corners=False)[0]
        depth_latent = torch.zeros_like(depth_edit)
        depth = torch.cat([depth_latent, depth_edit], dim=0).unsqueeze(0)  # (1, 2, H, W)

        # --- 3. Target pose ---
        # Pivot the rotation on the CENTRAL SUBJECT's depth so it stays centered
        # through the sweep. Use the median depth of the central region (robust to
        # a noisy single center pixel that might land on a background sliver).
        depth_squeeze = depth[0, 1]
        h_d, w_d = depth_squeeze.shape
        center_region = depth_squeeze[int(h_d * 0.3):int(h_d * 0.7),
                                      int(w_d * 0.3):int(w_d * 0.7)]
        center_depth = center_region.median().item()
        r = max(center_depth * AUTO_DIST_FACTOR, 1e-3)
        extrinsic_target = compute_target_extrinsic(float(yaw), float(pitch), r)
        # Dolly / zoom: shift the target camera along its optical axis. The pivot
        # (centered subject) stays on-axis, so this only changes its apparent size
        # — zoom>1 moves it farther (zoom out / reveal more), zoom<1 nearer. Scaled
        # by the subject depth so it behaves the same across scenes.
        extrinsic_target[2, 3] += center_depth * (float(zoom) - 1.0)
        extrinsic_source = np.eye(4)
        viewmats = torch.Tensor(
            np.stack((extrinsic_target, extrinsic_source), axis=0)
        ).unsqueeze(0)  # (1, 2, 4, 4) -> [target, source]

        # --- 4. Novel view generation (MetaView DiT) ---
        generated_image = pipe(
            PROMPT, edit_image=edit_image, edit_image_auto_resize=False,
            seed=0,
            cfg_scale=CFG_SCALE,
            viewmats=viewmats.to(device=device, dtype=dtype),
            Ks=Ks.to(device=device, dtype=dtype),
            prope_dim_arrange=PROPE_DIM_ARRANGE,
            add_attn=True,
            add_3D=True,
            feat_3D=feat_3D,
            depth=depth.to(device=device, dtype=dtype) if ADD_DEPTH else None,
            merge_3D=MERGE_3D,
            val=True,
            num_inference_steps=NUM_STEPS,
            height=gen_h, width=gen_w,
        )

    print(f"[*] Inference took {time.perf_counter() - t0:.1f}s")
    # Return at the original input's dimensions (aspect already matches, so this
    # is a clean rescale — no distortion) so the output matches what was uploaded.
    if generated_image.size != original_image.size:
        generated_image = generated_image.resize(original_image.size, Image.LANCZOS)
    return generated_image


CSS = """
#col-container { max-width: 1100px; margin: 0 auto; }
.dark .gradio-container { color: var(--body-text-color); }
#camera-control-wrapper { min-height: 380px; }
"""

# yaw ∈ [YAW_MIN, YAW_MAX] (left/right) and pitch ∈ [PITCH_MIN, PITCH_MAX]
# (down/up) are the only two dimensions MetaView's synthesize() consumes.
YAW_MIN, YAW_MAX = -60, 60
PITCH_MIN, PITCH_MAX = -45, 45


# ---------------------------------------------------------------------------
# 3D camera-control widget (Three.js), adapted from
# multimodalart/qwen-image-multiple-angles-3d-camera. Reduced from that demo's
# three dimensions (azimuth / elevation / distance) to only the two MetaView
# uses: yaw (green ring, horizontal rotation) and pitch (pink arc, vertical).
# The distance dimension and its orange handle are dropped entirely.
#
# Implemented as a plain gr.HTML block + inline Three.js script (the Space runs
# gradio 5.49.1, which predates the html_template/js_on_load custom-component
# API). Bidirectional coupling to the yaw & pitch sliders is done the same way
# the previous 2D pad did it: dragging a handle writes the target value into the
# slider's native <input> and dispatches an `input` event so Gradio updates its
# state; a polling loop reads the sliders back so external slider changes move
# the 3D handles. Three.js itself is loaded via demo.launch(head=...).
# ---------------------------------------------------------------------------
CAM_3D_JS = f"""
<div id="camera-control-wrapper" style="width: 100%; height: 380px; position: relative; background: #1a1a1a; border-radius: 12px; overflow: hidden;">
  <div id="cam3d-readout" style="position: absolute; bottom: 10px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); padding: 8px 16px; border-radius: 8px; font-family: monospace; font-size: 12px; color: #00ff88; white-space: nowrap; z-index: 10;">yaw 0&deg; &middot; pitch 0&deg;</div>
</div>
<script>
(function() {{
  const YAW_MIN = {YAW_MIN}, YAW_MAX = {YAW_MAX};
  const PITCH_MIN = {PITCH_MIN}, PITCH_MAX = {PITCH_MAX};

  function findRange(elemId) {{
    const root = document.getElementById(elemId);
    if (!root) return null;
    return root.querySelector('input[type=range]') || root.querySelector('input[type=number]');
  }}
  function setSlider(input, val) {{
    if (!input) return;
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, val);
    input.dispatchEvent(new Event('input',  {{ bubbles: true }}));
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }}
  function clamp(v, lo, hi) {{ return Math.max(lo, Math.min(hi, v)); }}

  function init() {{
    const wrapper = document.getElementById('camera-control-wrapper');
    const readout = document.getElementById('cam3d-readout');
    const yawInput = findRange('yaw_slider');
    const pitchInput = findRange('pitch_slider');
    if (!wrapper || !readout || !yawInput || !pitchInput || typeof THREE === 'undefined') {{
      return setTimeout(init, 150);
    }}
    if (wrapper.dataset.inited) return;
    wrapper.dataset.inited = '1';

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x1a1a1a);

    const camera = new THREE.PerspectiveCamera(50, wrapper.clientWidth / wrapper.clientHeight, 0.1, 1000);
    camera.position.set(4.5, 3, 4.5);
    camera.lookAt(0, 0.75, 0);

    const renderer = new THREE.WebGLRenderer({{ antialias: true }});
    renderer.setSize(wrapper.clientWidth, wrapper.clientHeight);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    wrapper.insertBefore(renderer.domElement, readout);

    scene.add(new THREE.AmbientLight(0xffffff, 0.6));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.6);
    dirLight.position.set(5, 10, 5);
    scene.add(dirLight);

    scene.add(new THREE.GridHelper(8, 16, 0x333333, 0x222222));

    const CENTER = new THREE.Vector3(0, 0.75, 0);
    const BASE_DISTANCE = 1.6;
    const AZIMUTH_RADIUS = 2.4;
    const ELEVATION_RADIUS = 1.8;

    // State (yaw + pitch only), seeded from the current slider values.
    let yawAngle = parseFloat(yawInput.value) || 0;
    let pitchAngle = parseFloat(pitchInput.value) || 0;

    // Target image plane (textured from the uploaded image, else a placeholder).
    function createPlaceholderTexture() {{
      const canvas = document.createElement('canvas');
      canvas.width = 256; canvas.height = 256;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#3a3a4a'; ctx.fillRect(0, 0, 256, 256);
      ctx.fillStyle = '#ffcc99';
      ctx.beginPath(); ctx.arc(128, 128, 80, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#333';
      ctx.beginPath();
      ctx.arc(100, 110, 10, 0, Math.PI * 2);
      ctx.arc(156, 110, 10, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#333'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(128, 130, 35, 0.2, Math.PI - 0.2); ctx.stroke();
      return new THREE.CanvasTexture(canvas);
    }}
    const planeMaterial = new THREE.MeshBasicMaterial({{ map: createPlaceholderTexture(), side: THREE.DoubleSide }});
    let targetPlane = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 1.2), planeMaterial);
    targetPlane.position.copy(CENTER);
    scene.add(targetPlane);

    function updateTextureFromUrl(url) {{
      if (!url) {{
        planeMaterial.map = createPlaceholderTexture();
        planeMaterial.needsUpdate = true;
        scene.remove(targetPlane);
        targetPlane = new THREE.Mesh(new THREE.PlaneGeometry(1.2, 1.2), planeMaterial);
        targetPlane.position.copy(CENTER);
        scene.add(targetPlane);
        return;
      }}
      const loader = new THREE.TextureLoader();
      loader.crossOrigin = 'anonymous';
      loader.load(url, (texture) => {{
        texture.minFilter = THREE.LinearFilter;
        texture.magFilter = THREE.LinearFilter;
        planeMaterial.map = texture;
        planeMaterial.needsUpdate = true;
        const img = texture.image;
        if (img && img.width && img.height) {{
          const aspect = img.width / img.height;
          const maxSize = 1.5;
          let pw, ph;
          if (aspect > 1) {{ pw = maxSize; ph = maxSize / aspect; }}
          else {{ ph = maxSize; pw = maxSize * aspect; }}
          scene.remove(targetPlane);
          targetPlane = new THREE.Mesh(new THREE.PlaneGeometry(pw, ph), planeMaterial);
          targetPlane.position.copy(CENTER);
          scene.add(targetPlane);
        }}
      }}, undefined, (err) => console.error('Failed to load texture:', err));
    }}

    // Expose the texture updater so the Gradio image upload can drive it.
    window.__metaviewSetCamTexture = updateTextureFromUrl;

    // Camera model.
    const cameraGroup = new THREE.Group();
    const bodyMat = new THREE.MeshStandardMaterial({{ color: 0x6699cc, metalness: 0.5, roughness: 0.3 }});
    cameraGroup.add(new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.22, 0.38), bodyMat));
    const lens = new THREE.Mesh(
        new THREE.CylinderGeometry(0.09, 0.11, 0.18, 16),
        new THREE.MeshStandardMaterial({{ color: 0x6699cc, metalness: 0.5, roughness: 0.3 }})
    );
    lens.rotation.x = Math.PI / 2;
    lens.position.z = 0.26;
    cameraGroup.add(lens);
    scene.add(cameraGroup);

    // GREEN: yaw ring (horizontal rotation, limited to [YAW_MIN, YAW_MAX]).
    const yawRing = new THREE.Mesh(
        new THREE.TorusGeometry(AZIMUTH_RADIUS, 0.04, 16, 64),
        new THREE.MeshStandardMaterial({{ color: 0x00ff88, emissive: 0x00ff88, emissiveIntensity: 0.3 }})
    );
    yawRing.rotation.x = Math.PI / 2;
    yawRing.position.y = 0.05;
    scene.add(yawRing);

    const yawHandle = new THREE.Mesh(
        new THREE.SphereGeometry(0.18, 16, 16),
        new THREE.MeshStandardMaterial({{ color: 0x00ff88, emissive: 0x00ff88, emissiveIntensity: 0.5 }})
    );
    yawHandle.userData.type = 'yaw';
    scene.add(yawHandle);

    // PINK: pitch arc (vertical, [PITCH_MIN, PITCH_MAX]).
    const arcPoints = [];
    for (let i = 0; i <= 32; i++) {{
      const a = THREE.MathUtils.degToRad(PITCH_MIN + ((PITCH_MAX - PITCH_MIN) * i / 32));
      arcPoints.push(new THREE.Vector3(-0.8, ELEVATION_RADIUS * Math.sin(a) + CENTER.y, ELEVATION_RADIUS * Math.cos(a)));
    }}
    const arcCurve = new THREE.CatmullRomCurve3(arcPoints);
    const pitchArc = new THREE.Mesh(
        new THREE.TubeGeometry(arcCurve, 32, 0.04, 8, false),
        new THREE.MeshStandardMaterial({{ color: 0xff69b4, emissive: 0xff69b4, emissiveIntensity: 0.3 }})
    );
    scene.add(pitchArc);

    const pitchHandle = new THREE.Mesh(
        new THREE.SphereGeometry(0.18, 16, 16),
        new THREE.MeshStandardMaterial({{ color: 0xff69b4, emissive: 0xff69b4, emissiveIntensity: 0.5 }})
    );
    pitchHandle.userData.type = 'pitch';
    scene.add(pitchHandle);

    function updatePositions() {{
      const distance = BASE_DISTANCE;
      const azRad = THREE.MathUtils.degToRad(yawAngle);
      const elRad = THREE.MathUtils.degToRad(pitchAngle);

      cameraGroup.position.set(
          distance * Math.sin(azRad) * Math.cos(elRad),
          distance * Math.sin(elRad) + CENTER.y,
          distance * Math.cos(azRad) * Math.cos(elRad)
      );
      cameraGroup.lookAt(CENTER);

      yawHandle.position.set(AZIMUTH_RADIUS * Math.sin(azRad), 0.05, AZIMUTH_RADIUS * Math.cos(azRad));
      pitchHandle.position.set(-0.8, ELEVATION_RADIUS * Math.sin(elRad) + CENTER.y, ELEVATION_RADIUS * Math.cos(elRad));

      readout.textContent = 'yaw ' + Math.round(yawAngle) + '\\u00b0 \\u00b7 pitch ' + Math.round(pitchAngle) + '\\u00b0';
    }}

    // Push the current yaw/pitch onto the Gradio sliders.
    function writeToSliders() {{
      setSlider(yawInput, Math.round(yawAngle));
      setSlider(pitchInput, Math.round(pitchAngle));
    }}

    // Raycasting / dragging.
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let isDragging = false;
    let dragTarget = null;
    const intersection = new THREE.Vector3();
    const canvas = renderer.domElement;
    const handles = [yawHandle, pitchHandle];

    function pointerToMouse(clientX, clientY) {{
      const rect = canvas.getBoundingClientRect();
      mouse.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((clientY - rect.top) / rect.height) * 2 + 1;
    }}
    function startDrag() {{
      raycaster.setFromCamera(mouse, camera);
      const hit = raycaster.intersectObjects(handles);
      if (hit.length > 0) {{
        isDragging = true;
        dragTarget = hit[0].object;
        dragTarget.material.emissiveIntensity = 1.0;
        dragTarget.scale.setScalar(1.3);
        canvas.style.cursor = 'grabbing';
      }}
    }}
    function moveDrag() {{
      raycaster.setFromCamera(mouse, camera);
      if (dragTarget.userData.type === 'yaw') {{
        const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), -0.05);
        if (raycaster.ray.intersectPlane(plane, intersection)) {{
          yawAngle = clamp(THREE.MathUtils.radToDeg(Math.atan2(intersection.x, intersection.z)), YAW_MIN, YAW_MAX);
        }}
      }} else if (dragTarget.userData.type === 'pitch') {{
        const plane = new THREE.Plane(new THREE.Vector3(1, 0, 0), -0.8);
        if (raycaster.ray.intersectPlane(plane, intersection)) {{
          const relY = intersection.y - CENTER.y;
          const relZ = intersection.z;
          pitchAngle = clamp(THREE.MathUtils.radToDeg(Math.atan2(relY, relZ)), PITCH_MIN, PITCH_MAX);
        }}
      }}
      updatePositions();
      writeToSliders();
    }}
    function endDrag() {{
      if (dragTarget) {{
        dragTarget.material.emissiveIntensity = 0.5;
        dragTarget.scale.setScalar(1);
        writeToSliders();
      }}
      isDragging = false;
      dragTarget = null;
      canvas.style.cursor = 'default';
    }}

    canvas.addEventListener('mousedown', (e) => {{ pointerToMouse(e.clientX, e.clientY); startDrag(); }});
    canvas.addEventListener('mousemove', (e) => {{
      pointerToMouse(e.clientX, e.clientY);
      if (isDragging && dragTarget) {{ moveDrag(); return; }}
      raycaster.setFromCamera(mouse, camera);
      const hit = raycaster.intersectObjects(handles);
      handles.forEach(h => {{ h.material.emissiveIntensity = 0.5; h.scale.setScalar(1); }});
      if (hit.length > 0) {{
        hit[0].object.material.emissiveIntensity = 0.8;
        hit[0].object.scale.setScalar(1.1);
        canvas.style.cursor = 'grab';
      }} else {{ canvas.style.cursor = 'default'; }}
    }});
    canvas.addEventListener('mouseup', endDrag);
    canvas.addEventListener('mouseleave', endDrag);

    canvas.addEventListener('touchstart', (e) => {{
      e.preventDefault(); const t = e.touches[0];
      pointerToMouse(t.clientX, t.clientY); startDrag();
    }}, {{ passive: false }});
    canvas.addEventListener('touchmove', (e) => {{
      e.preventDefault(); const t = e.touches[0];
      pointerToMouse(t.clientX, t.clientY);
      if (isDragging && dragTarget) moveDrag();
    }}, {{ passive: false }});
    canvas.addEventListener('touchend', (e) => {{ e.preventDefault(); endDrag(); }}, {{ passive: false }});
    canvas.addEventListener('touchcancel', (e) => {{ e.preventDefault(); endDrag(); }}, {{ passive: false }});

    updatePositions();

    function render() {{ requestAnimationFrame(render); renderer.render(scene, camera); }}
    render();

    new ResizeObserver(() => {{
      camera.aspect = wrapper.clientWidth / wrapper.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(wrapper.clientWidth, wrapper.clientHeight);
    }}).observe(wrapper);

    // sliders -> 3D: poll the inputs so external slider changes move the handles.
    let last = '';
    setInterval(() => {{
      if (isDragging) return;
      const key = yawInput.value + '|' + pitchInput.value;
      if (key !== last) {{
        last = key;
        yawAngle = clamp(parseFloat(yawInput.value) || 0, YAW_MIN, YAW_MAX);
        pitchAngle = clamp(parseFloat(pitchInput.value) || 0, PITCH_MIN, PITCH_MAX);
        updatePositions();
      }}
    }}, 150);
  }}
  init();
}})();
</script>
"""

# Load Three.js once for the whole app (used by the 3D camera widget).
# Vendored and inlined (not loaded from a CDN) so the "Camera position" 3D widget
# renders with the network disabled — the Hub is offline-first, and a CDN <script>
# would leave THREE undefined offline, so the widget's init() would loop forever
# and show only an empty dark box.
_THREE_JS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "three.min.js")
with open(_THREE_JS_PATH, "r", encoding="utf-8") as _f:
    _THREE_JS_SRC = _f.read()

# Split the camera widget into (a) the wrapper markup and (b) its init script.
# gr.HTML injects its content via innerHTML, and <script> tags inserted that way
# NEVER execute — so the init code has to live in <head> instead (where scripts
# do run). The wrapper divs stay in gr.HTML; the script + Three.js go in head.
# init() already polls (setTimeout) for the wrapper + sliders + THREE, so running
# from head before Gradio mounts the components is fine.
_cam_split = CAM_3D_JS.index("<script>")
CAM_3D_WIDGET = CAM_3D_JS[:_cam_split]
CAM_3D_SCRIPT = CAM_3D_JS[_cam_split:]
HEAD = f"<script>{_THREE_JS_SRC}</script>\n{CAM_3D_SCRIPT}"

with gr.Blocks(theme=gr.themes.Citrus(), css=CSS, head=HEAD) as demo:
    with gr.Column(elem_id="col-container"):
        gr.Markdown(
            """
            # MetaView ⚡ Lightning — Monocular Novel View Synthesis
            Synthesize a **novel camera view** from a single image. Upload an image,
            then **drag the 3D camera widget** (or the two linked sliders) to set the
            target **yaw** (left/right) and **pitch** (up/down) — MetaView renders
            the scene from that new viewpoint.

            Accelerated with the **Qwen-Image-Edit Lightning 8-step LoRA** (8 steps,
            no CFG). Built on Qwen-Image-Edit + Depth-Anything-3 geometry priors.
            [Model](https://huggingface.co/Kwai-Kolors/MetaView) ·
            [Code](https://github.com/KlingAIResearch/MetaView) ·
            [Paper](https://arxiv.org/abs/2607.12000)
            """
        )
        with gr.Row():
            with gr.Column():
                image = gr.Image(label="Input image", type="pil", height=340,
                                 elem_id="input-image")
                gr.Markdown("**Camera position** — drag the 🟢 yaw sphere / 🩷 pitch sphere in the 3D view; the sliders follow (and vice-versa).")
                gr.HTML(CAM_3D_WIDGET)
                yaw = gr.Slider(YAW_MIN, YAW_MAX, value=-15, step=1,
                                label="Yaw (°)  ←  left | right  →",
                                elem_id="yaw_slider")
                pitch = gr.Slider(PITCH_MIN, PITCH_MAX, value=8, step=1,
                                  label="Pitch (°)  ↓ down | up ↑",
                                  elem_id="pitch_slider")
                zoom = gr.Slider(0.6, 2.5, value=1.0, step=0.05,
                                 label="Zoom  ·  1.0 = original framing",
                                 info="Dolly the camera in/out. >1 pulls back "
                                      "(subject smaller, more surroundings — edges "
                                      "are extrapolated); <1 pushes in. Subject "
                                      "stays centered.")
                run = gr.Button("Synthesize novel view", variant="primary")
            with gr.Column():
                output = gr.Image(label="Novel view", height=340)

        gr.Examples(
            examples=[
                ["examples/1.png", -30, 10],
                ["examples/5.png", 30, 0],
                ["examples/9.png", -25, 15],
                ["examples/12.png", 40, -10],
            ],
            inputs=[image, yaw, pitch],
            outputs=output,
            fn=synthesize,
            cache_examples=True,
            cache_mode="lazy",
        )

    run.click(
        synthesize,
        inputs=[image, yaw, pitch, zoom],
        outputs=output,
        api_name="synthesize",
    )

    # Bidirectional coupling between the 3D widget and the yaw/pitch sliders is
    # done entirely in the inline JS above (dragging writes to the sliders;
    # a polling loop reads slider changes back into the 3D handles), matching
    # the previous 2D-pad behaviour. Texture the 3D target plane with the
    # uploaded image via a small client-side callback that reads the Gradio
    # image preview's <img> src and forwards it to the Three.js scene.
    _texture_js = """
    () => {
        const setter = window.__metaviewSetCamTexture;
        if (!setter) return;
        const img = document.querySelector('#input-image img');
        setter(img && img.src ? img.src : null);
    }
    """
    image.change(fn=None, inputs=None, outputs=None, js=_texture_js)

if __name__ == "__main__":
    # Bind all interfaces so the container port is reachable, and take the port
    # from the environment (the Hub maps host 7872 -> container PORT). mcp_server
    # is dropped: the Hub serves the plain Gradio UI, and enabling it would pull
    # an optional dependency we don't ship in the offline image.
    demo.launch(
        server_name="0.0.0.0",
        server_port=int(os.environ.get("PORT", "7860")),
    )
