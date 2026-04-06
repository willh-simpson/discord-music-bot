defmodule ElixirServiceWeb.MetricsController do
  use ElixirServiceWeb, :controller

  require Logger

  def index(conn, _params) do
    metrics = ElixirService.SessionMetrics.collect()

    json(
      conn,
      Map.put(metrics, :timestamp, DateTime.utc_now() |> DateTime.to_iso8601())
    )
  end
end
