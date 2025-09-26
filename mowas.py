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
import copy
import datetime
import json
import os
from osgeo import ogr
import re
import requests
import sys
import yaml



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
        return Config(subtree, "%s: Dictionary erwartet." % errmsg)


    def _get_value(self, key, default = None):
        if default is None and key not in self.tree:
            raise ConfigException("Attribut '%s' ist erfordertlich.\n" % key)
        return self.tree.get(key, default)


    def get_bool(self, key, default = None):
        value = self._get_value(key, default)
        if value not in [ True, False ]:
            raise ConfigException("Ungültiges Attribut '%s': Boolean erwartet." % key)
        return value


    def get_int(self, key, default = None):
        value = self._get_value(key, default)
        if not isinstance(value, int):
            raise ConfigException("Ungültiges Attribut '%s': Ganzzahl erwartet." % key)
        return value


    def get_str(self, key, default = None):
        value = self._get_value(key, default)
        if not isinstance(value, str):
            raise ConfigException("Ungültiges Attribut '%s': String erwartet." % key)
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



class SourceBBKUrl:
    def __init__(self, config):
        self.url = config.get_str('url')


    def fetch(self):
        r = requests.get(self.url)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            sys.stderr.write("Fehler bei der Abfrage von '%s': %s\n" % ( self.url, e ))
            return

        try:
            capdata = r.json()
        except requests.exceptions.JSONDecodeError as e:
            sys.stderr.write("Fehler beim Parsen der Rückgabe von '%s': %s\n" % ( self.url, e ))
            return

        for alertdata in capdata:
            yield Alert(alertdata)



class Cache:
    def __init__(self, config):
        self.path = config.get_str('path')
        self.age  = config.get_duration('purge', '31d')

        self.alerts = {}

        if os.path.isfile(self.path):
            with open(self.path) as f:
                try:
                    data = json.load(f)
                except json.decoder.JSONDecodeError:
                    sys.stderr.write("Fehler beim Laden des Caches '%s'." % self.path)
                    return
        else:
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
                if refs[aid] - set(pids.keys()):
                    nopids_new[aid] = alert
                    continue

                # Die Persistent-IDs aller referenzierten Warnungen vereinigen
                pid = set()
                for ref_aid in refs[aid]:
                    pid |= set(pids[ref_aid])

                # Sollte es keine refernzierten Warnungen geben, vergeben wir
                # eine neue Persistent-ID. Es wird bei 1 beginnend eine nicht
                # belegte ID nach First Fit gesucht.
                pid = sorted(pid)
                if len(pid) == 0:
                    pid = [ freepids.pop(0) ]

                pids[aid] = pid
                alert.attr_set('pids', pid)
                rerun = True

            nopids = nopids_new

        if aid in nopids.keys():
            sys.stderr.write("Warnung '%s' ist Bestandteil eines zirkulären Verweises." % aid)
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


    def __init__(self, config):
        # Gebietsschlüssel auf Plausibilität prüfen.
        geocodes = []
        for i, r in enumerate(config.get_list('geocodes', [])):
            if not isinstance(r, str):
                raise ConfigException("Ungültiger Gebietsschlüssel '%s': String erwartet." % r)

            if len(set(r) - set("0123456789")) > 0:
                raise ConfigException("Ungültiger Gebietsschlüssel '%s': Nur Ziffern erlaubt." % r)

            if len(r) > 12:
                sys.stderr.write("Ungültiger Gebietsschlüssel '%s': Zu lang. Kürze auf 12 Stellen.\n" % r)
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

        # Alle vorausliegenden Übertragungszeitpunkt berechnen
        diffs = [ d for d in self.sched if d > diff ]

        # Alle Übertragungen wurden abgeschlossen
        if len(diffs) == 0:
            return False

        return first + diffs[0] <= t



