defmodule ElixirServiceWeb.HealthController do
  use ElixirServiceWeb, :controller

  def index(conn, _params) do
    json(conn %{
      status: "ok",
      service: "elixir_realtime",
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
    })
  end
end
