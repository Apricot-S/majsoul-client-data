# Output formats

See the [README](../README.md) for basic usage and the
[command reference](commands.md) for processing rules and resource limits.
Paths below are relative to each stage's destination. Defaults are relative to
the current working directory; no timestamp or ID subdirectory is added.

## Fetched snapshot

```text
fetched/
  metadata/
    client-bundle-settings.json
    warehouse-settings.json
    bundle_hash.txt
    bundle_info_so.majset
  bundles/
    <selected bundle name>
  manifest.json
```

Downloaded bodies are saved unchanged. With `fetch --metadata-only`, `bundles/`
is omitted and the bundle index is not parsed.

The fetch manifest records:

| Field                                 | Meaning                                                                 |
| ------------------------------------- | ----------------------------------------------------------------------- |
| `schema_version`                      | Manifest format version                                                 |
| `snapshot_id`                         | Snapshot identifier                                                     |
| `tool_version`                        | Tool version used for fetching                                          |
| `retrieved_at`                        | UTC retrieval timestamp                                                 |
| `platform`, `texture_profile`         | WebGL platform and selected texture profile                             |
| `bundle_base_url`, `profile_base_url` | Warehouse and profile download locations                                |
| `bundle_hash`                         | Downloaded bundle hash                                                  |
| `stages.fetch`                        | Completion state and scope: `bundles` or `metadata`                     |
| `files`                               | Saved file records with `name`, source `url`, byte `size`, and `sha256` |
| `selected_bundles`                    | Selected bundle names, saved `file` paths, and target asset paths       |

`files[].name` is snapshot-relative, such as `metadata/...` or `bundles/...`.
After successful extraction, the source manifest also contains `stages.extract`
with `status: complete`, the extracted file count, and the extraction manifest
location. References to extraction destinations outside the snapshot are absolute.

## Extracted data

```text
extracted/
  LuaByte/Lua/Excels/...
  LuaByte/Lua/Protol/...
  MyAssets/docs/proto_config.bytes
  MyAssets/docs_version/version.json
  manifest.json
```

Lua filenames change from `.lua.bytes` to `.lua`. Plain Lua is preserved and
encoded Lua is decoded with repeating XOR. RPC mapping and data-version JSON
remain unchanged.

The extraction manifest records `schema_version`, `extraction_id`, `tool_version`,
`extracted_at`, `source_snapshot_id`, `source_bundle_hash`,
`source_manifest_sha256`, and `docs_version`. `stages.extract.status` is
`complete`. Each `files` entry records its relative `name`, byte `size`, `sha256`,
`source_asset`, `source_bundle`, and `transform` (`lua-xor` or `none`).

## Converted data

```text
converted/
  tables/<table name>/<sheet name>.json
  tables/index.json
  protocol/schema.json
  protocol/services.json
  protocol/unresolved_rpcs.json
  protocol/proto/*.proto
  manifest.json
```

### Configuration tables

Configuration JSON expands column definitions, defaults, omitted ranges, and
split data. Translation-array references are replaced with strings. Ordinary
translation columns become language-keyed objects, for example
`"name": {"jp": "...", "en": "..."}`. Direct language columns become strings.
Unset translation references (`nil` or `0`) are omitted. Missing references or
positive indices outside the translation array stop conversion.

### Protocol files

| File                   | Contents                                                      |
| ---------------------- | ------------------------------------------------------------- |
| `schema.json`          | Messages, enums, fields, and type references                  |
| `services.json`        | Request/response type names for every call in the RPC mapping |
| `unresolved_rpcs.json` | Calls whose types are missing from the extracted definitions  |
| `proto/*.proto`        | Generated proto3 wire definitions                             |

Generated protos do not restore original comments or custom options.
`services.proto` contains only calls with resolved request and response types;
it is omitted when no calls can be resolved. Such output cannot satisfy
`merge-proto`'s requirement for all eight communication modules.

Counts `rpcs`, `resolved_rpcs`, and `unresolved_rpcs` in the conversion report and
manifest distinguish all mapped calls from generated service calls.

The conversion manifest records `schema_version`, `conversion_id`, `tool_version`,
`converted_at`, `source_snapshot_id`, `source_extraction_id`, `source_bundle_hash`,
`source_manifest_sha256`, and `docs_version`. `stages.convert` records completion
and counts. Each `files` entry contains a relative `name`, byte `size`, and
`sha256`. Conversion does not modify the extraction manifest.

## Data version and integrity

`docs_version` is the `version` string in the bundle's
`MyAssets/docs_version/version.json`. It is `null` when that asset is absent.
The asset must contain valid JSON and a nonempty string version when present.
This value is carried through extraction and conversion manifests.

It is distinct from the Unity engine version and tool version. The HTML's
`productVersion` is not used by the saved-data pipeline. Compare recorded hashes
to determine content identity; a version label alone is not sufficient.

Stage identifiers and source-manifest hashes retain provenance, while per-file
sizes and SHA-256 hashes allow downstream stages to verify saved inputs.

## Merged protocol

`merge-proto` produces one file, `liqi.proto` by default, without another manifest.
It uses a single `syntax = "proto3";` and `package lq;`, preserving the selected
modules' type names, field numbers, and RPC definitions. See
[merge-proto](commands.md#merge-proto) for included/excluded modules and validation.

Its stdout JSON reports `input_dir`, `output_file`, `source_conversion_id`,
`source_manifest_sha256`, `included_protos`, `excluded_protos`, `unresolved_rpcs`,
`size`, and `sha256`. Split proto files and the conversion manifest are unchanged.

## Inspection JSON

`inspect` prints JSON without writing files:

| Field             | Meaning                                                          |
| ----------------- | ---------------------------------------------------------------- |
| `client_url`      | Official JP HTML URL                                             |
| `client`          | JP Unity loader and product version information parsed from HTML |
| `loader_url`      | Loader URL from HTML; the loader itself is not fetched           |
| `client_settings` | Default JP settings URL with `status: not_requested`             |
| `requested_urls`  | URLs actually requested                                          |

## Stage result JSON

`fetch` reports `snapshot_dir` and the fetch `manifest`. `extract` reports
`snapshot_dir`, `extracted_dir`, `manifest_path`, and `file_count`. `convert`
reports `input_dir`, `converted_dir`, `manifest_path`, `file_count`, and conversion
counts.

Successful `run` output has `status: complete` and stage results under
`stages.fetch`, `stages.extract`, and `stages.convert`. Optional merging adds
`stages.merge-proto`. Stage manifests use the same format as individual commands.
On failure, no pipeline success JSON is printed; already completed stages remain
saved.
