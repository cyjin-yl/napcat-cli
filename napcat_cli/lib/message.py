"""NapCat/OneBot 11 message segment parsing and formatting.

Converts between NapCat segment format (array of {type, data}) and display format.
Extracts file paths from image/video/record segments for agent to read.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def format_message(msg: list[dict[str, Any]]) -> str:
    """Convert NapCat message segments to display string.

    Handles all segment types: text, at, image, record (voice), reply, video, face.

    Args:
        msg: List of segment dicts like [{"type": "text", "data": {"text": "hi"}}]

    Returns:
        Formatted message string with inline indicators (e.g., [media], @user)
    """
    if not isinstance(msg, list):
        return str(msg)

    parts = []
    for seg in msg:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})

        if seg_type == "text":
            parts.append(data.get("text", ""))
        elif seg_type == "at":
            # @mention: qq is QQ number as string
            qq = data.get("qq", "")
            if qq:
                parts.append(f"@{qq}")
        elif seg_type == "image":
            # Image: file path, summary, url
            summary = data.get("summary", "").strip()
            if summary:
                parts.append(f"[图片: {summary}]")
            else:
                parts.append("[图片]")
        elif seg_type == "record":
            # Voice/audio: file, path, url
            parts.append("[语音]")
        elif seg_type == "video":
            # Video: file, url
            parts.append("[视频]")
        elif seg_type == "reply":
            # Reply reference
            reply_id = data.get("id", "")
            if reply_id:
                parts.append(f"[回复: {reply_id}]")
        elif seg_type == "face":
            # QQ face emoji
            face_id = data.get("id", "")
            parts.append(f"[表情: {face_id}]")
        else:
            # Unknown segment type
            parts.append(f"[{seg_type}]")

    return "".join(parts)


def extract_files(msg: list[dict[str, Any]], base_dir: str | None = None) -> list[Path]:
    """Extract file paths from image/video/record segments for agent reading.

    Args:
        msg: NapCat message segments
        base_dir: Optional base directory for relative paths

    Returns:
        List of Path objects pointing to files referenced in the message.
    """
    if not isinstance(msg, list):
        return []

    files = []
    for seg in msg:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})

        # Look for file, path, or url fields
        for key in ("file", "path"):
            path_str = data.get(key, "")
            if path_str:
                p = Path(path_str)
                if base_dir:
                    p = Path(base_dir) / p
                if p.exists():
                    files.append(p)
                    break  # Prefer file over path

    return files


def segments_from_raw(raw_message: str, msg: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parse message from raw_message string (legacy fallback).

    NapCat provides both raw_message (plain string) and message (segments).
    If raw_message differs from segments, this attempts to convert.

    Args:
        raw_message: Plain text message
        msg: Parsed message segments

    Returns:
        Normalized segment list
    """
    if not msg or raw_message != format_message(msg):
        # raw_message has different content, treat as plain text
        return [{"type": "text", "data": {"text": raw_message}}]

    return msg


def extract_file_paths(msg: list[dict[str, Any]]) -> list[str]:
    """Extract file path strings from image/video/record segments.

    Returns all file paths referenced in the message, regardless of whether
    the files exist. Useful for establishing message→file mapping.

    Args:
        msg: NapCat message segments

    Returns:
        List of file path strings
    """
    if not isinstance(msg, list):
        return []

    files = []
    for seg in msg:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})

        if seg_type in ("image", "record", "video"):
            for key in ("file", "path"):
                path_str = data.get(key, "")
                if path_str:
                    files.append(path_str)
                    break  # Prefer file over path

    return files


__all__ = [
    "format_message",
    "extract_files",
    "extract_file_paths",
    "segments_from_raw",
]