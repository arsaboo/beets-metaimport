# beets-metaimport

A [beets](https://github.com/beetbox/beets) plugin that aggregates metadata from multiple configured metadata source plugins and applies it to your library albums.

## Overview

The metaimport plugin works with your existing beets metadata source plugins (like MusicBrainz, Spotify, Deezer, etc.) to:

1. **Aggregate metadata**: Collect metadata from multiple sources for each album
2. **Apply all fields**: Each source applies all the fields it provides, with later sources overwriting earlier ones
3. **Reuse existing IDs**: Automatically use existing source IDs when available to avoid redundant lookups
4. **Interactive selection**: Present you with match candidates when automatic matching isn't confident enough

## Installation

```bash
pip install -U git+https://github.com/arsaboo/beets-metaimport.git
```

Add `metaimport` to your beets configuration:

```yaml
plugins: [..., metaimport]
```

## Configuration

```yaml
metaimport:
    sources: auto              # Use all available metadata plugins, or specify a list
    primary_source: null       # Which source to use for common fields (defaults to last in list)
    write: true               # Write changes to files
    max_distance: null        # Auto-accept threshold (lower = stricter)
    pretend: false           # Show changes without applying them
```

### Configuration Options

- **`sources`**:
  - `"auto"` (default): Use all available metadata source plugins
  - List of sources: `["musicbrainz", "spotify", "deezer"]` - only use specified sources

- **`primary_source`**: Which source to process last, giving it the final say on overlapping fields. Defaults to the last source in the list.

- **`write`**: Whether to write metadata changes to audio files (default: `true`)

- **`max_distance`**: Auto-accept matches below this distance threshold. If not set, you'll be prompted for all matches.

- **`pretend`**: Show what would be changed without actually applying changes

## Usage

```bash
beet metaimport [options] [query]
```

### Options

- `-f, --force`: Re-run lookups even when source IDs already exist
- `-p, --pretend`: Show planned changes without storing them
- `--primary-source SOURCE`: Override primary source for this run
- `--max-distance FLOAT`: Override max distance threshold

### Examples

Import metadata for all albums:
```bash
beet metaimport
```

Import for specific artist:
```bash
beet metaimport artist:Beatles
```

Import for an album, forcing new lookups:
```bash
beet metaimport --force album:"Abbey Road"
```

See what would change without applying:
```bash
beet metaimport --pretend artist:Beatles
```

Use specific primary source:
```bash
beet metaimport --primary-source spotify artist:Beatles
```

## How It Works

For each album in your query:

1. **Check existing IDs**: If an album already has a source ID (e.g., `spotify_album_id`) and `--force` isn't used, load metadata directly from that source

2. **Search sources**: For sources without existing IDs, search using the album's current metadata

3. **Present matches**: If automatic matching isn't confident enough, you'll see an interactive prompt with match candidates

4. **Apply metadata**:
   - Each source applies all the metadata fields it provides
   - Sources are processed in order, with later sources overwriting earlier ones
   - The primary source (processed last) has the final say on any overlapping fields

5. **Save changes**: Store to database and optionally write to files

## Field Handling

The plugin applies all metadata fields provided by each source:

- **All fields applied**: Each source contributes all the metadata it provides (e.g., `artist`, `album`, `spotify_album_id`, `genre`, etc.)
- **Processing order matters**: Sources are processed in the configured order, with later sources overwriting earlier ones for the same fields
- **Primary source wins**: The primary source (last in processing order) has the final say on any overlapping field values

### Example
If you configure sources as `["spotify", "musicbrainz"]` with `musicbrainz` as primary:
1. Spotify applies: `artist`, `album`, `spotify_album_id`, `spotify_track_id`, etc.
2. MusicBrainz applies: `artist`, `album`, `mb_albumid`, `mb_trackid`, `genre`, etc.
3. Final result: MusicBrainz values for `artist`, `album`, `genre` + Spotify IDs + MusicBrainz IDs

## Troubleshooting

**No metadata sources available**:
- Check that you have metadata source plugins installed and enabled
- Verify they're properly configured

**Existing ID errors**:
- Use `--force` to re-run lookups even when IDs exist
- Check that the source plugin supports `album_for_id()` method

**No candidates found**:
- Try a broader search query
- Check that your albums exist in the configured sources
- Verify source plugin configuration

**Distance threshold issues**:
- Use `--max-distance 0.2` to auto-accept closer matches
- Use `--max-distance 1.0` to be more permissive

## Contributing

Contributions welcome! The plugin is designed to work with any beets metadata source plugin that follows the standard interface.

## License

MIT
