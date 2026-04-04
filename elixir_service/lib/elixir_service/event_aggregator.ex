defmodule ElixirService.EventAggregator do
  use GenServer

  require Logger

  @flush_interval_ms 10_000 # 10 sec
  @django_url System.get_env("DJANGO_URL", "http://django:8000")

  defstruct buffer: []

  def start_link(opts), do: GenServer.start_link(__MODULE__, opts, name: __MODULE__)

  @doc """
  Pushes a listening event into the buffer.
  """
  def push(event), do: GenServer.cast(__MODULE__, {:push, event})

  @impl true
  def init(_opts) do
    schedule_flush()

    {:ok, %__MODULE__{}}
  end

  @impl true
  def handle_cast({:push, event}, state) do
    {:noreply, %{
      state |
      buffer: [event | state.buffer]
    }}
  end

  @impl true
  def handle_info(:flush, state) do
    new_state = flush_buffer(state)
    schedule_flush()

    {:noreply, new_state}
  end

  defp flush_buffer(%{buffer: []} = state), do: state
  defp flush_buffer(state) do
    events = Enum.reverse(state.buffer)
    Logger.info("[EventAggregator] Flushing #{length(events)} events to Django")

    case post_to_django(events) do
      :ok ->
        %{state | buffer: []}

      {:error, reason} ->
        Logger.error("[EventAggregator] Flush failed: #{inspect(reason)}. Keeping buffer")

        # keep buffer and retry on next flush
        state
    end
  end

  defp post_to_django(events) do
    url = "#{@django_url}/api/listening-events/"
    payload = Jason.encode!(%{events: events})
    headers = [{"Content-Type", "application/json"}]

    case HTTPoison.post(url, payload, headers, recv_timeout: 5_000) do
      {:ok, %HTTPoison.Response{status_code: status}} when status in 200.299 ->
        :ok

      {:ok, %HTTPoison.Response{status_code: status, body: body}} ->
        {:error, "HTTP #{status}: #{body}"}

      {:error, %HTTPoison.Error{reason: reason}} ->
        {:error, reason}
    end
  end

  defp schedule_flush do
    Process.send_after(self(), :flush, @flush_interval_ms)
  end
end
