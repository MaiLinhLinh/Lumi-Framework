"""Executor capabilities and concrete Task Manager bindings."""

from .registry import (
    COMPLETE_SURFACE_TASK,
    DRAW_IMAGE_TASK,
    SEARCH_IMAGE_TASK,
    SEARCH_INFORMATION_TASK,
    ExecutorRegistry,
    ExecutorRegistryError,
    TaskExecutor,
    build_default_executor_registry,
)
from .complete_surface import CompleteSurfaceTaskExecutor, bind_complete_surface_task

__all__ = [
    "COMPLETE_SURFACE_TASK",
    "CompleteSurfaceTaskExecutor",
    "DRAW_IMAGE_TASK",
    "SEARCH_IMAGE_TASK",
    "SEARCH_INFORMATION_TASK",
    "ExecutorRegistry",
    "ExecutorRegistryError",
    "TaskExecutor",
    "build_default_executor_registry",
    "bind_complete_surface_task",
]