class Target:
    def __init__(self, tname, config):
        self.tname = tname
        self.filter = Filter(config.get_subtree('filter', "Ungültige Filter-Konfiguration", True))


    def query(self, alerts, t):
        for alert in alerts:
            # Warnungen, die noch nie übertragen wurden, aber zu alt sind,
            # verwerfen wir. Sie kommen ggf. dadurch zu Stande, dass der Cache
            # leer war. Wir vermeiden es somit, veraltete Warnungen erneut zu
            # auszulösen.
            if not self.filter.match_age(alert, self.ttype, self.tname, t):
                continue

            capdata = copy.deepcopy(alert.capdata)

            if 'info' not in capdata:
                continue

            infos = []
            for info in capdata['info']:
                # Abgelaufene Meldungen verwerfen
                if 'expires' in info and info['expires'] < t:
                    continue

                # Meldungen verwerfen, die noch nicht aktiv sind
                if 'onset' in info and info['onset'] >= t:
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

        self.sched             = Schedule(config.get_subtree('schedule', "Ungültiger Widerholungsrhythmus"))
        self.dstcall           = config.get_str('dstcall', 'APMOWA')
        self.mycall            = config.get_str('mycall')
        self.digipath          = config.get_list('digipath', [ 'WIDE1-1' ])
        self.beacon_prefix     = config.get_str('beacon_prefix', 'MOWA')
        self.max_areas         = config.get_int('max_areas', 0)
        self.no_position       = config.get_bool('no_position', False)
        self.no_time           = config.get_bool('no_time', False)
        self.compress_position = config.get_bool('compress_position', False)
        self.bulletin_id       = config.get_str('bulletin_id', '0MOWAS')[0:6].ljust(6, ' ')
        self.bulletin_mode     = config.get_str('bulletin_mode', 'fallback').lower()

        if self.bulletin_mode not in [ 'never', 'fallback', 'always' ]:
            sys.stderr.write("Senke '%s/%s': Unbekannter Bulletin-Modus '%s'. Falle auf Standardeinstellung 'fallback' zurück.\n" % ( self.ttype, self.tname, self.bulletin_mode ))
            self.bulletin_mode = 'fallback'


    def query(self, alerts, t):
        for alert, capdata in super().query(alerts, t):
            # Nachrichten nur wiederholen, wenn es das Wiederholungsintervall
            # verlangt.
            if not self.sched.tx_required(alert, self.ttype, self.tname, t):
                continue

            yield alert, capdata


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
    def _get_pos(self, info):
        if self.no_position:
            return []

        # Wir behandeln jedes Gebiet einzeln.
        for area in info['area']:
            polys = []

            # Polygon in eine geometrische Datenstruktur überführen.
            # Selbst ein Gebiet kann aus mehreren Polygonen bestehen. Dies ist
            # z.B. bei Flächen mit Löchern der Fall. Wir müssen uns aber nicht
            # um diese Sonderfälle kümmern. Die `ogr`-Bibliothek berücksichtigt
            # das bereits alles.
            if 'polygon' in area:
                polygon = ogr.Geometry(ogr.wkbPolygon)
                for ringstr in area['polygon']:
                    ring = ogr.Geometry(ogr.wkbLinearRing)
                    for coords in ringstr.split():
                        x, y = coords.split(',')
                        ring.AddPoint(float(x), float(y))

                    ring.FlattenTo2D()
                    polygon.AddGeometry(ring)
                polys.append(polygon)

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
        if self.no_time:
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

        return time


    def _get_comment(self, info):
        # Meldungstext
        comment = ''
        if 'headline' in info:
            comment += info['headline']

        # Unicode-Umlaute werden auf Mobilgeräten evt. nicht korrekt
        # dargestellt.
        comment = re.sub(r'A([A-Z])', r'AE\1', comment)
        comment = re.sub(r'Ö([A-Z])', r'OE\1', comment)
        comment = re.sub(r'Ü([A-Z])', r'UE\1', comment)
        comment = re.sub(r'([A-Z])A', r'AE\1', comment)
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


    def _get_bulletin(self, pos, comment):
        if self.bulletin_mode == 'never':
            # Generell auf Bulletins verzichten.
            return []
        elif self.bulletin_mode == 'fallback':
            # Bulletins nur übertragen, wenn keine Positionen vorhanden sind.
            if len(pos) > 0:
                return []

        # Ohne Meldungstext gibt es nichts zu warnen.
        if comment is None:
            return []

        packet = (':BLN%s:' % self.bulletin_id) + comment.replace('|', '').replace('~', '')

        frame = APRSFrame(
            self.dstcall,
            self.mycall,
            packet.encode(),
            repeaters = [ AX25Address(addr) for addr in self.digipath ]
        )

        return [ frame ]


    def _get_beacon(self, pids, cancel, infoidx, symbol, pos, time, comment):
        multiarea = len(pos) > 1

        calls = []
        for pid in pids:
            for pidx, p in enumerate(pos):
                calls.append(( pid, p, chr(min(pidx, 26) + ord('A')) ))

            # Wir benutzen nur die erste Persistent-ID um die Airtime gering zu
            # halten. Ggf. können wir überlegen, ob wir die Meldung für alle
            # Persistent-IDs übertragen oder alle APRS-Objekte bis auf das
            # erste caceln.
            break

        frames = []
        for pid, p, pidx in calls:
            call = self.beacon_prefix
            call += '%d' % pid
            if multiarea:
                call += pidx

            if infoidx is not None:
                if not multiarea:
                    call += '-'
                call += '%d' % infoidx

            if len(call) > 9:
                newcall = call[:9]
                sys.stderr.write("APRS-Objektbezeichnung '%s' zu lang. Kürze auf '%s'.\n" % ( call, newcall ))
                call = newcall

            lat = (p.GetY() +  90.0) % 180 -  90.0
            lon = (p.GetX() + 180.0) % 360 - 180.0

            if self.compress_position:
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
                pos = self._get_pos(info)
                time = self._get_time(info, capdata, t)
                comment = self._get_comment(info)

                frames.extend(self._get_bulletin(pos, comment))
                frames.extend(self._get_beacon(pids, cancel, infoidx if multiinfo else None, symbol, pos, time, comment))



# Konfiguration einlesen
with open('mowas.yml') as f:
    CONFIG = Config(yaml.safe_load(f), "Ungültiges Konfiguration")

CACHE = Cache(CONFIG.get_subtree('cache', "Ungültige Cache-Konfiguration"))


# Quellen initialisieren
SOURCE_CONFIG = CONFIG.get_subtree('source', "Ungültige Quellen-Konfiguration")
SOURCES = []
for s in SOURCE_CONFIG.get_list('bbk_url', []):
    SOURCES.append(SourceBBKUrl(Config(s, "Ungültige Konfiguration für BBK-URL-Quelle")))

for s in SOURCES:
    for alert in s.fetch():
        CACHE.update(alert)

valid  = CACHE.purge()
CACHE.persistent_ids()
alerts = CACHE.query()

CACHE.dump()
