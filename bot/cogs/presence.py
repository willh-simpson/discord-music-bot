import logging

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)


class PresenceTracker(commands.Cog):
    """
    Tracks Discord Rich Presence data for users in voice channels.
    Game activity for each user is cached so it can be passed to LLM intent parser.

    Presence data is transient: stored in memory, cleared when user leaves.
    It's only used for real-time context for recommendations, so it's not necessary to store.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._presence_cache: dict[int, str | None] = {} # {user id, game name}

    def get_game(self, user_id: int) -> str | None:
        """
        Returns current game for a user, or None if not playing anything.
        """

        return self._presence_cache.get(user_id)
    
    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """
        Runs whenever a member's presence changes.
        """

        game = self._extract_game(after)
        if game != self._presence_cache.get(after.id):
            if game:
                logger.debug(f"[presence] {after.display_name} is now playing {game}")
            else:
                logger.debug(f"[presence] {after.display_name} stopped playing")
        
        self._presence_cache[after.id] = game

    def _extract_game(self, member: discord.Member) -> str | None:
        """
        Extracts game name from a member's activities via ActivityType.playing.
        """

        if not member.activities:
            return None
        
        for activity in member.activities:
            if isinstance(activity, discord.Game):
                return activity.name
            if isinstance(activity, discord.Activity) and activity.type == discord.ActivityType.playing:
                return activity.name
            
        return None
    
    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState
    ):
        """
        Clears presence cache when a user leaves all voice channels.
        """

        if before.channel and not after.channel:
            self._presence_cache.pop(member.id, None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PresenceTracker(bot))
