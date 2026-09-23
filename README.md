# plex-xmltv-enricher

`plex-xmltv-enricher` is a lightweight, conservative XMLTV proxy for Plex DVR. It sits between an existing XMLTV provider and Plex, adding episode identity when the source contains enough evidence but Plex would otherwise import a television episode as a movie.

It is useful for any XMLTV feed with incomplete episode metadata; it is not tied to a specific IPTV provider, tuner, language, or channel lineup.

```text
XMLTV provider ──► plex-xmltv-enricher ──► Plex DVR guide
                         │
                         └── SQLite identity store + last-known-good XML
```

The service never opens a media stream, stores provider credentials, changes channel numbers, or replaces programme descriptions and artwork. It only appends supported XMLTV episode markers to records that match explicitly configured series evidence.

## Why this exists

Some XMLTV sources provide useful programme data but represent episodes with values such as:

```xml
<title>Example Daily Show</title>
<sub-title>Day 3: Alex</sub-title>
<date>2026</date>
<episode-num system="onscreen">E121</episode-num>
```

Plex may ignore the bare `E121` value and classify the programme as a movie. For a configured episodic programme, this proxy preserves the original fields and appends:

```xml
<episode-num system="xmltv_ns">2025.120.</episode-num>
<category lang="en">Series</category>
```

XMLTV uses zero-based season and episode numbers, so Plex displays that value as Season 2026, Episode 121. The proxy never guesses that `E121` means Season 1, Episode 21.

## Features

- Standard-library Python runtime with no application dependencies.
- Runs as a small non-root container with a read-only root filesystem.
- Preserves channel IDs, channel order, schedules, titles, subtitles, descriptions, icons, ratings, and existing episode metadata.
- Opt-in matching by series title or provider category.
- Explicit movie-category denylist.
- Stable repeat identity backed by SQLite.
- Atomic last-known-good XMLTV output.
- Primary/fallback sources with exact ordered-channel-topology validation.
- Bounded downloads and timeouts.
- Health and XMLTV endpoints suitable for Docker and TrueNAS monitoring.
- Optional non-playable metadata-only tuner for isolated Plex parser testing.

## Quick start with Docker Compose

Requirements: Docker with Compose v2 and an existing HTTP or HTTPS XMLTV URL.

```sh
git clone https://github.com/zipkindev/plex-xmltv-enricher.git
cd plex-xmltv-enricher
cp config/config.example.toml config/config.toml
mkdir -p state
sudo chown 65532:65532 state
docker compose up -d --build
```

Edit `config/config.toml` before starting. At minimum, set `primary_url` and the enrichment rules appropriate for your feed.

Verify the service:

```sh
curl --fail http://127.0.0.1:9193/healthz
curl --fail --output guide.xml http://127.0.0.1:9193/xmltv/plex.xml
```

The container listens on port `9193` by default.

## Configuration

Configuration is a bind-mounted TOML file. See [`config/config.example.toml`](config/config.example.toml) for a complete example.

```toml
[source]
primary_url = "http://xmltv-primary.example/guide.xml"
fallback_url = "http://xmltv-fallback.example/guide.xml"
timeout_seconds = 30
max_bytes = 33554432
refresh_seconds = 900

[server]
listen_host = "0.0.0.0"
port = 9193

[storage]
state_dir = "/state"

[enrichment]
series_categories = ["Series", "Serie", "Kochdokusoap"]
series_titles = ["Example Daily Show"]
movie_categories = ["Film", "Movie", "Spielfilm", "Kinofilm", "Kurzfilm"]

[test_tuner]
enabled = false
```

Matching is case-insensitive. Begin with explicit `series_titles`, inspect the output, and only then add provider categories you know are exclusively episodic. Generic labels such as `Drama` and `Reality` are intentionally absent from the default rules because providers may also assign them to films or specials.

Source URLs must not contain embedded HTTP credentials. Put authentication in a trusted upstream proxy if it is required. The application deliberately avoids storing or logging provider secrets.

