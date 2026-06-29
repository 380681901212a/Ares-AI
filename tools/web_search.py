import asyncio
import json
import os
import pathlib
import random
import sys
import time

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from tools.asset_manifest import normalize_image_requirement, normalize_query, register_asset
from tools.runtime_paths import WORKSPACE_ROOT

load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

_BASE_DIR = pathlib.Path(__file__).parent.parent.resolve()
_DEFAULT_WORKSPACE_DIR = str(WORKSPACE_ROOT)
_DEFAULT_DOWNLOADED_URLS_FILE = str(_BASE_DIR / "workspace" / "downloaded_urls.txt")
_SEARCH_MIN_DELAY = float(os.getenv("ARES_SEARCH_MIN_DELAY", "1.1"))
_SEARCH_MAX_DELAY = float(os.getenv("ARES_SEARCH_MAX_DELAY", "2.4"))
_SEARXNG_ENGINES = os.getenv("ARES_SEARXNG_ENGINES", "").strip()


def _polite_delay() -> None:
    if _SEARCH_MAX_DELAY <= 0:
        return
    minimum = max(0.0, _SEARCH_MIN_DELAY)
    maximum = max(minimum, _SEARCH_MAX_DELAY)
    time.sleep(random.uniform(minimum, maximum))


def _format_query_results(query: str, provider: str, results: list[dict]) -> str:
    query_text = []
    for item in results[:5]:
        link = item.get("url") or item.get("href") or ""
        snippet = item.get("content") or item.get("body") or ""
        title = item.get("title", "")
        if link:
            query_text.append(
                f"Provider: {provider}\nSource: {link}\nTitle: {title}\nContent: {snippet}"
            )
    if not query_text:
        return "error: no results"
    return f"--- Results for '{query}' via {provider} ---\n" + "\n\n".join(query_text)


def _searxng_search_one(query: str) -> str:
    url = "http://localhost:8080/search"
    print(f"\n[SEARXNG LOCAL] Searching for: {query}")
    params = {"q": query, "format": "json"}
    if _SEARXNG_ENGINES:
        params["engines"] = _SEARXNG_ENGINES
    response = requests.get(url, params=params, timeout=10)
    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}")
    data = response.json()
    return _format_query_results(query, "searxng", data.get("results", []))


def _duckduckgo_search_one(query: str) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # fallback for older installs
        except ImportError:
            return "error: ddgs not installed"

    print(f"\n[DUCKDUCKGO FALLBACK] Searching for: {query}")
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=5))
    return _format_query_results(query, "duckduckgo", results)


def _searxng_local_search(queries: list[str]) -> str:
    all_results = []
    # Using local Docker instance as primary search provider
    url = "http://localhost:8080/search"
    for query in queries:
        print(f"\n[SEARXNG LOCAL] Searching for: {query}")
        try:
            response = requests.get(url, params={"q": query, "format": "json"}, timeout=10)
            if response.status_code != 200:
                raise Exception(f"HTTP {response.status_code}")
                
            data = response.json()
            results = data.get("results", [])[:5]
            query_text = []
            for item in results:
                link = item.get("url", "")
                snippet = item.get("content", "")
                title = item.get("title", "")
                if link:
                    query_text.append(f"Source: {link}\nTitle: {title}\nContent: {snippet}")
            if query_text:
                all_results.append(f"--- Results for '{query}' ---\n" + "\n\n".join(query_text))
        except Exception as exc:
            print(f" -> SearxNG Local error for '{query}': {exc}")
            
    if not all_results:
        return "error: no results"
    return "\n\n".join(all_results)


def _duckduckgo_search(queries: list[str]) -> str:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # fallback for older installs
        except ImportError:
            return "error: ddgs not installed"
    
    all_results = []
    try:
        with DDGS() as ddgs:
            for query in queries:
                print(f"\n[DUCKDUCKGO FALLBACK] Searching for: {query}")
                try:
                    results = list(ddgs.text(query, max_results=5))
                    query_text = []
                    for item in results:
                        link = item.get("href", "")
                        snippet = item.get("body", "")
                        title = item.get("title", "")
                        if link:
                            query_text.append(f"Source: {link}\nTitle: {title}\nContent: {snippet}")
                    if query_text:
                        all_results.append(f"--- Results for '{query}' ---\n" + "\n\n".join(query_text))
                except Exception as exc:
                    print(f" -> DuckDuckGo error for '{query}': {exc}")
    except Exception as e:
        return f"error: duckduckgo init failed: {e}"
        
    if not all_results:
        return "error: no results"
    return "\n\n".join(all_results)


