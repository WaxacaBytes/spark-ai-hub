import { useState, useEffect } from "react";

const RECIPES = [
  {
    slug: "ollama-openwebui",
    name: "Open WebUI + Ollama",
    description: "ChatGPT-like interface for local LLMs with model management, conversation history, and RAG plugins.",
    category: "llm",
    author: "NVIDIA Playbook",
    status: "official",
    ui_type: "web",
    port: 8080,
    mem_gb: 16,
    disk_gb: 8,
    build_min: 3,
    tags: ["chat", "llm", "ollama", "rag"],
    icon: "💬",
  },
  {
    slug: "comfyui-sparky",
    name: "ComfyUI (SparkyUI)",
    description: "Node-based image & video generation with SageAttention optimized for Blackwell GB10. Supports FLUX, SD3.5, WAN 2.2.",
    category: "image-gen",
    author: "ecarmen16",
    status: "community-verified",
    ui_type: "web",
    port: 8188,
    mem_gb: 40,
    disk_gb: 30,
    build_min: 15,
    tags: ["stable-diffusion", "flux", "wan2.2", "video", "comfyui"],
    icon: "🎨",
  },
  {
    slug: "hunyuan3d-2.1",
    name: "Hunyuan3D 2.1",
    description: "Text/image to 3D model generation with full PBR texturing. Dockerized with Blender bpy compiled for ARM64.",
    category: "3d-gen",
    author: "dr-vij",
    status: "community-verified",
    ui_type: "gradio",
    port: 7860,
    mem_gb: 40,
    disk_gb: 50,
    build_min: 30,
    tags: ["3d", "mesh", "texturing", "pbr"],
    icon: "🧊",
  },
  {
    slug: "trellis2",
    name: "TRELLIS.2",
    description: "Microsoft's 4B-parameter 3D generative model. Image-to-3D with O-Voxel representation and complex topology support.",
    category: "3d-gen",
    author: "raziel2001au",
    status: "community-verified",
    ui_type: "gradio",
    port: 7861,
    mem_gb: 45,
    disk_gb: 35,
    build_min: 20,
    tags: ["3d", "microsoft", "mesh", "gaussian"],
    icon: "🔮",
  },
  {
    slug: "localai",
    name: "LocalAI",
    description: "Drop-in OpenAI API replacement. LLMs, image gen, audio, video, voice cloning — all via REST API. DGX Spark CUDA 13 image.",
    category: "multi-modal",
    author: "mudler",
    status: "official",
    ui_type: "api",
    port: 8080,
    mem_gb: 20,
    disk_gb: 15,
    build_min: 4,
    tags: ["api", "openai-compatible", "tts", "image", "llm"],
    icon: "🤖",
  },
  {
    slug: "lm-studio",
    name: "LM Studio",
    description: "Desktop GUI for discovering, downloading, and serving LLMs. Native ARM64 AppImage with CUDA 13 and built-in API server.",
    category: "llm",
    author: "LM Studio",
    status: "official",
    ui_type: "desktop",
    port: 1234,
    mem_gb: 8,
    disk_gb: 5,
    build_min: 2,
    tags: ["llm", "gui", "gguf", "api-server"],
    icon: "🧪",
  },
  {
    slug: "vllm-sm121",
    name: "vLLM (Blackwell)",
    description: "High-throughput LLM serving with the community sm_121 fork. Supports Qwen3, Llama, DeepSeek with FP8/FP4 quantization.",
    category: "llm",
    author: "seli-equinix",
    status: "community-verified",
    ui_type: "api",
    port: 8888,
    mem_gb: 60,
    disk_gb: 40,
    build_min: 25,
    tags: ["inference", "vllm", "qwen", "llama", "fp4"],
    icon: "⚡",
  },
  {
    slug: "wan22-video",
    name: "WAN 2.2 Video Gen",
    description: "Qwen's video generation model via ComfyUI. Text-to-video and image-to-video up to 720p. NVFP4 quantized for Spark.",
    category: "video-gen",
    author: "NVIDIA CES 2026",
    status: "official",
    ui_type: "web",
    port: 8188,
    mem_gb: 80,
    disk_gb: 45,
    build_min: 20,
    tags: ["video", "wan", "qwen", "t2v", "i2v"],
    icon: "🎬",
  },
  {
    slug: "llama-cpp-ggml",
    name: "llama.cpp / ggml",
    description: "Lightweight inference stack. Runs text gen, image gen, STT, TTS, and embeddings simultaneously via REST APIs.",
    category: "llm",
    author: "ggml-org",
    status: "community-verified",
    ui_type: "api",
    port: 8080,
    mem_gb: 12,
    disk_gb: 10,
    build_min: 10,
    tags: ["inference", "gguf", "lightweight", "multi-service"],
    icon: "🦙",
  },
];

