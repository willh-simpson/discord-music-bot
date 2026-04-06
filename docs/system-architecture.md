## System architecture

Full architecture of the Discord music bot. Each section covers a different layer or component with diagrams showing both structure and data flow.

---

### 1. System overview

Four application services and three infrastructure services. All services run in Docker.

```mermaid
graph TD
    Discord["Discord API"]

    subgraph "Application services"
        Bot["Discord Bot\nPython · discord.py"]
        Elixir["Elixir Realtime Service\nPhoenix · OTP"]
        Django["Django ML Service\nDRF · Celery"]
        CeleryW["Celery Worker\nAsync task processing"]
        CeleryB["Celery Beat\nScheduled model rebuilds"]
    end

    subgraph "Infrastructure"
        Postgres["PostgreSQL\nPersistent store"]
        Redis["Redis\nTask queue · cache"]
        Ollama["Ollama\nLocal LLM · llama3.2"]
    end

    subgraph "Observability"
        Prometheus["Prometheus\nMetrics scraping"]
        Grafana["Grafana\nDashboards · alerts"]
    end

    Discord <-->|"gateway WebSocket\nvoice UDP"| Bot
    Bot -->|"POST /api/events\nHTTP"| Elixir
    Elixir -->|"batched events\nHTTP POST every 10s"| Django
    Django -->|"recommendation response\nHTTP"| Elixir
    Elixir -->|"ranked results\nHTTP"| Bot
    Bot -->|"intent queries\nHTTP"| Ollama

    Django --- Postgres
    Django --- Redis
    CeleryW --- Redis
    CeleryW --- Postgres
    CeleryB --- Redis

    Prometheus -->|scrape :4000/metrics| Elixir
    Prometheus -->|scrape :8000/metrics| Django
    Grafana --> Prometheus
```

---

### 2. Data flow: song play to recommendation

Primary data flow: a user requests a song, listening event travels through the system, eventually improves future recommendations.

```mermaid
sequenceDiagram
    participant User
    participant Bot
    participant Elixir
    participant Django
    participant Celery
    participant DB as PostgreSQL

    User->>Bot: !play <song>
    Bot->>Bot: yt-dlp resolves stream URL
    Bot->>Bot: FFmpeg streams audio to voice channel
    Bot->>Elixir: POST /api/events {type: song_started, ...}

    Elixir->>Elixir: GuildSession GenServer updates state
    Note over Elixir: current_song, listener_count,<br/>songs_played incremented

    User->>Bot: !skip
    Bot->>Elixir: POST /api/events {type: song_skipped, ...}
    Elixir->>Elixir: compute completion_ratio, push to EventAggregator buffer

    Note over Elixir: EventAggregator flushes every 10s

    Elixir->>Django: POST /api/listening-events/ {events: [...]}
    Django->>Celery: process_listening_events.delay(events)
    Django-->>Elixir: 202 Accepted (immediate)

    Celery->>DB: upsert Song, DiscordUser, ListenEvent rows
    Celery->>DB: update GuildSongStats aggregate signals

    Note over Celery,DB: Every 5 min (dev) / 1 hr (prod)

    Celery->>DB: build_embeddings task rebuilds<br/>song vectors, user profiles, FAISS index

    User->>Bot: !recommend chill focus music
    Bot->>Elixir: POST /api/events {type: recommendation_request}
    Bot->>Django: POST /api/recommend/ {guild_id, user_id, context}
    Django->>DB: Phase3Engine queries FAISS + CF signals
    Django-->>Bot: ranked recommendations JSON
    Bot-->>User: embed with top 5 recommendations + LLM explanation
```

---

### 3. Elixir OTP supervision tree

Every process in the Elixir service is supervised. The tree defines what starts, in what order, and what happens when something crashes.

