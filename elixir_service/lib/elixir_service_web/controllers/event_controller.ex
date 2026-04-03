defmodule ElixirServiceWeb.EventController do
  use ElixirServiceWeb, :controller

  def create(conn, params) do
    event_type = params["type"] || "unknown"
    timestamp = params["timestamp"] || "no timestamp"
    data = params["data"] || %{}

    IO.puts("[event] #{event_type} at #{timestamp}")
    IO.inspect(data, label: "[event data]", pretty: true)

    json(conn, %{status: "ok", received: event_type})
  end
end
