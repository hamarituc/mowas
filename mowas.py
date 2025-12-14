#!/bin/env python3

from aioax25.aprs.datetime import DHMUTCTimestamp
from aioax25.aprs.frame import APRSFrame
from aioax25.aprs.position import APRSLatitude
from aioax25.aprs.position import APRSLongitude
from aioax25.aprs.position import APRSUncompressedCoordinates
from aioax25.aprs.position import APRSCompressedLatitude
from aioax25.aprs.position import APRSCompressedLongitude
from aioax25.aprs.position import APRSCompressedCoordinates
from aioax25.aprs.symbol import APRSSymbol
from aioax25.frame import AX25Address
import argparse
import binascii
import copy
import datetime
import json
import logging
import os
from osgeo import gdal
from osgeo import ogr
import pytz
import random
import re
import requests
import serial
import socket
import sys
import time
import xmltodict
import yaml

gdal.UseExceptions()



#
# Spezifikationen
#  - CAP: https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html
#  - APRS:
#    - https://raw.githubusercontent.com/wb2osz/aprsspec/main/Understanding-APRS-Packets.pdf
#    - https://www.aprs.org/doc/APRS101.PDF
#



parser = argparse.ArgumentParser(
    description = "MoWaS-Alarmierung verarbeiten"
)

parser.add_argument(
    '-c', '--config',
    type = str,
    default = '/etc/mowas.yml',
    metavar = 'FILE',
    help = "Konfigurationsdatei")

parser.add_argument(
    '--log-level',
    type = str,
    metavar = 'LEVEL',
    choices = [ 'error', 'warning', 'info', 'debug' ],
    help = "Log-Level")

parser.add_argument(
    '--log-console',
    action = 'store_true',
    help = "Auf Konsole loggen")

parser.add_argument(
    '--log-file',
    type = str,
    metavar = 'FILE',
    help = "Log-Datei")


ARGS = parser.parse_args()



class JSONDateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.time, datetime.datetime)):
            return obj.isoformat()



class ConfigException(Exception):
    pass



def parse_duration(s):
    match = re.fullmatch('([0-9]+)([mhdw]?)', s)
    if match is None:
        raise ConfigException("Ungültiges Zeitintervall '%s'" % s)

    t = datetime.timedelta(minutes = int(match[1]))

    unit = match[2]
    if unit == 'h':
        t *= 60
    elif unit == 'd':
        t *= 60 * 24
    elif unit == 'w':
        t *= 60 * 24 * 7

    return t



class Config:
    def __init__(self, tree, errmsg):
        if not isinstance(tree, dict):
            raise ConfigException("%s: Dictionary erwartet." % errmsg)
        self.tree = tree


    def get_subtree(self, key, errmsg, optional = False):
        subtree = self.tree.get(key, {} if optional else None)
        return Config(subtree, errmsg)


    def _get_value(self, key, default = None, null = False):
        if default is None and key not in self.tree:
            if null:
                return None
            else:
                raise ConfigException("Attribut '%s' ist erforderlich.\n" % key)

        return self.tree.get(key, default)


    def get_bool(self, key, default = None):
        value = self._get_value(key, default)
        if value not in [ True, False ]:
            raise ConfigException("Ungültiges Attribut '%s': Boolean erwartet." % key)
        return value


    def get_int(self, key, default = None, null = False):
        value = self._get_value(key, default, null)
        if value is None and null:
            return None
        if not isinstance(value, int):
            raise ConfigException("Ungültiges Attribut '%s': Ganzzahl erwartet." % key)
        return value


    def get_str(self, key, default = None, null = False):
        value = self._get_value(key, default, null)
        if value is None and null:
            return None
        if not isinstance(value, str):
            raise ConfigException("Ungültiges Attribut '%s': String erwartet." % key)
        return value


    def get_bin(self, key, default = None, null = False):
        value = self.get_str(key, default, null)
        if value is None:
            return None

        try:
            value = binascii.unhexlify(value)
        except binascii.Error as e:
            raise ConfigException("Ungültiges Attribut '%s': %s." % ( key, e.args[0] ))

        return value


    def get_duration(self, key, default = None):
        value = self.get_str(key, default)

        try:
            value = parse_duration(value)
        except ConfigException as e:
            raise ConfigException("Ungültiges Attribut '%s': %s" % ( key, e.message ))

        return value


    def get_list(self, key, default = None):
        value = self._get_value(key, default)
        if not isinstance(value, list):
            raise ConfigException("Ungültiges Attribut '%s': Liste erwartet." % key)
        return value


    def get_dict(self, key, default = None):
        value = self._get_value(key, default)
        if not isinstance(value, dict):
            raise ConfigException("Ungültiges Attribut '%s': Dictionary erwartet." % key)
        return value



class Geodata:
    def __init__(self, config):
        self.logger = logging.getLogger('mowas.geodata')

        self.ars = {}

        self._load(config.get_str('path', None))


    def _load(self, path):
        if path is None:
            return

        self.logger.info("Lade '%s'." % path)

        ds = gdal.OpenEx(path, gdal.OF_READONLY)
        if ds is None:
            self.logger.error("Kann '%s' nicht öffnen." % path)
            return

        l = ds.GetLayer('region')

        if l is None:
            self.logger.error("Ebene 'region' in '%s' nicht vorhanden." % path)
            return

        if l.GetGeomType() not in [ ogr.wkbPolygon, ogr.wkbMultiPolygon ]:
            self.logger.error("Ebene 'region' in '%s' enthält keine Polygone." % path)
            return

        for f in l:
            ars = f.ARS

            # Ungültige Regionalschlüssel überspringen
            if len(ars) != 12:
                continue

            self.ars[ars] = f.GetGeometryRef().Clone()

        self.logger.info("%d Regionen geladen." % len(self.ars))


    def ars_get(self, ars):
        return self.ars.get(ars, None)



