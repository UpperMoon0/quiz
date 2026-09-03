from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

COMMAND_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class Choice(BaseModel):
    id: Annotated[str, Field(min_length=1, max_length=16)]
    # Choice text is rendered in an embed field, not on the Discord button.
    # 500 keeps five choices plus the 1,900-character prompt within Discord's
    # 6,000-character aggregate embed limit while leaving room for field names.
    text: Annotated[str, Field(min_length=1, max_length=500)]
    correct: bool = False


class Question(BaseModel):
    id: str
    prompt: Annotated[str, Field(min_length=1, max_length=1900)]
    weight: Annotated[float, Field(gt=0)] = 1.0
    category: Annotated[str | None, Field(max_length=80)] = None
    choices: Annotated[list[Choice], Field(min_length=2, max_length=5)]

    @model_validator(mode="after")
    def validate_question(self) -> "Question":
        if not ID_RE.fullmatch(self.id):
            raise ValueError("question id must be a stable lowercase identifier")
        choice_ids = [choice.id for choice in self.choices]
        if len(choice_ids) != len(set(choice_ids)):
            raise ValueError("choice ids must be unique within a question")
        choice_texts = [choice.text for choice in self.choices]
        if len(choice_texts) != len(set(choice_texts)):
            raise ValueError("choice text must be unique within a question")
        if sum(1 for choice in self.choices if choice.correct) != 1:
            raise ValueError("question must contain exactly one correct choice")
        return self

    @property
    def correct_choice(self) -> Choice:
        return next(choice for choice in self.choices if choice.correct)


class DiscordAlias(BaseModel):
    name: str
    description: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def validate_name(self) -> "DiscordAlias":
        if not COMMAND_RE.fullmatch(self.name):
            raise ValueError("Discord command alias must be lowercase and <= 32 characters")
        return self


class QuizManifest(BaseModel):
    schema_version: Literal[1]
    id: str
    title: Annotated[str, Field(min_length=1, max_length=100)]
    description: Annotated[str, Field(min_length=1, max_length=300)]
    sample_size: Annotated[int, Field(gt=0)]
    discord_aliases: list[DiscordAlias] = Field(default_factory=list)
    questions: Annotated[list[Question], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_manifest(self) -> "QuizManifest":
        if not ID_RE.fullmatch(self.id):
            raise ValueError("quiz id must be a stable lowercase identifier")
        question_ids = [question.id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question ids must be unique within a quiz")
        alias_names = [alias.name for alias in self.discord_aliases]
        if len(alias_names) != len(set(alias_names)):
            raise ValueError("Discord alias names must be unique within a quiz")
        if self.sample_size > len(self.questions):
            raise ValueError("sample_size cannot exceed question count")
        return self

    def question_by_id(self, question_id: str) -> Question:
        for question in self.questions:
            if question.id == question_id:
                return question
        raise KeyError(question_id)