const CATEGORIES = [
  { id: "all", label: "All", icon: "◆" },
  { id: "llm", label: "LLMs", icon: "💬" },
  { id: "image-gen", label: "Image Gen", icon: "🎨" },
  { id: "video-gen", label: "Video Gen", icon: "🎬" },
  { id: "3d-gen", label: "3D Gen", icon: "🧊" },
  { id: "multi-modal", label: "Multi-Modal", icon: "🤖" },
];

const STATUS_COLORS = {
  official: { bg: "rgba(16,185,129,0.12)", text: "#10b981", label: "Official" },
  "community-verified": { bg: "rgba(245,158,11,0.12)", text: "#f59e0b", label: "Verified" },
  experimental: { bg: "rgba(239,68,68,0.12)", text: "#ef4444", label: "Experimental" },
};

function StatusBadge({ status }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.experimental;
  return (
    <span style={{ background: s.bg, color: s.text, padding: "2px 8px", borderRadius: 4, fontSize: 11, fontWeight: 600, letterSpacing: 0.3, textTransform: "uppercase" }}>
      {s.label}
    </span>
  );
}

function SystemBar({ usedMem }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "10px 20px", background: "rgba(255,255,255,0.03)", borderBottom: "1px solid rgba(255,255,255,0.06)", fontSize: 12, color: "#8b8b9a" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "#76b900", fontSize: 14 }}>●</span>
        <span style={{ color: "#ccc" }}>GB10 Blackwell</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>RAM</span>
        <div style={{ width: 100, height: 6, background: "rgba(255,255,255,0.08)", borderRadius: 3, overflow: "hidden" }}>
          <div style={{ width: `${(usedMem / 128) * 100}%`, height: "100%", background: usedMem > 100 ? "#ef4444" : "#76b900", borderRadius: 3, transition: "width 0.5s" }} />
        </div>
        <span>{usedMem}GB / 128GB</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>GPU</span>
        <span style={{ color: "#76b900" }}>43%</span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>Disk</span>
        <span>1.2TB free</span>
      </div>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ color: "#76b900" }}>▲</span>
        <span>9 recipes available</span>
      </div>
    </div>
  );
}

function RecipeCard({ recipe, installed, running, onInstall, onLaunch, onStop, installing }) {
  const isInstalling = installing === recipe.slug;
  return (
    <div style={{
      background: "rgba(255,255,255,0.03)",
      border: "1px solid rgba(255,255,255,0.07)",
      borderRadius: 12,
      padding: 20,
      display: "flex",
      flexDirection: "column",
      gap: 12,
      transition: "all 0.2s",
      cursor: "default",
      position: "relative",
      overflow: "hidden",
    }}
    onMouseEnter={e => { e.currentTarget.style.borderColor = "rgba(118,185,0,0.3)"; e.currentTarget.style.background = "rgba(255,255,255,0.05)"; }}
    onMouseLeave={e => { e.currentTarget.style.borderColor = "rgba(255,255,255,0.07)"; e.currentTarget.style.background = "rgba(255,255,255,0.03)"; }}
    >
      {running && (
        <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 2, background: "linear-gradient(90deg, #76b900, #a3e635)" }} />
      )}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 28 }}>{recipe.icon}</span>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15, color: "#f0f0f0", lineHeight: 1.2 }}>{recipe.name}</div>
            <div style={{ fontSize: 11, color: "#6b6b7b", marginTop: 2 }}>by {recipe.author}</div>
          </div>
        </div>
        <StatusBadge status={recipe.status} />
      </div>
      <p style={{ fontSize: 13, color: "#9b9bab", lineHeight: 1.5, margin: 0, flex: 1 }}>
        {recipe.description}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {recipe.tags.slice(0, 4).map(t => (
          <span key={t} style={{ background: "rgba(255,255,255,0.06)", color: "#7b7b8b", padding: "2px 7px", borderRadius: 4, fontSize: 10, fontFamily: "monospace" }}>{t}</span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 12, fontSize: 11, color: "#6b6b7b", borderTop: "1px solid rgba(255,255,255,0.05)", paddingTop: 10 }}>
        <span>🧠 {recipe.mem_gb}GB RAM</span>
        <span>💾 {recipe.disk_gb}GB disk</span>
        <span>⏱ ~{recipe.build_min}min build</span>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        {!installed && !isInstalling && (
          <button onClick={() => onInstall(recipe.slug)} style={{
            flex: 1, padding: "8px 0", background: "linear-gradient(135deg, #76b900, #5a8f00)", color: "#fff",
            border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer", transition: "all 0.15s",
          }}
          onMouseEnter={e => e.currentTarget.style.transform = "translateY(-1px)"}
          onMouseLeave={e => e.currentTarget.style.transform = "translateY(0)"}
          >
            Install
          </button>
        )}
        {isInstalling && (
          <div style={{ flex: 1, padding: "8px 0", background: "rgba(118,185,0,0.1)", border: "1px solid rgba(118,185,0,0.3)", borderRadius: 8, textAlign: "center", fontSize: 13, color: "#76b900" }}>
            <span style={{ display: "inline-block", animation: "spin 1s linear infinite" }}>⟳</span> Building...
          </div>
        )}
        {installed && !running && (
          <>
            <button onClick={() => onLaunch(recipe.slug)} style={{
              flex: 1, padding: "8px 0", background: "linear-gradient(135deg, #76b900, #5a8f00)", color: "#fff",
              border: "none", borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: "pointer",
            }}>▶ Launch</button>
            <button style={{ padding: "8px 14px", background: "rgba(255,255,255,0.06)", color: "#9b9bab", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 13, cursor: "pointer" }}>⋯</button>
          </>
        )}
        {running && (
          <>
            <a href={`http://localhost:${recipe.port}`} target="_blank" rel="noreferrer" style={{
              flex: 1, padding: "8px 0", background: "rgba(118,185,0,0.1)", border: "1px solid rgba(118,185,0,0.3)",
              borderRadius: 8, textAlign: "center", fontSize: 13, color: "#76b900", fontWeight: 600, textDecoration: "none", cursor: "pointer",
            }}>Open UI ↗ :{recipe.port}</a>
            <button onClick={() => onStop(recipe.slug)} style={{ padding: "8px 14px", background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.2)", borderRadius: 8, fontSize: 13, cursor: "pointer" }}>■ Stop</button>
          </>
        )}
      </div>
    </div>
  );
}