def deep_research(queries: list[str]) -> str:
    all_results = []
    for index, query in enumerate(queries):
        if index > 0:
            _polite_delay()

        result = ""
        try:
            result = _searxng_search_one(query)
            if result and not result.startswith("error:"):
                all_results.append(result)
                continue
            print(f"[Search] SearXNG returned no results for '{query}'. Falling back to DuckDuckGo...")
        except Exception as e:
            print(f"[Search] SearXNG failed for '{query}': {e}. Falling back to DuckDuckGo...")

        _polite_delay()
        try:
            result = _duckduckgo_search_one(query)
            if result and not result.startswith("error:"):
                all_results.append(result)
            else:
                all_results.append(f"--- Results for '{query}' ---\nNo results from available providers.")
        except Exception as e:
            all_results.append(f"--- Results for '{query}' ---\nAll search providers unavailable: {e}")

    if not all_results:
        return "error: no results"
    return "\n\n".join(all_results)


def _safe_prefix(value: str) -> str:
    safe_prefix = "".join(c if c.isalnum() or c == "_" else "" for c in value.replace(" ", "_"))
    safe_prefix = safe_prefix[:25].strip("_")
    return safe_prefix or "image"


def deep_image_search(
    image_queries: list[dict],
    workspace_dir: str | None = None,
    downloaded_urls_file: str | None = None,
    manifest_path: str | None = None,
    run_id: str = "",
) -> dict[str, object]:
    """Searches for images and downloads them to the current run workspace."""
    workspace_dir = workspace_dir or _DEFAULT_WORKSPACE_DIR
    downloaded_urls_file = downloaded_urls_file or os.path.join(workspace_dir, "downloaded_urls.txt")

    os.makedirs(workspace_dir, exist_ok=True)
    downloaded_images: list[dict] = []

    downloaded_urls = set()
    if os.path.exists(downloaded_urls_file):
        try:
            with open(downloaded_urls_file, "r", encoding="utf-8") as handle:
                downloaded_urls = {line.strip() for line in handle if line.strip()}
        except Exception:
            pass

    for img_req in image_queries:
        query = img_req["query"]
        save_as = img_req.get("save_as", "image")

        print(f"\n[SEARXNG LOCAL IMAGES] Searching for: {query} (Saving as: {save_as})")
        images = []
        try:
            url = "http://localhost:8080/search"
            response = requests.get(url, params={"q": query, "format": "json", "categories": "images"}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                images = [{"imageUrl": item.get("img_src")} for item in data.get("results", []) if item.get("img_src")]
            else:
                raise Exception(f"HTTP {response.status_code}")
        except Exception as exc:
            print(f" -> SearxNG Images error for '{query}': {exc}")
            print(f" -> Falling back to DuckDuckGo Images...")
            try:
                try:
                    from ddgs import DDGS
                except ImportError:
                    from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    ddg_results = list(ddgs.images(query, max_results=5))
                    images = [{"imageUrl": img.get("image")} for img in ddg_results if img.get("image")]
            except Exception as e:
                print(f" -> DDG Fallback failed: {e}")

        for index, img in enumerate(images):
            if "imageUrl" not in img:
                continue

            img_url = img["imageUrl"]
            base_url = img_url.split("?")[0].split("#")[0]
            if base_url in downloaded_urls:
                print(f" -> Skipping already downloaded image: {base_url}")
                continue

            safe_prefix = _safe_prefix(save_as)
            filename = os.path.join(workspace_dir, f"{safe_prefix}_{index}.jpg")

            print(f" -> Downloading image: {img_url}")
            try:
                img_res = requests.get(img_url, timeout=10)
                if img_res.status_code != 200:
                    continue

                content_type = img_res.headers.get("Content-Type", "")
                if not content_type.startswith("image/"):
                    print(f" -> Skipping non-image response ({content_type}): {img_url}")
                    continue

                with open(filename, "wb") as handle:
                    handle.write(img_res.content)

                asset_record = {
                    "path": str(pathlib.Path(filename).resolve()),
                    "relative_path": f"workspace/{pathlib.Path(filename).name}",
                    "filename": pathlib.Path(filename).name,
                    "query": query,
                    "requirement_key": normalize_image_requirement(query),
                    "source_url": img_url,
                    "normalized_source_url": base_url,
                    "origin": "web_search",
                    "status": "unverified",
                    "reason": "",
                }
                if manifest_path:
                    register_asset(manifest_path, asset_record, run_id=run_id)
                downloaded_images.append(asset_record)

                downloaded_urls.add(base_url)
                try:
                    with open(downloaded_urls_file, "a", encoding="utf-8") as handle:
                        handle.write(base_url + "\n")
                except Exception:
                    pass
                break
            except Exception as exc:
                print(f" -> Failed to download {img_url}: {exc}")

    if downloaded_images:
        return {
            "summary": "Successfully downloaded images to workspace:\n"
            + "\n".join(asset["path"] for asset in downloaded_images),
            "downloaded_images": downloaded_images,
        }
    return {
        "summary": "Image search yielded no results or downloads failed.",
        "downloaded_images": [],
    }
