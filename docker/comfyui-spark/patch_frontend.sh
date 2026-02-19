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

python3 -c "
import re, sys

with open('${JS_FILE}', 'r') as f:
    content = f.read()

# The original triggerBrowserDownload creates an <a> element for browser download
old = r'triggerBrowserDownload=\(\)=>\{let n=document\.createElement\(\x60a\x60\);e\.includes\(\x60huggingface\.co\x60\)&&r\.value\?n\.href=F\(e\):\(n\.href=e,n\.download=t\|\|e\.split\(\x60/\x60\)\.pop\(\)\|\|\x60download\x60\),n\.target=\x60_blank\x60,n\.rel=\x60noopener noreferrer\x60,n\.click\(\)\}'

new = '''triggerBrowserDownload=()=>{let parts=(t||e.split(\x60/\x60).pop()||\x60download\x60).split(\x60 / \x60);let dir=parts.length>1?parts[0].trim():\x60checkpoints\x60;let fname=parts.length>1?parts.slice(1).join(\x60/\x60).trim():parts[0];fetch(\x60/api/download-model\x60,{method:\x60POST\x60,headers:{\x60Content-Type\x60:\x60application/json\x60},body:JSON.stringify({url:e,directory:dir,filename:fname})}).then(r=>r.json()).then(d=>{if(d.status===\x60started\x60)alert(\x60Server download started: \x60+fname);else if(d.status===\x60already_exists\x60)alert(\x60Model already exists: \x60+fname);else alert(\x60Download error: \x60+(d.error||\x60unknown\x60))}).catch(err=>alert(\x60Download failed: \x60+err))}'''

result = re.sub(old, new, content)

if result == content:
    print('[patch] WARNING: Pattern not found, trying simpler match...')
    # Simpler approach: find and replace the triggerBrowserDownload assignment
    old_simple = 'triggerBrowserDownload=()'
    if old_simple in content:
        # Find the full function by matching balanced braces
        idx = content.index(old_simple)
        # Find the end of the arrow function
        start = content.index('{', idx)
        depth = 0
        end = start
        for i in range(start, len(content)):
            if content[i] == '{':
                depth += 1
            elif content[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        old_func = content[idx:end]
        print(f'[patch] Found function ({len(old_func)} chars), replacing...')
        result = content[:idx] + new + content[end:]
    else:
        print('[patch] ERROR: Could not find triggerBrowserDownload')
        sys.exit(1)

with open('${JS_FILE}', 'w') as f:
    f.write(result)

print('[patch] Frontend patched successfully')
"
