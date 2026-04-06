import logging

from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from .client import get_client

logger = logging.getLogger(__name__)

_EXPLAIN_PROMPT = PromptTemplate(
    template="""
        You write brief, friendly one-sentence explanations for music recommendations.
        Be specific and personal. Never start with "I". Maximum 20 words. No quotes around the song title.

        Song: {song_title}
        Reason the engine chose it: {song_reason} ({engine_desc})
        User context: {context_str}

        Write a single sentence explanation. Nothing else.
    """,
    input_variables=["song_title", "song_reason", "engine_desc", "context_str"],
)

_PHASE_DESCRIPTIONS = {
    "phase3_embedding": "matched your taste profile via embedding similarity",
    "phase2_collaborative": "recommended by collaborative filtering",
    "phase1_rule_based": "trending and popular in this server",
    "phase1_fallback": "popular in this server",
    "phase2_fallback": "based on your listening history",
}


async def explain_recommendation(
        song_title: str,
        song_reason: str,
        phase: str,
        user_mood: list[str],
        game_context: str | None,
        time_label: str | None
) -> str:
    """
    Generate a conversational one-sentence explanation for a recommendation.
    """

    llm = get_client()
    chain = _EXPLAIN_PROMPT | llm | StrOutputParser()

    context_parts = []
    if user_mood:
        context_parts.append(f"mood: {', '.join(user_mood)}")
    if game_context:
        context_parts.append(f"playing {game_context}")
    if time_label:
        context_parts.append(time_label)

    context_str = "; ".join(context_parts) if context_parts else "no specific context"
    engine_desc = _PHASE_DESCRIPTIONS.get(phase, "recommended by the music engine")

    try:
        explanation = await chain.ainvoke({
            "song_title": song_title,
            "song_reason": song_reason,
            "engine_desc": engine_desc,
            "context_str": context_str,
        })
        explanation = explanation.strip().split("\n")[0]

        logger.info(f"[explainer] Generated explanation for '{song_title}'")

        return explanation
    except Exception as e:
        logger.error(f"[explainer] Failed: {type(e).__name__}: {e}")

        return song_reason