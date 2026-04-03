import os
import asyncio
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv
import events

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("Discord token is not set")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_opus() -> None:
    """
    Explicitly attempt to load opus from known locations.
    discord.py does this automatically but fails silently, so the
    result needs to be logged.
    """
    if discord.opus.is_loaded():
        print("[opus] Already loaded.")

        return
    
    candidates = [
        "libopus.so.0",
        "libopus.so",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
    ]

    for path in candidates:
        try:
            discord.opus.load_opus(path)
            print(f"[opus] Loaded from: {path}")

            return
        except Exception as e:
            print(f"[opus] Failed to load {path}: {e}")
    
    print("[opus] WARNING: Could not load opus. Voice will not work")


@bot.event
async def on_ready():
    print(f"[bot] Python {sys.version}")
    print(f"[bot] discord.py {discord.__version__}")
    print(f"[bot] Online as {bot.user} (id={bot.user.id})")
    print(f"[bot] Connected to {len(bot.guilds)} guild(s)")
    print(f"[bot] Opus loaded: {discord.opus.is_loaded()}")
    print(f"[bot] Commands: {[c.name for c in bot.commands]}")


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    # errors handled by cogs can be suppressed
    if isinstance(error, commands.CommandNotFound):
        return
    if hasattr(ctx.command, "on_error"):
        return
    
    print(f"[bot] Unhandled error in '{ctx.command}': {type(error).__name__}: {error}")


async def main():
    load_opus()

    async with bot:
        await bot.load_extension("cogs.music")
        print("[bot] Loaded cog: music")

        try:
            await bot.start(DISCORD_TOKEN)
        finally:
            await events.close()

    
asyncio.run(main())
