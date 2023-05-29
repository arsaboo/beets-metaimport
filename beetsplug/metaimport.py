"""
Adds metaimport plugin to beets.
"""

import collections
import re
import time
from io import BytesIO

import requests
from beets import config, ui
from beets.autotag.hooks import AlbumInfo, Distance, TrackInfo
from beets.dbcore import types
from beets.library import DateType
from beets.plugins import BeetsPlugin, get_distance
from beets.ui import print_
from musicapy.saavn_api.api import SaavnAPI
from PIL import Image

import beetsplug


class MetaImportPlugin(BeetsPlugin):

    def __init__(self):
        super().__init__()
        self.sources = config['metaimport']['sources'].as_str_seq()
        for source in self.sources:
            if source == 'youtube':
                from beetsplug.youtube import YouTubePlugin
                self.youtube = YouTubePlugin()
            elif source == 'jiosaavn':
                from beetsplug.jiosaavn import JioSaavnPlugin
                self.jiosaavn = JioSaavnPlugin()

    def commands(self):

        # metasync command
        sync_cmd = ui.Subcommand('get_ids',
                                 help="fetch track attributes from all sources")

        def func(lib, opts, args):
            albums = lib.albums(ui.decargs(args))
            self._fetch_ids(albums, ui.should_write())

        sync_cmd.func = func
        return [sync_cmd]

    def _fetch_ids(self, albums, write):
        """Obtain track information from Spotify."""

        self._log.debug('Total {} albums', len(albums))

        for index, album in enumerate(albums, start=1):
            self._log.info('Processing {}/{} album - {} ',
                           index, len(albums), album)
            query = album.albumartist + ' ' + album.album
            # If we're not forcing re-downloading for all tracks, check
            # whether the popularity data is already present
            if "youtube" in self.sources:
                albs = self.youtube.get_albums(query)
                if len(albs) > 0:
                    print_(f'Choose candidates for {album.albumartist} - {album.album}')
                    
                    for i, alb in enumerate(albs, start=1):
                        print(f'{alb}')
                        print("album distance: ", self.youtube.album_distance(album, alb))
                    sel = ui.input_options(('aBort', 'Skip'),
                                           numrange=(1, len(albs)),
                                           default=1)
                    if sel in ('b', 'B', 's', 'S'):
                        return None
                    choice = albs[sel - 1] if sel > 0 else None
            try:
                yt_album_id = choice.yt_album_id
            except AttributeError:
                self._log.debug('No albumid present for: {}', album)
                continue

            album['yt_album_id'] = yt_album_id
            album.store()
            if write:
                album.try_write()