function BuildLog({ recipe, onDone }) {
  const [lines, setLines] = useState([]);
  const logs = [
    `[SparkForge] Starting build for ${recipe}...`,
    `[docker] Pulling nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04`,
    `[docker] Layer 1/12: sha256:a3ed95... already exists`,
    `[docker] Layer 2/12: sha256:b4df2a... downloading 234MB`,
    `[docker] Layer 3/12: sha256:c5e831... downloading 89MB`,
    `[build] Installing Python 3.10.19 from source...`,
    `[build] Installing PyTorch 2.6.0+cu130 (ARM64)...`,
    `[build] Compiling flash-attn for sm_121...`,
    `[build] Installing project dependencies...`,
    `[build] Downloading model weights (25GB)...`,
    `[docker] Successfully built a7f3c2d1e8b9`,
    `[SparkForge] ✅ ${recipe} installed successfully!`,
  ];

  useEffect(() => {
    let i = 0;
    const timer = setInterval(() => {
      if (i < logs.length) {
        setLines(prev => [...prev, logs[i]]);
        i++;
      } else {
        clearInterval(timer);
        setTimeout(onDone, 800);
      }
    }, 600);
    return () => clearInterval(timer);
  }, []);

  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0, height: 220,
      background: "#0c0c10", borderTop: "1px solid rgba(118,185,0,0.3)",
      fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: 11,
      padding: 16, overflow: "auto", zIndex: 100,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <span style={{ color: "#76b900", fontWeight: 700, fontSize: 12 }}>BUILD LOG — {recipe}</span>
        <span style={{ color: "#6b6b7b" }}>⟳ Building...</span>
      </div>
      {lines.map((l, i) => (
        <div key={i} style={{ color: l.includes("✅") ? "#76b900" : l.includes("error") ? "#ef4444" : "#8b8b9a", lineHeight: 1.8 }}>
          {l}
        </div>
      ))}
      <div style={{ color: "#76b900", animation: "blink 1s infinite" }}>▋</div>
    </div>
  );
}

