# plex-xmltv-enricher

[![CI](https://github.com/zipkindev/plex-xmltv-enricher/actions/workflows/ci.yml/badge.svg)](https://github.com/zipkindev/plex-xmltv-enricher/actions/workflows/ci.yml)
[![License: 0BSD](https://img.shields.io/badge/License-0BSD-blue.svg)](LICENSE)

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
something provider-specific. It must not be interpreted as `S01E21`. The
resolver first tries a canonical provider episode. If that is unavailable, a
configured series can retain the conservative version 0.1 identity fallback so
Plex still treats it as episodic without claiming a canonical provider match.

```xml
<episode-num system="xmltv_ns">21.140.</episode-num>
<episode-num system="themoviedb.org">episode/1234567</episode-num>
<category lang="en">Series</category>
```

## Resolution pipeline

Programs are handled in this order:

1. Existing `xmltv_ns` and source `original-air-date` identifiers are preserved.
2. Explicit `S03E08`, `S3 E8`, and `S3 E8/12` values are normalized to
   `2.7.` without a network lookup.
3. The resolver maps a show to a stable provider ID using normalized title,
   aliases, country, language and network.
4. An episode is selected using compatible source numbering, an exact unique
   subtitle, an explicit `previously-shown` original-air date, or a reviewed
   `catalog`/`year`/`absolute` override. A schedule date alone never identifies
   a rerun.
5. Minimum confidence and runner-up margin gates must both pass.
6. After a provider miss, an explicit bare episode plus production year is
   normalized using the legacy year-season convention. If only a subtitle is
   available, SQLite assigns that normalized title/subtitle pair one stable
   `original-air-date` identity across reruns. The `identity_fallback` counter
   exposes these non-canonical results separately from `provider_resolved`.

The runtime is deterministic and does not use an LLM.

## Providers

Provider credentials are read only from environment variables. They are never
written to the TOML configuration, SQLite, logs, health responses, audit output,
or generated XMLTV.

| Provider | Intended role | Credential |
| --- | --- | --- |
| TheTVDB | Primary catalog for German and international television | Project API key and optional subscriber PIN |
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
replaces the last-known-good guide. Provider requests retry rate limits and
transient server/network failures with bounded backoff.

Enabled providers are tried in configuration order: TheTVDB, then TMDB, then
TVmaze. The first unambiguous high-confidence series match wins. Duplicate
records for the same show in multiple catalogs therefore do not create a false
tie, and fallback providers are not queried unnecessarily. TheTVDB requests
automatically translate common two-letter locale values such as `de`/`DE` to
the API's required `deu` identifiers.

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
max_series_lookups_per_refresh = 50

[providers.thetvdb]
enabled = true
api_key_env = "THETVDB_API_KEY"
pin_env = "THETVDB_PIN"

[providers.tmdb]
enabled = true
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
numbering = "catalog"
```

Supported numbering models are:

- `catalog`: trust a reviewed provider catalog identity even when its canonical
  numbering differs from a source's bare `E###` value;
- `year`: compare a source `E121` with catalog `S2026E121` when the source year
  is 2026;
- `absolute`: compare a source number with a provider absolute episode number.

Automatically discovered series use a stricter source-compatible model: when
the source supplies `E###`, a provider episode must carry the same regular or
absolute number. Otherwise the provider match is refused and the conservative
source/year fallback is used.

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

## Production evidence

Version 0.4.0 was promoted on 2026-09-24 after exact-input offline comparison,
a clean TrueNAS shadow, and a guarded in-place Plex DVR reload. The rolling
final snapshot retained all 160 ordered channels and 10,017 programmes with no
protected-field changes or removed source identities.

- 614 programmes gained episodic identity;
- 135 were provider-resolved, 290 used compatible source numbering, and 189
  used stable title/subtitle identity;
- 23 sampled GZSZ and Alles was zählt records retained exact source `E###`
  agreement;
- Plex imported current `Das perfekte Dinner` as canonical Season 2026
  Episodes 142–143 and current First Dates entries as episodes;
- DVR 7, both independent one-stream tuner devices and all 160 mappings per
  device were preserved.

The release image ID is
`sha256:6d93c3c44e7cacd837a14d03f7c628a21eda8a8b12a0c8ee1354046768784c3f`.
The production deployment did not proxy or open a media stream.

### Previous v0.3 evidence

Version 0.3.0 was promoted through an isolated shadow and guarded in-place Plex
DVR deployment on 2026-09-23. The live seven-day snapshot retained all 160
ordered channels and all 12,253 source programmes with zero changes to protected
title, subtitle, description, date, icon, or rating fields.

- 413 programmes across 89 titles gained episodic identity;
- 11 were canonical TMDB episode matches, 179 used explicit source/year
  numbering, and 223 used the stable identity fallback;
- Plex imported current `Das perfekte Dinner` broadcasts as canonical
  `S22E141`–`S22E143`;
- Plex imported all current `First Dates – Ein Tisch für zwei` broadcasts as
  episodes with show relationships;
- both independent source feeds retained identical 160-channel topology.

TMDB catalog coverage is incomplete for some high-volume German daily and
reality shows. The fallback keeps those entries episodic but deliberately does
not claim canonical provider season/episode metadata. Another licensed catalog
can be enabled later for greater canonical coverage without changing the XMLTV
topology or Plex tuner mappings.

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
- `0.3.0`: canonical-first resolution with a measured stable-identity fallback,
  punctuation-normalized configured titles, and bounded provider retry/backoff.
- `0.4.0`: corrected TheTVDB `deu` locale handling, ordered provider fallback,
  source-number conflict protection, original-air-date-aware rerun matching,
  and broader configurable German episodic categories.

## License

[0BSD](LICENSE). You may use, copy, modify, and distribute this software for
any purpose, with or without a fee and without an attribution condition.

This license covers the repository's source code. It does not grant rights to
Plex trademarks or to metadata, artwork, or other content returned by TMDB,
TheTVDB, TVmaze, XMLTV providers, or upstream guide services. Users remain
responsible for those services' terms and attribution requirements.
