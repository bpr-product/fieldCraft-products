"""
shared/config_utils.py — Shared YAML + environment-variable resolver.

Extracted from external/config/schema.py so both the external connector
config and the new environments config reuse the same resolver without
duplication.
"""
import os
import re

_ENV_RE = re.compile(r"\$\{([^}]+)\}")


def resolve_env(value: str, strict: bool = False) -> str:
    """Replace ${VAR} placeholders with environment variable values.

    strict=False (default): an unset variable warns and resolves to "".
        Used by the external-connector config loader, where some optional
        ${VAR} fields are legitimately absent.
    strict=True: an unset variable raises ValueError. Used for source/
        credential blocks (environments.yaml) so a missing DB password fails
        fast instead of silently deploying a connector with blank credentials.
    """
    def _sub(m: re.Match) -> str:
        var = m.group(1)
        val = os.environ.get(var)
        if val is None:
            if strict:
                raise ValueError(
                    f"Environment variable {var!r} is not set "
                    f"(required by a strict ${{VAR}} placeholder)"
                )
            import warnings
            warnings.warn(
                f"Environment variable {var!r} is not set — using empty string",
                stacklevel=3,
            )
            return ""
        return val
    return _ENV_RE.sub(_sub, value)


def resolve_dict(obj: object, strict: bool = False) -> object:
    """Recursively resolve ${VAR} placeholders in a parsed YAML structure.

    `strict` is propagated to resolve_env (see its docstring): strict=True
    raises ValueError on any unset ${VAR}, strict=False warns and uses "".
    """
    if isinstance(obj, dict):
        return {k: resolve_dict(v, strict=strict) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_dict(i, strict=strict) for i in obj]
    if isinstance(obj, str):
        return resolve_env(obj, strict=strict)
    return obj
