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

        configured_sources = self.config['sources'].as_str_seq()
        if not configured_sources:
            self._log.warning('No sources configured in metaimport.sources')
            return

        for source in configured_sources:
            try:
                # Dynamically import and initialize source plugins
                plugin_class = self._get_plugin_class(source)
                if plugin_class:
                    plugin_instance = plugin_class()
                    # Verify the plugin has the required methods
                    if hasattr(plugin_instance, 'get_albums') or hasattr(plugin_instance, 'get_track'):
                        self.source_plugins[source] = plugin_instance
                        self.sources.append(source)
                        self._log.debug(f'Successfully loaded source plugin: {source}')
                    else:
                        self._log.warning(f'Source plugin {source} missing required methods')
            except Exception as e:
                self._log.warning(f'Failed to initialize source {source}: {str(e)}')

    def _get_plugin_class(self, source):
        """Get the plugin class for a given source."""
        try:
            if source == 'jiosaavn':
                from beetsplug.jiosaavn import JioSaavnPlugin
                return JioSaavnPlugin
            elif source == 'youtube':
                from beetsplug.youtube import YouTubePlugin
                return YouTubePlugin
            else:
                self._log.warning(f'Unsupported source plugin: {source}')
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
        if not self.sources:
            self._log.warning('No metadata sources available. Check your configuration.')
            return

        items = lib.items(ui.decargs(args))
        if not items:
            self._log.warning('No items matched your query')
            return

        self._import_metadata(items)

    def _import_metadata(self, items):
        """Import metadata for the given items from all configured sources."""
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
                        self._log.debug(f'Got metadata from {source} for {item.title}')
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
            # Build a rich query using available item information
            query_parts = []
            if item.artist:
                query_parts.append(item.artist)
            if item.album:
                query_parts.append(item.album)
            if item.title:
                query_parts.append(item.title)

            query = ' '.join(query_parts)
            self._log.debug(f'Searching with query: {query}')

            if hasattr(plugin, 'get_track'):
                track_info = plugin.get_track(query)
                if track_info:
                    return track_info

            if hasattr(plugin, 'get_albums'):
                albums = plugin.get_albums(query)
                if albums:
                    album_info = self._choose_album_metadata(albums, item)
                    if album_info:
                        # Try to find the matching track in the album
                        if hasattr(plugin, 'get_album_tracks'):
                            tracks = plugin.get_album_tracks(album_info.album_id)
                            matching_track = self._find_matching_track(tracks, item)
                            if matching_track:
                                return matching_track
                        return album_info

        except Exception as e:
            self._log.debug('Error querying source: {}', str(e))
        return None

    def _find_matching_track(self, tracks, item):
        """Find the best matching track from a list of tracks."""
        if not tracks:
            return None

        # Try exact title match first
        for track in tracks:
            if track.title.lower() == item.title.lower():
                return track

        # If no exact match, try fuzzy matching
        best_match = None
        best_score = 0
        for track in tracks:
            score = self._compute_similarity(track.title.lower(), item.title.lower())
            if score > best_score and score > 0.8:  # 80% similarity threshold
                best_score = score
                best_match = track

        return best_match

    def _compute_similarity(self, str1, str2):
        """Compute string similarity score."""
        # Simple Levenshtein distance based similarity
        from difflib import SequenceMatcher
        return SequenceMatcher(None, str1, str2).ratio()

    def _choose_album_metadata(self, albums, item):
        """Let user choose the correct album if multiple matches found."""
        if not albums:
            return None

        if len(albums) == 1:
            return albums[0]

        print_(f'Multiple matches found for: {item.artist} - {item.title}')
        for i, album in enumerate(albums, 1):
            print_(f'{i}. {album.artist} - {album.album} ({getattr(album, "year", "N/A")})')

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
                        if key not in exclude_fields and value:
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
                        if key not in exclude_fields and key not in merged and value:
                            merged[key] = value

        return merged

    def _apply_metadata(self, item, metadata):
        """Apply merged metadata to the item."""
        changes = []
        for key, value in metadata.items():
            if hasattr(item, key):
                current_value = getattr(item, key)
                if current_value != value:
                    setattr(item, key, value)
                    changes.append(f'{key}: {current_value} -> {value}')

        if changes:
            self._log.info('Applied changes to {}: {}',
                          displayable_path(item.path), ', '.join(changes))
            item.store()
        else:
            self._log.info('No changes needed for: {}', displayable_path(item.path))
