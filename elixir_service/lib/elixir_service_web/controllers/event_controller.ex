defmodule ElixirServiceWeb.EventController do
  use ElixirServiceWeb, :controller

  require Logger

  def create(conn, params) do
    event_type = params["type"] || "unknown"
    data = params["data"] || %{}
    guild_id = data["guild_id"]

    if guild_id do
      ElixirService.GuildSession.handle_event(guild_id, event_type, data)
      Logger.info("[EventController] Routed #{event_type} -> guild #{guild_id}")
    else
      Logger.warning("[EventController] Event #{event_type} has no guild_id. Ignored")
    end

    json(conn, %{
      status: "ok",
      received: event_type
    })
  end
end
