## Discord Music Bot

A distributed Discord music bot used for streaming music in voice channels and generating song recommendations based on user activity and context.

This system builds user preference profiles based on listening behavior, improving personalized recommendations over time.

---

### Bot commands

* `!play, !p <song>` Directly play a song (searches YouTube), or add to queue if a song is currently playing. Bot will connect to voice channel if not currently in it.
* `!recommend, !rec <query>` Get AI-powered song recommendations from a given query specifying what kind of music the user wants to listen to, and recommends based on stored user profile. Empty query will recommend based on listening history.
* `!skip, !s` Skip currently playing song.
* `!queue, !q` Show current song queue.
* `!nowplaying, !np` Show currently playing song.
* `!stop` Stops currently playing song, clears queue, and bot leaves channel.
* `!join` Bot joins current voice channel if the user is in one.
* `!leave` Bot leaves current voice channel if it's in one.

---

### Architecture overview

```
Discord
   |
   v
Python Bot (discord.py)   <- voice playback, commands,
   |                         LLM intent parsing
   v
Elixir Realtime Service   <- OTP supervision, session
   |                         state, event aggregation
   v
Django ML Service         <- recommendation API, user
   |                         profiles, model pipeline
   |
   └> PostgreSQL          <- listen history, embeddings,
   |                         model cache
   └> Redis               <- Celery task queue
   └> Celery Worker       <- async event processing,
                             model rebuilds
```
> Full architectural diagrams with component breakdowns
> and data flow details can be found in
> [`docs/system-architecture.md`](docs/system-architecture.md).

---

### Tech stack

| Layer                 | Technology                                    |
| --------------------- | --------------------------------------------- |
| Discord Bot           | Python, discord.py, yt-dlp, FFmpeg            |
| Realtime Service      | Elixir, Phoenix, OTP/GenServer, PubSub        |
| ML Service            | Python, Django, Django REST Framework         |
| Task Queue            | Celery, Redis                                 |
| Recommendation Models | Sci-kit learn, numpy, FAISS                   |
| LLM Intent Parsing    | Ollama (llama3.2), LangChain                  |
| Database              | PostgreSQL                                    |
| Observability         | Prometheus, Grafana, Elixir Telemetry, PromEx |
| Infrastructure        | Docker, Docker Compose                        |

---

### Key design decisions

**Elixir owns all realtime state**: Instead of storing session state in Redis or a database, each active Discord guild gets a dedicated GenServer process, with guild session state living in that process's memory. BEAM VM's process model keeps this safe and efficient where crashed sessions restart cleanly with no stale data and idle sessions self-terminate after 30 minutes. This keeps realtime path fast and avoids database round-trips for every event.

**Event aggregation before ML ingestion**: The bot fires a structured event to Elixir on every meaningful action, and `EventAggregator` GenServer buffers these and flushes to Django every 10 seconds rather than for every HTTP call. This decouples the bot's realtime path from Django availability: if Django is slow or restarting then events buffer in memory and flush next cycle.

**Phased recommendation pipeline with stable interface**: The recommendation API has a fixed request/response contract. The underlying engine graduated through three phases (rule-based popularity -> collaborative filtering with cosine similarity -> embedding-based nearest-neighbor search with FAISS) without changing the API contract. Each phase falls back to the previous one if there's insufficient data. The bot and Elixir service never changed as ML capability improved and future updates won't add extra changes.

**LLM as translation layer and *not* recommendation engine**: The LLM only does intent parsing by turning conversational queries into structured context objects. Actual recommendation work is done by the embedding and collaborative filtering models. This keeps LLM calls optional, accurate, and fail-safe: if Ollama is slow or the parse fails then the sytsem falls back to treating the query as a direct search term, and real embedding models vs an LLM keep  recommendations accurate.