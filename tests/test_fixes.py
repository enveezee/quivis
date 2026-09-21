import sys
from pathlib import Path
import unittest

# Ensure parent directory is in python path so quivis can be imported
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from quivis.app import QivisController, create_app
from quivis.irc.parser import IrcMessage
from quivis.irc.state import CaseInsensitiveDict, IrcState
from quivis.ui.builder import UIContext, UIEvent
from quivis.ui.textual_backend import TextualBackend


class TestCaseInsensitiveDict(unittest.TestCase):
    def test_case_insensitive_access(self):
        d = CaseInsensitiveDict()
        d["##Llamas"] = "channel_llamas"
        self.assertEqual(d["##llamas"], "channel_llamas")
        self.assertEqual(d["##LLAMAS"], "channel_llamas")
        self.assertIn("##llamas", d)
        self.assertIn("##Llamas", d)
        self.assertEqual(d.get("##llamas"), "channel_llamas")

    def test_setdefault_and_pop(self):
        d = CaseInsensitiveDict()
        val = d.setdefault("##Llamas", "first")
        self.assertEqual(val, "first")
        val2 = d.setdefault("##llamas", "second")
        self.assertEqual(val2, "first")
        popped = d.pop("##LLAMAS")
        self.assertEqual(popped, "first")
        self.assertNotIn("##llamas", d)
        self.assertIsNone(d.get("##llamas"))


class TestIrcStateNickList(unittest.TestCase):
    def test_353_and_case_insensitive_channel_lookup(self):
        state = IrcState()
        # 353 names reply with uppercase channel name ##Llamas
        msg_353 = IrcMessage(
            "353",
            ("my_nick", "=", "##Llamas", "@JoePinball +Alice Bob"),
            prefix="irc.example.net",
        )
        state.apply(msg_353)

        # Lookup with lowercase autojoin name ##llamas
        self.assertIn("##llamas", state.channels)
        channel = state.channels.get("##llamas")
        self.assertIsNotNone(channel)
        self.assertIn("JoePinball", channel.users)
        self.assertIn("Alice", channel.users)
        self.assertIn("Bob", channel.users)

        # PART
        part_msg = IrcMessage("PART", ("##llamas",), prefix="Bob!bob@host")
        state.apply(part_msg)
        self.assertNotIn("Bob", channel.users)

        # KICK
        kick_msg = IrcMessage("KICK", ("##Llamas", "Alice", "kicked"), prefix="JoePinball!joe@host")
        state.apply(kick_msg)
        self.assertNotIn("Alice", channel.users)

        # NICK
        nick_msg = IrcMessage("NICK", ("JoePinball2",), prefix="JoePinball!joe@host")
        state.apply(nick_msg)
        self.assertNotIn("JoePinball", channel.users)
        self.assertIn("JoePinball2", channel.users)


class TestTagmsgHandler(unittest.TestCase):
    def test_tagmsg_is_not_spewed_into_chat(self):
        controller = QivisController()
        # Mock UIContext
        appended = []

        class MockUI(UIContext):
            def append_buffer(self, target: str, text: str) -> None:
                appended.append((target, text))

            def set(self, widget_id: str, field: str, value: object) -> None:
                pass

            def get(self, widget_id: str, field: str = "value", default: object = None) -> object:
                return default

            def active_buffer(self) -> str:
                return "##Llamas"

        ui = MockUI()

        # When a TAGMSG arrives
        tagmsg = IrcMessage(
            "TAGMSG",
            ("##Llamas",),
            prefix="JoePinball!joe_pinbal@user/JoePinball",
            tags={"+typing": "active"},
        )
        controller.render_message(ui, tagmsg)

        # TAGMSG should NOT be appended to buffer or transcript
        self.assertEqual(len(appended), 0)
        self.assertEqual(len(controller.transcript), 0)

        # Normal message should be appended
        privmsg = IrcMessage(
            "PRIVMSG",
            ("##Llamas", "hello world"),
            prefix="JoePinball!joe_pinbal@user/JoePinball",
        )
        controller.render_message(ui, privmsg)
        self.assertEqual(len(appended), 1)
        self.assertEqual(appended[0][0], "##Llamas")
        self.assertIn("hello world", appended[0][1])


