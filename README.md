# plex-xmltv-enricher

`plex-xmltv-enricher` is a small, conservative XMLTV proxy for Plex DVR. It fixes a common failure mode where valid episodic television is imported as a movie because the source supplies only an unsupported `onscreen` episode value.

It never opens a media stream, stores provider credentials, changes channel numbers, or replaces source programme data. It appends recognized episode identity only when configured evidence is sufficient.

The initial configuration is deliberately opt-in. Category rules should be added only after auditing the provider's values; generic labels such as `Drama` or `Reality` are not enabled by default. Any programme carrying a configured movie category is refused even if it also matches a series rule.

## Run

```sh
cp config/config.example.toml config/config.toml
mkdir -p state
docker compose up -d --build
```

The enriched feed is available at `http://HOST:9193/xmltv/plex.xml`; health is available at `/healthz`.

Configuration uses TOML and standard-library Python only. The two source URLs should expose the same ordered channel topology. Fallback is refused until a primary feed has established that topology, and a mismatched fallback never replaces the last known good output.

## Episode rules

- Existing `xmltv_ns` and `original-air-date` values are preserved.
- `S03E08` becomes the XMLTV zero-based value `2.7.`.
- A configured series with `E121` and year `2026` becomes `2025.120.`. It is never guessed as `S01E21`.
- A configured series with a subtitle but no number receives a durable first-seen `original-air-date` identity, reused for later repeats with the same title/subtitle.
- Configured movie categories are never enriched.

The durable date is a parser identity fallback, not a claim that the scheduled showing was the episode's historical premiere. Provider-specific metadata adapters can later replace this fallback when they can make an unambiguous match.

## Metadata-only Plex test

Set `[test_tuner] enabled = true` only during an isolated parser test. The endpoint `/test-tuner/discover.json` advertises RTL, SAT.1 and VOX but its stream URLs always return HTTP 503. It has no provider credentials and cannot play or record video. Disable it after the test.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check .
python -m mypy src
```