class Alert:
    def __init__(self, capdata):
        self.attrs = {}
        self.txstate = {}

        # Datentypen eines CAP-Datensatzes aufbereiten
        if 'sent' in capdata:
            capdata['sent'] = datetime.datetime.fromisoformat(capdata['sent'])

        for i in capdata['info']:
            if 'effective' in i:
                i['effective'] = datetime.datetime.fromisoformat(i['effective'])
            if 'onset' in i:
                i['onset'] = datetime.datetime.fromisoformat(i['onset'])
            if 'expires' in i:
                i['expires'] = datetime.datetime.fromisoformat(i['expires'])

        self.capdata = capdata


    def __str__(self):
        return self.aid


    @property
    def aid(self):
        return self.capdata['identifier']


    def update(self, alert):
        assert self.aid == alert.aid, "Inkompatible Alert-IDs '%s' und '%s' beim Update einer Warnung." % ( self.aid, alert.aid )

        self.capdata = alert.capdata
        self.attrs.update(alert.attrs)
        self.txstate.update(alert.txstate)


    @property
    def cache_ctx(self):
        ctx = \
        {
            'alert':   self.capdata,
            'attrs':   self.attrs,
            'txstate': self.txstate,
        }

        return ctx


    def cache_load(self, data):
        self.attrs   = data['attrs']
        self.txstate = data['txstate']

        for ttype, tdata in self.txstate.items():
            for tname, txdata in tdata.items():
                txdata['first'] = datetime.datetime.fromisoformat(txdata['first'])
                txdata['last']  = datetime.datetime.fromisoformat(txdata['last'])


    def attr_set(self, key, value):
        self.attrs[key] = value


    def attr_get(self, key):
        if key not in self.attrs:
            return None

        return self.attrs[key]


    def tx_status(self, ttype, tname):
        if ttype not in self.txstate or \
           tname not in self.txstate[ttype]:
            return ( None, None )

        first = self.txstate[ttype][tname]['first']
        last  = self.txstate[ttype][tname]['last']

        return ( first, last )


    def tx_done(self, ttype, tname, t):
        if ttype not in self.txstate:
            self.txstate[ttype] = {}
        if tname not in self.txstate[ttype]:
            self.txstate[ttype][tname] = { 'first': t }
        self.txstate[ttype][tname]['last'] = t



class Source:
    def __init__(self, sname):
        self.sname = sname
        self.logger = logging.getLogger('mowas.source.%s.%s' % ( self.stype, self.sname ))

    def purge(self, valid):
        pass



class SourceDARC(Source):
    stype = 'darc'


    def __init__(self, sname, config):
        super().__init__(sname)

        self.dir_json  = config.get_str('dir_json')
        self.dir_cap   = config.get_str('dir_cap')
        self.dir_audio = config.get_str('dir_audio', null = True)

        self.fetch_internet = config.get_bool('fetch_internet', False)
        self.fetch_hamnet   = config.get_bool('fetch_hamnet',   False)

        if not self.fetch_internet and not self.fetch_hamnet:
            raise ConfigException("Quelle 'DARC', Parameter 'fetch': Mind. eine Download-Quelle muss aktiviert sein.")

        if not os.path.isdir(self.dir_json):
            raise ConfigException("Quelle 'DARC', Parameter 'dir_json': '%s' ist kein Verzeichnis" % self.dir_json)

        if not os.path.isdir(self.dir_cap):
            raise ConfigException("Quelle 'DARC', Parameter 'dir_cap': '%s' ist kein Verzeichnis" % self.dir_cap)

        if self.dir_audio is not None and not os.path.isdir(self.dir_audio):
            raise ConfigException("Quelle 'DARC', Parameter 'dir_audio': '%s' ist kein Verzeichnis" % self.dir_audio)


    def _read_alert(self):
        with os.scandir(self.dir_json) as it:
            for entry in it:
                if not entry.is_file():
                    continue

                _, ext = os.path.splitext(entry.name)
                if ext != '.json':
                    continue

                with open(entry) as f:
                    try:
                        darc_alert = json.load(f)
                    except json.decoder.JSONDecodeError:
                        self.logger.error("Fehler beim Laden der Warnung '%s'." % entry)
                        self.logger.exception(e)
                        continue

                yield entry.path, darc_alert


    def _read_cap(self, path):
        if not os.path.isfile(path):
            return None

        with open(path) as f:
            capdata = xmltodict.parse(f.read())
        capdata = capdata['alert']
        del capdata['@xmlns']

        if 'info' in capdata and not isinstance(capdata['info'], list):
            capdata['info'] = [ capdata['info'] ]
        for i in capdata['info']:
            if 'resource' in i and not isinstance(i['resource'], list):
                i['resource'] = [ i['resource'] ]
            if 'area' in i and not isinstance(i['area'], list):
                i['area'] = [ i['area'] ]

        return Alert(capdata)


    def _safe_filename(self, filename):
        # Dateinamen werden ggf. aus extern geladenen Daten abgeleitet. Auf
        # diese Weise verhindern wir, dass eine nicht vertrauenswürdige Quelle
        # beliebig im Dateisystem navigiert.
        return filename.replace('/', '_')

    def _path_cap(self, aid):
        return os.path.join(self.dir_cap, self._safe_filename("%s.xml" % aid))

    def _path_audio(self, aid):
        if self.dir_audio is None:
            return None

        return os.path.join(self.dir_audio, self._safe_filename("%s.wav" % aid))


    def _fetch_file(self, path, urls):
        # Nichts tun, wenn File bereits existiert
        if os.path.isfile(path):
            self.logger.debug("Datei '%s' bereit vorhanden. Download nicht notwendig." % path)
            return True

        # URLs in zufälliger Reihenfolge abfragen
        random.shuffle(urls)
        for url in urls:
            self.logger.debug("Download von '%s'." % url)
            r = requests.get(url)
            try:
                r.raise_for_status()
            except requests.exceptions.HTTPError as e:
                self.logger.warning("Fehler beim Download von '%s'." % url)
                self.logger.exception(e)
                continue

            self.logger.debug("Download von '%s' erfolgreich." % url)
            with open(path, 'wb') as f:
                f.write(r.content)

            return True

        self.logger.warning("Download von '%s' nicht möglich." % path)

        return False


    def fetch(self):
        for path_json, darc_alert in self._read_alert():
            path_cap = self._path_cap(darc_alert['id'])
            path_audio = self._path_audio(darc_alert['id'])

            sources_cap = []
            if self.fetch_internet:
                sources_cap.extend(darc_alert['url']['xml']['internet'])
            if self.fetch_hamnet:
                sources_cap.extend(darc_alert['url']['xml']['hamnet'])

            if not self._fetch_file(path_cap, sources_cap):
                # Ohne CAP-Daten können wir nicht weiter arbeiten.
                self.logger.warning("Warnung '%s' kann nicht verarbeitet werden, da keine CAP-Daten vorliegen." % path_json)
                continue

            alert = self._read_cap(path_cap)

            if path_audio is not None:
                sources_audio = []
                if self.fetch_internet:
                    sources_audio.extend(darc_alert['url']['audio']['internet'])
                if self.fetch_hamnet:
                    sources_audio.extend(darc_alert['url']['audio']['hamnet'])

                if self._fetch_file(path_audio, sources_audio):
                    alert.attr_set('path_audio', path_audio)

            yield alert


    def purge(self, valid):
        super().purge(valid)

        files_keep = set()
        files_remove = set()

        # Alle Files einlesen
        for path_json, darc_alert in self._read_alert():
            path_cap = self._path_cap(darc_alert['id'])
            path_audio = self._path_audio(darc_alert['id'])

            alert = self._read_cap(path_cap)

            # Warnungen überspringen, zu denen wir keinen CAP-Datensatz
            # vorliegen haben. Der CAP-Datensatz kann z.B. durch einen
            # Netzwerkfehler nicht heruntergeladen worden sein. Wenn wir jetzt
            # das Alarmierungs-File löschen, würde es im nächsten Durchlauf
            # keinen Versuch mehr geben, den CAP-Datensatz erneut
            # herunterzuladen.
            if alert is None:
                self.logger.debug("Behalte '%s', da kein CAP-Datensatz vorliegt." % path_json)
                continue

            # Alte Alarmierungs-Files zur Löschung vormerken.
            if alert.aid not in valid:
                self.logger.info("Markiere '%s' zu Löschung, da Meldung aus Cache gelöscht wurde." % path_json)
                files_remove.add(path_json)
                continue

            self.logger.debug("Behalte '%s', da Meldung noch gültig ist." % path_json)

            # Die zugehörigen CAP-Files und Audio-Files einer gültigen Warnung
            # behalten wir.
            self.logger.debug("Behalte '%s'." % path_cap)
            files_keep.add(path_cap)
            if path_audio is not None:
                self.logger.debug("Behalte '%s'." % path_audio)
                files_keep.add(path_audio)

        # Alle CAP- und Audio-Files bestimmen, die wir nicht behalten wollen.
        dirs = { d for d in [ self.dir_cap, self.dir_audio ] if d is not None }
        for d in dirs:
            with os.scandir(d) as it:
                for entry in it:
                    if not entry.is_file():
                        continue

                    if entry.path not in files_keep:
                        self.logger.info("Markiere '%s' zu Löschung." % entry.path)
                        files_remove.add(entry.path)

        for path in files_remove:
            self.logger.info("Lösche '%s'." % path)
            os.unlink(path)



