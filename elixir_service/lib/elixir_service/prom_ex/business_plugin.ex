defmodule ElixirService.PromEx.BusinessPlugin do
  @moduledoc """
  Custom PromEx plugin for business-level metrics.
  """

  use PromEx.Plugin

  @impl true
  def event_metrics(_opts) do
    Event.build(
      :elixir_service_business_event_metrics,
      [
        counter(
          "elixir_service.songs.started.total",
          event_name: [:elixir_service, :song, :started],
          measurement: :count,
          description: "Total number of songs started across all guilds",
          tags: [:guild_id],
          tag_values: fn metadata -> %{guild_id: metadata[:guild_id] || "unknown"} end
        ),

        counter(
          "elixir_service.songs.skipped.total",
          event_name: [:elixir_service, :song, :skipped],
          measurement: :count,
          description: "Total number of songs skipped across all guilds",
          tags: [:guild_id],
          tag_values: fn metadata -> %{guild_id: metadata[:guild_id] || "unknown"} end
        ),

        counter(
          "elixir_service.events.routed.total",
          event_name: [:elixir_service, :event, :routed],
          measurement: :count,
          description: "Total Discord events routed to GuildSessions",
          tags: [:event_type],
          tag_values: fn metadata -> %{event_type: metadata[:event_type] || "unknown"} end
        ),

        counter(
          "elixir_service.events.dropped.total",
          event_name: [:elixir_service, :event, :dropped],
          measurement: :count,
          description: "Events dropped due to Django flush failure",
          tags: [:reason],
          tag_values: fn metadata -> %{reason: metadata[:reason] || "unknown"} end
        ),

        distribution(
          "elixir_service.event_aggregator.flush.duration.milliseconds",
          event_name: [:elixir_service, :event_aggregator, :flushed],
          measurement: :duration_ms,
          description: "EventAggregator flush duration in milliseconds",
          tags: [:status],
          tag_values: fn metadata -> %{status: metadata[:status] || "ok"} end,
          reporter_options: [
            buckets: [5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000]
          ]
        ),
      ]
    )
  end

  @impl true
  def polling_metrics(_opts) do
    Polling.build(
      :elixir_service_session_polling_metrics,
      5_000,
      {__MODULE__, :session_metrics, []},
      [
        last_value(
          "elixir_service.guild_sessions.active",
          event_name: [:elixir_service, :sessions, :poll],
          measurement: :active_count,
          description: "Number of active GuildSession processes"
        ),

        last_value(
          "elixir_service.listeners.active",
          event_name: [:elixir_service, :sessions, :poll],
          measurement: :listener_count,
          description: "Total active listeners across all guild sessions"
        ),

        last_value(
          "elixir_service.queue.depth",
          event_name: [:elixir_service, :sessions, :poll],
          measurement: :queue_depth,
          description: "Total songs queued across all active guilds"
        ),
      ]
    )
  end

  @doc """
  Walks Registry to collect live state from all GuildSessions and emits
  Telemetry event with aggregate measurements.

  Called every 5 seconds.
  """
  def session_metrics do
    %{
      active_guild_sessions: active_count,
      total_listener_count: listener_count,
      total_queue_depth: queue_depth,
    } = ElixirService.SessionMetrics.collect()

    :telemetry.execute(
      [:elixir_service, :sessions, :poll],
      %{
        active_count: active_count,
        listener_count: listener_count,
        queue_depth: queue_depth,
      },
      %{}
    )
  end
end
