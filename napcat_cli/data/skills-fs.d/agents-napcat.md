# NapCat Directory

This directory exposes NapCat QQ bot capabilities as files.
Subdirectories and files represent API endpoints, events, alerts, and conversations.

- `/napcat/groups/{group_id}/...` — group conversations and messages
- `/napcat/friends/{user_id}/...` — private conversations and messages
- `/napcat/send_group`, `/napcat/send_private` — legacy send endpoints (prefer group/friend `send` files)
- `/napcat/events/`, `/napcat/alerts/` — real-time events and notifications