class SourceBBKFile(Source):
    stype = 'bbk_file'


    def __init__(self, sname, config):
        super().__init__(sname)

        self.path = config.get_str('path')


    def fetch(self):
        with open(self.path) as f:
            try:
                capdata = json.load(f)
            except json.decoder.JSONDecodeError:
                self.logger.error("Fehler beim Laden der Warnung '%s'." % self.path)
                self.logger.exception(e)
                return

        for alertdata in capdata:
            yield Alert(alertdata)



class SourceBBKUrl(Source):
    stype = 'bbk_url'


    def __init__(self, sname, config):
        super().__init__(sname)

        self.url = config.get_str('url')


    def fetch(self):
        r = requests.get(self.url)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            self.logger.error("Fehler bei der Abfrage von '%s'." % self.url)
            self.logger.eception(e)
            return

        try:
            capdata = r.json()
        except requests.exceptions.JSONDecodeError as e:
            self.logger.error("Fehler beim Laden der Rückgabe von '%s'." % self.url)
            self.logger.exception(e)
            return

        for alertdata in capdata:
            yield Alert(alertdata)



class Cache:
    def __init__(self, config):
        self.logger = logging.getLogger('mowas.cache')

        self.path = config.get_str('path')
        self.age  = config.get_duration('purge', '31d')

        self.alerts = {}

        if os.path.isfile(self.path):
            with open(self.path) as f:
                try:
                    data = json.load(f)
                except json.decoder.JSONDecodeError as e:
                    self.logger.error("Fehler beim Laden des Caches '%s'." % self.path)
                    self.logger.exception(e)
                    return
        else:
            self.logger.debug("Cache '%s' existiert nicht." % self.path)
            return

        for aid, alertdata in data.items():
            alert = Alert(alertdata['alert'])
            alert.cache_load(alertdata)
            self.alerts[aid] = alert


    def dump(self):
        data = { aid: alert.cache_ctx for aid, alert in self.alerts.items() }
        with open(self.path, 'w') as f:
            json.dump(data, f, cls = JSONDateTimeEncoder, indent = 2)


    def update(self, alert):
        if alert.aid in self.alerts:
            self.alerts[alert.aid].update(alert)
        else:
            thresh = datetime.datetime.now(datetime.timezone.utc) - self.age
            if alert.capdata['sent'] >= thresh:
                self.alerts[alert.aid] = alert


    def purge(self):
        thresh = datetime.datetime.now(datetime.timezone.utc) - self.age

        valid = set()
        remove = set()

        # Veraltete Warnungen bestimmen
        for alert in self.alerts.values():
            if alert.capdata['sent'] >= thresh:
                valid.add(alert.aid)
            else:
                remove.add(alert.aid)

        # Wir löschen veraltete Warnungen nur, wenn keine gültige Warnung mehr
        # auf sie verweist.
        for aid in valid:
            alert = self.alerts[aid]

            if 'references' not in alert.capdata:
                continue

            references = alert.capdata['references']
            for ref in references.split():
                ref_sender, ref_aid, ref_sent = ref.split(',')
                remove.discard(ref_aid)

        for aid in remove:
            self.logger.info("Lösche Warnung '%s' aus Cache." % aid)
            del self.alerts[aid]

        return valid


    #
    # Wir weisen den Warnungen einen persistente ID zu. Zweck dieser ID ist es,
    # Warnungen eindeutig zu nummerieren. Die Nummerierung wird
    # wiederverwendet. Ziel ist es lediglich, dass eine Warnung, so lange sie
    # aktiv ist, die selbe Nummer erhält. Dies ist notwendig, um z.B.
    # APRS-Meldungen regelmäßig als Bake mit konsistenzen Bezeichnern
    # auszusenden.
    #
    # Eine Warnungen kann dabei eine Menge von Persistent-IDs erhalten. Das
    # trifft z.B. dann zu, wenn zwei Warnungen mit unterschiedlichen
    # Persistent-IDs durch eine Aktualisierung referenziert werden. Die
    # Aktualisierung erhält dann die beiden ursprünglichen Persistent-IDs.
    #
    def persistent_ids(self):
        nopids = {}
        pids   = {}
        refs   = {}

        for aid, alert in self.alerts.items():
            pid = alert.attr_get('pids')
            if pid is None:
                # Warnungen ohne Persistent-ID sammeln
                nopids[aid] = alert
            else:
                # Bestehende Persistent-IDs sammeln
                pids[aid] = pid

            refs[aid] = set()
            if 'references' in alert.capdata:
                for ref in alert.capdata['references'].split():
                    ref_sender, ref_aid, ref_sent = ref.split(',')

                    # Warnungen überspringen, die nicht mehr vorliegen
                    if ref_aid not in self.alerts:
                        continue

                    refs[aid].add(ref_aid)

        # Belegte Persistent-IDs bestimmen
        usedpids = set()
        for pid in pids.values():
            usedpids |= set(pid)

        # Freie Persistent-IDs bestimmen
        if len(usedpids) == 0:
            freepids = list(range(1, len(nopids) + 1))
        else:
            freepids = sorted(set(range(1, max(usedpids) + 1)) - usedpids)[:len(nopids)]
            if len(freepids) < len(nopids):
                freepids.extend(range(max(usedpids) + 1, max(usedpids) + len(nopids) - len(freepids) + 1))

        # Warnungen ohne Persistent-ID taggen
        rerun = True
        while rerun:
            rerun = False
            nopids_new = {}
            for aid, alert in nopids.items():
                # Wir berechnen die Menge aller Referenzen von `aid`, die noch
                # keine Persistent-ID haben. Dann können wir auch für `aid`
                # noch keine Persistent-ID vergeben und merken und diese
                # Warnung für den nächsten Durchlauf.
                if refs[aid] - set(pids.keys()):
                    nopids_new[aid] = alert
                    continue

                # Die Warnung `aid` erhält alle Persistent-IDs der Warnungen,
                # auf die sie referenziert.
                pid = set()
                for ref_aid in refs[aid]:
                    pid |= set(pids[ref_aid])

                # Sollte es keine referenzierten Warnungen geben, vergeben wir
                # eine neue Persistent-ID. Es wird bei 1 beginnend eine nicht
                # belegte ID nach First Fit gesucht.
                pid = sorted(pid)
                if len(pid) == 0:
                    pid = [ freepids.pop(0) ]

                # Persistent-IDs zuweisen
                pids[aid] = pid
                alert.attr_set('pids', pid)

                # Da wir neue Persistent-IDs vergeben haben, können weitrere
                # Nachrichten, die ggf. auf `aid` verweisen, mit einer
                # Persistent-ID versehen werden. Es muss also ein weiterer
                # Durchlauf erfolgen.
                rerun = True

            # Noch nicht mit einer Persistent-ID versehene Warnungen für den
            # nächsten Durchlauf vormerken.
            nopids = nopids_new

        for aid in nopids.keys():
            self.logger.error("Warnung '%s' ist Bestandteil eines zirkulären Verweises." % aid)
            # TODO: einzelne IDs vergeben?


    def query(self):
        aid_references = set()

        # Warnungen bestimmen, die durch Aktualisierungen ersetzt wurden
        for aid in sorted(self.alerts.keys()):
            alert = self.alerts[aid]
            if 'references' not in alert.capdata:
                continue
            for ref in alert.capdata['references'].split():
                ref_sender, ref_aid, ref_sent = ref.split(',')
                aid_references.add(ref_aid)

        return [ alert for aid, alert in self.alerts.items() if aid not in aid_references ]



