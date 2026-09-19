# plex2radarr

Safely reconcile movies that exist in Plex but are missing from Radarr.

`plex2radarr` is designed for legacy movie libraries where Plex can see files that Radarr does not manage yet, including files that qBittorrent is still seeding directly from the Plex library tree.

The tool is **dry-run by default**. It will not move torrent data or trigger Radarr imports unless you explicitly run it with `--execute`.

## What it does

For each selected movie in a configured Plex library, plex2radarr:

1. Reads the movie and its media-file path from Plex.
2. Uses TMDb/IMDb identifiers to determine whether the movie already exists in Radarr.
3. Skips movies already managed by Radarr.
4. Checks all configured qBittorrent instances for ownership of the exact Plex file.
5. Plans the safest migration:
   - **Seeded file:** relocate the torrent through qBittorrent first, then Radarr-import with `copy` so Radarr can hard-link it.
   - **Unseeded legacy file already inside the Radarr/Plex root:** Radarr-import with `move`, which removes the nonconforming Plex-visible path instead of creating a duplicate.
   - **Unseeded file outside the Radarr/Plex root:** Radarr-import with `copy` so hardlinking can be used when possible.
6. Adds the movie to Radarr with `searchForMovie: false`.
7. Waits for Radarr's ManualImport command to complete.

With no file arguments, the whole configured Plex library is evaluated. You can also target one file, several files, or a text file containing a batch of paths.

For a seeded legacy movie, the intended end state is:

```text
/torrents/...release-name...mkv
        │
        │ hardlink
        ▼
/_Movies/The Matrix (1999)/...Radarr-name...mkv
```

qBittorrent continues seeding the torrent-path file, while Radarr and Plex use the organized library path.

## Safety model

- **Dry-run is the default.**
- There is no config option that enables writes; mutating behavior requires `--execute` every time.
- File targeting is exact. A requested path that is not found in the configured Plex library aborts the plan instead of being silently ignored.
- qBittorrent-owned payloads are moved by qBittorrent itself, never with a direct Python filesystem move.
- Seeded files are imported into Radarr with `importMode=copy`.
- Unseeded files inside the library root use `importMode=move` to prevent duplicate Plex-visible paths.
- Radarr additions never automatically search for a replacement download.
- Ambiguous Radarr identities and multiple qBittorrent owners are skipped rather than guessed.
- Multiple qBittorrent instances are supported.
- API/container path mappings are supported in both directions.

**Review the complete dry-run before using `--execute`. Back up anything you cannot replace.**

## Requirements

- Python 3.10+
- Plex
- Radarr
- qBittorrent for seeded-file relocation
- Radarr **Use Hardlinks instead of Copy** enabled if you want hardlinked copy imports
- Source and Radarr destination on the same filesystem for hardlinks

## Installation

```bash
git clone https://github.com/pitchy3/plex2radarr.git
cd plex2radarr

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Development dependencies:

```bash
pip install -e ".[dev]"
```

## Configuration

```bash
cp config.example.yaml config.yaml
```

Secrets can be literal values or environment-variable placeholders such as `${PLEX_TOKEN}`.

```yaml
plex:
  url: http://127.0.0.1:32400
  token: ${PLEX_TOKEN}
  library: Movies

radarr:
  url: http://127.0.0.1:7878
  api_key: ${RADARR_API_KEY}
  root_folder: /movies
  quality_profile: Any
  monitored: false

qbittorrent:
  - name: main
    url: http://127.0.0.1:8080
    username: ${QBIT_USERNAME}
    password: ${QBIT_PASSWORD}
    relocation_root: /downloads/movies

path_mappings:
  - service: plex
    remote: /media/movies
    local: /data/_Movies

  - service: radarr
    remote: /movies
    local: /data/_Movies

  - service: qbittorrent:main
    remote: /downloads
    local: /data/torrents
```

`radarr.root_folder` must be the path as **Radarr sees it**. Each qBittorrent `relocation_root` must likewise be the path as that **qBittorrent instance sees it**. Path mappings translate those service paths to the local filesystem path visible to plex2radarr.

### Multiple qBittorrent instances

```yaml
qbittorrent:
  - name: 1080p
    url: http://127.0.0.1:8080
    username: user
    password: pass
    relocation_root: /downloads/movies-1080p

  - name: 4k
    url: http://127.0.0.1:18080
    username: user
    password: pass
    relocation_root: /downloads/movies-4k
