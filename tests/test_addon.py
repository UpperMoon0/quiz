import sys
import types
from types import SimpleNamespace

import pytest


if "discord" not in sys.modules:
    discord = types.ModuleType("discord")
    app_commands = types.ModuleType("discord.app_commands")

    class _View:
        def __init__(self, *, timeout=None):
            self.timeout = timeout
            self.children = []

        def add_item(self, item):
            self.children.append(item)

        def stop(self):
            pass

    class _Button:
        def __init__(self, **kwargs):
            self.callback = None
            self.disabled = False
            self.label = kwargs.get("label")
            self.custom_id = kwargs.get("custom_id")
            self.style = kwargs.get("style")

    class _Embed:
        def __init__(self, *, title=None, description=None):
            self.title = title
            self.description = description
            self.fields = []

        def add_field(self, *, name, value, inline):
            self.fields.append(SimpleNamespace(name=name, value=value, inline=inline))

    class _ButtonStyle:
        primary = 1

    class _Group:
        def __init__(self, *, name, description):
            self.name = name
            self.description = description

        def command(self, **_kwargs):
            return lambda fn: fn

    class _Command:
        def __init__(self, *, name, description, callback):
            self.name = name
            self.description = description
            self.callback = callback

    def _describe(**_kwargs):
        return lambda fn: fn

    discord.ui = SimpleNamespace(View=_View, Button=_Button)
    discord.ButtonStyle = _ButtonStyle
    discord.Embed = _Embed
    discord.Interaction = object
    app_commands.Group = _Group
    app_commands.Command = _Command
    app_commands.describe = _describe
    discord.app_commands = app_commands
    sys.modules["discord"] = discord
    sys.modules["discord.app_commands"] = app_commands

from quiz_discord_addon.addon import QuizAddon, QuizView, _question_embed
from quiz_discord_addon.catalog import QuizCatalog
from quiz_discord_addon.models import QuizManifest


def manifest(quiz_id: str, alias: str | None = None) -> QuizManifest:
    raw = {
        "schema_version": 1,
        "id": quiz_id,
        "title": quiz_id,
        "description": f"{quiz_id} quiz",
        "sample_size": 1,
        "questions": [
            {
                "id": f"{quiz_id}-q1",
                "prompt": "Question?",
                "choices": [
                    {"id": "a", "text": "A", "correct": True},
                    {"id": "b", "text": "B", "correct": False},
                ],
            }
        ],
    }
    if alias:
        raw["discord_aliases"] = [{"name": alias, "description": "Legacy alias"}]
    return QuizManifest.model_validate(raw)


class FakeTree:
    def __init__(self):
        self.added = []
        self.removed = []

    def add_command(self, command):
        self.added.append(command.name)

    def remove_command(self, name):
        self.removed.append(name)


class FakeBot:
    def __init__(self):
        self.tree = FakeTree()


def test_descriptive_choices_render_in_embed_with_short_selector_buttons():
    raw = {
        "schema_version": 1,
        "id": "long-choice",
        "title": "Long choice",
        "description": "Long choice quiz",
        "sample_size": 1,
        "questions": [
            {
                "id": "long-choice-q1",
                "prompt": "Choose the best progression decision.",
                "choices": [
                    {
                        "id": "route-a",
                        "text": "Prioritize the Moon trip because Titanium is needed for EV and AE2 progression.",
                        "correct": True,
                    },
                    {
                        "id": "route-b",
                        "text": "Stay in HV and assume additional power can substitute for missing Titanium.",
                        "correct": False,
                    },
                ],
            }
        ],
    }
    question = QuizManifest.model_validate(raw).questions[0]
    assert len(question.choices[0].text) > 80

    view = QuizView(SimpleNamespace(service=None), "session", 123, 0, question)
    assert [button.label for button in view.children] == ["A", "B"]
    assert view.children[0].custom_id.endswith(":route-a")

    embed = _question_embed(0, question)
    assert embed.title == "Question 1"
    assert embed.description == question.prompt
    assert [field.name for field in embed.fields] == ["A", "B"]
    assert embed.fields[0].value == question.choices[0].text
    assert embed.fields[1].value == question.choices[1].text


@pytest.mark.asyncio
async def test_strict_question_set_failure_aborts_before_command_registration(monkeypatch):
    catalog = QuizCatalog({}, {"gtnh": "NotInstalled: missing"})
    monkeypatch.setattr(QuizCatalog, "discover", classmethod(lambda cls: catalog))
    monkeypatch.setenv("QUIZ_QUESTION_SET_STRICT", "true")
    bot = FakeBot()

    with pytest.raises(RuntimeError, match="gtnh"):
        await QuizAddon().setup(SimpleNamespace(bot=bot))

    assert bot.tree.added == []


@pytest.mark.asyncio
async def test_duplicate_alias_is_preflighted_before_tree_mutation(monkeypatch):
    catalog = QuizCatalog(
        {
            "one": manifest("one", "same_alias"),
            "two": manifest("two", "same_alias"),
        }
    )
    monkeypatch.setattr(QuizCatalog, "discover", classmethod(lambda cls: catalog))
    bot = FakeBot()

    with pytest.raises(RuntimeError, match="duplicate Discord quiz command alias"):
        await QuizAddon().setup(SimpleNamespace(bot=bot))

    assert bot.tree.added == []


@pytest.mark.asyncio
async def test_shutdown_unregisters_commands(monkeypatch):
    catalog = QuizCatalog({"one": manifest("one", "legacy_one")})
    monkeypatch.setattr(QuizCatalog, "discover", classmethod(lambda cls: catalog))
    bot = FakeBot()
    addon = QuizAddon()

    await addon.setup(SimpleNamespace(bot=bot))
    assert bot.tree.added == ["quiz", "legacy_one"]

    await addon.shutdown()
    assert bot.tree.removed == ["legacy_one", "quiz"]
