defmodule ElixirService.SessionSupervisor do
  use DynamicSupervisor

  require Logger

  def start_link(opts), do: DynamicSupervisor.start_link(__MODULE__, opts, name: __MODULE__)

  @impl true
  def init(_opts), do: DynamicSupervisor.init(strategy: :one_for_one)

  @doc """
  Finds an existing GuildSession or starts a new one.
  Single entry point for all guild session access.
  """
  def ensure_guild_session(guild_id) do
    case Registry.lookup(ElixirService.Registry, guild_id) do
      [{pid, _}] ->
        # already running, just return pid
        {:ok, pid}

      [] ->
        # not running, start a new session
        child_spec = {ElixirService.GuildSession, guild_id: guild_id}

        case DynamicSupervisor.start_child(__MODULE__, child_spec) do
          {:ok, pid} ->
            Logger.info("[SessionSupervisor] Started GuildSession for guild #{guild_id}")
            {:ok, pid}

          {:error, {:already_started, pid}} ->
            # race condition: another requested started this session so just pass along
            {:ok, pid}

          error ->
            Logger.error("[SessionSupervisor] Failed to start GuildSession: #{inspect(error)}")
            error
        end
    end
  end
end
