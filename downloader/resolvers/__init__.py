"""Resolvers package."""

from .base import BaseResolver
from .dispatcher import ResolverDispatcher, default_dispatcher

__all__ = ["BaseResolver", "ResolverDispatcher", "default_dispatcher"]