class Filter:
    # Übergeordnete Bereiche bestimmen
    def _area_superset(self, geocode : str) -> set:
        areas = []
        areas.append("000000000000")
        areas.append(geocode[0:2] + "0000000000")
        areas.append(geocode[0:3] + "000000000")
        areas.append(geocode[0:5] + "0000000")
        areas.append(geocode[0:9] + "000")
        areas.append(geocode)

        return set(areas)


    # Redundante Bereiche filtern
    def _area_filter_redundant(self, geocodes : [ list, set ]) -> set:
        return { g for g in geocodes if not (self._area_superset(g) - { g }) & set(geocodes) }


    def __init__(self, config, logger):
        self.logger = logger

        # Gebietsschlüssel auf Plausibilität prüfen.
        geocodes = []
        for i, r in enumerate(config.get_list('geocodes', [])):
            if not isinstance(r, str):
                raise ConfigException("Ungültiger Gebietsschlüssel '%s': String erwartet." % r)

            if len(set(r) - set("0123456789")) > 0:
                raise ConfigException("Ungültiger Gebietsschlüssel '%s': Nur Ziffern erlaubt." % r)

            if len(r) > 12:
                self.logger.warning("Ungültiger Gebietsschlüssel '%s': Zu lang. Kürze auf 12 Stellen." % r)
                geocodes.append(r[0:12])
            elif len(r) in [ 2, 3, 5, 9, 12 ]:
                # Zu Kurze Regionalschlüssel ggf. erweitern
                geocodes.append(r.ljust(12, "0"))
            else:
                raise ConfigException("Ungültiger Gebietsschlüssel '%s': Zu kurz." % r)

        # Gebietsschlüssel verwerfen, die bereits durch übergeordnete
        # Gebietsschlüssel erfasst sind.
        self.geocodes = self._area_filter_redundant(geocodes)

        # Alle übergeordneten Gebiete einbeziehen.
        self.geocodes_super = set()
        for r in self.geocodes:
            self.geocodes_super |= self._area_superset(r)

        # Maximales Altert eine Warnung bei Erstalarmierung
        self.max_age = config.get_duration('max_age', '4h')


    def match_age(self, alert, ttype, tname, t):
        tfirst, tlast = alert.tx_status(ttype, tname)

        if tfirst is None and alert.capdata['sent'] + self.max_age <= t:
            return False

        return True


    def match_geo(self, geocode):
        # Eine Nachricht wird übernommen, wenn sie unterhalb der von uns
        # spezifizierten Gebiete liegt oder für eines der uns
        # übergeordneten Gebiete kodiert ist.
        geocodes = []
        for g in geocode:
            gcode = g['value']
            geocode_super = self._area_superset(gcode)
            if gcode in self.geocodes_super or \
               len(geocode_super & self.geocodes) > 0:
                geocodes.append(g)

        return geocodes



