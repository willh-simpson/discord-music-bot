import os
import asyncio

import discord
import requests
from dotenv import load_dotenv

load_dotenv()

ELIXIR_URL = os.getenv("ELIXIR_URL", "http://elixir:4000")
DJANGO_URL = os.getenv("DJANGO_URL", "http://django:8000")

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


def check_service(url: str) -> str:
    try:
        r = requests.get(url, timeout=5)
        data = r.json()

        return data.get("status", "unknown")
    except requests.exceptions.ConnectionError:
        return "unreachable"
    except Exception as e:
        return f"error: {type(e).__name__}"
    

@client.event
async def on_ready():
    print(f"[bot] Online as {client.user} (id={client.user.id})")
    print(f"[bot] Elixir URL: {ELIXIR_URL}")
    print(f"[bot] Django URL: {DJANGO_URL}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return
    
    if message.content.strip() == "!ping":
        loop = asyncio.get_event_loop()

        elixir_status = await loop.run_in_executor(
            None, check_service, f"{ELIXIR_URL}/api/health"
        )

        django_status = await loop.run_in_executor(
            None, check_service, f"{DJANGO_URL}/api/health"
        )

        lines = [
            "**System health check**",
            f"Elixir realtime -> `{elixir_status}`",
            f"Django ML -> `{django_status}`",
        ]

        await message.channel.send("\n".join(lines))


token = os.getenv("DISCORD_TOKEN")
if not token:
    raise RuntimeError("DISCORD_TOKEN is not set in environment")

client.run(token)