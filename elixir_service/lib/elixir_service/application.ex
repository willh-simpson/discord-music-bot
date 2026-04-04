defmodule ElixirService.Application do
  @moduledoc false

  use Application

  @impl true
  def start(_type, _args) do
    children = [
      ElixirServiceWeb.Telemetry,
      {DNSCluster, query: Application.get_env(:elixir_service, :dns_cluster_query) || :ignore},
      {Phoenix.PubSub, name: ElixirService.PubSub},
      {Registry, keys: :unique, name: ElixirService.Registry},

      # spawns GuildSessions
      ElixirService.SessionSupervisor,
      # collects and batches events to django
      ElixirService.EventAggregator,

      # Start a worker by calling: ElixirService.Worker.start_link(arg)
      # {ElixirService.Worker, arg},
      # Start to serve requests, typically the last entry
      ElixirServiceWeb.Endpoint
    ]

    # children are not tightly coupled, so using :one_for_one to restart just the one child if it crashes
    opts = [strategy: :one_for_one, name: ElixirService.Supervisor]
    Supervisor.start_link(children, opts)
  end

  # Tell Phoenix to update the endpoint configuration
  # whenever the application is updated.
  @impl true
  def config_change(changed, _new, removed) do
    ElixirServiceWeb.Endpoint.config_change(changed, removed)
    :ok
  end
end
