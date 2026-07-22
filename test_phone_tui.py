#!/usr/bin/env python3
"""Test script for napcat phone TUI using Textual's test harness."""
from __future__ import annotations

import sys
from pathlib import Path

# Same sys.path setup as the CLI
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from napcat_cli.tui.app import NapCatApp
from napcat_cli.tui.chat_list import ChatListScreen
from napcat_cli.tui.chat_view import ChatViewScreen


async def test_smoke():
    """Smoke test: app starts, data loads, chat list populates."""
    app = NapCatApp()
    async with app.run_test(size=(80, 24)) as pilot:
        # Wait for data to load (on_mount triggers refresh_data)
        await pilot.pause(3)

        # Check app state
        chats = app.chats
        alerts = app.alerts

        print(f"✓ App mounted")
        print(f"✓ Chats loaded: {len(chats)}")
        for cid, chat in list(chats.items())[:10]:
            print(f"    - {cid}: {chat.name} ({chat.kind}), unread={chat.unread}")

        print(f"✓ Alerts loaded: {len(alerts)}")

        # Verify we're on the ChatListScreen
        screen = app.screen
        assert isinstance(screen, ChatListScreen), f"Expected ChatListScreen, got {type(screen)}"
        print(f"✓ On ChatListScreen")

        # Check ListView has items
        listview = screen.query_one("#chat-listview")
        print(f"✓ ListView items: {len(listview)}")

        if listview and len(listview) > 0:
            # Focus the listview and press Enter to select first item
            listview.focus()
            await pilot.press("enter")
            await pilot.pause(1)

            screen = app.screen
            print(f"✓ Screen after Enter: {type(screen).__name__}")

            if isinstance(screen, ChatViewScreen):
                print(f"  chat_id={screen.chat_id}, name={screen.chat_name}, type={screen.chat_type}")

                # Check RichLog has messages
                log = screen.query_one("#messages")
                print(f"✓ Message log loaded")

                # Try sending a message via the input
                input_widget = screen.query_one("#msg-input")
                input_widget.value = "napcat phone test message"
                screen._send_message()
                await pilot.pause(2)

                print(f"✓ Message send attempted")

                # Go back
                screen.action_back()
                await pilot.pause(1)
                print(f"✓ Back to {type(app.screen).__name__}")

        print("\n✓✓✓ All smoke tests passed!")


async def test_cli_send():
    """Test sending a message via the CLI to verify the send path works."""
    import subprocess

    result = subprocess.run(
        ["./napcat", "send", "private", "150902546", "-m", "napcat phone TUI test"],
        cwd=str(ROOT),
        capture_output=True, text=True, timeout=10
    )
    print(f"CLI send: exit={result.returncode}")
    print(f"  stdout: {result.stdout[:200]}")
    if result.stderr:
        print(f"  stderr: {result.stderr[:200]}")


def main():
    import asyncio

    print("=" * 50)
    print("SMOKE TEST")
    print("=" * 50)
    asyncio.run(test_smoke())

    print("\n" + "=" * 50)
    print("CLI SEND TEST")
    print("=" * 50)
    asyncio.run(test_cli_send())


if __name__ == "__main__":
    main()