```mermaid
graph TD
    App["ElixirService.Application\nOTP Application root"]

    App --> PubSub["Phoenix.PubSub\nInternal event fan-out"]
    App --> Registry["Registry\n:unique — guild_id → PID"]
    App --> SessionSup["SessionSupervisor\nDynamicSupervisor"]
    App --> Aggregator["EventAggregator\nGenServer · 10s flush timer"]
    App --> Endpoint["Phoenix Endpoint\nHTTP + PromEx /metrics"]

    SessionSup -->|"spawned on first event\nfrom that guild"| GuildA["GuildSession\nGenServer — guild A"]
    SessionSup --> GuildB["GuildSession\nGenServer — guild B"]
    SessionSup --> GuildN["GuildSession\nGenServer — guild N"]

    GuildA --> UserA1["UserSession\nGenServer — user 1 in guild A"]
    GuildA --> UserA2["UserSession\nGenServer — user 2 in guild A"]

    style App fill:#4A4A4A,stroke:#999
    style SessionSup fill:#20C743,stroke:#28a745
    style Aggregator fill:#20C743,stroke:#28a745
    style GuildA fill:#1A7AD6,stroke:#004085
    style GuildB fill:#1A7AD6,stroke:#004085
    style GuildN fill:#A8AAB3,stroke:#6c757d
    style UserA1 fill:#E3B619,stroke:#856404
    style UserA2 fill:#E3B619,stroke:#856404
```

**Supervision strategies:**
- `Application` supervisor: `:one_for_one` -> crashed child restarts independently
- `SessionSupervisor`: `DynamicSupervisor` -> guild sessions created and removed at runtime
- `GuildSession`: self-terminates via `:timeout` after 30 minutes of inactivity
- `UserSession`: self-terminates via `:timeout` after 60 minutes of inactivity

---

### 4. GuildSession state machine

`GuildSession` GenServer transitions through states based on incoming Discord events.

```mermaid
stateDiagram-v2
    [*] --> Idle: spawned by SessionSupervisor

    Idle --> Playing: song_started event
    Playing --> Playing: song_queued event\n(queue grows)
    Playing --> Playing: song_skipped event\n(emit listen event → advance queue)
    Playing --> Idle: playback_stopped event\n(clear queue + listeners)
    Playing --> Idle: queue exhausted\n(no more songs)
    Idle --> [*]: 30 min idle timeout\nnormal shutdown

    note right of Playing
        State held in GenServer memory:
        current_song, queue [],
        listeners %{}, songs_played,
        voice_channel_id
    end note
```

---

### 5. Event aggregation pipeline

`EventAggregator` is a single GenServer that prevents the bot's realtime path from coupling to Django's availability.

```mermaid
graph LR
    subgraph "Elixir process boundary"
        GS1["GuildSession A"]
        GS2["GuildSession B"]
        EA["EventAggregator\nGenServer\nbuffer: []"]
    end

    GS1 -->|"GenServer.cast\n{:push, event}"| EA
    GS2 -->|"GenServer.cast\n{:push, event}"| EA

    EA -->|"Process.send_after\n:flush every 10s"| EA

    EA -->|"HTTPoison.post\nif buffer non-empty"| Django["Django\nPOST /api/listening-events/"]

    Django -->|"202 Accepted\nimmediate"| EA

    Django -->|".delay(events)"| Celery["Celery Worker\nasync processing"]

    style EA fill:#3097FF,stroke:#004085
    style Django fill:#FF3B4F,stroke:#721c24
```

**Failure behavior:** If the Django POST fails then the buffer is retained and retried on the next flush cycle. Events are never lost within a session lifetime. If Elixir crashes then buffered events not yet flushed are lost. This is an acceptable tradeoff before eventually adding a persistent queue via RabbitMQ/Kafka.

---

### 6. ML recommendation pipeline

Three recommendation phases are available. The system uses the most advanced phase for which sufficient data exists, falling back automatically.

```mermaid
graph TD
    Request["POST /api/recommend/\n{guild_id, user_id, limit, context}"]

    Request --> P3["Phase3Engine\nembedding-based"]
    P3 -->|"no user embedding"| P2["Phase2Engine\ncollaborative filtering"]
    P2 -->|"no interaction matrix"| P1["Phase1Engine\nrule-based popularity"]

    subgraph "Phase 3 signals (weight)"
        FAISS["FAISS nearest-neighbor\n0.40 weight\nuser profile → similar songs"]
        Cluster["Cluster peers\n0.25 weight\nsame taste group activity"]
        CF["CF signals from Phase2\n0.25 weight"]
        Context["Context boost\n0.10 weight\ntime of day · game"]
    end

    subgraph "Phase 2 signals"
        UserCF["User-based CF\ncosine similarity\nwhat similar users liked"]
        ItemCF["Item-based CF\ncosine similarity\nsongs similar to your history"]
    end

    subgraph "Phase 1 signals"
        GlobalPop["Global popularity\n0.30 weight"]
        GuildTrend["Guild trending 7d\n0.50 weight"]
        CompRate["Completion rate\n0.20 weight"]
    end

    P3 --- FAISS
    P3 --- Cluster
    P3 --- CF
    P3 --- Context

    P2 --- UserCF
    P2 --- ItemCF

    P1 --- GlobalPop
    P1 --- GuildTrend
    P1 --- CompRate
```

---

### 7. ML model build pipeline (Celery Beat)

Model data is rebuilt periodically by scheduled Celery tasks. The pipeline runs in dependency order: embeddings must exist before the FAISS index can be built.

```mermaid
graph LR
    Beat["Celery Beat\nscheduler"]

    Beat -->|"every 5 min (dev)\nevery 1 hr (prod)"| Matrix["build_interaction_matrix\nuser × song matrix\ncosine similarity"]

    Beat -->|"every 10 min (dev)\nevery 2 hr (prod)"| Embed["build_embeddings\npipeline task"]

    subgraph "build_embeddings pipeline"
        direction TB
        S1["1. build_song_embeddings\nTF-IDF title features\n+ behavioral signals\n→ 72-dim vectors"]
        S2["2. build_user_embeddings\nweighted avg of song vectors\nweighted by completion ratio"]
        S3["3. build_faiss_index\nIndexFlatIP over song vectors\nstored in ModelCache"]
        S4["4. build_user_clusters\nK-means K=8\ntaste group assignment"]
        S5["5. build_interaction_matrix\nrefresh CF signals"]

        S1 --> S2 --> S3 --> S4 --> S5
    end

    Embed --> S1

    S1 -->|"stored in"| SongEmb["SongEmbedding\nPostgres table"]
    S2 -->|"stored in"| UserEmb["UserEmbedding\nPostgres table"]
    S3 -->|"stored in"| ModelCache["ModelCache\nPostgres table\nJSON-serialised index"]
    S4 -->|"stored in"| Clusters["UserCluster\nPostgres table"]
    Matrix -->|"stored in"| ModelCache
```

---

### 8. LLM intent parsing

LLM is a translation layer only. It converts natural language into structured context that the existing recommendation pipeline consumes.

```mermaid
graph TD
    UserMsg["User: !recommend <query of user's mood>"]

    RP["Rich Presence tracker\ngame context if active"]
    TimeCtx["Time of day\nfrom system clock"]

    UserMsg --> Parser
    RP --> Parser
    TimeCtx --> Parser

    Parser["LangChain + Ollama\nllama3.2 local inference\nPydanticOutputParser"]

    Parser -->|"MusicIntent\n{mood, energy, context,\nis_direct_request, ...}"| Router{is_direct_request?}

    Router -->|"Yes, confidence > 0.7"| Play["route to !play\ndirect yt-dlp search"]
    Router -->|"No"| RecAPI["POST /api/recommend/\nwith context dict\n{mood, energy, llm_parsed: true}"]

    RecAPI --> Phase3["Phase3Engine\nFAISS + CF + context boost"]
    Phase3 --> Results["Ranked results"]

    Results --> Explainer["LangChain + Ollama\nexplain_recommendation\nStrOutputParser"]
    Explainer --> Embed["Discord embed\ntop 5 songs\n+ 1-sentence explanation"]

    style Parser fill:#B038FF,stroke:#6f42c1
    style Explainer fill:#B038FF,stroke:#6f42c1
```

**Failure modes:** If Ollama is unavailable or the parse fails, `extract_intent` returns `MusicIntent(is_direct_request=True, raw_query=original_query, confidence=0.0)`. The bot treats the message as a direct song search. LLM failure is never surfaced to the user.

---

