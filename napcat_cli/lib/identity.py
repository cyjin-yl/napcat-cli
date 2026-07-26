"""Canonical display labels for QQ groups and people.

Wake prompts, logs, and agent-facing text must use one builder so Hermes
builds stable mental models:

- Group:  (群备注|此群无备注)(群名)(群号)
- Person: (群名片|无群名片)(用户昵称)(QQ号)

Empty remark/card uses an explicit Chinese placeholder (not bare ``()``) so
models do not treat a missing field as noise. Numeric IDs are always labeled
in surrounding prompt text (see ``IDENTITY_LEGEND`` / field tags).

Lookups (get_group_info / get_group_member_info / get_stranger_info) are cached
in-process with a short TTL. Failures fall back to event-local fields.
"""
from __future__ import annotations

import time
from typing import Any

_group_cache: dict[str, tuple[dict[str, str], float]] = {}
_person_cache: dict[str, tuple[dict[str, str], float]] = {}
_CACHE_TTL = 300.0

# Explicit empty-slot placeholders (never bare empty parens for name slots).
NO_GROUP_REMARK = "此群无备注"
NO_GROUP_CARD = "无群名片"
NO_NICKNAME = "未知昵称"
NO_GROUP_NAME = "未知群名"

# Shown once in wake prompts so models know what each slot/number means.
IDENTITY_LEGEND = (
    "[身份格式说明] "
    "群：`(群备注|此群无备注)(群名)(群号数字)` — 第三段纯数字是 QQ 群号 group_id。"
    "人：`(群名片|无群名片)(用户昵称)(QQ号数字)` — 第三段纯数字是用户 QQ 号 user_id。"
    "私聊没有群名片时第二段仍是昵称，第三段仍是 QQ 号。"
    "引用回复时："
    "`触发消息ID` = 对方刚发来、需要你 reply 的那条 message_id（`napcat reply` 的 <mid> 用这个）；"
    "`你的原消息ID` = 对方引用的、你自己之前发过的 message_id（仅上下文，不要当成 reply 目标）。"
)


def format_group(remark: str | None, name: str | None, group_id: str | int | None) -> str:
    """Return ``(群备注|此群无备注)(群名)(群号)``."""
    gid = "" if group_id is None else str(group_id).strip()
    r = _slot(remark) or NO_GROUP_REMARK
    n = _slot(name) or NO_GROUP_NAME
    return f"({r})({n})({gid})"


def format_person(card: str | None, nickname: str | None, user_id: str | int | None) -> str:
    """Return ``(群名片|无群名片)(用户昵称)(QQ号)``."""
    uid = "" if user_id is None else str(user_id).strip()
    c = _slot(card) or NO_GROUP_CARD
    n = _slot(nickname) or NO_NICKNAME
    return f"({c})({n})({uid})"


def _slot(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cache_get(cache: dict, key: str) -> dict[str, str] | None:
    hit = cache.get(key)
    if not hit:
        return None
    val, exp = hit
    if time.monotonic() > exp:
        cache.pop(key, None)
        return None
    return val


def _cache_put(cache: dict, key: str, val: dict[str, str]) -> dict[str, str]:
    cache[key] = (val, time.monotonic() + _CACHE_TTL)
    return val


def resolve_group(group_id: str | int | None, *, api: Any | None = None) -> dict[str, str]:
    """Return ``{remark, name, group_id}`` best-effort."""
    gid = "" if group_id is None else str(group_id).strip()
    if not gid:
        return {"remark": "", "name": "", "group_id": ""}
    cached = _cache_get(_group_cache, gid)
    if cached is not None:
        return cached
    remark = name = ""
    try:
        if api is None:
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
        result = api.call("get_group_info", group_id=int(gid) if gid.isdigit() else gid)
        if result.get("retcode") == 0:
            data = result.get("data") or {}
            remark = str(data.get("group_remark") or data.get("remark") or "")
            name = str(data.get("group_name") or data.get("name") or "")
    except Exception:
        pass
    return _cache_put(_group_cache, gid, {"remark": remark, "name": name, "group_id": gid})


def resolve_person(
    user_id: str | int | None,
    *,
    group_id: str | int | None = None,
    sender: dict | None = None,
    api: Any | None = None,
) -> dict[str, str]:
    """Return ``{card, nickname, user_id}`` best-effort."""
    uid = "" if user_id is None else str(user_id).strip()
    if not uid and isinstance(sender, dict):
        uid = str(sender.get("user_id") or "").strip()
    gid = "" if group_id is None else str(group_id).strip()
    cache_key = f"{gid}:{uid}" if gid else f":{uid}"

    card = nick = ""
    if isinstance(sender, dict):
        card = str(sender.get("card") or "")
        nick = str(sender.get("nickname") or sender.get("nick") or "")
        if not uid:
            uid = str(sender.get("user_id") or "").strip()

    if card and nick and uid:
        return _cache_put(_person_cache, cache_key, {"card": card, "nickname": nick, "user_id": uid})

    cached = _cache_get(_person_cache, cache_key)
    if cached is not None:
        return {
            "card": card or cached.get("card", ""),
            "nickname": nick or cached.get("nickname", ""),
            "user_id": uid or cached.get("user_id", ""),
        }

    try:
        if api is None:
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
        if gid and uid and (not card or not nick):
            result = api.call(
                "get_group_member_info",
                group_id=int(gid) if gid.isdigit() else gid,
                user_id=int(uid) if uid.isdigit() else uid,
            )
            if result.get("retcode") == 0:
                data = result.get("data") or {}
                card = card or str(data.get("card") or "")
                nick = nick or str(data.get("nickname") or "")
        if uid and not nick:
            result = api.call(
                "get_stranger_info",
                user_id=int(uid) if uid.isdigit() else uid,
            )
            if result.get("retcode") == 0:
                data = result.get("data") or {}
                nick = nick or str(data.get("nickname") or data.get("nick") or "")
    except Exception:
        pass

    return _cache_put(
        _person_cache,
        cache_key,
        {"card": card, "nickname": nick, "user_id": uid},
    )


def label_group(group_id: str | int | None, *, api: Any | None = None) -> str:
    if group_id is None or str(group_id).strip() == "":
        return "私聊"
    info = resolve_group(group_id, api=api)
    return format_group(info.get("remark"), info.get("name"), info.get("group_id"))


def label_person(
    user_id: str | int | None = None,
    *,
    group_id: str | int | None = None,
    sender: dict | None = None,
    api: Any | None = None,
) -> str:
    info = resolve_person(user_id, group_id=group_id, sender=sender, api=api)
    return format_person(info.get("card"), info.get("nickname"), info.get("user_id"))


def label_from_event_who(event: dict, *, api: Any | None = None) -> str:
    if not isinstance(event, dict):
        return format_person("", "?", "")
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    uid = sender.get("user_id") or event.get("user_id") or event.get("operator_id")
    return label_person(uid, group_id=event.get("group_id"), sender=sender or None, api=api)


def label_from_event_where(event: dict, *, api: Any | None = None) -> str:
    if not isinstance(event, dict):
        return "私聊"
    g = event.get("group_id")
    if g is None or str(g).strip() == "":
        return "私聊"
    return label_group(g, api=api)


def clear_caches() -> None:
    _group_cache.clear()
    _person_cache.clear()
