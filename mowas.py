#!/bin/env python3

import datetime
import json
import os
import requests
import sys
import yaml



class JSONDateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.time, datetime.datetime)):
            return obj.isoformat()



def parse_time(s):
    match = re.fullmatch('([0-9]+)([mhdw]?)', s)
    if match is None:
        raise ValueError("Ungültiges Zeitintervall '%s'" % s)

    t = datetime.timedelta(minutes = int(match[1]))

    unit = match[2]
    if unit == 'h':
        t *= 60
    elif unit == 'd':
        t *= 60 * 24
    elif unit == 'w':
        t *= 60 * 24 * 7

    return t



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
        if not isinstance(config, dict):
            sys.stderr.write("Ungültige Konfiguration für BBK-URL-Quelle: Konfiguration muss ein Dictionary sein.\n")
            sys.exit(-1)

        if 'url' not in config:
            sys.stderr.write("Ungültige Konfiguration für BBK-URL-Quelle: Kein Pfad mit Parameter 'path' angegeben.\n")
            sys.exit(-1)

        self.url = config['url']


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
        self.alerts = {}

        if not isinstance(config, dict):
            sys.stderr.write("Ungültige Cache-Konfiguration: Konfiguration muss ein Dictionary sein.\n")
            sys.exit(-1)

        if 'path' not in config:
            sys.stderr.write("Ungültige Cache-Konfiguration: Kein Pfad mit Parameter 'path' angegeben.\n")
            sys.exit(-1)

        self.path = os.path.join(config['path'])
        self.age  = parse_time(config.get('purge', '31d'))

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



# Konfiguration einlesen
with open('mowas.yml') as f:
    CONFIG = yaml.safe_load(f)

SOURCES = []

if 'source' not in CONFIG or not isinstance(CONFIG['source'], dict):
    sys.stderr.write("Ungültige Konfiguration: Quellenangabe 'source' muss ein Dictionary sein.")
    sys.exit(-1)

CACHE = Cache(CONFIG.get('cache', {}))

for s in CONFIG['source'].get('bbk_url', []):
    SOURCES.append(SourceBBKUrl(s))

valid  = CACHE.purge()
CACHE.persistent_ids()
alerts = CACHE.query()

CACHE.dump()
