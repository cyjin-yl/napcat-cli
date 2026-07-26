"""OCR integration for napcat-cli using PaddleOCR 3.x with MKLDNN disabled.

PaddleOCR is an optional heavy dependency. It is commonly installed into a
project virtualenv (e.g. ``.test-venv``) rather than system Python (PEP 668).
The daemon is often launched with ``/usr/bin/python3``, so we try a few known
site-packages locations before giving up with a clear warning.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Disable Paddle oneDNN/MKLDNN at import time to avoid PIR/oneDNN crash
os.environ.setdefault("FLAGS_use_onednn", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

logger = logging.getLogger(__name__)

# Global OCR instance (lazy-initialized)
_ocr_instance: Any = None
_ocr_available: bool = False
_ocr_init_attempted: bool = False

# OCR cache by file hash
_ocr_cache: dict[str, str] = {}


def _file_hash(file_path: str) -> str:
    """Compute SHA256 hash of a file for OCR caching."""
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()



def _ensure_paddle_on_path() -> None:
    """If paddleocr is not importable, try known venv site-packages.

    Search order:
    1. NAPCAT_OCR_SITE_PACKAGES (explicit override)
    2. NAPCAT_VENV / VIRTUAL_ENV site-packages
    3. repo-local .test-venv / .venv next to the package tree
    """
    try:
        import paddleocr  # noqa: F401
        return
    except ImportError:
        pass

    candidates: list[Path] = []
    env_site = os.environ.get("NAPCAT_OCR_SITE_PACKAGES", "").strip()
    if env_site:
        candidates.append(Path(env_site))
    for env_name in ("NAPCAT_VENV", "VIRTUAL_ENV"):
        root = os.environ.get(env_name, "").strip()
        if root:
            candidates.append(Path(root) / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
            candidates.append(Path(root) / "lib" / "python3" / "site-packages")
    # napcat_cli/lib/ocr.py -> parents[2] == repo root (napcat-cli/)
    repo = Path(__file__).resolve().parents[2]
    for venv_name in (".test-venv", ".venv", "venv"):
        vroot = repo / venv_name
        candidates.append(vroot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages")
        candidates.append(vroot / "lib" / "python3" / "site-packages")

    seen: set[str] = set()
    for site in candidates:
        key = str(site)
        if key in seen:
            continue
        seen.add(key)
        if not site.is_dir():
            continue
        if not (site / "paddleocr").exists() and not list(site.glob("paddleocr-*.dist-info")):
            continue
        site_s = str(site)
        if site_s not in sys.path:
            sys.path.insert(0, site_s)
            logger.info("OCR: added site-packages for PaddleOCR: %s", site_s)
        try:
            import paddleocr  # noqa: F401
            return
        except ImportError as e:
            logger.warning("OCR: site-packages %s present but import failed: %s", site_s, e)
            # keep trying other candidates
            continue


def get_ocr_instance() -> Optional[Any]:
    """Get or create the global PaddleOCR instance.
    
    Returns the OCR instance if available, None otherwise.
    Uses enable_mkldnn=False to avoid PaddlePaddle 3.x PIR/oneDNN crash.
    """
    global _ocr_instance, _ocr_available, _ocr_init_attempted
    
    if _ocr_init_attempted:
        return _ocr_instance if _ocr_available else None
    
    _ocr_init_attempted = True

    _ensure_paddle_on_path()

    try:
        from paddleocr import PaddleOCR
        # Disable MKLDNN to avoid PIR/oneDNN crash on CPU
        _ocr_instance = PaddleOCR(
            use_textline_orientation=True,
            lang='ch',
            enable_mkldnn=False,
            use_doc_orientation_classify=False,
        )
        _ocr_available = True
        logger.info("PaddleOCR 3.x initialized successfully (MKLDNN disabled)")
        return _ocr_instance
    except ImportError as e:
        logger.warning(f"PaddleOCR not installed: {e}")
        _ocr_available = False
        return None
    except Exception as e:
        logger.error(f"Failed to initialize PaddleOCR: {e}")
        _ocr_available = False
        return None



def _download_image(url: str) -> str:
    """Download an image URL to a temp file, handling QQ anti-leech (防盗链).

    QQ multimedia URLs reject plain ``urllib.urlretrieve`` — they return a tiny
    HTML error page instead of the image. We send browser-like headers to get
    the real bytes. If direct download fails, we fall back to NapCat's
    ``get_image`` API (which downloads from inside the authenticated session).

    Returns the temp file path, or "" on failure.
    """
    import tempfile
    import urllib.request

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/107.0.5304.141 Mobile Safari/537.36 V1_AND_SQ_8.9.63",
        "Referer": "https://user.qzone.qq.com",
        "Accept": "image/*,*/*;q=0.8",
    }

    # Attempt 1: direct download with browser headers
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        if data and len(data) > 100:  # real image, not error HTML
            suffix = ".png"
            ct = resp.headers.get("Content-Type", "")
            if "jpeg" in ct or "jpg" in ct:
                suffix = ".jpg"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                return tmp.name
        else:
            logger.warning(f"OCR download too small ({len(data)} bytes) — likely anti-leech block")
    except Exception as e:
        logger.warning(f"OCR direct download failed: {e}")

    # Attempt 2: NapCat get_image API (authenticated, bypasses anti-leech)
    try:
        from napcat_cli.lib.api import NapCatAPI
        api = NapCatAPI()
        result = api.call("get_image", file=url)
        if result.get("retcode") == 0:
            data = result.get("data") or {}
            # NapCat may return a container-local path (useless on host)
            # or a fresh URL; try downloading the fresh URL with headers
            fresh_url = data.get("url") or ""
            if fresh_url and fresh_url != url:
                req2 = urllib.request.Request(fresh_url, headers=headers)
                with urllib.request.urlopen(req2, timeout=15) as resp:
                    data_bytes = resp.read()
                if data_bytes and len(data_bytes) > 100:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp.write(data_bytes)
                        return tmp.name
            # If NapCat returned base64 data, write it
            b64 = data.get("base64") or data.get("data") or ""
            if isinstance(b64, str) and b64:
                import base64
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                    tmp.write(base64.b64decode(b64))
                    return tmp.name
    except Exception as e:
        logger.warning(f"OCR NapCat get_image fallback failed: {e}")

    return ""


def ocr_image(image_path: str) -> list[dict[str, Any]]:
    """Perform OCR on an image file.
    
    Args:
        image_path: Path to image file or URL.
        
    Returns:
        List of OCR results with text, scores, and bounding boxes.
        Empty list if OCR unavailable or failed.
    """
    ocr = get_ocr_instance()
    if ocr is None:
        logger.warning("OCR requested but PaddleOCR not available")
        return []
    
    try:
        # Handle URLs by downloading first.
        # QQ multimedia URLs have anti-leech (防盗链): plain urlretrieve gets
        # a tiny HTML error page, not the image. We must send browser-like
        # headers (User-Agent + Referer) to get the real bytes.
        if image_path.startswith(('http://', 'https://')):
            image_path = _download_image(image_path)
            if not image_path:
                logger.error(f"OCR: could not download image (anti-leech or network)")
                return []
        
        result = ocr.predict(image_path)
        return parse_ocr_result(result)
    except Exception as e:
        logger.error(f"OCR failed for {image_path}: {e}")
        return []


def parse_ocr_result(result: Any) -> list[dict[str, Any]]:
    """Parse PaddleOCR 3.x predict() result into standardized format.
    
    Args:
        result: Raw result from PaddleOCR.predict()
        
    Returns:
        List of dicts with keys: text, score, box (polygon coordinates)
    """
    if not result:
        return []
    
    parsed = []
    try:
        # PaddleOCR 3.x predict() returns a list of result dicts
        for page_result in result:
            # Extract text recognition results
            rec_texts = page_result.get('rec_texts', [])
            rec_scores = page_result.get('rec_scores', [])
            rec_polys = page_result.get('rec_polys', [])
            
            for i, text in enumerate(rec_texts):
                parsed.append({
                    'text': text,
                    'score': float(rec_scores[i]) if i < len(rec_scores) else 0.0,
                    'box': rec_polys[i].tolist() if i < len(rec_polys) and hasattr(rec_polys[i], 'tolist') else rec_polys[i],
                })
    except Exception as e:
        logger.error(f"Failed to parse OCR result: {e}")
    
    return parsed


def ocr_file(file_path: str) -> str:
    """Convenience function: OCR a file and return extracted text as string.
    
    Args:
        file_path: Path to image file.
        
    Returns:
        Extracted text as a single string, or empty string if failed.
    """
    results = ocr_image(file_path)
    if not results:
        return ""
    return "\n".join(r['text'] for r in results if r.get('text'))


# CLI entry point
def main():
    """CLI entry point for napcat ocr command."""
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(description="OCR an image using PaddleOCR")
    parser.add_argument("image", help="Image file path or URL")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()
    
    ocr = get_ocr_instance()
    if ocr is None:
        print("PaddleOCR not available. Install paddleocr and paddlepaddle.", file=sys.stderr)
        return 1
    
    try:
        result = ocr.predict(args.image)
        if args.json:
            import json
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            parsed = parse_ocr_result(result)
            for item in parsed:
                print(item['text'])
    except Exception as e:
        print(f"OCR failed: {e}", file=sys.stderr)
        return 1
    
    return 0


if __name__ == "__main__":
    main()