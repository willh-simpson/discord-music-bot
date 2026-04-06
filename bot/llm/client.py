import os
from langchain_ollama import OllamaLLM

_client: OllamaLLM | None = None


def get_client() -> OllamaLLM:
    """
    Lazy singleton for Ollama LLM client.

    Client connects to Ollama server over HTTP and doesn't hold a persistent connection,
    so creating it as a singleton and reusing it is safe and efficient.
    """

    global _client
    if _client is None:
        _client = OllamaLLM(
            model=os.getenv("OLLAMA_MODEL", "llama3.2"),
            base_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            temperature=0.1, # using low temperature to get more controlled, non-conversational outputs
            num_predict=512, # max output tokens
        )

    return _client
