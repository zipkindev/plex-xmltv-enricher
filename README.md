# plex-xmltv-enricher

`plex-xmltv-enricher` is a small, conservative XMLTV proxy for Plex DVR. It
preserves an existing guide and adds standard episode identity only when the
source or a configured metadata provider supplies enough evidence.

```text
XMLTV source ──► source normalizer ──► metadata resolver ──► Plex DVR
                                           │
                                           └── SQLite mappings, catalogs,
                                               provenance and audit results
```

The service does not proxy video, increase tuner capacity, alter channel
numbers, or continuously guess from genre labels. Ambiguous programs are left
unchanged.

## Why this exists

Some XMLTV feeds contain useful episode evidence in a form Plex does not fully
interpret. For example:

```xml
<title>Example Daily Show</title>
<sub-title>Day 3: Alex</sub-title>
<date>2026</date>
<episode-num system="onscreen">E121</episode-num>
```

`E121` may mean episode 121 of a year-season, an absolute episode number, or
something provider-specific. It must not be interpreted as `S01E21`, and the
year must not be treated as a season for every series. Version 0.2 stores a
numbering model per resolved series and corroborates the episode against a
catalog before emitting zero-based XMLTV numbering:

```xml
<episode-num system="xmltv_ns">2025.120.</episode-num>
<episode-num system="thetvdb.com">episode/1234567</episode-num>
<category lang="en">Series</category>
```

## Resolution pipeline

Programs are handled in this order:

1. Existing `xmltv_ns` and source `original-air-date` identifiers are preserved.
2. Explicit `S03E08`, `S3 E8`, and `S3 E8/12` values are normalized to
   `2.7.` without a network lookup.
3. Explicitly configured version 0.1 compatibility rules remain available while
   deployments migrate to provider-backed resolution.
4. The resolver maps a show to a stable provider ID using normalized title,
   aliases, country, language and network.
5. An episode is selected using catalog numbering, exact unique subtitle,
   exact unique airdate, or a configured `year`/`absolute` numbering model.
6. Minimum confidence and runner-up margin gates must both pass. Ties and weak
   matches remain unchanged.

The runtime is deterministic and does not use an LLM.

## Providers

Provider credentials are read only from environment variables. They are never
written to the TOML configuration, SQLite, logs, health responses, audit output,
or generated XMLTV.

| Provider | Intended role | Credential |
| --- | --- | --- |
| TheTVDB | Primary optional catalog for German and international television | Project API key and optional subscriber PIN |
| TMDB | Optional secondary catalog and cross-check | API read token |
| TVmaze | Free zero-credential fallback | None |

Users are responsible for the terms, attribution and licensing requirements of
each enabled provider. The project does not scrape provider websites and does
not embed shared API keys.

## Dynamic updates

Every XMLTV refresh discovers new show fingerprints. Known mappings and episode
catalogs are reused immediately from SQLite. Unknown titles are searched only
after the configured cache TTL, including negative-cache entries. Episode
catalogs use stale data if a refresh fails, while invalid source XML never
replaces the last-known-good guide.

SQLite records:

- stable normalized-title to provider-series mappings;
- numbering model, confidence and provenance;
- provider episode catalogs and fetch times;
- resolved, unresolved and ambiguous decisions.

No manual action is needed when an unambiguous new episode appears. A genuinely
new or ambiguous show remains visible in the guide without fabricated episode
metadata and appears in the audit report.

Only records explicitly identified as episodic by configured source categories
or titles are sent to providers. Cold-cache discovery is also capped per
refresh, so a large guide cannot create an unbounded provider request burst.

## Configuration

Copy the complete example:

```sh
cp config/config.example.toml config/config.toml
```

The important resolver settings are:

```toml
[resolver]
enabled = true
language = "de"
country = "DE"
minimum_series_confidence = 0.92
minimum_episode_confidence = 0.95
ambiguity_margin = 0.08
catalog_ttl_hours = 168
negative_ttl_hours = 24
max_series_lookups_per_refresh = 10

[providers.thetvdb]
enabled = true
api_key_env = "THETVDB_API_KEY"
pin_env = "THETVDB_PIN"

[providers.tmdb]
enabled = false
api_key_env = "TMDB_READ_TOKEN"

[providers.tvmaze]
enabled = true
```

An override declares a reviewed series identity and numbering model. It does not
hard-code individual episodes:

```toml
[series_overrides."Das perfekte Dinner"]
provider = "thetvdb"
series_id = "266543"
numbering = "year"
```

Supported numbering models are:

- `catalog`: match catalog season/episode, subtitle or airdate;
- `year`: compare a source `E121` with catalog `S2026E121` when the source year
  is 2026;
- `absolute`: compare a source number with a provider absolute episode number.

## Docker Compose

```sh
git clone https://github.com/zipkindev/plex-xmltv-enricher.git
cd plex-xmltv-enricher
cp config/config.example.toml config/config.toml
mkdir -p state
sudo chown 65532:65532 state
export THETVDB_API_KEY='your-project-key'
export THETVDB_PIN='your-pin-if-required'
docker compose up -d --build
```

Verify it without changing Plex:

```sh
curl --fail http://127.0.0.1:9193/healthz
curl --fail http://127.0.0.1:9193/audit.json
curl --fail --output guide.xml http://127.0.0.1:9193/xmltv/plex.xml
```

Run one refresh and print coverage counters:

```sh
docker compose run --rm plex-xmltv-enricher audit --config /config/config.toml
```

## TrueNAS SCALE

Deploy `compose.yaml` as a Custom App. Provide:

- `/config/config.toml`: read-only host file;
- `/state`: persistent writable directory owned by UID/GID `65532`;
- port `9193` or a separate shadow-test port;
- provider keys as application environment variables.

No media mount, tuner device, host networking, privileged mode, provider stream
credential, or Docker socket is required.

For an existing production guide, run version 0.2 on a different port first.
Compare channel topology, program count and audit decisions, then use the
metadata-only test tuner before changing a DVR lineup.

## HTTP endpoints

| Endpoint | Purpose |
| --- | --- |
| `/healthz` | Refresh age, active source, topology and current result counters |
| `/audit.json` | Feed counters plus cached mapping/catalog/decision counts |
| `/xmltv/plex.xml` | Atomic last-known-good XMLTV document |

The optional `/test-tuner` endpoint is non-playable and exists only for isolated
Plex parser tests.

## Reliability and security

- Primary/fallback feeds must have identical ordered channel topology.
- Source channel IDs, ordering, times, titles, descriptions, subtitles, icons
  and ratings are preserved.
- Download size and time are bounded.
- Output replacement is atomic and synced to disk.
- Provider failures use fresh or stale caches and cannot erase the current
  guide.
- Logs contain event names and error types, not URLs, tokens or provider data.
- The container runs as UID/GID `65532`, with a read-only root filesystem,
  dropped capabilities and `no-new-privileges`.

## Development

Python 3.13 is required.

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check .
python -m mypy src
```

Tests use synthetic providers and XMLTV. They do not contact Plex, production
feeds or metadata services.

## Project status

- `0.1.0`: explicit conservative XMLTV normalization, production-proven for one
  configured series.
- `0.2.0`: provider-backed identity, persistent catalogs, confidence gates,
  ambiguity refusal and audit reporting. It must be shadow-tested before
  replacing an existing production guide.
- `0.2.1`: episodic-only provider dispatch, bounded cold-cache discovery and
  corrected TMDB locale handling for safe shadow operation.
- `0.2.2`: exposes the current refresh's provider-series lookup count separately
  from cumulative cache statistics for durable budget monitoring.

## License

MIT. See [`LICENSE`](LICENSE).
