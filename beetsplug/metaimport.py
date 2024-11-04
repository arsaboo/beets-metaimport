"""
Adds metaimport plugin to beets.
Allows importing metadata from multiple sources in order of preference.
"""

from beets import config, ui
from beets.plugins import BeetsPlugin
from beets.ui import print_
from beets.util import displayable_path
import collections


class MetaImportPlugin(BeetsPlugin):
    def __init__(self):
        super().__init__()

        # Default config
        self.config.add({
            'sources': [],  # List of metadata sources in order of preference
            'exclude_fields': [],  # Fields to exclude from metadata import
            'merge_strategy': 'priority',  # How to handle conflicts: priority/all
        })

        # Initialize source plugins
        self.sources = []
        self.source_plugins = {}

        for source in self.config['sources'].as_str_seq():
            try:
                # Dynamically import and initialize source plugins
                module_name = f'beetsplug.{source}'
                plugin_class = self._get_plugin_class(source)
                if plugin_class:
                    self.source_plugins[source] = plugin_class()
                    self.sources.append(source)
                else:
                    self._log.warning(f'Could not find plugin class for source: {source}')
            except Exception as e:
                self._log.warning(f'Failed to initialize source {source}: {str(e)}')

    def _get_plugin_class(self, source):
        """Get the plugin class for a given source."""
        try:
            if source == 'youtube':
                from beetsplug.youtube import YouTubePlugin
                return YouTubePlugin
            elif source == 'jiosaavn':
                from beetsplug.jiosaavn import JioSaavnPlugin
                return JioSaavnPlugin
            # Add more source plugins here
            else:
                self._log.warning(f'Unknown source plugin: {source}')
                return None
        except ImportError as e:
            self._log.warning(f'Could not import plugin for source {source}: {str(e)}')
            return None

    def commands(self):
        import_cmd = ui.Subcommand(
            'metaimport',
            help='import metadata from configured sources'
        )
        import_cmd.func = self._command
        return [import_cmd]

    def _command(self, lib, opts, args):
        """Main command implementation."""
        items = lib.items(ui.decargs(args))
        self._import_metadata(items)

    def _import_metadata(self, items):
        """Import metadata for the given items from all configured sources."""
        if not self.sources:
            self._log.warning('No metadata sources configured')
            return

        for item in items:
            self._log.info('Processing track: {}', displayable_path(item.path))

            # Collect metadata from all sources
            metadata = {}
            for source in self.sources:
                try:
                    plugin = self.source_plugins[source]
                    source_metadata = self._get_metadata_from_source(plugin, item)
                    if source_metadata:
                        metadata[source] = source_metadata
                except Exception as e:
                    self._log.warning('Error getting metadata from {}: {}', source, str(e))

            # Merge metadata according to priority
            if metadata:
                merged = self._merge_metadata(metadata)
                self._apply_metadata(item, merged)
            else:
                self._log.info('No metadata found for: {}', displayable_path(item.path))

    def _get_metadata_from_source(self, plugin, item):
        """Get metadata for an item from a specific source."""
        try:
            # Query the source using available item information
            query = f"{item.artist} {item.title}"
            if hasattr(plugin, 'get_track'):
                return plugin.get_track(query)
            elif hasattr(plugin, 'get_albums'):
                albums = plugin.get_albums(query)
                if albums:
                    # Let user choose the correct album if multiple matches
                    return self._choose_album_metadata(albums, item)
        except Exception as e:
            self._log.debug('Error querying source: {}', str(e))
        return None

    def _choose_album_metadata(self, albums, item):
        """Let user choose the correct album if multiple matches found."""
        if len(albums) == 1:
            return albums[0]

        print_(f'Multiple matches found for: {item.artist} - {item.title}')
        for i, album in enumerate(albums, 1):
            print_(f'{i}. {album.artist} - {album.album} ({album.year})')

        sel = ui.input_options(
            ('aBort', 'Skip'),
            numrange=(1, len(albums)),
            default=1
        )

        if sel in ('b', 'B', 's', 'S'):
            return None
        return albums[sel - 1] if sel > 0 else None

    def _merge_metadata(self, metadata):
        """Merge metadata from multiple sources according to priority."""
        merged = {}
        exclude_fields = self.config['exclude_fields'].as_str_seq()

        if self.config['merge_strategy'].get() == 'all':
            # Collect all unique values
            for source in self.sources:
                if source in metadata:
                    for key, value in metadata[source].items():
                        if key not in exclude_fields:
                            if key not in merged:
                                merged[key] = value
                            elif isinstance(merged[key], list):
                                if value not in merged[key]:
                                    merged[key].append(value)
                            else:
                                if merged[key] != value:
                                    merged[key] = [merged[key], value]
        else:
            # Priority-based merge (first source wins)
            for source in self.sources:
                if source in metadata:
                    for key, value in metadata[source].items():
                        if key not in exclude_fields and key not in merged:
                            merged[key] = value

        return merged

    def _apply_metadata(self, item, metadata):
        """Apply merged metadata to the item."""
        changes = []
        for key, value in metadata.items():
            if hasattr(item, key) and item[key] != value:
                old_value = item[key]
                item[key] = value
                changes.append(f'{key}: {old_value} -> {value}')

        if changes:
            self._log.info('Applied changes to {}: {}',
                          displayable_path(item.path), ', '.join(changes))
            item.store()
        else:
            self._log.info('No changes needed for: {}', displayable_path(item.path))
