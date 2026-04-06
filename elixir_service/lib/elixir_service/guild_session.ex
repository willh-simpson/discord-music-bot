defmodule ElixirService.GuildSession do
  use GenServer

  require Logger

  # how long a guild session stays alive after all activity stops
  @idle_timeout_ms 30 * 60 * 1_000 # 30 min

  defstruct [
    :guild_id,
    current_song: nil,
    queue: [],
    listeners: %{}, # %{ user_id => %{ joined_at, songs_heard } }
    voice_channel_id: nil,
    songs_played: 0,
    session_started_at: nil
  ]

  #
  # public api endpoints
  #
  def start_link(opts) do
    guild_id = Keyword.fetch!(opts, :guild_id)

    # registering process in Registry to enable lookup without manually storing PID
    GenServer.start_link(__MODULE__, opts, name: via_tuple(guild_id))
  end

  def handle_event(guild_id, event_type, data) do
    case ElixirService.SessionSupervisor.ensure_guild_session(guild_id) do
      {:ok, _pid} ->
        # cast fire and forget, no reply expected
        GenServer.cast(via_tuple(guild_id), {:event, event_type, data})

      error ->
        Logger.error("[GuildSession] Could not ensure session for #{guild_id}: #{inspect(error)}")
    end
  end

  def get_state(guild_id) do
    case Registry.lookup(ElixirService.Registry, guild_id) do
      [{_pid}] ->
        # call synchronous, reply expected
        GenServer.call(via_tuple(guild_id), :get_state)

      [] ->
        {:error, :not_found}
    end
  end

  #
  # GenServer callbacks
  #
  @impl true
  def init(opts) do
    guild_id = Keyword.fetch!(opts, :guild_id)
    Logger.info("[GuildSession: #{guild_id}] Initializing")

    state = %__MODULE__{
      guild_id: guild_id,
      session_started_at: DateTime.utc_now()
    }

    {:ok, state, @idle_timeout_ms}
  end

  @impl true
  def handle_call(:get_state, _from, state), do: {:reply, {:ok, state}, state, @idle_timeout_ms}

  @impl true
  def handle_cast({:event, event_type, data}, state) do
    new_state = process_event(event_type, data, state)

    {:noreply, new_state, @idle_timeout_ms}
  end

  @impl true
  def handle_info(:timeout, state) do
    Logger.info("[GuildSession: #{state.guild_id}] Idle timeout. Shutting down")

    {:stop, :normal, state}
  end

  #
  # event processors for each event type
  #

  defp process_event("song_started", data, state) do
    :telemetry.execute(
      [:elixir_service, :song, :started],
      %{count: 1},
      %{guild_id: state.guild_id}
    )

    song = %{
      title:          data["title"],
      webpage_url:    data["webpage_url"],
      duration:       data["duration"],
      requester_id:   data["requester_id"],
      requester_name: data["requester_name"],
      started_at:     DateTime.utc_now()
    }

    new_state = %{
      state |
      current_song:     song,
      voice_channel_id: data["voice_channel_id"] || state.voice_channel_id,
      songs_played:     state.songs_played + 1
    }

    broadcast(state.guild_id, {:song_started, song})
    update_listener(new_state, data["requester_id"], data["requester_name"])
  end

  defp process_event("song_queued", data, state) do
    song = %{
      title:          data["title"],
      webpage_url:    data["webpage_url"],
      duration:       data["duration"],
      requester_id:   data["requester_id"],
      requester_name: data["requester_name"],
      queued_at:      DateTime.utc_now()
    }

    broadcast(state.guild_id, {:song_queued, song})
    %{
      state |
      queue: state.queue ++ [song]
    }
  end

  defp process_event("song_skipped", data, state) do
    :telemetry.execute(
      [:elixir_service, :song, :skipped],
      %{count: 1},
      %{guild_id: state.guild_id}
    )

    skipped = state.current_song

    # emit listening event before clearing song in order to
    # record how long the user actually listened to the song.
    if skipped do
      emit_listening_event(state, skipped, :skipped)
    end

    broadcast(state.guild_id, {:song_skipped, skipped})

    %{
      state |
      current_song: nil,
      queue: tl_safe(state.queue)
    }
  end

  defp process_event("playback_stopped", data, state) do
    if state.current_song do
      emit_listening_event(state, state.current_song, :stopped)
    end

    broadcast(state.guild_id, :playback_stopped)

    %{
      state |
      current_song: nil,
      queue: [],
      voice_channel_id: nil,
      listeners: %{}
    }
  end

  defp process_event("bot_joined_voice", data, state) do
    broadcast(state.guild_id, {:bot_joined_voice, data["voice_channel_id"]})

    %{
      state |
      voice_channel_id: data["voice_channel_id"]
    }
  end

  defp process_event(unknown, _data, state) do
    Logger.warning("[GuildSession: #{state.guild_id}] Unknown event")

    state
  end

  #
  # helpers
  #
  defp via_tuple(guild_id), do: {:via, Registry, {ElixirService.Registry, guild_id}}

  defp broadcast(guild_id, message) do
    Phoenix.PubSub.broadcast(
      ElixirService.PubSub,
      "guild:#{guild_id}",
      message
    )
  end

  defp update_listener(state, user_id, user_name) do
    listener = Map.get(state.listeners, user_id, %{
      user_name: user_name,
      joined_at: DateTime.utc_now(),
      songs_heard: 0
    })

    updated = %{
      listener |
      songs_heard: listener.songs_heard + 1
    }

    %{
      state |
      listeners: Map.put(state.listeners, user_id, updated)
    }
  end

  defp emit_listening_event(state, song, reason) do
    duration_listened = if song[:started_at] do
      DateTime.diff(DateTime.utc_now(), song.started_at, :second)
    else
      0
    end

    event = %{
      guild_id:          state.guild_id,
      user_ids:          Map.keys(state.listeners),
      song_title:        song.title,
      song_url:          song.webpage_url,
      duration_listened: duration_listened,
      full_duration:     song.duration,
      completion_ratio:  completion_ratio(duration_listened, song.duration),
      reason:            Atom.to_string(reason),
      timestamp:         DateTime.utc_now() |> DateTime.to_iso8601()
    }

    ElixirService.EventAggregator.push(event)
  end

  defp completion_ratio(_listened, 0), do: 0.0
  defp completion_ratio(listened, total) do
    ratio = listened / total

    # yt-dlp duration might be slightly under actual playback length,
    # so this prevents completion ratio > 1.0
    Float.round(min(ratio, 1.0), 3)
  end

  defp tl_safe([]), do: []
  defp tl_safe([_ | rest]), do: rest
end
