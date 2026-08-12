import builtins


class BasicInput:
    def __init__(self):
        self.completion_provider = None

    def prepare(self, entries):
        pass

    def set_completion_provider(self, provider):
        self.completion_provider = provider

    def read(self, prompt):
        return builtins.input(prompt)

    def record(self, command):
        pass


class ReadlineInput(BasicInput):
    def __init__(self, readline_module):
        super().__init__()
        self.readline = readline_module
        self._matches = []

    def prepare(self, entries):
        self.readline.clear_history()
        for entry in entries:
            self.readline.add_history(entry)
        if hasattr(self.readline, "set_auto_history"):
            self.readline.set_auto_history(False)
        try:
            self.readline.parse_and_bind("tab: complete")
        except (AttributeError, RuntimeError):
            pass

    def _complete(self, _text, state):
        if state == 0:
            try:
                line = self.readline.get_line_buffer()
                cursor = self.readline.get_endidx()
                self._matches = [
                    completion.value
                    for completion in self.completion_provider.complete(line, cursor)
                ]
            except Exception:
                self._matches = []
        return self._matches[state] if state < len(self._matches) else None

    def read(self, prompt):
        if self.completion_provider is None:
            return super().read(prompt)
        previous_completer = None
        previous_delimiters = None
        installed = False
        try:
            previous_completer = self.readline.get_completer()
            previous_delimiters = self.readline.get_completer_delims()
            self.readline.set_completer(self._complete)
            self.readline.set_completer_delims(" \t\n")
            installed = True
        except (AttributeError, RuntimeError):
            return super().read(prompt)
        try:
            return super().read(prompt)
        finally:
            if installed:
                try:
                    self.readline.set_completer(previous_completer)
                    self.readline.set_completer_delims(previous_delimiters)
                except (AttributeError, RuntimeError):
                    pass

    def record(self, command):
        self.readline.add_history(command)


def interactive_input():
    try:
        import readline
    except ImportError:
        return BasicInput()
    return ReadlineInput(readline)
