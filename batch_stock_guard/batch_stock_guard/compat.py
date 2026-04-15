from __future__ import annotations

from functools import lru_cache
import inspect


@lru_cache(maxsize=None)
def get_parameter_names(fn):
    """Return the accepted keyword parameters for a callable."""
    return set(inspect.signature(fn).parameters)


def filter_kwargs(fn, kwargs):
    """Keep only kwargs supported by the callable's current signature."""
    accepted = get_parameter_names(fn)
    return {key: value for key, value in kwargs.items() if key in accepted}


def call_with_supported_kwargs(fn, /, **kwargs):
    """Call a function with only the kwargs it supports in this environment."""
    return fn(**filter_kwargs(fn, kwargs))
