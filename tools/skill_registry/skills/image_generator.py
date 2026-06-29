"""
image_generator — AresAI SkillRegistry

Generates AI images using the free Pollinations.ai API (no API key needed).
Supports logos, product renders, illustrations.

Config:
    prompts: list of {
        prompt:   str — text description of the image
        filename: str — output filename (e.g. 'workspace/audi_logo.png')
        width:    int — default 1024
        height:   int — default 768
        model:    str — 'flux' (default) | 'flux-realism' | 'flux-anime'
    }

Returns:
    str — comma-separated list of generated file paths,
          or error string starting with 'Error:'
"""

import os
import time
import urllib.parse
from pathlib import Path

import requests


_BASE_URL = "https://image.pollinations.ai/prompt/{prompt}"
_TIMEOUT  = 90  # Pollinations can be slow


def _generate_one(prompt: str, out_abs: str, width: int, height: int, model: str) -> bool:
    """Download one AI-generated image. Returns True on success."""
    encoded = urllib.parse.quote(prompt, safe="")
    url = (
        f"{_BASE_URL.format(prompt=encoded)}"
        f"?width={width}&height={height}&nologo=true&seed=42&model={model}"
    )

    print(f"[image_generator] Generating: '{prompt[:60]}...' → {os.path.basename(out_abs)}")
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()

        # Verify it's actually an image (not an HTML error page)
        ct = resp.headers.get("Content-Type", "")
        if "image" not in ct and len(resp.content) < 1024:
            print(f"[image_generator] Warning: unexpected content-type '{ct}', skipping.")
            return False

        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        with open(out_abs, "wb") as f:
            f.write(resp.content)
        print(f"[image_generator] ✅ Saved: {out_abs} ({len(resp.content)//1024}KB)")
        return True

    except Exception as exc:
        print(f"[image_generator] ❌ Failed '{prompt[:40]}': {exc}")
        return False


def execute(config: dict, workdir: str = ".") -> str:
    """
    Generate one or more AI images from text prompts.

    Returns comma-separated list of successfully generated file paths (relative to workdir).
    """
    prompts = config.get("prompts", [])
    if not prompts:
        return "Error: No prompts provided in image_generator config."

    generated: list[str] = []

    for item in prompts:
        if isinstance(item, str):
            # Simple string prompt → auto-name
            prompt   = item
            filename = f"workspace/generated_{len(generated)+1}.png"
            width    = 1024
            height   = 768
            model    = "flux"
        else:
            prompt   = item.get("prompt", "")
            filename = item.get("filename", f"workspace/generated_{len(generated)+1}.png")
            width    = int(item.get("width",  1024))
            height   = int(item.get("height", 768))
            model    = item.get("model", "flux")

        if not prompt:
            continue

        out_abs = os.path.join(workdir, filename)
        ok = _generate_one(prompt, out_abs, width, height, model)
        if ok:
            generated.append(filename)
            time.sleep(1)   # polite rate-limiting

    if not generated:
        return "Error: All image generation attempts failed."

    result = ", ".join(generated)
    print(f"[image_generator] Generated {len(generated)} image(s): {result}")
    return result
