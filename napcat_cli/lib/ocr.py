"""OCR integration for napcat-cli using PaddleOCR 3.x with MKLDNN disabled."""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import urllib.request
from typing import Any, Optional
from pathlib import Path

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


def get_ocr_instance() -> Optional[Any]:
    """Get or create the global PaddleOCR instance.
    
    Returns the OCR instance if available, None otherwise.
    Uses enable_mkldnn=False to avoid PaddlePaddle 3.x PIR/oneDNN crash.
    """
    global _ocr_instance, _ocr_available, _ocr_init_attempted
    
    if _ocr_init_attempted:
        return _ocr_instance if _ocr_available else None
    
    _ocr_init_attempted = True
    
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
        # Handle URLs by downloading first
        if image_path.startswith(('http://', 'https://')):
            import tempfile
            import urllib.request
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                import urllib.request
                urllib.request.urlretrieve(image_path, tmp.name)
                image_path = tmp.name
        
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