class TestTextualBackendAndScrolling(unittest.IsolatedAsyncioTestCase):
    async def test_auto_scroll_and_scrollback(self):
        backend = TextualBackend()
        app = create_app(backend)

        async with app.run_test() as pilot:
            # Transition to workspace screen
            app.context.open_feature("workspace")
            await pilot.pause()

            # Add lines to server buffer
            for i in range(80):
                app.context.append_buffer("server", f"server message {i}")
            await pilot.pause()

            scroll = app.query_one("#server-scroll")
            # Should auto scroll to end
            self.assertTrue(scroll.is_vertical_scroll_end)

            # User scrolls up manually
            scroll.scroll_to(y=10, animate=False)
            await pilot.pause()
            self.assertEqual(scroll.scroll_offset.y, 10)
            self.assertFalse(scroll.is_vertical_scroll_end)

            # New message arrives while user is scrolled up
            app.context.append_buffer("server", "new server message while reading history")
            await pilot.pause()

            # Manual scrollback should be preserved at y=10
            self.assertEqual(scroll.scroll_offset.y, 10)

            # User scrolls back to bottom
            scroll.scroll_end(animate=False)
            await pilot.pause()
            self.assertTrue(scroll.is_vertical_scroll_end)

            # New message arrives, auto-scrolling resumes
            app.context.append_buffer("server", "resumed auto scroll message")
            await pilot.pause()
            self.assertTrue(scroll.is_vertical_scroll_end)


class TestNickListPopulationOnTabSwitch(unittest.IsolatedAsyncioTestCase):
    async def test_nick_list_updates_on_tab_activation(self):
        backend = TextualBackend()
        app = create_app(backend)
        controller = app.controller

        async with app.run_test() as pilot:
            app.context.open_feature("workspace")
            await pilot.pause()

            # Simulate state with channel users
            from quivis.irc.connection import ConnectionConfig, IrcConnection
            controller.connection = IrcConnection(
                ConnectionConfig(host="localhost", port=6667, nickname="my_nick"),
                state=IrcState(),
            )
            # Apply 353 for ##Llamas
            controller.connection.state.apply(
                IrcMessage("353", ("my_nick", "=", "##Llamas", "@JoePinball +Alice"), prefix="srv")
            )

            # Open buffer ##llamas (different case!)
            app.context.open_buffer("##llamas", activate=True)
            await pilot.pause()
            await pilot.pause()

            # Verify user-list widget content contains nicks
            user_list = app.query_one("#user-list")
            self.assertIn("JoePinball", str(user_list.renderable))
            self.assertIn("Alice", str(user_list.renderable))

            # Switch back to server buffer
            tabbed = app.query_one("#buffer-tabs")
            tabbed.active = "buffer-server"
            await pilot.pause()
            await pilot.pause()

            # Server buffer has no channel users, so content should be "Users"
            self.assertEqual(str(user_list.renderable).strip(), "Users")

            # Switch back to ##llamas
            tabbed.active = app._buffer_id("##llamas")
            await pilot.pause()
            await pilot.pause()
            self.assertIn("JoePinball", str(user_list.renderable))

            # Simulate someone joining via render_message
            join_msg = IrcMessage("JOIN", ("##Llamas",), prefix="Charlie!charlie@host")
            controller.connection.state.apply(join_msg)
            controller.render_message(app.context, join_msg)
            await pilot.pause()
            self.assertIn("Charlie", str(user_list.renderable))

            # Simulate someone parting via render_message
            part_msg = IrcMessage("PART", ("##Llamas",), prefix="Charlie!charlie@host")
            controller.connection.state.apply(part_msg)
            controller.render_message(app.context, part_msg)
            await pilot.pause()
            self.assertNotIn("Charlie", str(user_list.renderable))

            # Simulate someone kicked via render_message
            kick_msg = IrcMessage("KICK", ("##Llamas", "Alice", "bye"), prefix="JoePinball!joe@host")
            controller.connection.state.apply(kick_msg)
            controller.render_message(app.context, kick_msg)
            await pilot.pause()
            self.assertNotIn("Alice", str(user_list.renderable))

            # Simulate nick change via render_message
            nick_msg = IrcMessage("NICK", ("JoePinballSuper",), prefix="JoePinball!joe@host")
            controller.connection.state.apply(nick_msg)
            controller.render_message(app.context, nick_msg)
            await pilot.pause()
            self.assertNotIn("JoePinball\n", str(user_list.renderable) + "\n")
            self.assertIn("JoePinballSuper", str(user_list.renderable))


if __name__ == "__main__":
    unittest.main()

