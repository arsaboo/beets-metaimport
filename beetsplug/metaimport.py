"""MetaImport plugin: aggregate metadata from multiple sources."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from beets import autotag, importer, metadata_plugins, plugins, ui
from beets.autotag import hooks as autotag_hooks
from beets.autotag.match import assign_items
from beets.importer import ImportAbortError
from beets.importer.tasks import ImportTask
from beets.library import Album, Item
from beets.plugins import BeetsPlugin
from beets.ui import Subcommand
from beets.ui import commands as ui_commands
from beets.metadata_plugins import MetadataSourcePlugin

PREFIX_OVERRIDES: Dict[str, Tuple[str, ...]] = {
    "musicbrainz": ("mb_", "musicbrainz_"),
}

ID_FIELD_OVERRIDES: Dict[str, Tuple[str, ...]] = {
    "musicbrainz": ("mb_albumid",),
    "spotify": ("spotify_album_id", "spotify_albumid"),
    "deezer": ("deezer_album_id", "deezer_albumid"),
}

ALBUM_PASSTHROUGH_FIELDS = {"data_source", "data_url"}
TRACK_PASSTHROUGH_FIELDS = {"data_source", "data_url"}


@dataclass
class SourceMatchResult:
    """Outcome of processing a source for an album."""

    source: str
    plugin: MetadataSourcePlugin
    match: Optional[autotag_hooks.AlbumMatch]
    used_existing_id: bool
    skipped: bool = False
    reason: Optional[str] = None


@dataclass
class MetaImportContext:
    """Resolved configuration for a metaimport run."""

    sources: List[str]
    plugins: Dict[str, MetadataSourcePlugin]
    primary_source: str
    force: bool
    write: bool
    dry_run: bool
    max_distance: Optional[float]


class MetaImportPlugin(BeetsPlugin):
    """Aggregate metadata from all configured metadata sources."""

    def __init__(self) -> None:
        super().__init__()
        self.config.add(
            {
                "sources": "auto",
                "primary_source": None,
                "write": True,
                "max_distance": None,
                "dry_run": False,
            }
        )
        self._terminal_session: ui_commands.TerminalImportSession | None = None

    # --------------------------------- Commands ---------------------------------

    def commands(self) -> List[Subcommand]:
        cmd = Subcommand("metaimport", help="merge metadata from configured sources")
        cmd.parser.add_option(
            "-f",
            "--force",
            action="store_true",
            dest="force",
            default=False,
            help="re-run lookups even when source IDs already exist",
        )
        cmd.parser.add_option(
            "--dry-run",
            action="store_true",
            dest="dry_run",
            default=False,
            help="show planned changes without storing them",
        )
        cmd.parser.add_option(
            "--primary-source",
            action="store",
            dest="primary_source",
            help="override the primary source for this run",
        )
        cmd.parser.add_option(
            "--max-distance",
            action="store",
            dest="max_distance",
            type="float",
            help="maximum distance to accept automatically per source",
        )

        def func(lib, opts, args):
            query = list(args)
            context = self._build_context(opts)
            if not context.sources:
                self._log.warning("No metadata sources available; nothing to do")
                return

            joined_sources = ", ".join(context.sources)
            self._log.debug(
                f"Metaimport starting for {len(context.sources)} sources: {joined_sources}"
            )

            self._run(lib, query, context)

        cmd.func = func
        return [cmd]

    # ----------------------------- Context Utilities ----------------------------

    def _build_context(self, opts) -> MetaImportContext:
        configured_sources = self.config["sources"].get()
        override_list: Optional[Sequence[str]] = None
        if isinstance(configured_sources, str):
            if configured_sources.lower() != "auto":
                override_list = [configured_sources]
        else:
            override_list = [str(s) for s in configured_sources]

        sources, plugins_by_key = self._resolve_sources(override_list)

        primary_source_cfg = opts.primary_source or self.config["primary_source"].get()
        primary_source: Optional[str]
        if primary_source_cfg:
            candidate = self._normalize_source(primary_source_cfg)
            if candidate not in plugins_by_key:
                self._log.warning(
                    f"Configured primary source '{primary_source_cfg}' is not available; ignoring"
                )
                primary_source = None
            else:
                primary_source = candidate
        else:
            primary_source = None

        if not primary_source and sources:
            primary_source = sources[-1]

        force = bool(opts.force)
        dry_run = bool(opts.dry_run) or self.config["dry_run"].get(bool)
        write = self.config["write"].get(bool)
        max_distance_opt = opts.max_distance
        if max_distance_opt is None:
            max_distance_cfg = self.config["max_distance"].get()
            max_distance: Optional[float]
            if max_distance_cfg is None:
                max_distance = None
            else:
                try:
                    max_distance = float(max_distance_cfg)
                except (TypeError, ValueError):
                    self._log.warning(
                        f"Invalid max_distance value {max_distance_cfg}; ignoring"
                    )
                    max_distance = None
        else:
            max_distance = float(max_distance_opt)

        return MetaImportContext(
            sources=sources,
            plugins=plugins_by_key,
            primary_source=primary_source or "",
            force=force,
            write=write,
            dry_run=dry_run,
            max_distance=max_distance,
        )

    def _resolve_sources(
        self, override: Optional[Sequence[str]]
    ) -> Tuple[List[str], Dict[str, MetadataSourcePlugin]]:
        available_plugins = metadata_plugins.find_metadata_source_plugins()
        source_map: Dict[str, MetadataSourcePlugin] = {}
        ordered_keys: List[str] = []

        for plugin in available_plugins:
            key = self._normalize_source(plugin.data_source)
            if key not in source_map:
                source_map[key] = plugin
                ordered_keys.append(key)

        if override is None:
            return ordered_keys, source_map

        resolved: List[str] = []
        for name in override:
            key = self._normalize_source(name)
            if key in source_map:
                resolved.append(key)
            else:
                self._log.warning(
                    f"Configured metadata source '{name}' is not loaded; skipping"
                )

        return resolved, source_map

    @staticmethod
    def _normalize_source(name: str) -> str:
        return name.replace("_", "").replace("-", "").replace(" ", "").lower()

    # ------------------------------ Core Execution ------------------------------

    def _run(
        self,
        lib,
        query: Sequence[str],
        context: MetaImportContext,
    ) -> None:
        terminal_session = self._ensure_terminal_session(lib)
        album_iter = lib.albums(query) if query else lib.albums()

        processed = 0
        for album in album_iter:
            processed += 1
            self._log.info(
                f"Metaimport {album.albumartist or album.artist} - {album.album}"
            )
            try:
                self._process_album(album, context, terminal_session)
            except ImportAbortError:
                self._log.warning("Metaimport aborted by user")
                break
            except Exception:
                album_label = f"{album.albumartist or album.artist} - {album.album}"
                self._log.exception(
                    f"Unexpected error processing album {album_label}"
                )

        if processed == 0:
            self._log.info("No albums matched the query; nothing processed")

    def _ensure_terminal_session(self, lib) -> ui_commands.TerminalImportSession:
        if self._terminal_session is None:
            self._terminal_session = ui_commands.TerminalImportSession(lib, None, [], None)
        return self._terminal_session

    # ----------------------------- Album Processing -----------------------------

    def _process_album(
        self,
        album: Album,
        context: MetaImportContext,
        terminal_session: ui_commands.TerminalImportSession,
    ) -> None:
        items = list(album.items())
        if not items:
            self._log.debug(f"Album {album.id} has no items; skipping")
            return

        for source_key in context.sources:
            plugin = context.plugins.get(source_key)
            if not plugin:
                self._log.debug(f"Source {source_key} no longer available; skipping")
                continue

            allow_common = source_key == context.primary_source
            result = self._process_source_for_album(
                album,
                items,
                plugin,
                source_key,
                allow_common,
                context,
                terminal_session,
            )
            if result.skipped:
                reason = f" ({result.reason})" if result.reason else ""
                self._log.info(
                    f"Skipping {plugin.data_source} for {format(album)}{reason}"
                )
                continue

            self._apply_result(album, result, allow_common, context)

    def _process_source_for_album(
        self,
        album: Album,
        items: List[Item],
        plugin: MetadataSourcePlugin,
        source_key: str,
        allow_common: bool,
        context: MetaImportContext,
        terminal_session: ui_commands.TerminalImportSession,
    ) -> SourceMatchResult:
        existing_id = self._current_source_id(album, source_key)
        used_existing_id = False

        if existing_id and not context.force:
            self._log.debug(
                f"{plugin.data_source} already has ID {existing_id}; loading existing metadata"
            )
            try:
                album_info = plugin.album_for_id(existing_id)
            except Exception:
                self._log.exception(
                    f"Failed fetching album info for {plugin.data_source} id {existing_id}; falling back to search"
                )
                album_info = None

            if album_info:
                used_existing_id = True
                mapping, extra_items, extra_tracks = self._assign_tracks(
                    items, album_info, plugin
                )
                if not mapping:
                    return SourceMatchResult(
                        source=source_key,
                        plugin=plugin,
                        match=None,
                        used_existing_id=True,
                        skipped=True,
                        reason="no track mapping",
                    )

                match = autotag_hooks.AlbumMatch(
                    distance=0.0,
                    info=album_info,
                    mapping=mapping,
                    extra_items=extra_items,
                    extra_tracks=extra_tracks,
                )
                return SourceMatchResult(
                    source=source_key,
                    plugin=plugin,
                    match=match,
                    used_existing_id=True,
                )

        with self._limit_metadata_plugins(plugin):
            cur_artist, cur_album, proposal = autotag.tag_album(items)

        if not proposal.candidates:
            return SourceMatchResult(
                source=source_key,
                plugin=plugin,
                match=None,
                used_existing_id=used_existing_id,
                skipped=True,
                reason="no candidates",
            )

        if (
            context.max_distance is not None
            and proposal.candidates
            and proposal.candidates[0].distance > context.max_distance
        ):
            self._log.info(
                f"{plugin.data_source} candidate distance {proposal.candidates[0].distance:.3f} "
                f"above threshold {context.max_distance:.3f}; skipping"
            )
            return SourceMatchResult(
                source=source_key,
                plugin=plugin,
                match=None,
                used_existing_id=used_existing_id,
                skipped=True,
                reason="distance threshold",
            )

        task = ImportTask(
            None,
            [item.path for item in items],
            items,
        )
        task.cur_artist = cur_artist
        task.cur_album = cur_album
        task.candidates = proposal.candidates
        task.rec = proposal.recommendation

        plugins.send("import_task_start", session=terminal_session, task=task)
        try:
            choice = terminal_session.choose_match(task)
        except ImportAbortError:
            raise

        if choice is None:
            return SourceMatchResult(
                source=source_key,
                plugin=plugin,
                match=None,
                used_existing_id=used_existing_id,
                skipped=True,
                reason="no selection",
            )

        if isinstance(choice, importer.Action):
            task.set_choice(choice)
            plugins.send("import_task_choice", session=terminal_session, task=task)

            if choice in (importer.Action.SKIP, importer.Action.ASIS):
                return SourceMatchResult(
                    source=source_key,
                    plugin=plugin,
                    match=None,
                    used_existing_id=used_existing_id,
                    skipped=True,
                    reason="user skipped",
                )

            self._log.warning(
                f"Action {choice.name} is not supported in metaimport; skipping {plugin.data_source}"
            )
            return SourceMatchResult(
                source=source_key,
                plugin=plugin,
                match=None,
                used_existing_id=used_existing_id,
                skipped=True,
                reason="unsupported action",
            )

        assert isinstance(choice, autotag_hooks.AlbumMatch)
        task.set_choice(choice)
        plugins.send("import_task_choice", session=terminal_session, task=task)

        return SourceMatchResult(
            source=source_key,
            plugin=plugin,
            match=choice,
            used_existing_id=used_existing_id,
        )

    def _assign_tracks(
        self,
        items: Sequence[Item],
        album_info: autotag_hooks.AlbumInfo,
        plugin: MetadataSourcePlugin,
    ) -> Tuple[Dict[Item, autotag_hooks.TrackInfo], List[Item], List[autotag_hooks.TrackInfo]]:
        with self._limit_metadata_plugins(plugin):
            mapping, extra_items, extra_tracks = assign_items(items, album_info.tracks)
        return mapping, extra_items, extra_tracks

    @contextmanager
    def _limit_metadata_plugins(
        self, plugin: MetadataSourcePlugin
    ) -> Iterator[None]:
        original = metadata_plugins.find_metadata_source_plugins

        def _filtered() -> List[MetadataSourcePlugin]:
            return [plugin]

        metadata_plugins.find_metadata_source_plugins = _filtered  # type: ignore[assignment]
        try:
            yield
        finally:
            metadata_plugins.find_metadata_source_plugins = original  # type: ignore[assignment]

    # ------------------------------ Metadata Apply ------------------------------

    def _apply_result(
        self,
        album: Album,
        result: SourceMatchResult,
        allow_common: bool,
        context: MetaImportContext,
    ) -> None:
        if not result.match:
            return

        album_info = result.match.info
        mapping = result.match.mapping

        album_changes = self._apply_album_fields(
            album,
            album_info,
            result.source,
            allow_common,
            context.dry_run,
        )

        track_changed = False
        for item, track_info in mapping.items():
            changes = self._apply_track_fields(
                item,
                track_info,
                result.source,
                allow_common,
                context.dry_run,
            )
            if changes:
                track_changed = True
                if not context.dry_run:
                    item.store()
                    if context.write:
                        item.try_write()

        if album_changes and not context.dry_run:
            album.store()

        if album_changes or track_changed:
            suffix = " (dry run)" if context.dry_run else ""
            self._log.info(f"Applied {result.plugin.data_source} metadata{suffix}")

    def _apply_album_fields(
        self,
        album: Album,
        album_info: autotag_hooks.AlbumInfo,
        source_key: str,
        allow_common: bool,
        dry_run: bool,
    ) -> Dict[str, Tuple[object, object]]:
        changes: Dict[str, Tuple[object, object]] = {}
        prefixes = PREFIX_OVERRIDES.get(source_key, (f"{source_key}_",))
        valid_fields = {name.lower() for name in album.keys(computed=True)}

        for field, value in album_info.items():
            if value is None:
                continue

            lname = field.lower()
            if not self._field_allowed(
                lname, prefixes, allow_common, valid_fields, ALBUM_PASSTHROUGH_FIELDS
            ):
                continue

            current = album.get(field)
            if current == value:
                continue
            changes[field] = (current, value)
            if not dry_run:
                album[field] = value

        return changes

    def _apply_track_fields(
        self,
        item: Item,
        track_info: autotag_hooks.TrackInfo,
        source_key: str,
        allow_common: bool,
        dry_run: bool,
    ) -> Dict[str, Tuple[object, object]]:
        changes: Dict[str, Tuple[object, object]] = {}
        prefixes = PREFIX_OVERRIDES.get(source_key, (f"{source_key}_",))
        valid_fields = {
            name.lower() for name in item.keys(computed=True, with_album=False)
        }

        for field, value in track_info.items():
            if value is None:
                continue

            lname = field.lower()
            if not self._field_allowed(
                lname, prefixes, allow_common, valid_fields, TRACK_PASSTHROUGH_FIELDS
            ):
                continue

            current = item.get(field)
            if current == value:
                continue
            changes[field] = (current, value)
            if not dry_run:
                item[field] = value

        return changes

    def _field_allowed(
        self,
        field: str,
        prefixes: Tuple[str, ...],
        allow_common: bool,
        valid_fields: set[str],
        passthrough: set[str],
    ) -> bool:
        if any(field.startswith(prefix) for prefix in prefixes):
            return True

        if allow_common and (field in valid_fields or field in passthrough):
            return True

        return False

    def _current_source_id(self, album: Album, source_key: str) -> Optional[str]:
        for field in ID_FIELD_OVERRIDES.get(
            source_key, (f"{source_key}_album_id", f"{source_key}_albumid")
        ):
            try:
                value = album.get(field)
            except KeyError:
                continue
            if value:
                return str(value)
        return None
