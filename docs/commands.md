# Command reference

See the [README](../README.md) for setup and a quick start, and
[output formats](output-formats.md) for generated files and provenance records.
Examples use sh and `majsoul-client-data`; the same subcommands and options work with Docker.

## Shared behavior

All relative paths are resolved from the current working directory. Stage output
options identify the destination itself, without an added timestamp/ID directory.
Absolute paths and mounted directories are supported. Symbolic links are resolved
before destination validation. Files and paths below files are invalid directory
destinations. Extraction and conversion reject overlapping input/output roots and
input paths that escape their saved root.

Existing outputs require `--overwrite`. Directory stages generate temporary
results and replace the entire destination after successful validation/generation.
Obsolete files are removed. Ordinary failures restore previous results; forced
termination and power loss are not covered by an atomicity guarantee. `.partial-*`
areas are incomplete. Merging replaces only its output file after success.

Success prints JSON and exits with 0. Processing errors print to stderr and exit
with 1; argument errors exit with 2. A subcommand is required. Use `--help` or
`<command> --help` to list accepted options.

### HTTP policy

`inspect`, `fetch`, and the fetch stage of `run` use sequential requests with at
least two seconds from the end of one request to the next. `--timeout` accepts a
positive finite number of seconds (default: 20). Retries and redirects are
disabled. HTTP 403/429, timeouts, invalid formats, or limits stop processing;
other hosts or profiles are not tried automatically. These safeguards do not
guarantee protection against IP bans.

## inspect

```sh
majsoul-client-data inspect --timeout 20
```

