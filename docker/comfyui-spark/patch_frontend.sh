#!/bin/bash
# Patch ComfyUI frontend to use server-side model downloads instead of browser downloads
set -e

JS_DIR="/app/venv/lib/python3.12/site-packages/comfyui_frontend_package/static/assets"
JS_FILE=$(ls "$JS_DIR"/MissingModelsWarning-*.js 2>/dev/null | grep -v '.map$' | head -1)

if [ -z "$JS_FILE" ]; then
    echo "[patch] MissingModelsWarning JS not found, skipping patch"
    exit 0
fi

echo "[patch] Patching $JS_FILE for server-side downloads..."
cp "$JS_FILE" "${JS_FILE}.bak"

python3 << 'PYEOF'
import glob, sys

files = glob.glob("/app/venv/lib/python3.12/site-packages/comfyui_frontend_package/static/assets/MissingModelsWarning-*.js")
files = [f for f in files if not f.endswith('.map') and not f.endswith('.bak')]
JS_FILE = files[0]

with open(JS_FILE, 'r') as f:
    content = f.read()

# Strategy: Replace the FileDownload component's onClick for the download button.
# The FileDownload component (Q) has props: url, hint, label, error
# The label format is "directory / filename" (e.g. "checkpoints / model.safetensors")
# We replace the onClick that calls triggerBrowserDownload with our server-side download.

# Find: onClick:u(s).triggerBrowserDownload
# This is where the Download button calls the browser download function.
# Replace it with an inline function that calls our API using the component's props.

old = 'onClick:u(s).triggerBrowserDownload'

if old not in content:
    print('[patch] ERROR: onClick:u(s).triggerBrowserDownload not found')
    sys.exit(1)

# The new onclick extracts directory and filename from the label prop (t.label)
# t is the component props (set earlier as: let t=e)
# t.label = "checkpoints / v1-5-pruned-emaonly-fp16.safetensors"
# t.url = the download URL
new = r'''onClick:function(){var _parts=t.label?t.label.split(" / "):[];var _dir=_parts.length>1?_parts[0].trim():"checkpoints";var _fn=_parts.length>1?_parts.slice(1).join("/").trim():(t.url||"").split("/").pop().split("?")[0];var _h=new Headers();_h.set("Content-Type","application/json");fetch("/api/download-model",{method:"POST",headers:_h,body:JSON.stringify({url:t.url,directory:_dir,filename:_fn})}).then(function(r){return r.json()}).then(function(d){if(d.status==="started"){alert("Server download started: "+_fn)}else if(d.status==="already_exists"){alert("Model already exists: "+_fn)}else{alert("Download error: "+(d.error||"unknown"))}}).catch(function(err){alert("Download failed: "+err)})}'''

result = content.replace(old, new)

with open(JS_FILE, 'w') as f:
    f.write(result)

print('[patch] Frontend patched successfully')
print(f'[patch] Replaced {content.count(old)} occurrence(s) of onClick handler')
PYEOF
