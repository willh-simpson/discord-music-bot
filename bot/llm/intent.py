import logging

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate

from .client import get_client

logger = logging.getLogger(__name__)


class MusicIntent(BaseModel):
    mood: list[str] = Field(
        default_factory=list,
        description="Mood descriptors (e.g., chill, energetic, melancholic, happy, intense, peaceful, dark, upbeat)",
    )
    energy_level: str | None = Field(
        default=None,
        description="Energy level of desired music: low, medium, or high",
    )
    context: str | None = Field(
        default=None,
        description="Activity context (e.g., focus, party, background, gaming, sleep, workout, commute)",
    )
    search_terms: list[str] = Field(
        default_factory=list,
        description="Specific search terms (e.g., genre, artist, era)",
    )
    exclude_terms: list[str] = Field(
        default_factory=list,
        description="Things to avoid in recommendations",
    )
    time_context: str | None = Field(
        default=None,
        description="time of day if mentioned: morning, afternoon, evening, or late_night",
    )
    is_direct_request: bool = Field(
        default=False,
        description="True if user named a specific song or artist",
    )
    raw_query: str = Field(
        default="",
        description="Cleaned query to pass to music search if is_direct_request is true",
    )
    confidence: float = Field(
        default=1.0,
        description="Confidence in the extraction between 0.0 and 1.0",
    )

    def to_context_dict(self) -> dict:
        """
        Serializes for Django recommendation API context field.
        """

        return {
            "mood": self.mood,
            "energy_level": self.energy_level,
            "context": self.context,
            "time_context": self.time_context,
            "llm_parsed": True,
            "confidence": self.confidence,
        }
    

_parser = PydanticOutputParser(pydantic_object=MusicIntent)
_PROMPT_TEMPLATE = PromptTemplate(
    template="""
        You are a music intent parser for a Discord music bot.
        Extract structured music preferences from the user's request.

        {context_block}

        User request: {query}

        {format_instructions}

        Response ONLY with the JSON object. No explanation, no markdown, no extra text.
    """,
    input_variables=["query", "context_block"],
    partial_variables={"format_instructions": _parser.get_format_instructions()},
)


async def extract_intent(
        query: str,
        game_context: str | None = None,
        time_label: str | None = None,
        recent_songs: list[str] | None = None,
) -> MusicIntent:
    """
    Parses natural language music request into structured MusicIntent.

    Args:
        query:          user's raw message
        game_context:   what game the user is currently playing, sourced from Rich Presence
        time_label:     "morning" | "afternoon" | "evening" | "late_night"
        recent_songs:   titles of recently played songs for context
    """

    llm = get_client()
    chain = _PROMPT_TEMPLATE | llm | _parser
    context_block = _build_context_block(game_context, time_label, recent_songs)

    logger.info(f"[intent] Extracting intent from: '{query}")

    try:
        intent: MusicIntent = await chain.ainvoke({
            "query": query,
            "context_block": context_block,
        })

        logger.info(
            f"[intent] Extracted: mood={intent.mood}, context={intent.context}, "
            f"direct={intent.is_direct_request}, confidence={intent.confidence}"
        )

        return intent
    except Exception as e:
        logger.error(f"[intent] Extraction failed: {type(e).__name__}: {e}")

        # just treat as direct search query if intent fails
        return MusicIntent(
            is_direct_request=True,
            raw_query=query,
            confidence=0.0,
        )
    

def _build_context_block(
        game_context: str | None,
        time_label: str | None,
        recent_songs: list[str] | None
) -> str:
    """
    Builds additional context section for the prompt.
    """

    lines = []

    if game_context:
        lines.append(
            f"Context: user is currently playing {game_context}. "
            "Consider music that fits that game's atmosphere."
        )
    
    if time_label:
        lines.append(f"Time of day: {time_label}.")

    if recent_songs:
        recent = ", ".join(recent_songs[:5])
        lines.append(
            f"Recently played in this session: {recent}. Use these to infer current taste."
        )

    return "\n".join(lines) if lines else ""
