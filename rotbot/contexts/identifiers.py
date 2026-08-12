import uuid


LEGACY_CONTEXT_NAMESPACE = uuid.UUID("a312f9e4-d09c-4cf7-b88a-753f5873e27f")


class ContextIdentifierError(Exception):
    pass


def new_context_id():
    return str(uuid.uuid4())


def validate_context_id(value):
    if not isinstance(value, str):
        raise ContextIdentifierError("Context ID must be a UUID string.")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise ContextIdentifierError(f"Invalid context ID: {value}") from None
    canonical = str(parsed)
    if value != canonical:
        raise ContextIdentifierError(f"Context ID must use canonical UUID form: {value}")
    return canonical


def legacy_context_id(context_type, name):
    return str(uuid.uuid5(LEGACY_CONTEXT_NAMESPACE, f"{context_type}:{name}"))
