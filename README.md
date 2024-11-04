# beets-metaimport

A [beets](https://github.com/beetbox/beets) plugin that imports metadata from multiple sources in order of preference.

## Installation

```bash
pip install beets-metaimport
```

## Configuration

Add `metaimport` to your beets configuration file's plugins section:

```yaml
plugins: [..., metaimport]

metaimport:
    sources:
        - youtube
        - jiosaavn
        # Add more sources as they become available
    exclude_fields:
        - id
        - path
    merge_strategy: priority  # or 'all' to collect all unique values
```

### Configuration Options

- `sources`: List of metadata sources in order of preference. Values from sources listed earlier will take precedence in case of conflicts.
- `exclude_fields`: List of fields to exclude from metadata import.
- `merge_strategy`: How to handle conflicting values:
  - `priority`: Use values from the first source that provides them (default)
  - `all`: Collect all unique values in a list

## Usage

```bash
beet metaimport [query]
```

The plugin will:
1. Search for tracks matching your query
2. For each track, fetch metadata from all configured sources
3. Merge the metadata according to your configuration
4. Apply the merged metadata to your tracks

### Examples

Import metadata for all tracks:
```bash
beet metaimport
```

Import metadata for specific artist:
```bash
beet metaimport artist:Beatles
```

## Adding New Sources

The plugin is designed to work with any beets metadata source plugin. Currently supported sources:
- YouTube (requires [beets-youtube](https://github.com/arsaboo/beets-youtube))
- JioSaavn (requires [beets-jiosaavn](https://github.com/arsaboo/beets-jiosaavn))

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT
