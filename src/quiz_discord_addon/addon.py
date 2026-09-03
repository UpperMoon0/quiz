from __future__ import annotations

import logging
import os

import discord
from discord import app_commands

from .catalog import QuizCatalog
from .models import Question
from .service import QuizAccessError, QuizService, QuizStateError

logger = logging.getLogger("quiz-discord-addon")

CHOICE_LABELS = "ABCDE"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _choice_label(index: int) -> str:
    return CHOICE_LABELS[index]


def _question_embed(question_index: int, question: Question) -> discord.Embed:
    embed = discord.Embed(
        title=f"Question {question_index + 1}",
        description=question.prompt,
    )
    for index, choice in enumerate(question.choices):
        embed.add_field(
            name=_choice_label(index),
            value=choice.text,
            inline=False,
        )
    return embed


class QuizView(discord.ui.View):
    def __init__(self, addon: "QuizAddon", session_id: str, user_id: int, question_index: int, question: Question):
        super().__init__(timeout=180)
        self.addon = addon
        self.session_id = session_id
        self.user_id = user_id
        self.question_index = question_index

        for index, choice in enumerate(question.choices):
            button = discord.ui.Button(
                label=_choice_label(index),
                style=discord.ButtonStyle.primary,
                custom_id=f"quiz:{session_id}:{question_index}:{choice.id}",
            )
            button.callback = self._callback_for(choice.id)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message("This quiz attempt belongs to someone else.", ephemeral=True)
        return False

    def _callback_for(self, choice_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            try:
                result = await self.addon.service.answer(
                    self.session_id,
                    user_id=interaction.user.id,
                    question_index=self.question_index,
                    choice_id=choice_id,
                )
            except (QuizAccessError, QuizStateError) as exc:
                if interaction.response.is_done():
                    await interaction.followup.send(str(exc), ephemeral=True)
                else:
                    await interaction.response.send_message(str(exc), ephemeral=True)
                return

            for item in self.children:
                item.disabled = True
            self.stop()
            await interaction.response.edit_message(view=self)

            feedback = "Correct." if result.correct else "Incorrect."
            if result.complete:
                await interaction.followup.send(
                    f"{feedback} Quiz complete — final score: {result.score:.2f} / 100."
                )
                return

            await interaction.followup.send(feedback, ephemeral=True)
            await self.addon.send_question(
                interaction,
                self.session_id,
                self.user_id,
                self.question_index + 1,
                result.next_question,
            )

        return callback

    async def on_timeout(self) -> None:
        self.addon.service.cancel(self.session_id)


class QuizAddon:
    name = "quiz"

    def __init__(self) -> None:
        self.catalog = QuizCatalog({})
        self.service = QuizService(self.catalog)
        self._registered_commands: list[str] = []
        self._bot = None

    async def setup(self, context) -> None:
        self.catalog = QuizCatalog.discover()
        if self.catalog.failures and _env_flag("QUIZ_QUESTION_SET_STRICT", True):
            details = "; ".join(
                f"{name}: {reason}" for name, reason in sorted(self.catalog.failures.items())
            )
            raise RuntimeError(f"configured quiz question sets failed to load: {details}")

        self.service = QuizService(self.catalog)
        bot = context.bot

        seen_aliases: set[str] = {"quiz"}
        aliases: list[tuple[str, str, str]] = []
        for manifest in self.catalog.all():
            for alias in manifest.discord_aliases:
                if alias.name in seen_aliases:
                    raise RuntimeError(f"duplicate Discord quiz command alias: {alias.name}")
                seen_aliases.add(alias.name)
                aliases.append((alias.name, alias.description, manifest.id))

        group = app_commands.Group(name="quiz", description="Play installed multiple-choice quizzes")

        @group.command(name="list", description="List available quizzes")
        async def list_quizzes(interaction: discord.Interaction) -> None:
            manifests = self.catalog.all()
            if not manifests:
                detail = ""
                if self.catalog.failures:
                    detail = "\nLoad failures: " + "; ".join(
                        f"{name}: {reason}" for name, reason in sorted(self.catalog.failures.items())
                    )
                await interaction.response.send_message("No quiz question sets are loaded." + detail, ephemeral=True)
                return
            lines = [f"`{manifest.id}` — {manifest.title}" for manifest in manifests]
            await interaction.response.send_message("Available quizzes:\n" + "\n".join(lines), ephemeral=True)

        @group.command(name="start", description="Start a quiz")
        @app_commands.describe(quiz_id="Quiz ID shown by /quiz list")
        async def start_quiz(interaction: discord.Interaction, quiz_id: str) -> None:
            await self.start_interaction(interaction, quiz_id)

        registered: list[str] = []
        try:
            bot.tree.add_command(group)
            registered.append("quiz")
            for name, description, quiz_id in aliases:
                command = app_commands.Command(
                    name=name,
                    description=description,
                    callback=self._legacy_callback(quiz_id),
                )
                bot.tree.add_command(command)
                registered.append(name)
        except Exception:
            for name in reversed(registered):
                bot.tree.remove_command(name)
            raise

        self._bot = bot
        self._registered_commands = registered
        logger.info(
            "Quiz addon loaded %s question set(s); failures=%s",
            len(self.catalog.all()),
            self.catalog.failures,
        )

    def _legacy_callback(self, quiz_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self.start_interaction(interaction, quiz_id)

        return callback

    async def start_interaction(self, interaction: discord.Interaction, quiz_id: str) -> None:
        try:
            manifest = self.catalog.get(quiz_id)
        except KeyError:
            await interaction.response.send_message(f"Unknown quiz `{quiz_id}`.", ephemeral=True)
            return

        if interaction.channel_id is None:
            await interaction.response.send_message("This quiz needs a Discord channel.", ephemeral=True)
            return

        session, first_question = self.service.start(
            quiz_id,
            user_id=interaction.user.id,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
        )
        await interaction.response.send_message(
            f"Starting **{manifest.title}** for {interaction.user.mention} — "
            f"{len(session.question_ids)} questions."
        )
        await self.send_question(interaction, session.id, interaction.user.id, 0, first_question)

    async def send_question(
        self,
        interaction: discord.Interaction,
        session_id: str,
        user_id: int,
        question_index: int,
        question: Question | None,
    ) -> None:
        if question is None:
            return
        view = QuizView(self, session_id, user_id, question_index, question)
        embed = _question_embed(question_index, question)
        await interaction.followup.send(embed=embed, view=view)

    async def shutdown(self) -> None:
        if self._bot is not None:
            for name in reversed(self._registered_commands):
                self._bot.tree.remove_command(name)
        self._registered_commands.clear()
        self._bot = None
        self.service.clear()
