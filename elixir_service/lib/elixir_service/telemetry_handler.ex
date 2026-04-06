defmodule ElixirService.TelemetryHandler do
  @moduledoc """
  Attaches handlers to custom Telemetry events and records them as
  Prometheus metrics via PromEx event metrics system.
  """

  require Logger

  def attach do
    # events = [
    #   [:elixir_service, :song, :started],
    #   [:elixir_service, :song, :skipped],
    #   [:elixir_service, :event, :routed],
    # ]

    # :telemetry.attach_many(
    #   "elixir-service-metrics",
    #   events,
    #   &handle_event/4,
    #   nil
    # )

    :ok
  end

  # def handle_event([:elixir_service, :song, :started], measurements, metadata, _config) do
  #   Logger.debug("[Telemetry] song.started guild=#{metadata[:guild_id]}")
  # end

  # def handle_event([:elixir_service, :song, :skipped], measurements, metadata, _config) do
  #   Logger.debug("[Telemetry] song.skipped guild=#{metadata[:guild_id]}")
  # end

  # def handle_event([:elixir_service, :event, :routed], measurements, metadata, _config) do
  #   Logger.debug("[Telemetry] event.routed type=#{metadata[:event_type]}")
  # end

  # def handle_event(event, measurements, metadata, _config) do
  #   Logger.warning("[Telemetry] Unhandled event: #{inspect(event)}")
  # end
end
