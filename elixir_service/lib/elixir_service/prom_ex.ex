defmodule ElixirService.PromEx do
  @moduledoc """
  PromEx configuration for Elixir realtime service.

  Converts Telemetry events emitted by Phoenix and BEAM VM to Prometheus metrics.
  Defines custom metrics: songs played, events routed, active guild sessions.
  """

  use PromEx, otp_app: :elixir_service

  alias PromEx.Plugins

  @impl true
  def plugins do
    [
      Plugins.Application,
      Plugins.Beam,

      {Plugins.Phoenix, router: ElixirServiceWeb.Router, endpoint: ElixirServiceWeb.Endpoint},

      ElixirService.PromEx.BusinessPlugin,
    ]
  end

  @impl true
  def dashboard_assigns do
    [
      datasource_id: "Prometheus",
      default_selected_interval: "30s",
    ]
  end

  @impl true
  def dashboards do
    []
  end
end
