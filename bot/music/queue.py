import asyncio
from typing import Optional

import discord

import events
from music.player import Song, YTDLSource, FFMPEG_OPTIONS


class MusicPlayer:
    """
    Manages audio playback state for a given Discord guild.
    """

    def __init__(self, voice_client: discord.VoiceClient, text_channel: discord.TextChannel):
        self.voice_client = voice_client
        self.text_channel = text_channel
        self.queue: list[Song] = []
        self.current: Optional[Song] = None
        self._loop = asyncio.get_running_loop()

    def refresh_voice_client(self, voice_client: discord.VoiceClient) -> None:
        """
        Re-sync voice client reference after async gaps.
        """
        self.voice_client = voice_client
    
    async def play_song(self, song: Song, source: YTDLSource) -> None:
        """
        Start playing a song. Trusts that caller verified the voice client.
        """
        self.current = song
        self.voice_client.play(source, after=self._after_song)

        await self.text_channel.send(embed=self._now_playing_embed(song))

    def _after_song(self, error: Optional[Exception]) -> None:
        """
        Called by discord.py audio thread when a song ends or raises error.
        """
        if error:
            print(f"[player:{self.voice_client.guild.id}] Playback error: {error}")

        future = asyncio.run_coroutine_threadsafe(self._advance(), self._loop)
        try:
            future.result(timeout=10)
        except Exception as e:
            print(f"[player:{self.voice_client.guild.id}] Advance error: {e}")
    
    async def _advance(self) -> None:
        """
        Pop the next song from the queue and play it or mark idle.
        """
        if not self.queue:
            self.current = None
            
            return
        
        next_song = self.queue.pop(0)

        try:
            source = YTDLSource(
                discord.FFmpegPCMAudio(next_song.url, **FFMPEG_OPTIONS),
                data={
                    "title": next_song.title,
                    "url": next_song.url,
                    "webpage_url": next_song.webpage_url,
                    "duration": next_song.duration,
                    "thumbnail": next_song.thumbnail,
                },
            )

            await self.play_song(next_song, source)

            await events.emit("song_started", {
                **next_song.to_event_data(),
                "channel_id": str(self.text_channel.id),
                "voice_channel_id": str(self.voice_client.channel.id),
            })
        except Exception as e:
            await self.text_channel.send(f"Failed to play **{next_song.title}: {e}")
            await self._advance() # skip broken song and try next instead of retrying
    
    def enqueue(self, song: Song) -> int:
        """
        Adds a song to the queue and returns its position.
        """
        self.queue.append(song)

        return len(self.queue) # keeps indexes 1-indexed for better readability to users
    
    def skip(self) -> Optional[Song]:
        """
        Skips current song and returns skipped song.
        """
        if self.voice_client.is_playing() or self.voice_client.is_paused():
            skipped = self.current
            self.voice_client.stop() # this will trigger _after_song() -> _advance()

            return skipped
        
        return None
    
    def stop(self) -> None:
        """
        Clear queue and stop playback.
        """
        self.queue.clear()
        self.current = None

        if self.voice_client.is_playing():
            self.voice_client.stop()

    @property
    def is_playing(self) -> bool:
        return self.voice_client.is_playing()
    
    @property
    def is_idle(self) -> bool:
        return self.current is None and not self.queue
    
    def _now_playing_embed(self, song: Song) -> discord.Embed:
        embed = discord.Embed(
            title="Now playing",
            description=f"[{song.title}]({song.webpage_url})",
            color=discord.Color.blurple(),
        )

        embed.add_field(name="Duration", value=song.duration_fmt, inline=True)
        embed.add_field(name="Requested by", value=song.requester_name, inline=True)
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)

        return embed
    
    def queue_embed(self) -> discord.Embed:
        embed = discord.Embed(title="Current queue", color=discord.Color.blurple())

        if self.current:
            embed.add_field(
                name="Now playing",
                value=f"[{self.current.title}]({self.current.webpage_url}) - {self.current.duration_fmt}",
                inline=False
            )

        if self.queue:
            lines = []

            for i, song in enumerate(self.queue[:10], start=1):
                lines.append(f"`{i}.` [{song.title}]({song.webpage_url}) - {song.duration_fmt}")
            if len(self.queue) > 10:
                lines.append(f"**+ {len(self.queue) - 10}**")

            embed.add_field(name="Up next", value="\n".join(lines), inline=False)
        elif not self.current:
            embed.description = "Queue is empty."
        
        return embed
