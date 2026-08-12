__all__ = ("RotSession", "run_interactive")


def __getattr__(name):
    if name in __all__:
        from rotbot.session import interactive

        return getattr(interactive, name)
    raise AttributeError(name)
