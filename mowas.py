#!/bin/env python3

import datetime
import json
import os
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



# Konfiguration einlesen
with open('mowas.yml') as f:
    CONFIG = yaml.safe_load(f)

CACHE = Cache(CONFIG.get('cache', {}))

valid  = CACHE.purge()

CACHE.dump()
