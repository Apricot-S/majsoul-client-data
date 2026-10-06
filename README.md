# majsoul-client-data

Fetch, extract and convert Mahjong Soul client configuration and protocol data.

The tool targets the JP client. Bulk downloading, extracting, or converting
individual images, audio, and other media assets is outside its scope.

## Installation

Requires Python **3.12 or later**. Install from the repository directory with
pip or [uv](https://docs.astral.sh/uv/):

```sh
# With pip.
majsoul-client-data$ pip install .
# With uv.
majsoul-client-data$ uv tool install .
```

## Usage

```sh
majsoul-client-data run --merge-proto
```

This runs `fetch → extract → convert → merge-proto`. Outputs are saved relative to
your current working directory:

| Path         | Contents                                                              |
| ------------ | --------------------------------------------------------------------- |
| `fetched/`   | Downloaded metadata, selected bundles, and a manifest                 |
| `extracted/` | Decoded Lua, RPC mapping, data version, and a manifest                |
| `converted/` | Configuration JSON, protocol JSON, split proto3 files, and a manifest |
| `liqi.proto` | Merged communication protocol definitions                             |

Omit `--merge-proto` if you only need the converted data.

See [output formats](docs/output-formats.md) for file layouts and manifest details.

### Run with Docker

```sh
docker build -t majsoul-client-data .
mkdir -p output
docker run --rm \
  --mount "type=bind,source=$(pwd)/output,target=/work" \
  majsoul-client-data run --merge-proto
```

This example saves outputs in the host's `output/` directory.

The container runs as a non-root user (UID 10001); the mounted directory must be
writable. On Linux hosts, add `--user "$(id -u):$(id -g)"` to `docker run` to use
your host user's UID and GID.

## Commands

| Command       | Purpose                                                               | Network access   |
| ------------- | --------------------------------------------------------------------- | ---------------- |
| `inspect`     | Read JP client information from the official HTML page; save no files | One GET          |
| `fetch`       | Save metadata and bundles containing the target data                  | Yes              |
| `extract`     | Validate saved bundles and extract/decode target TextAssets           | No               |
| `convert`     | Convert extracted configuration and protocol data                     | No               |
| `merge-proto` | Merge converted communication protocols into `liqi.proto`             | No               |
| `run`         | Run fetch, extraction, and conversion, with optional merging          | Fetch stage only |

Run stages separately to process saved inputs:

```sh
majsoul-client-data inspect
majsoul-client-data fetch
majsoul-client-data extract
majsoul-client-data convert
majsoul-client-data merge-proto
```

Use `--help` or `<command> --help` for arguments. See the
[command reference](docs/commands.md) for options, processing rules, and limits.

## Output locations and reruns

Use separate destinations to retain multiple snapshots:

```sh
majsoul-client-data run \
  --output-dir output/fetched \
  --extracted-dir output/extracted \
  --converted-dir output/converted \
  --merge-proto output/liqi.proto
```

Existing destinations require `--overwrite`, which replaces the entire destination
after that stage succeeds.

Updating a stage does not automatically update downstream outputs. After a new
fetch, rerun extraction and conversion in order. To redo only conversion:

```sh
majsoul-client-data convert --overwrite
```

`run` stops at the first failure. Use individual commands to continue from saved
inputs. Input and output directories must not overlap.

## Network behavior and limitations

Requests run sequentially, with at least two seconds between requests. The default
timeout is 20 seconds, configurable with `--timeout`. Automatic retries are disabled.

`fetch` downloads only bundles containing configuration/translation Lua,
protocol Lua, RPC mapping, or the data version. Other assets may be transferred
if they share a selected bundle. Scenario, UI configuration, and Spine data are
not download targets.

## Exit codes

- `0`: Success. Commands print JSON to standard output.
- `1`: Processing failure. An error is printed to standard error.
- `2`: Invalid arguments, including running without a subcommand.

## Documentation

- [Command reference](docs/commands.md)
- [Output formats](docs/output-formats.md)

## License

Copyright (c) Apricot S. All rights reserved.

Licensed under the [MIT license](LICENSE).
