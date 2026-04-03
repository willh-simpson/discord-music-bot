import asyncio
import traceback
from typing import Optional

import discord
from discord.ext import commands
import events
from music.player import Song, YTDLSource
from music.queue import MusicPlayer


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

    @play.error
    async def play_error(self, ctx: commands.Context, error: Exception) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Usage: `!play <song name or URL>`")
        else:
            await ctx.send(f"An error occurred: `{error}`")
            print(f"[player error] {type(error).__name__}: {error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
