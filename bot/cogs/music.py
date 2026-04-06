import asyncio
from datetime import datetime, timezone
import os
import traceback
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

import events
from llm.intent import extract_intent
from llm.explainer import explain_recommendation
from music.player import Song, YTDLSource
from music.queue import MusicPlayer

DJANGO_URL = os.getenv("DJANGO_URL", "http://localhost:8000")


class Music(commands.Cog):
    """
    All music playback commands.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._players: dict[int, MusicPlayer] = {} # one MusicPlayer per guild; created on demand

    def _get_player(self, ctx: commands.Context) -> Optional[MusicPlayer]:
        return self._players.get(ctx.guild.id)
    
    def _create_player(self, ctx: commands.Context) -> MusicPlayer:
        player = MusicPlayer(ctx.voice_client, ctx.channel)
        self._players[ctx.guild.id] = player

        return player
    
    def _get_time_label(self) -> str:
        hour = datetime.now(timezone.utc).hour

        if 5 <= hour < 12:
            return "morning"
        elif 12 <= hour < 17:
            return "afternoon"
        elif 17 <= hour < 21:
            return "evening"
        else:
            return "late_night"
    
    async def _connect_voice(self, ctx: commands.Context) -> bool:
        """
        Connect to voice just before playing, after yt-dlp fetch.
        """
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel first.")

            return False
        
        voice_channel = ctx.author.voice.channel

        try:
            if ctx.voice_client is None:
                await voice_channel.connect(timeout=15.0, reconnect=True)
            elif ctx.voice_client.channel != voice_channel:
                await ctx.voice_client.move_to(voice_channel)
        except asyncio.TimeoutError:
            await ctx.send("Timed out connecting to voice. Try again.")

            return False
        except Exception as e:
            await ctx.send(f"Could not connect to voice: `{e}`")

            return False
        
        if ctx.voice_client is None:
            await ctx.send("Voice client not available. Try again.")

            return False
        
        return True
    
    @commands.command(name="play", aliases=["p"])
    async def play(self, ctx: commands.Context, *, query: str) -> None:
        """
        Play a song or add to queue. Accepts URLs or search terms.
        """
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel first.")

            return
        
        async with ctx.typing():
            try:
                loop = asyncio.get_running_loop()
                source = await YTDLSource.from_query(query, loop=loop)
            except Exception as e:
                await ctx.send(f"Could not find that song: `{e}`")

                return
        
        if not await self._connect_voice(ctx):
            return
        
        song = Song(
            title=source.title,
            url=source.url,
            webpage_url=source.webpage_url,
            duration=source.duration,
            requester_id=ctx.author.id,
            requester_name=ctx.author.display_name,
            guild_id=ctx.guild.id,
            thumbnail=source.thumbnail,
        )

        player = self._get_player(ctx) or self._create_player(ctx)
        player.refresh_voice_client(ctx.voice_client)

        try:
            if player.is_playing:
                position = player.enqueue(song)

                await ctx.send(
                    f"Added to queue at position **{position}**: "
                    f"[{song.title}]({song.webpage_url}) — {song.duration_fmt}"
                )
                await events.emit("song_queued", {
                    **song.to_event_data(),
                    "queue_position": position,
                    "channel_id": str(ctx.channel.id),
                })
            else:
                await player.play_song(song, source)
                await events.emit("song_started", {
                    **song.to_event_data(),
                    "channel_id": str(ctx.channel.id),
                    "voice_channel_id": str(ctx.author.voice.channel.id),
                })
        except Exception as e:
            traceback.print_exc()
            await ctx.send(f"Failed to start playback: `{e}`")

    @commands.command(name="skip", aliases=["s"])
    async def skip(self, ctx: commands.Context) -> None:
        """
        Skip current song.
        """
        player = self._get_player(ctx)

        if not player or not player.is_playing:
            await ctx.send("Nothing is playing right now.")

            return
        
        skipped = player.skip()
        if skipped:
            await ctx.send(f"Skipped **{skipped.title}**")
            await events.emit("song_skipped", {
                **skipped.to_event_data(),
                "channel_id": str(ctx.channel.id),
                "queue_remaining": len(player.queue),
            })

    @commands.command(name="queue", aliases=["q"])
    async def queue(self, ctx: commands.Context) -> None:
        """
        Show current queue.
        """
        player = self._get_player(ctx)
        if not player or player.is_idle:
            await ctx.send("Queue is empty and nothing is playing.")

            return
        
        await ctx.send(embed=player.queue_embed())

    @commands.command(name="nowplaying", aliases=["np"])
    async def nowplaying(self, ctx: commands.Context) -> None:
        """
        Show what's currently playing.
        """
        player = self._get_player(ctx)
        if not player or not player.current:
            await ctx.send("Nothing is playing right now.")

            return
        
        song = player.current
        embed = discord.Embed(
            title="Now playing",
            description=f"[{song.title}]({song.webpage_url})",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="Duration", value=song.duration_fmt, inline=True)
        embed.add_field(name="Requested by", value=song.requester_name, inline=True)
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        
        await ctx.send(embed=embed)

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context) -> None:
        """
        Stop playback, clear queue, and make bot leave voice channel.
        """
        player = self._get_player(ctx)
        if not player and not ctx.voice_client:
            await ctx.send("I'm not in a voice channel.")

            return
        
        if player:
            await events.emit("playback_stopped", {
                "guild_id": str(ctx.guild.id),
                "stopped_by_id": str(ctx.author.id),
                "stopped_by_name": ctx.author.display_name,
                "channel_id": str(ctx.channel.id),
                "songs_cleared": len(player.queue),
            })

            player.stop()
            del self._players[ctx.guild.id]
        
        if ctx.voice_client:
            await ctx.voice_client.disconnect()

        await ctx.send("Stopped and disconnected.")

    @commands.command(name="join")
    async def join(self, ctx: commands.Context) -> None:
        """
        Join a given voice channel.
        """
        if not ctx.author.voice:
            await ctx.send("You're not in a voice channel.")

            return
        
        channel = ctx.author.voice.channel

        if ctx.voice_client:
            await ctx.voice_client.move_to(channel)
        else:
            await channel.connect()
        
        await ctx.send(f"Joined **{channel.name}**")
        await events.emit("bot_joined_voice", {
            "guild_id": str(ctx.guild.id),
            "voice_channel_id": str(channel.id),
            "voice_channel_name": channel.name,
            "requested_by_id": str(ctx.author.id),
            "requested_by_name": ctx.author.display_name,
        })

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context) -> None:
        """
        Leave a given voice channel.
        """
        if not ctx.voice_client:
            await ctx.send("I'm not in a voice channel.")

            return
        
        if ctx.guild.id in self._players:
            self._players[ctx.guild.id].stop()
            del self._players[ctx.guild.id]

        channel_name = ctx.voice_client.channel.name
        await ctx.voice_client.disconnect()
        await ctx.send(f"Left **{channel_name}**")

    @commands.command(name="recommend", aliases=["rec"])
    async def recommend(self, ctx: commands.Context, *, query: str = "") -> None:
        """
        Get AI-powered music recommendations.
        Empty query will recommend based on listening history.
        """

        if not ctx.author.voice:
            await ctx.send("Join a voice channel first.")

        async with ctx.typing():
            presence_cog = self.bot.get_cog("PresenceTracker")
            game_context = None
            if presence_cog:
                game_context = presence_cog.get_game(ctx.author.id)

            time_label = self._get_time_label()

            player = self._get_player(ctx)
            recent_titles = []
            if player and player.current:
                recent_titles.append(player.current.title)

            intent = None
            if query:
                intent = await extract_intent(
                    query=query,
                    game_context=game_context,
                    time_label=time_label,
                    recent_songs=recent_titles,
                )

                if intent.is_direct_request and intent.confidence > 0.7:
                    await ctx.send(f"Sounds like a direct request. Queueing `{intent.raw_query}`")
                    await ctx.invoke(self.play, query=intent.raw_query)

                    return
            
            context = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if game_context:
                context["game"] = game_context
            if intent:
                context.update(intent.to_context_dict())
            if time_label:
                context["time_context"] = time_label

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{DJANGO_URL}/api/recommend/",
                        json={
                            "guild_id": str(ctx.guild.id),
                            "user_id": str(ctx.author.id),
                            "limit": 5,
                            "context": context,
                        },
                    ) as resp:
                        data = await resp.json()
            except Exception as e:
                await ctx.send(f"Could not get recommendations: `{e}`")

                return
            
            recommendations = data.get("recommendations", [])
            if not recommendations:
                await ctx.send(
                    "I don't have enough listening history to recommend anything yet. "
                    "Play some songs first and try again."
                )

                return
            
            top = recommendations[0]
            phase = data.get("phase", "unknown")

            explanation = await explain_recommendation(
                song_title=top["title"],
                song_reason=top.get("reason", ""),
                phase=phase,
                user_mood=intent.mood if intent else [],
                game_context=game_context,
                time_label=time_label,
            )

            embed = discord.Embed(title="Recommended for you", color=discord.Color.blurple())
            if game_context:
                embed.set_footer(text=f"Context: playing {game_context} · {time_label}")
            else:
                embed.set_footer(text=f"Context: {time_label}")

            for i, rec in enumerate(recommendations[:5], start=1):
                duration_fmt = _fmt_duration(rec.get("duration", 0))
                explanation_text = explanation if i == 1 else rec.get("reason", "")

                embed.add_field(
                    name=f"{i}. {rec['title']} ({duration_fmt})",
                    value=f"{explanation_text}\n[YouTube]({rec['webpage_url']})",
                    inline=False,
                )

            embed.add_field(
                name="How to play",
                value="`!play <song name>` to queue any of these",
                inline=False,
            )

            await ctx.send(embed=embed)

            # elixir also tracks recommendations served
            await events.emit("recommendations_served", {
                "guild_id": str(ctx.guild.id),
                "user_id": str(ctx.author.id),
                "count": len(recommendations),
                "phase": phase,
                "game_context": game_context,
                "intent_mood": intent.mood if intent else [],
                "log_id": data.get("log_id"),
            })


    @play.error
    async def play_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Usage: `!play <song name or URL>`")
        else:
            await ctx.send(f"An error occurred: `{error}`")
            print(f"[player error] {type(error).__name__}: {error}")


def _fmt_duration(seconds: int) -> str:
    mins, secs = divmod(seconds, 60)
    hours, mins = divmod(mins, 60)

    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    
    return f"{mins}:{secs:02d}"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
