defmodule ElixirService.SessionMetrics do

  @doc """
  Collects current state snapshots from all active Guild Sessions.

  Returns a map with per-guild details and pre-aggregated totals.
  """
  def collect() do
    guild_states =
      Registry.select(
        ElixirService.Registry,
        [
          {
            {:"$1", :"$2", :"$3"},
            [],
            [{
              {:"$1", :"$2"}
            }]
          }
        ]
      )
      |> Enum.filter(fn {key, _pid} ->
        is_binary(key)
      end)
      |> Enum.flat_map(fn {guild_id, _pid} ->
        case ElixirService.GuildSession.get_state(guild_id) do
          {:ok, state} ->
            [%{
              guild_id: guild_id,
              songs_played: state.songs_played,
              listener_count: map_size(state.listeners),
              queue_length: length(state.queue),
              is_playing: state.current_song != nil,
            }]

          _ ->
            []
        end
      end)

    %{
      active_guild_sessions: length(guild_states),
      total_listener_count: Enum.sum(Enum.map(guild_states, & &1.listener_count)),
      total_queue_depth: Enum.sum(Enum.map(guild_states, & &1.queue_length)),
      guilds: guild_states,
    }
  end
end