export default function SparkForge() {
  const [category, setCategory] = useState("all");
  const [search, setSearch] = useState("");
  const [installed, setInstalled] = useState(new Set(["ollama-openwebui", "comfyui-sparky"]));
  const [running, setRunning] = useState(new Set(["ollama-openwebui"]));
  const [installing, setInstalling] = useState(null);
  const [tab, setTab] = useState("catalog");

  const usedMem = [...running].reduce((sum, slug) => {
    const r = RECIPES.find(x => x.slug === slug);
    return sum + (r ? r.mem_gb : 0);
  }, 0);

  const filtered = RECIPES.filter(r => {
    if (category !== "all" && r.category !== category) return false;
    if (tab === "running" && !running.has(r.slug)) return false;
    if (tab === "installed" && !installed.has(r.slug)) return false;
    if (search && !r.name.toLowerCase().includes(search.toLowerCase()) && !r.tags.some(t => t.includes(search.toLowerCase()))) return false;
    return true;
  });

  const handleInstall = (slug) => {
    setInstalling(slug);
  };
  const handleInstallDone = () => {
    setInstalled(prev => new Set([...prev, installing]));
    setInstalling(null);
  };
  const handleLaunch = (slug) => setRunning(prev => new Set([...prev, slug]));
  const handleStop = (slug) => setRunning(prev => { const n = new Set(prev); n.delete(slug); return n; });

  return (
    <div style={{ minHeight: "100vh", background: "#0e0e14", color: "#e0e0e8", fontFamily: "'DM Sans', 'Segoe UI', sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 3px; }
      `}</style>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 24px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div style={{ width: 32, height: 32, borderRadius: 8, background: "linear-gradient(135deg, #76b900, #4a7a00)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, fontWeight: 800, color: "#fff" }}>⚡</div>
          <div>
            <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: -0.5 }}>SparkForge</span>
            <span style={{ fontSize: 11, color: "#6b6b7b", marginLeft: 8, fontWeight: 500 }}>v0.1.0</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, background: "rgba(255,255,255,0.04)", borderRadius: 8, padding: 3 }}>
          {[
            { id: "catalog", label: "Catalog", count: RECIPES.length },
            { id: "installed", label: "Installed", count: installed.size },
            { id: "running", label: "Running", count: running.size },
          ].map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} style={{
              padding: "6px 14px", borderRadius: 6, border: "none", fontSize: 13, fontWeight: 600, cursor: "pointer",
              background: tab === t.id ? "rgba(118,185,0,0.15)" : "transparent",
              color: tab === t.id ? "#76b900" : "#7b7b8b",
              transition: "all 0.15s",
            }}>
              {t.label} <span style={{ fontSize: 11, opacity: 0.7 }}>({t.count})</span>
            </button>
          ))}
        </div>
      </div>

      <SystemBar usedMem={usedMem} />

      {/* Search + Filters */}
      <div style={{ padding: "16px 24px", display: "flex", gap: 12, alignItems: "center" }}>
        <div style={{ position: "relative", flex: 1, maxWidth: 360 }}>
          <span style={{ position: "absolute", left: 12, top: "50%", transform: "translateY(-50%)", color: "#5b5b6b", fontSize: 14 }}>⌕</span>
          <input
            type="text" placeholder="Search recipes, tags..."
            value={search} onChange={e => setSearch(e.target.value)}
            style={{
              width: "100%", padding: "8px 12px 8px 34px", background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
              borderRadius: 8, color: "#e0e0e8", fontSize: 13, outline: "none", fontFamily: "inherit",
            }}
          />
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {CATEGORIES.map(c => (
            <button key={c.id} onClick={() => setCategory(c.id)} style={{
              padding: "6px 12px", borderRadius: 6, border: "1px solid transparent", fontSize: 12, cursor: "pointer", fontWeight: 500,
              background: category === c.id ? "rgba(118,185,0,0.12)" : "rgba(255,255,255,0.03)",
              color: category === c.id ? "#76b900" : "#8b8b9b",
              borderColor: category === c.id ? "rgba(118,185,0,0.25)" : "rgba(255,255,255,0.06)",
              transition: "all 0.15s",
            }}>
              {c.icon} {c.label}
            </button>
          ))}
        </div>
      </div>

      {/* Recipe Grid */}
      <div style={{ padding: "0 24px 120px", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 16 }}>
        {filtered.map(r => (
          <RecipeCard
            key={r.slug}
            recipe={r}
            installed={installed.has(r.slug)}
            running={running.has(r.slug)}
            installing={installing}
            onInstall={handleInstall}
            onLaunch={handleLaunch}
            onStop={handleStop}
          />
        ))}
        {filtered.length === 0 && (
          <div style={{ gridColumn: "1 / -1", textAlign: "center", padding: 60, color: "#5b5b6b" }}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>🔍</div>
            <div style={{ fontSize: 15 }}>No recipes match your filters</div>
          </div>
        )}
      </div>

      {installing && <BuildLog recipe={installing} onDone={handleInstallDone} />}
    </div>
  );
}
