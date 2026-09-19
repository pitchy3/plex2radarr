# plex2radarr

Safely reconcile movies that exist in Plex but are missing from Radarr.

`plex2radarr` is designed for legacy movie libraries where Plex can see files that Radarr does not manage yet, including files that qBittorrent is still seeding directly from the Plex library tree.

The tool is **dry-run by default**. It will not move torrent data or trigger Radarr imports unless you explicitly run it with `--execute`.

## What it does

For each movie in a configured Plex library, plex2radarr:

1. Reads the movie and its media-file path from Plex.
2. Uses TMDb/IMDb identifiers to determine whether the movie already exists in Radarr.
3. Skips movies already managed by Radarr.
4. Checks configured qBittorrent instances to determine whether the Plex file belongs to an existing torrent.
5. Plans a safe reconciliation:
   - If qBittorrent owns the file, relocate the torrent through qBittorrent to a dedicated torrent/download root first.
   - Add the movie to Radarr without searching for a replacement download.
   - Ask Radarr to manually import the relocated/existing file using **copy mode** so Radarr can hard-link it when hardlinks are enabled and the paths share a filesystem.
6. Verifies the result where possible.

This produces the desired end state:

```text
/torrents/...release-name...mkv
        │
        │ hardlink
        ▼
/_Movies/The Matrix (1999)/...Radarr-name...mkv
```

qBittorrent continues seeding the torrent-path file, while Radarr and Plex use the organized library path. Only one set of file data is stored when hardlinks are possible.

## Safety model

Safety is the primary design goal.

- **Dry-run is the default.** Running `plex2radarr` with no execution flag only reports what it would do.
- File/torrent movement requires the explicit `--execute` command-line flag.
- Radarr movie additions use `searchForMovie: false`.
- Radarr manual imports explicitly request `importMode: copy`; they do not intentionally move the seeding source out from under qBittorrent.
- qBittorrent-owned payloads are relocated by qBittorrent itself rather than by Python filesystem moves.
- Ambiguous movie matches, ambiguous torrent ownership, missing IDs, inaccessible files, and unsafe destination collisions are skipped instead of guessed.
- Multiple qBittorrent instances are supported.
- Optional path mappings allow API/container paths to be translated into paths visible to this Python process.

**Before using `--execute`, review the full dry-run output and back up anything you cannot replace.**

## Requirements

- Python 3.11+
- Plex
- Radarr
- qBittorrent (optional, but required to safely relocate files that are actively seeded)
- Radarr configured with **Use Hardlinks instead of Copy** if you want zero-extra-space imports
- Torrent/download storage and the Radarr library on the same filesystem for hardlinks

## Installation

Clone the repository and install it into a virtual environment:

```bash
git clone https://github.com/pitchy3/plex2radarr.git
cd plex2radarr

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development/test dependencies:

```bash
pip install -e ".[dev]"
```

## Configuration

Copy the example configuration:

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml`.

Secrets may be supplied literally or with environment-variable placeholders such as `${PLEX_TOKEN}`. Environment-variable placeholders are expanded when the configuration is loaded.

Example:

```yaml
plex:
  url: http://127.0.0.1:32400
  token: ${PLEX_TOKEN}
  library: Movies

radarr:
  url: http://127.0.0.1:7878
  api_key: ${RADARR_API_KEY}
  root_folder: /data/_Movies
  quality_profile: Any
  monitored: false

qbittorrent:
  - name: main
    url: http://127.0.0.1:8080
    username: ${QBIT_USERNAME}
    password: ${QBIT_PASSWORD}
    relocation_root: /data/torrents/movies

path_mappings:
  # Optional. First matching prefix wins.
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

### Multiple qBittorrent instances

Add as many entries as required:

```yaml
qbittorrent:
  - name: 1080p
    url: http://127.0.0.1:8080
    username: user
    password: pass
    relocation_root: /data/torrents/movies-1080p

  - name: 4k
    url: http://127.0.0.1:18080
    username: user
    password: pass
    relocation_root: /data/torrents/movies-4k
```

plex2radarr checks each configured instance and only acts when exactly one torrent payload matches the Plex file. Ambiguous matches are skipped.

## Usage

### Dry run

Dry run is the default:

```bash
plex2radarr
```

or explicitly:

```bash
plex2radarr --config config.yaml
```

The default config path is `./config.yaml`.

### Execute

To perform planned qBittorrent relocations, Radarr additions, and Radarr imports:

```bash
plex2radarr --execute
```

There is intentionally no config option that enables execution. Mutating behavior must be requested on the command line each time.

### Useful options

```bash
plex2radarr --help
plex2radarr --config /path/to/config.yaml
plex2radarr --verbose
plex2radarr --execute
```

## Reconciliation behavior

A Plex movie can result in one of several outcomes:

| Situation | Action |
|---|---|
| Movie already exists in Radarr | Skip |
| Plex item has no usable TMDb/IMDb identity | Skip |
| Multiple Radarr matches are possible | Skip |
| Plex file is owned by exactly one qBittorrent torrent | Relocate torrent through qBittorrent, then import via Radarr |
| Plex file has no qBittorrent owner | Add to Radarr and import the existing file |
| More than one torrent appears to own the same file | Skip |
| Destination/relocation collision is detected | Skip |
| File is inaccessible to this process | Skip |

### Why qBittorrent relocation comes first

Consider a legacy movie:

```text
_Movies/The.Matrix.1999.1080p.BluRay/
└── The.Matrix.1999.1080p.BluRay.mkv
```

If qBittorrent seeds that exact path, simply hard-linking it into a new Radarr-standard folder would leave two Plex-visible paths.

plex2radarr instead asks qBittorrent to relocate the torrent payload to its configured torrent root first. Once the original legacy library path is no longer present, Radarr can import/hard-link from the torrent root into its canonical library folder without Plex seeing duplicate copies.

## Path mappings

API applications frequently expose different paths than the host running plex2radarr.

For example:

```text
Plex reports:       /media/movies/The Matrix/movie.mkv
Host filesystem:   /data/_Movies/The Matrix/movie.mkv
```

A mapping translates the reported path:

```yaml
path_mappings:
  - service: plex
    remote: /media/movies
    local: /data/_Movies
```

Service names are:

- `plex`
- `radarr`
- `qbittorrent:<configured-name>`

Mappings are prefix-based and case-sensitive.

## Hardlink verification

After an import, you can verify two files are hardlinked on Linux:

```bash
stat -c '%d %i %h %n' \
  "/data/torrents/movies/.../movie.mkv" \
  "/data/_Movies/The Matrix (1999)/.../movie.mkv"
```

A successful hardlink has the same device ID and inode number for both paths.

## Important limitations

- plex2radarr does not bypass normal filesystem rules. Hardlinks cannot span filesystems.
- The tool does not delete duplicate files on its own outside the qBittorrent relocation flow.
- Plex items containing multiple movie files/versions are treated conservatively; the tool currently reconciles only items with exactly one local media file.
- Extras, trailers, subtitles, and other sidecar files are not migrated in the first release.
- Radarr naming is ultimately controlled by your Radarr Media Management settings.
- qBittorrent relocation behavior depends on the torrent's own internal file/folder layout; plex2radarr re-queries qBittorrent after relocation rather than assuming the resulting path.

## Development

Run tests with:

```bash
pytest
```

Static checks:

```bash
ruff check .
```

## License

No license has been selected yet.