class Schedule:
    def __init__(self, config):
        sched = []
        for thresh, interval in config.tree.items():
            try:
                thresh = parse_duration(thresh)
            except ConfigException:
                raise ConfigException("Ungültiger Wiederholungsrhythmus: Schwellwert '%s' ist keine gültige Zeitdauer." % thresh)

            try:
                interval = parse_duration(interval)
            except ConfigException:
                raise ConfigException("Ungültiger Wiederholungsrhythmus: Intervall '%s' ist keine gültige Zeitdauer." % interval)

            sched.append(( thresh, interval ))
        sched.sort()

        # Wir bestimmen alle Wiederholungszeitpunkt ab t = 0.
        self.sched = [ datetime.timedelta(seconds = 0) ]
        for thresh, interval in sched:
            n = (thresh - self.sched[-1]) // interval
            for i in range(n):
                self.sched.append(self.sched[-1] + interval)


    def tx_required(self, alert, ttype, tname, t):
        first, last = alert.tx_status(ttype, tname)
        if first is None or last is None:
            return True

        diff = last - first

        # Alle vorausliegenden Übertragungszeitpunkte berechnen
        diffs = [ d for d in self.sched if d > diff ]

        # Alle Übertragungen wurden abgeschlossen
        if len(diffs) == 0:
            return False

        # Wir sehen einen Jitter-Puffer von 5 Sekunden vor.
        return first + diffs[0] <= t + datetime.timedelta(seconds = 5)



