"""AI agent selection and execution."""

__all__ = ("ask_agent", "stream_agent")


def __getattr__(name):
    if name in __all__:
        from rotbot.agents import runner

        return getattr(runner, name)
    raise AttributeError(name)