```

Use matching path mappings when those instances expose different container paths.

plex2radarr acts only when exactly one qBittorrent torrent matches the exact Plex file path. If more than one matches, it skips the movie.

## Usage

### Entire Plex library

Dry run:

```bash
plex2radarr
```

Execute:

```bash
plex2radarr --execute
```

### One file

Dry run just one Plex movie file:

```bash
plex2radarr "/data/_Movies/The.Matrix.1999.1080p.BluRay/The.Matrix.1999.1080p.BluRay.mkv"
```

Execute just that file:

```bash
plex2radarr --execute "/data/_Movies/The.Matrix.1999.1080p.BluRay/The.Matrix.1999.1080p.BluRay.mkv"
```

### Several files

Pass multiple paths directly:

```bash
plex2radarr \
  "/data/_Movies/Movie.One/movie1.mkv" \
  "/data/_Movies/Movie.Two/movie2.mkv"
```

Add `--execute` to perform the displayed plan:

```bash
plex2radarr --execute \
  "/data/_Movies/Movie.One/movie1.mkv" \
  "/data/_Movies/Movie.Two/movie2.mkv"
```

### File list

For a larger batch, create a text file with one movie path per line:

```text
# movies-to-migrate.txt
/data/_Movies/Movie.One/movie1.mkv
/data/_Movies/Movie.Two/movie2.mkv
/data/_Movies/Movie.Three/movie3.mkv
```

Dry run:

```bash
plex2radarr --files-from movies-to-migrate.txt
```

Execute:

```bash
plex2radarr --execute --files-from movies-to-migrate.txt
```

Blank lines and lines beginning with `#` are ignored. Direct file arguments and `--files-from` can be combined; duplicate paths are processed only once.

The paths you supply are paths visible to the machine running plex2radarr, after any configured Plex path mapping.

### Other options

Custom config:

```bash
plex2radarr --config /path/to/config.yaml
```

Verbose logging:

```bash
plex2radarr --verbose
```

## Reconciliation behavior

| Situation | Planned action |
|---|---|
| Movie already exists in Radarr | Skip |
| No TMDb/IMDb ID | Skip |
| Multiple Radarr identity matches | Skip |
| Exactly one qBittorrent torrent owns the file | qBittorrent relocation → Radarr copy/hardlink import |
| No qBittorrent owner; file is inside Radarr library root | Radarr move import |
| No qBittorrent owner; file is outside Radarr library root | Radarr copy/hardlink import |
| Multiple qBittorrent torrents own the file | Skip |
| Requested file is not present in Plex | Abort selection with an error |
| Source file cannot be accessed during execute | Skip |

## Why qBittorrent relocation comes first

Given:

```text
_Movies/The.Matrix.1999.1080p.BluRay/
└── The.Matrix.1999.1080p.BluRay.mkv
```

if qBittorrent seeds that exact path, hardlinking it immediately into a new Radarr-standard directory would leave two Plex-visible movie paths.

plex2radarr first calls qBittorrent's set-location operation. After qBittorrent reports the new save path, plex2radarr re-queries its file list to discover the payload's actual relocated path. Radarr then imports from that torrent path with copy mode, allowing its normal hardlink behavior.

## Path mappings

Mappings are prefix-based and case-sensitive. They work in both directions:

```text
Radarr sees:       /movies/The Matrix (1999)/movie.mkv
Host sees:         /data/_Movies/The Matrix (1999)/movie.mkv
```

Supported service names:

- `plex`
- `radarr`
- `qbittorrent:<configured-name>`

## Hardlink verification

```bash
stat -c '%d %i %h %n' \
  "/data/torrents/movies/.../movie.mkv" \
  "/data/_Movies/The Matrix (1999)/.../movie.mkv"
```

The device ID and inode should match.

## Current limitations

- Hardlinks cannot span filesystems.
- Plex items with more than one local movie file/version are skipped.
- Extras, trailers, subtitles, and sidecars are not migrated in the first release.
- The first release does not attempt fuzzy filename-to-torrent matching; torrent ownership must match the exact translated file path.
- Radarr naming is controlled by your Radarr Media Management configuration.

## Development

```bash
ruff check .
pytest
```

GitHub Actions runs both checks on Python 3.10, 3.11, 3.12, and 3.13.

## License

No license has been selected yet.
