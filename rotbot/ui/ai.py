import os
import sys
import threading


SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_output_lock = threading.RLock()


class ThinkingSpinner:
    def __init__(self, assistant="rot", stream=None, interval=0.09, activity="thinking"):
        self.assistant = assistant
        self.stream = sys.stdout if stream is None else stream
        self.interval = interval
        self.activity = activity
        self.stop_event = threading.Event()
        self.thread = None
        self.animated = False

    def _write(self, text):
        with _output_lock:
            self.stream.write(text)
            self.stream.flush()

    def start(self):
        if self.thread is not None:
            return
        self.stop_event.clear()
        self.animated = (
            getattr(self.stream, "isatty", lambda: False)()
            and os.environ.get("TERM", "").lower() not in {"", "dumb"}
        )
        if not self.animated:
            self._write(f"\n{self.assistant} · {self.activity}\n")
            return
        self.thread = threading.Thread(target=self._run, name="rot-thinking-spinner")
        self.thread.start()

    def _run(self):
        index = 0
        while not self.stop_event.is_set():
            self._write(
                f"\r\033[2K{self.assistant} · {self.activity}  "
                f"{SPINNER_FRAMES[index % len(SPINNER_FRAMES)]}"
            )
            index += 1
            self.stop_event.wait(self.interval)

    def stop(self, clear=True):
        self.stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self.thread = None
        was_animated = self.animated
        self.animated = False
        if was_animated and clear:
            self._write("\r\033[2K")


class AIActivityPresenter:
    def __init__(
        self, activity="thinking", assistant="rot", stream=None,
        stop_on_stream=True
    ):
        self.spinner = ThinkingSpinner(assistant, stream, activity=activity)
        self.stop_on_stream = stop_on_stream
        self.events = []
        self.active = False

    def __call__(self, event):
        self.events.append(event)
        if event == "started" and not self.active:
            self.spinner.start()
            self.active = True
        elif self.active and (
            event in {"completed", "failed"}
            or (event == "streaming" and self.stop_on_stream)
        ):
            self.spinner.stop(clear=True)
            self.active = False
