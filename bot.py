import asyncio
import logging
import sys
import time

import discord
from discord import app_commands

import config
import llm
from guides import GuideStore
from retrieval import Retriever, format_context

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
log = logging.getLogger("maplebot")

NO_KEY_MSG = ("🍁 The AI key isn't configured yet, so I can't answer questions right now. "
              "The server admin needs to set `XAI_API_KEY` for me — please check back soon!")
MAX_Q = 800


def split_message(text: str, limit: int = 1900) -> list[str]:
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # hard-split very long lines
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:cut])
            line = line[cut:].lstrip()
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    # keep ``` fences balanced across chunks
    out, open_fence = [], False
    for c in chunks:
        if open_fence:
            c = "```\n" + c
        open_fence = c.count("```") % 2 == 1
        if open_fence:
            c += "\n```"
        out.append(c)
    return out


def sources_block(snippets) -> str:
    seen, lines = set(), []
    for i, s in enumerate(snippets, 1):
        if s.url in seen:
            continue
        seen.add(s.url)
        lines.append(f"[{i}] {s.title} — <{s.url}>")
    return "\n\n**Sources**\n" + "\n".join(lines) if lines else ""


class Bot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()  # no privileged intents needed: Discord sends content for @mentions/DMs
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)
        self.store = GuideStore()
        self.retriever = Retriever(self.store)
        self.last_used: dict[int, float] = {}
        self._tasks: list[asyncio.Task] = []

    async def setup_hook(self):
        self._tasks.append(asyncio.create_task(self.store.refresh_loop()))
        gid = config.dev_guild_id()
        if gid:
            g = discord.Object(id=gid)
            self.tree.copy_global_to(guild=g)
            cmds = await self.tree.sync(guild=g)
            log.info("synced %d commands to guild %s", len(cmds), gid)
        cmds = await self.tree.sync()
        log.info("slash commands synced globally: %s", ", ".join(f"/{c.name}" for c in cmds))

    async def on_ready(self):
        log.info("LOGIN OK: logged in as %s (id=%s), in %d guild(s)", self.user, self.user.id, len(self.guilds))
        log.info("config: model=%s xai_key=%s guides_cached=%d", config.xai_model(),
                 "set" if config.xai_api_key() else "NOT SET", len(self.store.guides))

    async def close(self):
        await self.retriever.aclose()
        await super().close()

    def cooldown_left(self, user_id: int) -> float:
        cd = config.cooldown_seconds()
        return max(0.0, self.last_used.get(user_id, 0) + cd - time.monotonic())

    async def make_answer(self, question: str) -> str:
        snippets = await self.retriever.retrieve(question)
        log.info("retrieved %d snippets: %s", len(snippets), [s.url for s in snippets])
        try:
            text = await llm.answer(question, format_context(snippets))
        except llm.NotConfigured:
            return NO_KEY_MSG
        except Exception as e:
            log.exception("LLM call failed")
            return f"😵 Sorry, something went wrong asking the AI ({type(e).__name__}). Please try again in a bit."
        return text + sources_block(snippets)

    async def on_message(self, message: discord.Message):
        if message.author.bot or self.user is None or self.user not in message.mentions:
            return
        q = message.content
        for m in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            q = q.replace(m, "")
        q = q.strip()
        if not q:
            await message.reply("Hi! Ask me anything about MapleStory Classic, e.g. `@me best assassin skill build?` or use `/ask`.")
            return
        if not config.xai_api_key():
            await message.reply(NO_KEY_MSG)
            return
        left = self.cooldown_left(message.author.id)
        if left > 0:
            await message.reply(f"⏳ Easy there! Try again in {left:.0f}s.")
            return
        self.last_used[message.author.id] = time.monotonic()
        async with message.channel.typing():
            text = await self.make_answer(q[:MAX_Q])
        first = True
        for chunk in split_message(text):
            if first:
                await message.reply(chunk)
                first = False
            else:
                await message.channel.send(chunk)


bot = Bot()


@bot.tree.command(name="ask", description="Ask anything about MapleStory Classic (classes, training, bossing, mechanics, events...)")
@app_commands.describe(question="Your MapleStory Classic question")
async def ask(interaction: discord.Interaction, question: str):
    if not config.xai_api_key():
        await interaction.response.send_message(NO_KEY_MSG, ephemeral=True)
        return
    left = bot.cooldown_left(interaction.user.id)
    if left > 0:
        await interaction.response.send_message(f"⏳ Easy there! Try again in {left:.0f}s.", ephemeral=True)
        return
    bot.last_used[interaction.user.id] = time.monotonic()
    await interaction.response.defer(thinking=True)
    try:
        text = await bot.make_answer(question[:MAX_Q])
        text = f"> **{discord.utils.escape_markdown(question[:200])}**\n\n{text}"
        for chunk in split_message(text):
            await interaction.followup.send(chunk)
    except Exception:
        log.exception("/ask failed")
        await interaction.followup.send("😵 Something went wrong. Please try again.")


@bot.tree.command(name="help", description="How to use the MapleStory Classic assistant")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🍁 **MapleStory Classic Assistant**\n"
        "• `/ask question:<text>` — ask about classes & builds, grind maps, EXP, mechanics, events, patch notes…\n"
        "• Or just @mention me with your question.\n"
        "• I look up the MeowDB MapleStory Classic guides (plus Nexon news and wikis as fallback) and cite sources. "
        "If I'm not sure, I'll say so.\n"
        f"• Cooldown: {config.cooldown_seconds():.0f}s per user.\n"
        "Guides: <https://meowdb.com/msclassic/guides>", ephemeral=True)


def main():
    token = config.discord_token()
    if not token:
        log.error("DISCORD_BOT_TOKEN is not set")
        sys.exit(1)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
