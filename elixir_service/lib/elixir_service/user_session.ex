defmodule ElixirService.UserSession do
  use GenServer

  require Logger

  @idle_timeout_ms 60 * 60 * 1_000 # 1 hour

  defstruct [
    :user_id,
    :guild_id,
    :user_name,
    songs_heard: [],
    total_listen_time: 0, # seconds
    session_started: nil
  ]

  #
  # public api endpoints
  #
  def start_link(opts) do
    user_id = Keyword.fetch!(opts, :user_id)
    guild_id = Keyword.fetch!(opts, :guild_id)

    GenServer.start_link(__MODULE__, opts, name: via_tuple(guild_id, user_id))
  end

  def record_listen(guild_id, user_id, song_data) do
    GenServer.cast(via_tuple(guild_id, user_id), {:record_listen, song_data})
  end

  def get_history(guild_id, user_id) do
    case Registry.lookup(ElixirService.Registry, {guild_id, user_id}) do
      [{_pid, _}] ->
        GenServer.call(via_tuple(guild_id, user_id), :get_history)

      [] ->
        {:error, :not_found}
    end
  end

  #
  # GenServer callbacks
  #
  @impl true
  def init(opts) do
    user_id = Keyword.fetch!(opts, :user_id)
    guild_id = Keyword.fetch!(opts, :guild_id)
    user_name = Keyword.get(opts, :user_name, "unknown")

    state = %__MODULE__{
      user_id:         user_id,
      guild_id:        guild_id,
      user_name:       user_name,
      session_started: DateTime.utc_now()
    }

    {:ok, state, @idle_timeout_ms}
  end

  @impl true
  def handle_call({:get_history, _from, state}), do: {:reply, {:ok, state}, state, @idle_timeout_ms}

  @impl true
  def handle_cast({:record_listen, song_data}, state) do
    entry = %{
      title:            song_data.song_title,
      url:              song_data.song_url,
      duration_listened: song_data.duration_listened,
      completion_ratio: song_data.completion_ratio,
      listened_at:      DateTime.utc_now()
    }

    new_state = %{state |
      songs_heard:       [entry | state.songs_heard] |> Enum.take(100),
      total_listen_time: state.total_listen_time + song_data.duration_listened
    }

    {:noreply, new_state, @idle_timeout_ms}
  end

  @impl true
  def handle_info(:timeout, state) do
    Logger.info("[UserSession: #{state.guild_id}/#{state.user_id}] Idle timeout")

    {:stop, :normal, state}
  end

  #
  # helpers
  #
  defp via_tuple(guild_id, user_id) do
    {:via, Registry, {ElixirService.Registry, {guild_id, user_id}}}
  end
end
