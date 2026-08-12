import builtins


class BasicInput:
    def prepare(self, entries):
        pass

    def read(self, prompt):
        return builtins.input(prompt)

    def record(self, command):
        pass


class ReadlineInput(BasicInput):
    def __init__(self, readline_module):
        self.readline = readline_module

    def prepare(self, entries):
        self.readline.clear_history()
        for entry in entries:
            self.readline.add_history(entry)
        if hasattr(self.readline, "set_auto_history"):
            self.readline.set_auto_history(False)

    def record(self, command):
        self.readline.add_history(command)


def interactive_input():
    try:
        import readline
    except ImportError:
        return BasicInput()
    return ReadlineInput(readline)