class Target:
    def __init__(self, tname, config):
        self.tname = tname
        self.logger = logging.getLogger('mowas.target.%s.%s' % ( self.ttype, self.tname ))

        self.sched = Schedule(config.get_subtree('schedule', "Ungültiger Widerholungsrhythmus für Senke '%s/%s'" % ( self.ttype, self.tname )))
        self.filter = Filter(config.get_subtree('filter', "Ungültige Filter-Konfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ), True), self.logger)


    def query(self, alerts, t):
        for alert in alerts:
            # Warnungen, die noch nie übertragen wurden, aber zu alt sind,
            # verwerfen wir. Sie kommen ggf. dadurch zu Stande, dass der Cache
            # leer war. Wir vermeiden es somit, veraltete Warnungen erneut zu
            # auszulösen.
            if not self.filter.match_age(alert, self.ttype, self.tname, t):
                continue

            # Nachrichten nur wiederholen, wenn es das Wiederholungsintervall
            # verlangt.
            if not self.sched.tx_required(alert, self.ttype, self.tname, t):
                continue

            capdata = copy.deepcopy(alert.capdata)

            if 'info' not in capdata:
                continue

            infos = []
            for info in capdata['info']:
                # Abgelaufene Meldungen verwerfen
                if 'expires' in info and info['expires'] < t:
                    continue

                if 'area' not in info:
                    continue

                areas = []
                for area in info['area']:
                    # Ohne Gebietsschlüssel können wir die Warnung nicht
                    # verarbeiten. Ggf. könnten wir bei der Veröffentlichung
                    # von Polygonen auf geometrische Überschneidungen mit den
                    # von uns spezifierten Warngebieten prüfen.
                    if 'geocode' not in area:
                        continue

                    area['geocode'] = self.filter.match_geo(area['geocode'])
                    if len(area['geocode']) == 0:
                        continue

                    areas.append(area)

                if len(areas) == 0:
                    continue

                info['area'] = areas
                infos.append(info)

            if len(infos) == 0:
                continue

            capdata['info'] = infos

            yield alert, capdata



class TargetAprs(Target):
    def __init__(self, tname, config):
        super().__init__(tname, config)

        config_aprs     = config.get_subtree('aprs', "Ungültige APRS-Konfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ))
        config_beacon   = config_aprs.get_subtree('beacon', "Ungültige Baken-Konfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ), optional = True)
        config_bulletin = config_aprs.get_subtree('bulletin', "Ungültige Bulletin-Konfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ), optional = True)

        self.dstcall           = config_aprs.get_str('dstcall', 'APMOWA')
        self.mycall            = config_aprs.get_str('mycall')
        self.digipath          = config_aprs.get_list('digipath', [ 'WIDE1-1' ])
        self.truncate          = config_aprs.get_bool('truncate_comment', True)
        self.beacon            = config_beacon.get_bool('enabled', True)
        self.beacon_prefix     = config_beacon.get_str('prefix', 'MOWA')
        self.beacon_time       = config_beacon.get_bool('time', True)
        self.beacon_compressed = config_beacon.get_bool('compressed', False)
        self.max_areas         = config_beacon.get_int('max_areas', 0)
        self.bulletin_mode     = config_bulletin.get_str('mode', 'fallback').lower()
        self.bulletin_id       = config_bulletin.get_str('id', '0MOWAS')[0:6].ljust(6, ' ')

        if self.bulletin_mode not in [ 'never', 'fallback', 'always' ]:
            self.logger.warning("Unbekannter Bulletin-Modus '%s'. Falle auf Standardeinstellung 'fallback' zurück." % self.bulletin_mode)
            self.bulletin_mode = 'fallback'


    #
    # APRS kann im Endeffekt nur Punktkoordinaten behandeln. Es besteht eine
    # Möglichkeit eine Ellipse um diesen Punkt herum zu definieren. Diese
    # Kodierung unterstützt unsere APRS-Bibliothek aber nicht, weswegen wir
    # darauf verzichten.
    #
    # Die Gebietsdaten liegen jedoch meist als Flächen vor, entweder direkt
    # im Warndatensatz oder anhand amtlicher Polygone für jeden
    # Gebietsschlüssel.
    #
    # In einem ersten Schritt wird jedem Warnereignis eine Reihe von Polygonen
    # zugeordnet. Bezieht sich ein Ereignisse auf eine Menge mehrerer Gebiete,
    # werden diese bis zu einem konfigurierbaren Schwellwert getrennt
    # behandelt. Wird die Anzahl Gebiete zu groß, werden diese zu einem
    # Gesamtgebiet vereinigt, um das APRS-Netz nicht mit zu vielen
    # Positionsmeldungen zu überlasten.
    #
    # In einem zweiten Schritt werden die Schwerpunkte zu diesen
    # Gebietspolygonen bestimmt. Diese spiegeln dann die APRS-Positionen
    # wieder.
    #
    # Es kann auch passieren, dass keine Position bestimmbar ist. Dies ist
    # jedoch kein Fehler, da wir dann in der Lage sind per APRS-Bulletin zu
    # warnen.
    #
    def _get_pos(self, alert, info):
        if not self.beacon:
            return []

        polys = []

        # Wir behandeln jedes Gebiet einzeln.
        for area in info['area']:
            # Polygon in eine geometrische Datenstruktur überführen.
            # Selbst ein Gebiet kann aus mehreren Polygonen bestehen. Dies ist
            # z.B. bei Flächen mit Löchern der Fall. Wir müssen uns aber nicht
            # um diese Sonderfälle kümmern. Die `ogr`-Bibliothek berücksichtigt
            # das bereits alles.
            if 'polygon' in area:
                polygon = ogr.Geometry(ogr.wkbPolygon)
                for ringstr in area['polygon']:
                    ring = ogr.Geometry(ogr.wkbLinearRing)
                    ringcoords = ringstr.split()

                    # Manche Geometrien enthalten einen falschen Punkt `-1 -1`
                    # als erste Koordinate. Wir reparieren diesen Fehler.
                    if len(ringcoords) > 2:
                        if ringcoords[0] == '-1.0,-1.0' and ringcoords[1] == ringcoords[-1]:
                            ringcoords = ringcoords[1:]
                            self.logger.info("Warnung '%s': Geometrie enthält ungültige Koordinate `-1.0 -1.0`. Diese wurde entfernt, um die Geometrie zu reparieren." % alert.aid)

                    # Zur Sicherheit prüfen wir, ob die Ringe geschlossen sind.
                    # Wir könnten sie alternativ auch schließen.
                    if ringcoords[0] != ringcoords[-1]:
                        self.logger.error("Warnung '%s': Geometrie hat nicht geschlossen Polygonringe. Diese Gebieter werden verworfen." % alert.aid)
                        continue

                    for coords in ringcoords:
                        x, y = coords.split(',')
                        ring.AddPoint(float(x), float(y))

                    ring.FlattenTo2D()
                    polygon.AddGeometry(ring)
                polys.append(polygon)

            # Enthält der Warndatensatz keine Gebietsangabe, verwenden wir den
            # kodierten Regionalschlüssel und schlagen in der amtlichen
            # Datenbank nach.
            elif 'geocode' in area:
                for geocode in area['geocode']:
                    arsmultipolygon = GEODATA.ars_get(geocode['value'])
                    if arsmultipolygon is None:
                        self.logger.warning("Warnung '%s': Gebietsschlüssel '%s' (%s) nicht in Polygon auflösbar." % ( alert.aid, geocode['value'], geocode['valueName'] ))
                    else:
                        for i in range(arsmultipolygon.GetGeometryCount()):
                            polys.append(arsmultipolygon.GetGeometryRef(i))

        # Zu viele Einzelflächen bei Bedarf zusammenführen
        if self.max_areas > 0 and len(polys) > self.max_areas:
            multipolygon = ogr.Geometry(ogr.wkbMultiPolygon)
            for poly in polys:
                multipolygon.AddGeometry(poly)
            polys = [ multipolygon ]

        # Polygone in Koordinaten auflösen
        pos = []
        for poly in polys:
            p = poly.Centroid()

            # ungültige Geometrien verwerfen
            if not p.IsValid() or p.IsEmpty():
                continue

            pos.append(p)

        return pos


    #
    # APRS-Baken können mit einem fixen Zeitpunkt verknüpft werden.
    #
    def _get_time(self, info, capdata, t):
        # Generell auf Zeitangaben verzichten
        if not self.beacon_time:
            return None

        # Es gibt mehrere Zeitstempel, die für das Ereigniss kodiert sein
        # können. Die Zeitstempel müssen jedoch nicht angegeben sein. Der
        # zutreffendeste angegebene Zeitstempel gewinnt.
        if 'onset' in info:
            # Veröffentlicher Anfangszeitpunkt des Ereignisses
            time = info['onset']
        elif 'effective' in info:
            # Veröffentlichungszeitpunkt der Warnmeldung
            time = info['effective']
        elif 'sent' in capdata:
            # Alarmierungszeitpunkt
            time = capdata['sent']
        else:
            return None

        # Bei lang zurückliegenden Meldungen geben wir keinen Zeitpunkt an,
        # da wir bei APRS nur den Monatstag übermitteln können. Um
        # Eindeutigkeit sicherzustellen, werden zukünftige Meldungen eine Woche
        # im Voraus und vergangene Meldungen 3 Wochen im Nachgang mit einer
        # Zeitangabe versehen.
        if t - time >= datetime.timedelta(days = 21) or \
           t - time <= datetime.timedelta(days = -7):
            return None

        return time.astimezone(pytz.utc)


    def _get_comment(self, info):
        # Meldungstext
        comment = ''
        if 'headline' in info:
            comment += info['headline']

        # Unicode-Umlaute werden auf Mobilgeräten evt. nicht korrekt
        # dargestellt.
        comment = comment.replace('ÄE', 'AE')
        comment = comment.replace('ÖE', 'OE')
        comment = comment.replace('ÜE', 'UE')
        comment = comment.replace('Äe', 'Ae')
        comment = comment.replace('Öe', 'Oe')
        comment = comment.replace('Üe', 'Ue')
        comment = comment.replace('äe', 'ae')
        comment = comment.replace('oe', 'oe')
        comment = comment.replace('üe', 'ue')
        comment = re.sub(r'Ä([A-Z])', r'AE\1', comment)
        comment = re.sub(r'Ö([A-Z])', r'OE\1', comment)
        comment = re.sub(r'Ü([A-Z])', r'UE\1', comment)
        comment = re.sub(r'([A-Z])Ä', r'AE\1', comment)
        comment = re.sub(r'([A-Z])Ö', r'OE\1', comment)
        comment = re.sub(r'([A-Z])Ü', r'UE\1', comment)
        comment = comment.replace("Ä", "Ae")
        comment = comment.replace("Ö", "Oe")
        comment = comment.replace("Ü", "Ue")
        comment = comment.replace("ä", "ae")
        comment = comment.replace("ö", "oe")
        comment = comment.replace("ü", "ue")
        comment = comment.replace("ß", "ss")

        if comment.strip() == '':
            comment = None

        return comment


    def _get_bulletin(self, alert, pos, comment):
        if self.bulletin_mode == 'never':
            # Generell auf Bulletins verzichten.
            return []
        elif self.bulletin_mode == 'fallback':
            # Bulletins nur übertragen, wenn keine Positionen vorhanden sind.
            if len(pos) > 0:
                return []

        # Ohne Meldungstext gibt es nichts zu warnen.
        if comment is None:
            self.logger.warning("Warnung '%s': Kein Kommentar. Es wird kein APRS-Bulletins ausgegeben." % alert.aid)
            return []

        # Zu lange Meldungen bei Bedarf einkürzen.
        if len(comment) > 67:
            self.logger.warning("Warnung '%s': Kommentar '%s' überschreitet die Längenbegrenzung von APRS-Bulletins." % ( alert.aid, comment ))
            if self.truncate:
                comment = comment[0:64]
                self.logger.info("Warnung '%s': Kürze auf '%s'." % ( alert.aid, comment ))
                comment += "..."

        packet = (':BLN%s:' % self.bulletin_id) + comment.replace('|', '').replace('~', '')

        frame = APRSFrame(
            self.dstcall,
            self.mycall,
            packet.encode(),
            repeaters = [ AX25Address(addr) for addr in self.digipath ]
        )

        return [ frame ]


    def _get_beacon(self, alert, pids, cancel, infoidx, symbol, pos, time, comment):
        multiarea = len(pos) > 1

        calls = []
        for pid in pids:
            for pidx, p in enumerate(pos):
                calls.append(( pid, p, chr(min(pidx, 26) + ord('A')) ))

            # Wir benutzen nur die erste Persistent-ID um die Airtime gering zu
            # halten. Ggf. können wir überlegen, ob wir die Meldung für alle
            # Persistent-IDs übertragen oder alle APRS-Objekte bis auf das
            # erste canceln.
            break

        frames = []
        for pid, p, pidx in calls:
            call = self.beacon_prefix
            call += '%d' % pid
            if multiarea:
                call += pidx

            if infoidx is not None:
                infoidxnum = infoidx
                infoidxstr = ""
                while infoidxnum:
                    infoidxstr = (chr(ord('A') + (infoidxnum % 26))) + infoidxstr
                call += infoidxstr

            if len(call) > 9:
                newcall = call[:9]
                self.logger.warning("Warnung '%s': APRS-Objektbezeichnung '%s' zu lang. Kürze auf '%s'." % ( alert.aid, call, newcall ))
                call = newcall

            lat = (p.GetY() +  90.0) % 180 -  90.0
            lon = (p.GetX() + 180.0) % 360 - 180.0

            if self.beacon_compressed:
                coord = APRSCompressedCoordinates(
                    lat = APRSCompressedLatitude(lat),
                    lng = APRSCompressedLongitude(lon),
                    symbol = symbol
                )
            else:
                coord = APRSUncompressedCoordinates(
                    lat = APRSLatitude(lat),
                    lng = APRSLongitude(lon),
                    symbol = symbol
                )

            if time is not None:
                time = DHMUTCTimestamp(
                    day = time.day,
                    hour = time.hour,
                    minute = time.minute
                )

            packet = ''
            if time is None:
                packet += ')'
                packet += call.ljust(3)
                packet += '_' if cancel else '!'
            else:
                packet += ';'
                packet += call.ljust(9)
                packet += '_' if cancel else '*'
                packet += str(time)

            packet += str(coord)

            if comment is None:
                if cancel:
                    comment = "Unspezifische MoWaS-Warnung"
                else:
                    comment = "Unspezifische MoWaS-Entwarnung"

            # Zu lange Meldungen bei Bedarf einkürzen.
            # TODO: Bei Area-Baken sind es max 36 Zeichen.
            max_comment = 43
            if len(comment) > max_comment:
                self.logger.warning("Warnung '%s': Kommentar '%s' überschreitet die Längenbegrenzung von APRS-Baken." % ( alert.aid, comment ))
                if self.truncate:
                    comment = comment[0:max_comment - 3]
                    self.logger.info("Warnung '%s': Kürze auf '%s'." % ( alert.aid, comment ))
                    comment += "..."

            packet += comment

            frames.append(
                APRSFrame(
                    self.dstcall,
                    self.mycall,
                    packet.encode(),
                    repeaters = [ AX25Address(addr) for addr in self.digipath ]
                )
            )

        return frames


    def alert(self, alerts):
        t = datetime.datetime.now(datetime.timezone.utc)

        frames = []
        alerts_send = []
        for alert, capdata in self.query(alerts, t):
            pids = alert.attr_get('pids')
            multiinfo = len(capdata['info']) > 1

            # Feststellen, ob es eine Entwarnung ist.
            if 'msgType' in capdata:
                cancel = capdata['msgType'].lower() == 'cancel'
            else:
                cancel = False

            # Wir betrachten alle Ereignisse einer Warnung als separate
            # APRS-Objekte.
            for infoidx, info in enumerate(capdata['info']):
                symbol = APRSSymbol('\\', '\'')
                pos = self._get_pos(alert, info)
                time = self._get_time(info, capdata, t)
                comment = self._get_comment(info)

                frames.extend(self._get_bulletin(alert, pos, comment))
                frames.extend(self._get_beacon(alert, pids, cancel, infoidx if multiinfo else None, symbol, pos, time, comment))

            alerts_send.append(alert)

        # Alle Frames auf einmal senden
        self.send(frames)

        for alert in alerts_send:
            alert.tx_done(self.ttype, self.tname, t)


    def send(self, alerts):
        pass



class TargetAprsKiss(TargetAprs):
    def __init__(self, tname, config):
        super().__init__(tname, config)

        config_kiss = config.get_subtree('kiss', "Ungültige KISS-konfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ))

        self.kiss_ports = config_kiss.get_list('ports')
        self.kiss_ports = [ p for p in self.kiss_ports if isinstance(p, int) and p < 16 ]


    def send(self, frames):
        kissdata = bytes()

        for p in self.kiss_ports:
            for f in frames:
                kissdata += b'\xc0'
                kissdata += bytes([ 16 * (p % 16) ])
                kissdata += bytes(f).replace(b'\xdb', b'\xdb\xdd').replace(b'\xc0', b'\xdb\xdc')
                kissdata += b'\xc0'

        return kissdata



class TargetAprsKissSerial(TargetAprsKiss):
    ttype = 'aprs_kiss_serial'


    def __init__(self, tname, config):
        super().__init__(tname, config)

        config_serial = config.get_subtree('serial', "Ungültige Schnittstellenkonfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ))

        self.serial_device = config_serial.get_str('device')
        self.serial_baud   = config_serial.get_int('baud', 115200)
        self.cmd_up        = config_serial.get_bin('cmd_up', '')
        self.cmd_down      = config_serial.get_bin('cmd_down', '')
        self.cmd_pre       = config_serial.get_bin('cmd_pre', '')
        self.cmd_post      = config_serial.get_bin('cmd_post', '')

        with serial.Serial(self.serial_device, self.serial_baud) as conn:
            conn.write(self.cmd_up)


    def __del__(self):
        if hasattr(self, 'cmd_down'):
            with serial.Serial(self.serial_device, self.serial_baud) as conn:
                conn.write(self.cmd_down)


    def send(self, frames):
        if len(frames) == 0:
            return

        kissdata = super().send(frames)

        with serial.Serial(self.serial_device, self.serial_baud) as conn:
            conn.write(self.cmd_pre)
            conn.write(kissdata)
            conn.write(self.cmd_post)



class TargetAprsKissTcp(TargetAprsKiss):
    ttype = 'aprs_kiss_tcp'


    def __init__(self, tname, config):
        super().__init__(tname, config)

        config_remote = config.get_subtree('remote', "Ungültige Verbindungskonfiguration für Senke '%s/%s'" % ( self.ttype, self.tname ))

        self.remote_host = config_remote.get_str('host')
        self.remote_port = config_remote.get_int('port')


    def send(self, frames):
        if len(frames) == 0:
            return

        kissdata = super().send(frames)

        try:
            sock = socket.socket()
            sock.connect(( self.remote_host, self.remote_port ))
            sock.shutdown(socket.SHUT_RD)
            sock.send(kissdata)
            sock.shutdown(socket.SHUT_WR)
            sock.close()
        except ConnectionRefusedError:
            self.logger.error("Kann keine Verbindung zum TNC '%s:%s' aufbauen. Es wird nicht alarmiert." % ( self.remote_host, self.remote_port ))



# Konfiguration einlesen
with open(ARGS.config) as f:
    CONFIG = Config(yaml.safe_load(f), "Ungültige Konfiguration")


# Logging konfigurieren
log_config = CONFIG.get_subtree('logging', "Ungültige Logging-Konfiguration", optional = True)

log_fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
log_level = ARGS.log_level or log_config.get_str('level', 'warning').lower()
log_console = True if ARGS.log_console else log_config.get_bool('console', True)
log_file    = ARGS.log_file or log_config.get_str('file', null = True)

LOG_LEVELS = \
{
    'error':   logging.ERROR,
    'warning': logging.WARNING,
    'info':    logging.INFO,
    'debug':   logging.DEBUG,
}

LOGGER = logging.getLogger('mowas')

if log_level not in LOG_LEVELS:
    LOGGER.setLevel(logging.WARNING)
    log_fallback = True
else:
    LOGGER.setLevel(LOG_LEVELS[log_level])
    log_fallback = False

if log_console:
    log_console_handler = logging.StreamHandler(stream = sys.stdout)
    log_console_handler.setFormatter(log_fmt)
    LOGGER.addHandler(log_console_handler)

if log_file is not None:
    log_file_handler = logging.FileHandler(log_file)
    log_file_handler.setFormatter(log_fmt)
    LOGGER.addHandler(log_file_handler)

if log_fallback:
    LOGGER.warning("Unbekannter Log-Level '%s'. Falle auf 'warning' zurück." % log_level)


# Datenstrukturen initialisieren
GEODATA = Geodata(CONFIG.get_subtree('geodata', "Ungültige Geodaten-Konfiguration", optional = True))
CACHE = Cache(CONFIG.get_subtree('cache', "Ungültige Cache-Konfiguration"))


# Quellen initialisieren
SOURCE_CLASSES = \
[
    ( 'darc',     SourceDARC    ),
    ( 'bbk_file', SourceBBKFile ),
    ( 'bbk_url',  SourceBBKUrl  ),
]

SOURCE_CONFIG = CONFIG.get_subtree('source', "Ungültige Quellen-Konfiguration")
SOURCES = []
for stype, sclass in SOURCE_CLASSES:
    sources = SOURCE_CONFIG.get_dict(stype, {})
    for sname, s in sources.items():
        SOURCES.append(sclass(sname, Config(s, "Ungültige Konfiguration für Quelle '%s/%s'" % ( stype, sname ))))


# Senken initialisieren
TARGET_CLASSES = \
[
    ( 'aprs_kiss_serial', TargetAprsKissSerial ),
    ( 'aprs_kiss_tcp',    TargetAprsKissTcp    ),
]

TARGET_CONFIG = CONFIG.get_subtree('target', "Ungültige Senken-Konfiguration")
TARGETS = []
for ttype, tclass in TARGET_CLASSES:
    targets = TARGET_CONFIG.get_dict(ttype, {})
    for tname, t in targets.items():
        TARGETS.append(tclass(tname, Config(t, "Ungültige Konfiguration für Senke '%s/%s'" % ( ttype, tname ))))


# Prüfintervall festlegen
PERIOD = 60

# Hauptschleife
while True:
    try:
        # Zeit bestimmen
        t1 = datetime.datetime.now(datetime.UTC)

        LOGGER.debug("Alarmierungsschleife beginnt.")

        # Alle Quellen abrufen
        for s in SOURCES:
            try:
                for alert in s.fetch():
                    CACHE.update(alert)
            except Exception as e:
                LOGGER.error("Fehler beim Abfragen der Quelle '%s'" % s.stype)
                LOGGER.exception(e)

        # Veraltete Warnungen löschen
        valid = CACHE.purge()

        # IDs vergeben
        CACHE.persistent_ids()

        # Zu alarmierende Warnungen abfragen
        alerts = CACHE.query()

        # Alarmierung vornehmen
        for t in TARGETS:
            try:
                t.alert(alerts)
            except Exception as e:
                LOGGER.error("Fehler bei der Alarmierung über Senke '%s/%s'" % ( t.ttype, t.tname ))
                LOGGER.exception(e)

        # Cache aktualisieren
        CACHE.dump()

        # Temporäre Daten der Quellen aufräumen
        for s in SOURCES:
            try:
                s.purge(valid)
            except Exception as e:
                LOGGER.error("Fehler beim Aufräumen der Quelle '%s'" % s.stype)
                LOGGER.exception(e)

        LOGGER.debug("Alarmierungsschleife abgearbeitet.")

        # Zeit bestimmen
        t2 = datetime.datetime.now(datetime.UTC)

        # Wartezeit ausrechnen, sodass wir die Schleife in passender Phasenlage
        # zu `t1` wieder beginnen.
        time.sleep((t1 - t2).total_seconds() % PERIOD)

    except KeyboardInterrupt:
        break
