import asyncio
from dataclasses import dataclass

import certifi
import discord
import yt_dlp

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "prefer_free_formats": True,
    "extractor_retries": 3,
    "fragment_retries": 3,
}

FFMPEG_OPTIONS = {
    # handles stream expiry mid-song
    "before_options": (
        "-reconnect 1 ",
        "-reconnect_streamed 1 ",
        "-reconnect_delay_max 5",
    ),
    "options": "-vn", # no video
}


@dataclass
class Song:
    title: str
    url: str
    webpage_url: str
    duration: int
    requester_id: int
    requester_name: str
    guild_id: int
    thumbnail: str | None = None

    @property
    def duration_fmt(self) -> str:
        mins, secs = divmod(self.duration, 60)
        hours, mins = divmod(mins, 60)

        if hours:
            return f"{hours}:{mins:02d}:{secs:02d}"
        
        return f"{mins}:{secs:02d}"
    
    def to_event_data(self) -> dict:
        """
        Serializable dict for Elixir event payloads.
        """
        return {
            "title": self.title,
            "webpage_url": self.webpage_url,
            "duration": self.duration,
            "requester_id": str(self.requester_id),
            "requester_name": self.requester_name,
            "guild_id": str(self.guild_id),
            "thumbnail": self.thumbnail,
        }
    

class YTDLSource(discord.PCMVolumeTransformer):
    """
    Wraps FFmpegPCMAudio with yt-dlp metadata.
    """

    def __init__(self, source: discord.FFmpegPCMAudio, *, data: dict):
        super().__init__(source, volume=0.5)

        self.data = data
        self.title: str = data.get("title", "Unknown")
        self.url: str = data.get("url", "")
        self.webpage_url: str = data.get("webpage_url", "")
        self.duration: int = data.get("duration", 0)
        self.thumbnail: str | None = data.get("thumbnail")

    @classmethod
    async def from_query(cls, query: str, *, loop: asyncio.AbstractEventLoop) -> "YTDLSource":
        """
        Resolves search query or URL into streamable audio source.
        Runs in a thread executor because yt-dlp is blocking.
        """
        opts = {
            **YTDL_OPTIONS,
            "ssl_certificate": certifi.where(),
        }
        ytdl = yt_dlp.YoutubeDL(opts)

        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(query, download=False),
        )

        # ytsearch returns list under 'entries', so grab the first entry
        if "entries" in data:
            data = data["entries"][0]

        return cls(
            discord.FFmpegPCMAudio(data["url"], **FFMPEG_OPTIONS),
            data=data
        )