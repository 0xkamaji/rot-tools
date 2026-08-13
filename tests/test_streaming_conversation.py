from datetime import datetime
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import tempfile

from rotbot.agents.conversation import (
    BackendResult, BackendStateReference, TextDelta
)
from rotbot.agents import invocation
from rotbot.contexts.inspection import IdentificationSources, InspectedContext
from rotbot.session import ai
from rotbot.session.conversations import ConversationStore
from rotbot.ui import interactive as ui


def inspected(cwd):
    return InspectedContext(
        "Rot", "assistant-id", "Kamaji", "user-id", "laptop", "machine-id",
        "rotbot", "project-id", Path(cwd),
        IdentificationSources("local", "local", "local", "source"), ()
    )


class NativeStreamingBackend:
    name = "TestBackend"
    agent_name = "opencode"

    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.aborted = False

    def prepare(self, authority, cwd):
        return False

    def stream_generate(self, message, cwd, authority="TALK"):
        text = ""
        for event in self.events:
            text += event.text
            yield event
        if self.error is not None:
            raise self.error
        return BackendResult(
            text,
            (BackendStateReference(
                "backend", "test", "session", "ses_stream", "ephemeral"
            ),),
            "test/model"
        )

    def known_remote_state(self):
        return ()

    def abort_current(self):
        self.aborted = True

    def close(self):
        pass


