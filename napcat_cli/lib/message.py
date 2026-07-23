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
            text = data.get("text", "")
            if isinstance(text, str):
                parts.append(text)
        elif seg_type == "image":
            details = []
            summary = data.get("summary", "")
            if isinstance(summary, str):
                summary = summary.strip()
            file_id = data.get("file", "") or data.get("file_id", "")
            url = data.get("url", "")
            sub_type = data.get("sub_type", "")
            file_size = data.get("file_size", "")
            ocr_text = data.get("ocr_text", "")
            if summary:
                details.append(f"摘要:{summary}")
            if file_id:
                details.append(f"id:{file_id}")
            if url and len(url) < 200:
                details.append(f"url:{url}")
            if sub_type:
                details.append(f"sub:{sub_type}")
            if file_size:
                details.append(f"size:{file_size}")
            if ocr_text:
                details.append(f"OCR:{ocr_text}")
            # Add seen/read status
            seen = data.get("seen", 0)
            read_ts = data.get("read_timestamp", 0)
            status = []
            if seen:
                status.append("已读")
            elif read_ts:
                status.append("读过")
            if status:
                details.append(f"状态:{','.join(status)}")
            parts.append(f"[图片{'(' + ', '.join(details) + ')' if details else ''}]")
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
        elif seg_type == "forward":
            # Forward/merged messages
            fid = data.get("id", "")
            if fid:
                parts.append(f"[合并转发: {fid}]")
            else:
                parts.append("[合并转发]")
        elif seg_type == "at":
            # @ mention
            qq = data.get("qq", "")
            name = data.get("name", "")
            if name:
                parts.append(f"@{name}")
            elif qq:
                parts.append(f"@{qq}")
            else:
                parts.append("[@]")
        else:
            # Unknown segment type
            parts.append(f"[{seg_type}]")
    return "".join(parts)


def extract_files(msg: list[dict[str, Any]], base_dir: str | None = None) -> list[Path]:
    """Extract file paths from image/video/record segments for agent reading.

    Returns Path objects for local files AND url references.
    If a value starts with http:// or https://, it is wrapped as a Path
    for the agent to download via its own HTTP client.
    """
    if not isinstance(msg, list):
        return []

    files = []
    for seg in msg:
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        if not isinstance(data, dict):
            continue

        if seg_type not in ("image", "video", "record"):
            continue

        # Collect file, path, url fields
        for key in ("file", "path", "url"):
            val = data.get(key, "")
            if val and isinstance(val, str):
                p = Path(val)
                files.append(p)
                break  # One file ref per segment

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
    the files exist. Useful for establishing message->file mapping.

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
            for key in ("file", "path", "url"):
                path_str = data.get(key, "")
                if path_str:
                    files.append(path_str)
                    break  # Prefer file over path over url

    return files


__all__ = [
    "format_message",
    "extract_files",
    "extract_file_paths",
    "segments_from_raw",
]