## Enrichment rules

The rules are applied in this order:

1. Existing `xmltv_ns` or `original-air-date` episode markers are preserved without modification.
2. A valid configured `S03E08` value gains the zero-based `xmltv_ns` value `2.7.`.
3. A configured episodic programme with `E121` and `<date>2026</date>` gains `2025.120.`. The year acts as the season only when both values are valid.
4. A configured episodic programme with a subtitle but no trustworthy number receives a durable first-seen `original-air-date` identity. Later airings with the same normalized title and subtitle reuse that identity.
5. Any record carrying a configured movie category is refused, even if another rule would otherwise match it.
6. Ambiguous records remain untouched.

The durable first-seen date is a parser identity fallback, not a claim that the scheduled airing was the episode's historical premiere. A future provider-specific adapter can replace this fallback when it has an unambiguous authoritative match.

## Connect Plex

Use this URL as the XMLTV guide during Plex Live TV & DVR setup:

```text
http://YOUR-CONTAINER-HOST:9193/xmltv/plex.xml
```

Keep your real tuner or IPTV proxy configured exactly as it is. This service supplies guide metadata only; it does not proxy video and does not increase tuner capacity.

For an existing DVR, back up its device, lineup, channel-mapping, and recording-rule configuration before changing the guide. Confirm that the enriched feed exposes the same channel IDs and order, refresh the guide, and verify several programmes before removing the prior guide association. Plex may briefly show entries from both guides while its old guide cache clears.

## TrueNAS SCALE

The project can be deployed as a Custom App using the service definition in [`compose.yaml`](compose.yaml).

Create persistent host paths for:

- `/config/config.toml`: read-only configuration file.
- `/state`: writable state directory owned by UID/GID `65532`.

Publish container port `9193` on the desired TrueNAS address. No media directory, tuner device, provider credentials, privileged mode, host networking, or Docker socket is required. Configuration remains editable through the mounted TOML file and normal TrueNAS host-path settings.

## HTTP endpoints

| Endpoint | Purpose |
| --- | --- |
| `/healthz` | Refresh age, active source, channel count, programme count, and enrichment count. |
| `/xmltv/plex.xml` | Current atomic last-known-good enriched XMLTV document. |

If the source is invalid or unavailable, the last valid document remains installed. A fallback source is accepted only after a primary topology has been established and only when its ordered channel IDs and display names match exactly.

## Metadata-only Plex test tuner

Setting `[test_tuner] enabled = true` exposes an HDHomeRun-compatible test endpoint under `/test-tuner`. Its stream URLs always return HTTP 503, so it cannot play or record video and carries no provider credentials.

This mode is intended for controlled Plex parser tests. The current sample lineup uses channel IDs `1029`, `1035`, and `1049`; leave it disabled for ordinary deployments unless those IDs exist in the feed or you are adapting the source for your own test.

## Run without Compose

```sh
docker build -t plex-xmltv-enricher:0.1.0 -f Containerfile .
docker run -d \
  --name plex-xmltv-enricher \
  --restart unless-stopped \
  --user 65532:65532 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:size=16m,mode=1777 \
  -p 9193:9193 \
  -v "$PWD/config/config.toml:/config/config.toml:ro" \
  -v "$PWD/state:/state" \
  plex-xmltv-enricher:0.1.0
```

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

The test suite uses synthetic XMLTV and does not contact production services.

## Project status and limitations

- Version `0.1.0` implements conservative rule-based enrichment; it is not a general metadata scraper.
- The proxy cannot invent authoritative season/episode numbers when the source lacks them.
- A provider's use of `<date>` must be understood before enabling bare-episode conversion for broad categories.
- Channel topology is intentionally immutable. A legitimate provider channel change requires review rather than silent fallback acceptance.
- Always test guide changes before replacing a working Plex lineup.

## License

MIT. See [`LICENSE`](LICENSE).
