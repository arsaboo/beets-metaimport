"""
Adds metaimport plugin to beets.
Allows importing metadata from multiple sources in order of preference.
"""

from beets import config, ui
from beets.plugins import BeetsPlugin
from beets.ui import print_
from beets.util import displayable_path
import collections
from collections import defaultdict
import pprint


class MetaImportPlugin(BeetsPlugin):
    # List of currently supported source plugins
    SUPPORTED_SOURCES = ['youtube', 'jiosaavn']

    def __init__(self):
        super().__init__()

        # Default config
        self.config.add({
            'sources': [],  # List of metadata sources in order of preference
            'exclude_fields': [],  # Fields to exclude from metadata import
            'merge_strategy': 'priority',  # How to handle conflicts: priority/all
            'debug': False,  # Enable detailed debug logging
        })

        # Initialize source plugins
        self.sources = []
        self.source_plugins = {}

        # Only try to load sources if they are explicitly configured
        if self.config['sources'].exists():
            configured_sources = self.config['sources'].as_str_seq()
            if configured_sources:
                self._log.debug(f'Configured sources: {configured_sources}')
                # Filter out unsupported sources with a warning
                for source in configured_sources:
                    if source not in self.SUPPORTED_SOURCES:
                        self._log.warning(f'Unsupported source plugin: {source}. '
                                        f'Supported sources are: {", ".join(self.SUPPORTED_SOURCES)}')
                    else:
                        self._init_source(source)
            else:
                self._log.debug('No sources configured in metaimport.sources')

    def _debug_log(self, msg, *args):
        """Log debug messages if debug mode is enabled."""
        if self.config['debug'].get():
            if args:
                msg = msg.format(*args)
            self._log.info('[DEBUG] {}', msg)

    def _init_source(self, source):
        """Initialize a single source plugin."""
        try:
            plugin_class = self._get_plugin_class(source)
            if plugin_class:
                plugin_instance = plugin_class()
                # Verify the plugin has the required methods
                if hasattr(plugin_instance, 'get_albums'):
                    self.source_plugins[source] = plugin_instance
                    self.sources.append(source)
                    self._log.debug(f'Successfully loaded source plugin: {source}')
                else:
                    self._log.warning(f'Source plugin {source} missing required method: get_albums')
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
            return None
        except ImportError as e:
            self._log.warning(f'Could not import plugin for source {source}: {str(e)}')
            return None

    def commands(self):
        import_cmd = ui.Subcommand(
            'metaimport',
            help='import metadata from configured sources'
        )
        import_cmd.parser.add_option(
            '-d', '--debug',
            action='store_true',
            help='enable debug logging'
        )
        import_cmd.func = self._command
        return [import_cmd]

    def _command(self, lib, opts, args):
        """Main command implementation."""
        # Set debug mode if requested
        if opts.debug:
            self.config['debug'].set(True)

        if not self.sources:
            self._log.warning('No valid metadata sources available. '
                            f'Supported sources are: {", ".join(self.SUPPORTED_SOURCES)}')
            return

        items = lib.items(ui.decargs(args))
        if not items:
            self._log.warning('No items matched your query')
            return

        # Group items by album
        albums = self._group_items_by_album(items)
        self._import_albums_metadata(albums)

    def _group_items_by_album(self, items):
        """Group items by album for batch processing."""
        albums = defaultdict(list)
        for item in items:
            key = (item.albumartist or item.artist, item.album)
            albums[key].append(item)
        return albums

    def _import_albums_metadata(self, albums):
        """Import metadata for albums from all configured sources."""
        for (albumartist, album_name), items in albums.items():
            self._log.info('Processing album: {} - {}', albumartist, album_name)

            # Collect metadata from all sources
            metadata = {}
            for source in self.sources:
                try:
                    plugin = self.source_plugins[source]
                    source_metadata = self._get_album_metadata(plugin, albumartist, album_name, items, source)
                    if source_metadata:
                        self._debug_log('Raw metadata from {}: {}', source,
                                      pprint.pformat(source_metadata))
                        metadata[source] = source_metadata
                        self._log.debug(f'Got metadata from {source} for {album_name}')
                except Exception as e:
                    self._log.warning('Error getting metadata from {}: {} ({})',
                                    source, str(e), type(e).__name__)
                    if self.config['debug'].get():
                        import traceback
                        self._log.debug('Traceback: {}', traceback.format_exc())

            # Apply metadata if found
            if metadata:
                self._apply_album_metadata(items, metadata)
            else:
                self._log.info('No metadata found for album: {} - {}', albumartist, album_name)

    def _build_album_query(self, albumartist, album_name, source):
        """Build an appropriate album search query based on the source."""
        if source == 'jiosaavn':
            # For JioSaavn, use album name and artist
            return f"{album_name} {albumartist}"
        else:
            # For other sources, same format
            return f"{album_name} {albumartist}"

    def _get_album_metadata(self, plugin, albumartist, album_name, items, source):
        """Get metadata for an album from a specific source."""
        try:
            query = self._build_album_query(albumartist, album_name, source)
            self._log.info(f'Searching {source} for album: {query}')

            albums = plugin.get_albums(query)
            if not albums:
                return None

            # Let user choose the correct album
            album_info = self._choose_album_metadata(albums, items[0])
            if not album_info:
                return None

            self._debug_log('Selected album info from {}: {}', source,
                          pprint.pformat(vars(album_info)))

            # Get tracks for the album
            if hasattr(plugin, 'get_album_tracks'):
                try:
                    tracks = plugin.get_album_tracks(album_info.album_id)
                    if tracks:
                        self._debug_log('Found {} tracks for album', len(tracks))
                        # Match tracks with items
                        return self._match_tracks_to_items(tracks, items)
                except Exception as e:
                    self._log.warning('Error getting album tracks: {} ({})',
                                    str(e), type(e).__name__)
                    if self.config['debug'].get():
                        import traceback
                        self._log.debug('Traceback: {}', traceback.format_exc())

            return None

        except Exception as e:
            self._log.debug('Error querying source: {}', str(e))
            raise

    def _match_tracks_to_items(self, tracks, items):
        """Match source tracks to local items and return metadata."""
        matched_metadata = {}

        # Create a mapping of normalized titles to tracks
        track_map = {self._normalize_title(t.title): t for t in tracks}

        # Try to match each item to a track
        for item in items:
            normalized_title = self._normalize_title(item.title)

            # Try exact match first
            track = track_map.get(normalized_title)

            # If no exact match, try fuzzy matching
            if not track:
                best_match = None
                best_score = 0
                for track_title, track_info in track_map.items():
                    score = self._compute_similarity(normalized_title, track_title)
                    if score > best_score and score > 0.8:  # 80% similarity threshold
                        best_score = score
                        best_match = track_info
                track = best_match

            if track:
                self._debug_log('Matched track {} to {}', item.title, track.title)
                # Convert track object to dict if needed
                if not isinstance(track, dict):
                    track_dict = {}
                    # Extract common metadata fields
                    for field in ['title', 'artist', 'album', 'year', 'track',
                                'albumartist', 'genre', 'composer', 'lyricist']:
                        if hasattr(track, field):
                            value = getattr(track, field)
                            if value:  # Only include non-empty values
                                track_dict[field] = value
                else:
                    track_dict = track
                matched_metadata[item] = track_dict
            else:
                self._debug_log('No match found for track: {}', item.title)

        return matched_metadata if matched_metadata else None

    def _normalize_title(self, title):
        """Normalize a title for comparison."""
        if not title:
            return ""
        # Remove special characters and convert to lowercase
        import re
        return re.sub(r'[^\w\s]', '', title.lower())

    def _compute_similarity(self, str1, str2):
        """Compute string similarity score."""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, str1, str2).ratio()

    def _choose_album_metadata(self, albums, item):
        """Let user choose the correct album if multiple matches found."""
        if not albums:
            return None

        if len(albums) == 1:
            return albums[0]

        print_(f'Multiple matches found for: {item.albumartist} - {item.album}')
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

    def _apply_album_metadata(self, items, metadata):
        """Apply metadata to all items in an album."""
        for item in items:
            merged = self._merge_metadata_for_item(item, metadata)
            if merged:
                self._debug_log('Merged metadata for {}: {}', item.title,
                              pprint.pformat(merged))
                self._apply_metadata(item, merged)

    def _merge_metadata_for_item(self, item, metadata):
        """Merge metadata from multiple sources for a specific item."""
        merged = {}
        exclude_fields = self.config['exclude_fields'].as_str_seq()

        if self.config['merge_strategy'].get() == 'all':
            # Collect all unique values
            for source in self.sources:
                if source in metadata and metadata[source] and item in metadata[source]:
                    source_meta = metadata[source][item]
                    for key, value in source_meta.items():
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
                if source in metadata and metadata[source] and item in metadata[source]:
                    source_meta = metadata[source][item]
                    for key, value in source_meta.items():
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