### 9. Observability stack

```mermaid
graph TD
    subgraph "Metric sources"
        ElixirMetrics[":4000/metrics\nPromEx — Phoenix HTTP\nBEAM VM\ncustom business counters"]
        DjangoMetrics[":8000/metrics\nprometheus-client\nHTTP middleware\ntask + model metrics"]
        CeleryMetrics[":9808/metrics\ntask counts\ndurations"]
    end

    Prometheus["Prometheus\nscrape every 15s\n30d retention"]

    ElixirMetrics -->|scrape| Prometheus
    DjangoMetrics -->|scrape| Prometheus
    CeleryMetrics -->|scrape| Prometheus

    Prometheus --> Grafana["Grafana :3000\n3 dashboards"]
    Prometheus --> Alerts["Alert rules\nerror rate · latency\nmodel staleness"]

    subgraph "Grafana dashboards"
        D1["Elixir dashboard\nPhoenix request rate\nBEAM memory · processes\nactive sessions · queue depth"]
        D2["Django dashboard\nHTTP latency percentiles\nerror rate\nCelery task durations"]
        D3["Business metrics\nsongs played rate\nrecommendation acceptance\nmodel age · DB sizes"]
    end

    Grafana --> D1
    Grafana --> D2
    Grafana --> D3
```

**Key metrics by service:**

| Service | Metric                                                        | Type      | Purpose                    |
| ------- | ------------------------------------------------------------- | --------- | -------------------------- |
| Elixir  | `elixir_service_songs_started_total`                          | Counter   | Songs played per guild     |
| Elixir  | `elixir_service_guild_sessions_active`                        | Gauge     | Live session count         |
| Elixir  | `elixir_service_event_aggregator_flush_duration_milliseconds` | Histogram | Flush latency              |
| Django  | `django_recommendation_duration_seconds`                      | Histogram | Recommendation p95 latency |
| Django  | `ml_recommendations_served_total`                             | Counter   | Requests by phase          |
| Django  | `django_model_last_built_timestamp_seconds`                   | Gauge     | Model staleness            |
| Django  | `ml_listen_events_processed_total`                            | Counter   | Ingestion throughput       |

---

### 10. Database schema

```mermaid
erDiagram
    DiscordUser {
        bigint id PK
        varchar discord_id UK
        varchar username
        int total_listen_time
        int songs_heard_count
        timestamp first_seen
        timestamp last_active
    }

    Song {
        bigint id PK
        varchar webpage_url UK
        varchar title
        int duration
        int play_count
        int total_completions
        int skip_count
        timestamp first_played
        timestamp last_played
    }

    ListenEvent {
        bigint id PK
        bigint user_id FK
        bigint song_id FK
        varchar guild_id
        int duration_listened
        float completion_ratio
        varchar reason
        timestamp listened_at
    }

    GuildSongStats {
        bigint id PK
        varchar guild_id
        bigint song_id FK
        int play_count
        int unique_listeners
        timestamp last_played
    }

    SongEmbedding {
        bigint id PK
        bigint song_id FK
        text vector
        int dimensions
        timestamp built_at
    }

    UserEmbedding {
        bigint id PK
        bigint user_id FK
        text vector
        int dimensions
        int song_count
        timestamp built_at
    }

    UserCluster {
        bigint id PK
        bigint user_id FK
        int cluster_label
        varchar cluster_name
        float distance_to_centroid
        timestamp built_at
    }

    ModelCache {
        bigint id PK
        varchar cache_key UK
        text data
        jsonb metadata
        int user_count
        int song_count
        timestamp built_at
    }

    RecommendationLog {
        bigint id PK
        varchar guild_id
        varchar user_id
        jsonb recommendations
        jsonb accepted_urls
        float acceptance_rate
        varchar phase
        timestamp created_at
    }

    DiscordUser ||--o{ ListenEvent : "has"
    Song ||--o{ ListenEvent : "in"
    Song ||--o{ GuildSongStats : "tracked in"
    Song ||--|| SongEmbedding : "has"
    DiscordUser ||--|| UserEmbedding : "has"
    DiscordUser ||--|| UserCluster : "assigned to"
```