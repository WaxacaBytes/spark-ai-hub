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
import sys

JS_FILE = sys.argv[1] if len(sys.argv) > 1 else ""
if not JS_FILE:
    # Find it from env
    import glob
    files = glob.glob("/app/venv/lib/python3.12/site-packages/comfyui_frontend_package/static/assets/MissingModelsWarning-*.js")
    files = [f for f in files if not f.endswith('.map') and not f.endswith('.bak')]
    JS_FILE = files[0]

with open(JS_FILE, 'r') as f:
    content = f.read()

marker = 'triggerBrowserDownload=()'
if marker not in content:
    print('[patch] ERROR: triggerBrowserDownload not found')
    sys.exit(1)

idx = content.index(marker)
start = content.index('{', idx)
depth = 0
end = start
for i in range(start, len(content)):
    if content[i] == '{': depth += 1
    elif content[i] == '}':
        depth -= 1
        if depth == 0:
            end = i + 1
            break

old_func = content[idx:end]
print(f'[patch] Found function ({len(old_func)} chars)')

# New function that calls server-side download API
# Using String.fromCharCode to avoid any quote/backtick conflicts
new_func = r"""triggerBrowserDownload=()=>{var _l=t||e.split("/").pop()||"download";var _p=_l.split(" / ");var _dir=_p.length>1?_p[0].trim():"checkpoints";var _fn=_p.length>1?_p.slice(1).join("/").trim():_p[0];var _h=new Headers();_h.set("Content-Type","application/json");fetch("/api/download-model",{method:"POST",headers:_h,body:JSON.stringify({url:e,directory:_dir,filename:_fn})}).then(function(r){return r.json()}).then(function(d){if(d.status==="started"){alert("Server download started: "+_fn)}else if(d.status==="already_exists"){alert("Model already exists: "+_fn)}else{alert("Download error: "+(d.error||"unknown"))}}).catch(function(err){alert("Download failed: "+err)})}"""

result = content[:idx] + new_func + content[end:]

with open(JS_FILE, 'w') as f:
    f.write(result)

print('[patch] Frontend patched successfully')
PYEOF
