FROM elixir:1.16-alpine

WORKDIR /app

RUN apk add --no-cache build-base git inotify-tools

RUN mix local.hex --force && \
    mix local.rebar --force

ENV MIX_ENV=dev

COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 4000

ENTRYPOINT ["./entrypoint.sh"]