class StreamingConversationTests(unittest.TestCase):
    def send(self, conversation, on_text=None):
        with patch.object(invocation, "resolve_egress_context", return_value=Mock()), patch.object(
            invocation, "build_ask_prompt", return_value="PROMPT"
        ):
            return conversation.send(
                "Question", inspected("/work"), Path("/work"), on_text=on_text
            )

    def test_native_deltas_render_before_generator_completes_and_preserve_format(self):
        backend = NativeStreamingBackend([
            TextDelta("Hello, "),
            TextDelta("世界\n\n```py\nprint('x')\n```")
        ])
        conversation = ai.AIConversation.create(backend)
        visible = []
        completion_observed = []

        def on_text(text):
            visible.append(text)
            completion_observed.append(conversation.messages[-1].role)

        result = self.send(conversation, on_text)

        self.assertEqual(visible, ["Hello, ", "世界\n\n```py\nprint('x')\n```"])
        self.assertEqual(completion_observed, ["user", "user"])
        self.assertEqual(result.response, "".join(visible))
        self.assertEqual(conversation.messages[-1].content, result.response)
        self.assertEqual(conversation.messages[-1].status, "complete")

    def test_non_streaming_backend_emits_one_complete_chunk_without_fake_replay(self):
        backend = Mock()
        backend.name = "Batch"
        backend.agent_name = "opencode"
        backend.prepare.return_value = False
        backend.generate.return_value = BackendResult("whole response", (), None)
        conversation = ai.AIConversation.create(backend)
        visible = []

        self.send(conversation, visible.append)

        self.assertEqual(visible, ["whole response"])
        backend.generate.assert_called_once()

    def test_native_backend_batch_result_is_used_when_no_deltas_are_yielded(self):
        class Backend(NativeStreamingBackend):
            def stream_generate(self, message, cwd, authority="TALK"):
                if False:
                    yield
                return BackendResult("whole response", (), None)

        conversation = ai.AIConversation.create(Backend([]))
        visible = []

        result = self.send(conversation, visible.append)

        self.assertEqual(visible, ["whole response"])
        self.assertEqual(result.response, "whole response")
        self.assertEqual(conversation.messages[-1].content, "whole response")

    def test_callback_observes_active_status_on_first_visible_text(self):
        conversation = ai.AIConversation.create(
            NativeStreamingBackend([TextDelta("visible")])
        )
        observed = []

        self.send(conversation, lambda _text: observed.append(conversation.status))

        self.assertEqual(observed, ["active"])

    def test_interrupt_after_partial_text_persists_one_aborted_assistant(self):
        backend = NativeStreamingBackend(
            [TextDelta("partial answer")], error=KeyboardInterrupt()
        )
        conversation = ai.AIConversation.create(backend)
        visible = []

        with self.assertRaises(KeyboardInterrupt):
            self.send(conversation, visible.append)

        self.assertEqual(visible, ["partial answer"])
        self.assertEqual(
            [(message.role, message.status, message.content) for message in conversation.messages],
            [
                ("user", "aborted", "Question"),
                ("assistant", "aborted", "partial answer")
            ]
        )

    def test_interrupted_partial_response_survives_store_reload_without_spinner(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ConversationStore(Path(temporary) / "conversations")
            backend = NativeStreamingBackend(
                [TextDelta("saved partial")], error=KeyboardInterrupt()
            )
            conversation = ai.AIConversation.create(backend, store)
            with self.assertRaises(KeyboardInterrupt):
                self.send(conversation, lambda _text: None)

            loaded = store.load(conversation.id)

        self.assertEqual(
            [(message.role, message.status, message.content) for message in loaded.messages],
            [
                ("user", "aborted", "Question"),
                ("assistant", "aborted", "saved partial")
            ]
        )
        self.assertTrue(all("thinking" not in message.content for message in loaded.messages))

    def test_internal_non_text_events_are_never_rendered(self):
        class Backend(NativeStreamingBackend):
            def stream_generate(self, message, cwd, authority="TALK"):
                yield {"type": "reasoning", "text": "hidden"}
                yield TextDelta("visible")
                return BackendResult("visible", (), None)

        visible = []
        self.send(ai.AIConversation.create(Backend([])), visible.append)

        self.assertEqual(visible, ["visible"])


class SpinnerTests(unittest.TestCase):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    def test_spinner_cycles_in_place_stops_and_is_idempotent(self):
        stream = self.Tty()
        spinner = ui.ThinkingSpinner("rot", stream, interval=0.001)
        with patch.dict("os.environ", {"TERM": "xterm"}):
            spinner.start()
            self.assertTrue(spinner.thread.is_alive())
            spinner.stop()
            spinner.stop()

        rendered = stream.getvalue()
        self.assertIn("rot · thinking", rendered)
        self.assertIn("\r\033[2K", rendered)
        self.assertNotIn("thinking\nrot · thinking", rendered)
        self.assertIsNone(spinner.thread)

    def test_stream_renderer_stops_spinner_on_first_text(self):
        session = SimpleNamespace(context=SimpleNamespace(assistant="Rot"))
        stream = io.StringIO()
        spinner = Mock()
        renderer = ui.StreamingRotResponse(session, stream, spinner)
        renderer.start()
        renderer.write("first")
        renderer.write(" second")
        renderer.finish()

        spinner.start.assert_called_once_with()
        self.assertTrue(spinner.stop.call_count >= 2)
        self.assertEqual(stream.getvalue(), "\nrot [x_o]\nfirst second\n\n")

    def test_finishing_response_does_not_clear_streamed_text(self):
        session = SimpleNamespace(context=SimpleNamespace(assistant="Rot"))
        stream = self.Tty()
        spinner = ui.ThinkingSpinner("rot", stream, interval=0.001)
        renderer = ui.StreamingRotResponse(session, stream, spinner)

        with patch.dict("os.environ", {"TERM": "xterm"}):
            renderer.start()
            renderer.write("visible answer")
            before_finish = stream.getvalue()
            renderer.finish()

        finish_output = stream.getvalue()[len(before_finish):]
        self.assertEqual(finish_output, "\n\n")
        self.assertIn("\nrot [x_o]\nvisible answer\n\n", stream.getvalue())

    def test_plain_stream_uses_static_thinking_line(self):
        stream = io.StringIO()
        spinner = ui.ThinkingSpinner("rot", stream)
        spinner.start()
        spinner.stop()

        self.assertEqual(stream.getvalue(), "\nrot · thinking\n")


if __name__ == "__main__":
    unittest.main()
