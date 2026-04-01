#!/bin/sh

set -e

echo "==> Fetching Elixir dependencies..."
mix deps.get

echo "==> Compiling..."
mix compile

echo "==> Starting Phoenix server..."
exec mix phx.server