Makes exactly one GET to `https://game.mahjongsoul.com/`. The URL is fixed to the
JP official page. It prints client/loader information without saving files or
fetching the loader, settings, or bundles. The response is limited to 1 MiB,
including after decompression. Only `--timeout` is accepted in addition to help.
See [inspection JSON](output-formats.md#inspection-json) for output fields.

## fetch

```sh
majsoul-client-data fetch --texture-profile DXT
majsoul-client-data fetch --metadata-only
majsoul-client-data fetch --output-dir output/jp --overwrite
```

| Option                         | Default              | Meaning                                                        |
| ------------------------------ | -------------------- | -------------------------------------------------------------- |
| `--client-settings-url URL`    | JP release URL below | Override settings with an HTTPS URL, including a versioned URL |
| `--texture-profile {ASTC,DXT}` | `DXT`                | Choose the warehouse texture path                              |
| `--timeout SECONDS`            | `20`                 | HTTP timeout                                                   |
| `--metadata-only`              | Disabled             | Save four metadata files without parsing the bundle index      |
| `--output-dir PATH`            | `./fetched`          | Snapshot destination                                           |
| `--overwrite`                  | Disabled             | Replace the entire existing snapshot after success             |

The default settings URL is
`https://appstatic.mahjongsoul.com/v4/jp/clientbundlesettings/jp-release.json`.
Fetch downloads two settings JSON files, a hash file, and the bundle index. Normal
fetch parses the index with UnityPy and downloads each selected bundle once.
HTML, loader code, and WASM are not fetched.

Targets are bundles containing:

- `LuaByte/Lua/Excels/*.lua.bytes`: configuration and translations.
- `LuaByte/Lua/Protol/*.lua.bytes`: protocol definitions.
- `MyAssets/docs/proto_config.bytes`: RPC mapping.
- `MyAssets/docs_version/version.json`: data version.

Selection includes matching files in subdirectories. Assets sharing a selected
bundle may also be transferred. Bundles for scenarios, UI configuration, or Spine
data are outside the selection scope. Bodies are saved unchanged; bundle headers
are checked, but fetch does not extract TextAssets, decode Lua, or convert data.
The warehouse path uses `WebGL/DXT/` or `WebGL/ASTC/`.

`--metadata-only` makes at most four GETs and saves only metadata and a manifest.
It checks the index's Unity header without parsing its contents or fetching
individual bundles. Such a snapshot cannot be used by `extract`.

| Resource                     | Limit                            |
| ---------------------------- | -------------------------------- |
| Each settings/hash response  | 1 MiB                            |
| Bundle index                 | 32 MiB                           |
| Selected bundles             | 64                               |
| Each selected bundle         | 64 MiB                           |
| Total selected bundle bodies | 256 MiB                          |
| GET requests                 | 68 normally; 4 for metadata-only |

Limits are not automatically increased. Invalid destinations are rejected before
network access; completed snapshots are published only after all downloads and
validation succeed.

### UnityFS support

Index parsing and extraction support UnityFS. Expanded block information and data
are limited to 128 MiB per bundle, and LZMA dictionaries to 64 MiB. Nested
containers and unsupported compression/encryption are rejected.

## extract

```sh
majsoul-client-data extract
majsoul-client-data extract \
  --input-dir output/jp --output-dir output/decoded --overwrite
```

Options: `--input-dir` (default `./fetched`), `--output-dir` (default
`./extracted`), and `--overwrite`.

Runs offline. It verifies the saved index and bundles against their recorded sizes
and SHA-256 hashes, then extracts target TextAssets. Plain Lua is preserved;
repeating-XOR Lua is decoded. RPC bytes and version JSON are saved unchanged.
Unicode whitespace and formatting characters in translations are preserved.
Lua is never executed.

Lua bytecode, unknown formats, missing TextAssets, and ambiguous names are errors.
If multiple supported name representations exist, extraction fails rather than
choosing a preferred one. Plain/decoded Lua undergoes full lexical and bracket
checks, which are not a complete Lua syntax validation.

On success, extraction updates the source snapshot's `stages.extract` record with
completion, file count, and the extraction manifest location. Failures preserve
the source manifest and previous extraction. Outputs inside the input's
`bundles/` or `metadata/` directories are rejected.

| Resource                      | Limit         |
| ----------------------------- | ------------- |
| Input manifest                | 4 MiB         |
| Input bundle count/size/total | Same as fetch |
| Expanded UnityFS bundle       | 128 MiB       |
| Each TextAsset                | 16 MiB        |
| Total extracted output        | 256 MiB       |
| Extracted file count          | 16,384        |

## convert

```sh
majsoul-client-data convert
majsoul-client-data convert \
  --input-dir output/decoded --output-dir output/json --overwrite
```

Options: `--input-dir` (default `./extracted`), `--output-dir` (default
`./converted`), and `--overwrite`.

Runs offline without executing Lua. It verifies extraction completion and every
input's size/SHA-256, expands configuration columns, defaults, omitted ranges,
and split data, and resolves translation references. Missing references and
out-of-range positive translation indices are errors. See
[configuration tables](output-formats.md#configuration-tables) for JSON behavior.

Protocol conversion produces JSON descriptions and proto3 wire definitions.
Ambiguous type references and unsupported descriptions stop processing.
Unresolved RPC types are retained in JSON and excluded from generated services.
Protoc is not required. The input manifest is not updated by conversion.

Outputs inside input `LuaByte/` or `MyAssets/` directories are rejected.

| Resource                            | Limit             |
| ----------------------------------- | ----------------- |
| Input manifest                      | 4 MiB             |
| Each input file                     | 16 MiB            |
| Total input / file count            | 256 MiB / 16,384  |
| Lua tokens per file / nesting depth | 2,000,000 / 64    |
| Configuration columns / rows        | 4,096 / 1,000,000 |
| Cells across all tables             | 8,000,000         |
| Total output bodies                 | 256 MiB           |

## merge-proto

```sh
majsoul-client-data merge-proto
majsoul-client-data merge-proto \
  --input-dir output/converted --output output/liqi.proto --overwrite
```

Options: `--input-dir` (default `./converted`), `--output` (default
`./liqi.proto`), and `--overwrite`.

Runs offline and verifies conversion completion and the size/SHA-256 of every
proto recorded in the conversion manifest. It supports this tool's conversion
outputs, rather than arbitrary proto inputs. Original split protos and their
manifest remain unchanged. No separate output manifest is created.

The eight required modules are merged in this order: `com_struct`,
`amulet_struct`, `liqi_struct`, `cli_game`, `cli_lobby`, `cli_route`,
`notify_lobby`, and `services`. A single `syntax = "proto3";` and `package lq;`
are emitted, imports between these modules are removed, and definition bodies
are concatenated. Type names, field numbers, and RPCs are preserved.
`config.proto`, `excel.proto`, `client.proto`, and `com_const.proto` are excluded.
Unresolved RPCs are not filled in, and older definitions are not restored.

Missing required files, syntax/package mismatches, imports outside the merge
set, name collisions, and unknown proto files stop merging. Total input proto
size and output size are each limited to 16 MiB. Output inside the input
directory and replacing a directory are rejected.

To generate Python code separately with protoc:

```sh
protoc --proto_path=. --python_out=. liqi.proto
```

Using `liqi_pb2.py` requires a compatible Python protobuf runtime. Generated
classes and `DESCRIPTOR` can be accessed from a single module.

## run

```sh
majsoul-client-data run
majsoul-client-data run --overwrite
majsoul-client-data run --merge-proto
majsoul-client-data run --merge-proto output/liqi.proto
majsoul-client-data run \
  --output-dir output/fetched \
  --extracted-dir output/extracted \
  --converted-dir output/converted
```

Runs `fetch → extract → convert`, followed by merging when `--merge-proto` is
present. Fetch options `--client-settings-url`, `--texture-profile`, and
`--timeout`, including defaults and limits, apply here too.

| Option                   | Default                                             | Meaning                                                      |
| ------------------------ | --------------------------------------------------- | ------------------------------------------------------------ |
| `--output-dir PATH`      | `./fetched`                                         | Fetch destination                                            |
| `--extracted-dir PATH`   | `./extracted`                                       | Extraction destination                                       |
| `--converted-dir PATH`   | `./converted`                                       | Conversion destination                                       |
| `--merge-proto [OUTPUT]` | Disabled; `./liqi.proto` when passed without a path | Also merge communication protocols                           |
| `--overwrite`            | Disabled                                            | Allow replacement of each stage and the optional merged file |

`--metadata-only` and `--input-dir` are not accepted. Duplicate/overlapping stage
paths and existing destinations without `--overwrite` are rejected before any
requests. Optional merge output collisions, existing files, and overlap with
stage directories are also checked before requests.

Each stage commits independently. On failure, later stages stop and no success
JSON is printed. Completed upstream results remain; the failed stage preserves
its previous outputs through its own recovery logic. There is no whole-pipeline
rollback or automatic resume. Continue with `extract`, `convert`, or `merge-proto`
on saved inputs as appropriate.
