from __future__ import annotations

import inspect
import os
from importlib.metadata import entry_points
from typing import Any, Mapping

from . import QUESTION_SET_ENTRYPOINT_GROUP
from .models import QuizManifest


class QuestionSetLoadError(RuntimeError):
    pass


def parse_enabled_question_sets(raw: str | None) -> frozenset[str] | None:
    if raw is None or not raw.strip():
        return frozenset()
    if raw.strip() == "*":
        return None
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class QuizCatalog:
    def __init__(self, manifests: Mapping[str, QuizManifest], failures: Mapping[str, str] | None = None):
        self._manifests = dict(manifests)
        self.failures = dict(failures or {})

    @classmethod
    def discover(cls, enabled: frozenset[str] | None = None) -> "QuizCatalog":
        if enabled is None:
            enabled = parse_enabled_question_sets(os.getenv("QUIZ_QUESTION_SETS"))

        eps = entry_points()
        if hasattr(eps, "select"):
            discovered = list(eps.select(group=QUESTION_SET_ENTRYPOINT_GROUP))
        else:  # pragma: no cover
            discovered = list(eps.get(QUESTION_SET_ENTRYPOINT_GROUP, ()))
        discovered.sort(key=lambda item: item.name)

        if enabled is not None:
            discovered = [item for item in discovered if item.name in enabled]

        manifests: dict[str, QuizManifest] = {}
        failures: dict[str, str] = {}

        discovered_names = {item.name for item in discovered}
        if enabled is not None:
            for missing in sorted(enabled - discovered_names):
                failures[missing] = "NotInstalled: no matching quiz.question_sets entry point"

        for entry_point in discovered:
            try:
                provider: Any = entry_point.load()
                raw = provider() if callable(provider) else provider
                if inspect.isawaitable(raw):
                    raise TypeError("question-set provider must be synchronous")
                manifest = QuizManifest.model_validate(raw)
                if manifest.id in manifests:
                    raise ValueError(f"duplicate quiz id: {manifest.id}")
                manifests[manifest.id] = manifest
            except Exception as exc:
                failures[entry_point.name] = f"{type(exc).__name__}: {exc}"

        return cls(manifests, failures)

    def get(self, quiz_id: str) -> QuizManifest:
        try:
            return self._manifests[quiz_id]
        except KeyError as exc:
            raise KeyError(f"unknown quiz: {quiz_id}") from exc

    def all(self) -> tuple[QuizManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))
