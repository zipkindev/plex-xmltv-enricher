# plex-xmltv-enricher guardrails

- Preserve all source channel IDs, names, ordering, programme channel/timing/title/subtitle/description/icon fields.
- Enrichment is append-only and conservative. Never turn a configured movie or ambiguous programme into a series.
- Never interpret a bare value such as `E121` as `S01E21`.
- The service never opens media streams or stores provider credentials.
- Primary/fallback failover requires identical ordered channel topology.
- Last-known-good output is atomic and durable. Invalid input never replaces it.
- Tests use synthetic XMLTV and must not contact production services.
- Keep this repository standalone; it must not import the migration or normalizer repositories.
