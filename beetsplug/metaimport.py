from beets import config, ui, autotag
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

            # Create an info object for autotag
            info = autotag.AlbumInfo(
                album=album_name,
                album_id=None,
                artist=albumartist,
                artist_id=None,
                tracks=[],
                year=None,
                month=None,
                day=None,
                label=None,
                mediums=1,
                artist_sort=None,
                releasegroup_id=None,
                asin=None,
                catalognum=None,
                script=None,
                language=None,
                country=None,
                style=None,
                genre=None,
                albumtype=None,
                albumstatus=None,
                media=None,
                albumdisambig=None,
                artist_credit=None,
                data_source='metaimport',
                data_url=None
            )

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
                except Exception as e:
                    self._log.warning('Error getting metadata from {}: {} ({})',
                                    source, str(e), type(e).__name__)
                    if self.config['debug'].get():
                        import traceback
                        self._log.debug('Traceback: {}', traceback.format_exc())

            # Apply metadata if found
            if metadata:
                # Create a match proposal
                candidates = [info]
                # Calculate recommendation based on string similarity
                recommendation = autotag.Recommendation(
                    strong=True,
                    strong_rec=0.5,  # Medium-strong recommendation
                    medium_rec=0.3,  # Medium recommendation
                    rec=0.2  # Weak recommendation
                )
                proposal = autotag.Proposal(candidates, recommendation)
                proposal.show()

                # Get user choice using beets' standard interface
                sel = ui.input_options(
                    ('Apply', 'More', 'Skip', 'Use as-is', 'as Tracks', 'Group albums'),
                    'Enter search, enter Id, Apply, More, Skip, Use as-is, '
                    'as Tracks, Group albums?'
                )

                if sel == 'a':
                    self._apply_album_metadata(items, metadata)
                elif sel == 's':
                    self._log.info('Skipped album: {} - {}', albumartist, album_name)
            else:
                self._log.info('No metadata found for album: {} - {}', albumartist, album_name)

    def _build_album_query(self, albumartist, album_name, source):
        """Build an appropriate album search query based on the source."""
        if source == 'jiosaavn':
            # For JioSaavn, use album name and artist
            return f"{album_name} {albumartist}"
        else:
            # For YouTube, use just the album name for better results
            return album_name

    def _get_album_metadata(self, plugin, albumartist, album_name, items, source):
        """Get metadata for an album from a specific source."""
        try:
            query = self._build_album_query(albumartist, album_name, source)
            self._log.info(f'Searching {source} for album: {query}')

            # For YouTube, skip album lookup and go straight to track search
            if source == 'youtube':
                return self._get_youtube_tracks_metadata(plugin, items)

            try:
                albums = plugin.get_albums(query)
            except Exception as e:
                self._log.warning(f'Error getting albums from {source}: {str(e)}')
                return None

            if not albums:
                return None

            # Create candidates for beets matching
            candidates = []
            for album in albums:
                info = autotag.AlbumInfo(
                    album=album.album,
                    album_id=album.album_id,
                    artist=album.artist,
                    artist_id=None,
                    tracks=[],
                    year=getattr(album, 'year', None),
                    month=None,
                    day=None,
                    label=getattr(album, 'label', None),
                    mediums=1,
                    artist_sort=None,
                    releasegroup_id=None,
                    asin=None,
                    catalognum=None,
                    script=None,
                    language=None,
                    country=None,
                    style=None,
                    genre=getattr(album, 'genre', None),
                    albumtype=None,
                    albumstatus=None,
                    media=None,
                    albumdisambig=None,
                    artist_credit=None,
                    data_source=source,
                    data_url=None
                )
                candidates.append(info)

            if not candidates:
                return None

            # Calculate recommendation based on string similarity
            similarity = self._compute_similarity(album_name, candidates[0].album)
            recommendation = autotag.Recommendation(
                strong=similarity > 0.8,
                strong_rec=0.8,
                medium_rec=0.5,
                rec=0.3
            )

            # Create a match proposal
            proposal = autotag.Proposal(candidates, recommendation)
            proposal.show()

            # Get user choice using beets' standard interface
            sel = ui.input_options(
                ('Apply', 'More', 'Skip', 'Use as-is', 'as Tracks', 'Group albums'),
                'Enter search, enter Id, Apply, More, Skip, Use as-is, '
                'as Tracks, Group albums?'
            )

            if sel == 'a':
                # Get tracks for the selected album
                if hasattr(plugin, 'get_album_tracks'):
                    try:
                        tracks = plugin.get_album_tracks(candidates[0].album_id)
                        if not tracks:
                            self._log.warning(f'No tracks found for album in {source}')
                            return None

                        self._debug_log('Found {} tracks for album', len(tracks))
                        # Match tracks with items
                        return self._match_tracks_to_items(tracks, items, source)
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

    def _get_youtube_tracks_metadata(self, plugin, items):
        """Get metadata for individual tracks from YouTube."""
        self._log.info('Searching YouTube tracks individually')
        matched_metadata = {}

        for item in items:
            try:
                # Build search query using track title and artist
                query = f"{item.title}"
                if item.artist and item.artist.lower() != "various artists":
                    query = f"{query} {item.artist}"
                self._debug_log('Searching YouTube for track: {}', query)

                # Search for the track
                try:
                    search_results = plugin.yt.search(query, filter="songs")
                    if not search_results:
                        continue

                    # Create candidates for beets matching
                    candidates = []
                    for result in search_results[:5]:  # Top 5 results
                        artists = [a['name'] for a in result.get('artists', [])]
                        album = result.get('album', {}).get('name', 'N/A')
                        info = autotag.TrackInfo(
                            title=result.get('title'),
                            track_id=result.get('videoId'),
                            artist=artists[0] if artists else None,
                            artist_id=None,
                            length=result.get('duration', 0),
                            index=None,
                            medium=None,
                            medium_index=None,
                            medium_total=None,
                            artist_sort=None,
                            disctitle=None,
                            artist_credit=None,
                            data_source='youtube',
                            data_url=None
                        )
                        candidates.append(info)

                    if candidates:
                        # Calculate recommendation based on string similarity
                        similarity = self._compute_similarity(item.title, candidates[0].title)
                        recommendation = autotag.Recommendation(
                            strong=similarity > 0.8,
                            strong_rec=0.8,
                            medium_rec=0.5,
                            rec=0.3
                        )

                        # Create a match proposal
                        proposal = autotag.Proposal(candidates, recommendation)
                        proposal.show()

                        # Get user choice using beets' standard interface
                        sel = ui.input_options(
                            ('Apply', 'More', 'Skip', 'Use as-is', 'as Tracks', 'Group albums'),
                            'Enter search, enter Id, Apply, More, Skip, Use as-is, '
                            'as Tracks, Group albums?'
                        )

                        if sel == 'a':
                            # Convert the selected candidate to a metadata dictionary
                            track_dict = {
                                'title': candidates[0].title,
                                'artist': candidates[0].artist,
                                'youtube_id': candidates[0].track_id,
                                'length': candidates[0].length
                            }
                            matched_metadata[item] = track_dict
                            self._debug_log('Using YouTube metadata for track: {}',
                                          pprint.pformat(track_dict))

                except Exception as e:
                    self._log.warning('YouTube search failed for {}: {}', item.title, str(e))
                    continue

            except Exception as e:
                self._log.warning('Error getting YouTube metadata for track {}: {}',
                                item.title, str(e))

        return matched_metadata if matched_metadata else None

    def _match_tracks_to_items(self, tracks, items, source):
        """Match source tracks to local items and return metadata."""
        matched_metadata = {}

        print_(f'\nMatching tracks for {source}:')
        print_('=' * 80)

        # Convert tracks to candidates for beets matching
        candidates = []
        for track in tracks:
            info = autotag.TrackInfo(
                title=track.title,
                track_id=None,
                artist=track.artist if hasattr(track, 'artist') else None,
                artist_id=None,
                length=track.duration if hasattr(track, 'duration') else 0,
                index=None,
                medium=None,
                medium_index=None,
                medium_total=None,
                artist_sort=None,
                disctitle=None,
                artist_credit=None,
                data_source=source,
                data_url=None
            )
            candidates.append(info)

        if candidates:
            # Calculate recommendation based on average string similarity
            similarities = []
            for item, candidate in zip(items, candidates):
                if item and candidate:
                    similarity = self._compute_similarity(item.title, candidate.title)
                    similarities.append(similarity)

            avg_similarity = sum(similarities) / len(similarities) if similarities else 0
            recommendation = autotag.Recommendation(
                strong=avg_similarity > 0.8,
                strong_rec=0.8,
                medium_rec=0.5,
                rec=0.3
            )

            # Create a match proposal
            proposal = autotag.Proposal(candidates, recommendation)
            proposal.show()

            # Get user choice using beets' standard interface
            sel = ui.input_options(
                ('Apply', 'More', 'Skip', 'Use as-is', 'as Tracks', 'Group albums'),
                'Enter search, enter Id, Apply, More, Skip, Use as-is, '
                'as Tracks, Group albums?'
            )

            if sel == 'a':
                # Match tracks based on order
                for item, candidate in zip(items, candidates):
                    if candidate:
                        # Convert the candidate to a metadata dictionary
                        track_dict = {
                            'title': candidate.title,
                            'artist': candidate.artist,
                            'length': candidate.length
                        }
                        # Add any additional fields from the original track
                        track_index = candidates.index(candidate)
                        original_track = tracks[track_index]
                        for attr in dir(original_track):
                            if not attr.startswith('_') and not callable(getattr(original_track, attr)):
                                value = getattr(original_track, attr)
                                if value and attr not in track_dict:
                                    track_dict[attr] = value

                        matched_metadata[item] = track_dict
                        self._debug_log('Matched track {} to {}',
                                      item.title,
                                      pprint.pformat(track_dict))

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

    def _apply_album_metadata(self, items, metadata):
        """Apply metadata to all items in an album."""
        # First, collect all proposed changes
        proposed_changes = {}
        for item in items:
            merged = self._merge_metadata_for_item(item, metadata)
            if merged:
                changes = self._get_proposed_changes(item, merged)
                if changes:
                    proposed_changes[item] = changes

        if not proposed_changes:
            self._log.info('No changes needed for any tracks')
            return

        # Show proposed changes and get confirmation
        self._show_proposed_changes(proposed_changes)
        if self._confirm_changes():
            # Apply the changes
            for item, changes in proposed_changes.items():
                self._apply_changes(item, changes)
        else:
            self._log.info('Changes cancelled by user')

    def _get_proposed_changes(self, item, metadata):
        """Get proposed changes for an item."""
        changes = {}
        for key, value in metadata.items():
            try:
                if hasattr(item, key):
                    current_value = getattr(item, key)
                    if current_value != value:
                        changes[key] = {
                            'current': current_value,
                            'new': value
                        }
            except Exception as e:
                self._log.warning('Error checking field {}: {} ({})',
                                key, str(e), type(e).__name__)
        return changes

    def _show_proposed_changes(self, proposed_changes):
        """Show proposed changes in a user-friendly format."""
        print_('\nProposed changes:')
        print_('=' * 80)

        for item, changes in proposed_changes.items():
            print_(f'\nTrack: {item.title}')
            print_('-' * 40)

            # Show current path
            print_(f'Path: {displayable_path(item.path)}')

            # Show changes
            for field, values in changes.items():
                print_(f'  {field}:')
                print_(f'    Current: {values["current"]}')
                print_(f'    New    : {values["new"]}')
            print_()

    def _confirm_changes(self):
        """Get user confirmation for changes."""
        return ui.input_yn('Apply these changes? (Y/n)', True)

    def _apply_changes(self, item, changes):
        """Apply confirmed changes to an item."""
        applied_changes = []
        for key, values in changes.items():
            try:
                setattr(item, key, values['new'])
                applied_changes.append(f'{key}: {values["current"]} -> {values["new"]}')
            except Exception as e:
                self._log.warning('Error setting field {}: {} ({})',
                                key, str(e), type(e).__name__)

        if applied_changes:
            self._log.info('Applied changes to {}: {}',
                          displayable_path(item.path), ', '.join(applied_changes))
            try:
                item.store()
                self._log.info('Successfully stored changes to database')
            except Exception as e:
                self._log.error('Failed to store changes: {} ({})',
                              str(e), type(e).__name__)

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
