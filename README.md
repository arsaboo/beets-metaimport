# beets-metaimport

A [beets](https://github.com/beetbox/beets) plugin that aggregates metadata from multiple configured metadata source plugins and applies it to your library albums.

## Overview

The metaimport plugin works with your existing beets metadata source plugins (like MusicBrainz, Spotify, Deezer, etc.) to:

1. **Aggregate metadata**: Collect metadata from multiple sources for each album
2. **Smart field handling**: Apply source-specific fields (e.g., `spotify_album_id`) from all sources, and common fields (e.g., `artist`, `album`) only from the primary source
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
    dry_run: false           # Show changes without applying them
```

### Configuration Options

- **`sources`**:
  - `"auto"` (default): Use all available metadata source plugins
  - List of sources: `["musicbrainz", "spotify", "deezer"]` - only use specified sources

- **`primary_source`**: Which source to trust for common fields like `artist`, `album`, `genre`. Defaults to the last source in the list. Source-specific fields (like `spotify_album_id`) are always applied from their respective sources.

- **`write`**: Whether to write metadata changes to audio files (default: `true`)

- **`max_distance`**: Auto-accept matches below this distance threshold. If not set, you'll be prompted for all matches.

- **`dry_run`**: Show what would be changed without actually applying changes

## Usage

```bash
beet metaimport [options] [query]
```

### Options

- `-f, --force`: Re-run lookups even when source IDs already exist
- `--dry-run`: Show planned changes without storing them
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
beet metaimport --dry-run artist:Beatles
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
   - **Source-specific fields**: Always applied (e.g., `mb_albumid`, `spotify_track_id`)
   - **Common fields**: Only applied from the primary source (e.g., `artist`, `album`, `genre`)

5. **Save changes**: Store to database and optionally write to files

## Field Handling

The plugin handles two types of fields differently:

### Source-Specific Fields
Always applied from their respective sources:
- `mb_albumid`, `mb_trackid` (from MusicBrainz)
- `spotify_album_id`, `spotify_track_id` (from Spotify)
- `deezer_album_id`, `deezer_track_id` (from Deezer)
- etc.

### Common Fields
Only applied from the primary source to avoid conflicts:
- `artist`, `albumartist`, `album`, `title`
- `genre`, `year`, `label`
- `bpm`, `key`, `energy`
- etc.

## Requirements

The plugin works with any beets metadata source plugins you have installed, such as:
- Built-in MusicBrainz support
- [beets-spotify](https://github.com/timothyb89/beets-spotify)
- [beets-deezer](https://github.com/rhlabs/beets-deezer)
- Any other metadata source plugin

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
