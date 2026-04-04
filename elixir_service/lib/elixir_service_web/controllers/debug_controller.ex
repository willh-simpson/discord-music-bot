defmodule ElixirServiceWeb.DebugController do
  use ElixirServiceWeb, :controller

  def guild_state(conn, %{"guild_id" => guild_id}) do
    case ElixirService.GuildSession.get_state(guild_id) do
      {:ok, state} ->
        json(conn, %{
          guild_id:         state.guild_id,
          songs_played:     state.songs_played,
          current_song:     state.current_song,
          queue_length:     length(state.queue),
          listener_count:   map_size(state.listeners),
          listeners:        state.listeners,
          voice_channel_id: state.voice_channel_id,
          session_started:  state.session_started_at
        })

      {:error, :not_found} ->
        conn
        |> put_status(404)
        |> json(%{
          error: "No active session for guild #{guild_id}"
        })
    end
  end
end
