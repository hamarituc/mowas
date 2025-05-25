#!/bin/env python3

from aioax25.aprs.datetime import DHMUTCTimestamp
from aioax25.aprs.frame import APRSFrame
from aioax25.aprs.position import APRSLatitude
from aioax25.aprs.position import APRSLongitude
from aioax25.aprs.position import APRSUncompressedCoordinates
# from aioax25.aprs.position import APRSCompressedLatitude
# from aioax25.aprs.position import APRSCompressedLongitude
# from aioax25.aprs.position import APRSCompressedCoordinates
from aioax25.aprs.symbol import APRSSymbol
from aioax25.frame import AX25Address
import argparse
import datetime
import json
from osgeo import ogr
from osgeo import osr
import pytz
import requests
import serial
import socket
import sys



#
# Spezifikationen
#  - CAP: https://docs.oasis-open.org/emergency/cap/v1.2/CAP-v1.2-os.html
#  - APRS:
#    - https://raw.githubusercontent.com/wb2osz/aprsspec/main/Understanding-APRS-Packets.pdf
#    - https://www.aprs.org/doc/APRS101.PDF
#



parser = argparse.ArgumentParser(
    description = "MoWaS-Meldungen per APRS verteilen"
)

parser.add_argument(
    '-r', '--regions',
    type = str,
    nargs = '+',
    required = True,
    help = "Gebietsschlüssel der zu warnenden Bereiche")

parser.add_argument(
    '-U', '--bbk-url',
    type = str,
    nargs = '+',
    default = [],
    help = "URL für das BBK-Warn-File")

parser.add_argument(
    '-J', '--bbk-file',
    type = str,
    nargs = '+',
    default = [],
    help = "JSON-File")

parser.add_argument(
    '-D', '--darc-file',
    type = str,
    help = "DARC JSON Notify-File")

parser.add_argument(
    '--darc-fetch-cap',
    type = str,
    choices = [ 'internet', 'hamnet' ],
    help = "Erweiterte CAP-Daten des DARC-Datensatzes herunterladen")

parser.add_argument(
    '-S', '--kiss-serial',
    type = str,
    help = "Serielle Schnittstelle für KISS-Modem")

parser.add_argument(
    '--baudrate',
    type = int,
    default = 115200,
    help = "Baudrate KISS-Modem")

parser.add_argument(
    '--kiss-port-serial',
    type = int,
    nargs = '+',
    default = [ 0 ],
    help = "KISS-Ports, an die die Pakete per serieller Schnittstelle geschickt werden sollen")

parser.add_argument(
    '-T', '--kiss-tcp',
    type = str,
    metavar = "HOST:PORT",
    help = "Netzwerkadresse für TCP-basierte KISS-Schnittstelle")

parser.add_argument(
    '--kiss-port-tcp',
    type = int,
    nargs = '+',
    default = [ 0 ],
    help = "KISS-Ports, an die die Pakete per TCP-Schnittstelle geschickt werden sollen")

parser.add_argument(
    '--no-position',
    action = 'store_true',
    help = "Warnung niemals mit Ortsbezug aussenden sondern generell als Bulletins")

parser.add_argument(
    '--no-time',
    action = 'store_true',
    help = "Keine Zeitpunkte in APRS-Baken kodieren")

parser.add_argument(
    '--beacon-prefix',
    type = str,
    default = 'MOWA-',
    help = "Präfix für MoWaS-Beacons")

parser.add_argument(
    '--mycall',
    type = str,
    required = True,
    help = "Rufzeichen der aussendenden Station")

parser.add_argument(
    '--dstcall',
    type = str,
    default = 'APMOWA',
    help = "Gerätetype in Form eines 'Zielrufzeichens'")

parser.add_argument(
    '-d', '--digipath',
    type = str,
    default = [ 'WIDE1-1' ],
    help = "Digipath für APRS-Frames")


ARGS = parser.parse_args()



# Übergeordnete Bereiche bestimmen
def area_superset(geocode : str) -> set:
    areas = []
    areas.append("000000000000")
    areas.append(geocode[0:2] + "0000000000")
    areas.append(geocode[0:3] + "000000000")
    areas.append(geocode[0:5] + "0000000")
    areas.append(geocode[0:9] + "000")
    areas.append(geocode)

    return set(areas)


# Redundante Bereiche filtern
def area_filter_redundant(geocodes : [ list, set ]) -> set:
    return { g for g in geocodes if not (area_superset(g) - { g }) & set(geocodes) }


def bbk_fetch(urls):
    for url in urls:
        # TODO: Parallel
        # TODO: Timeout
        r = requests.get(url)
        try:
            r.raise_for_status()
        except requests.exceptions.HTTPError as e:
            sys.stderr.write("Fehler bei der Abfrage von '%s': %s\n" % ( url, e ))
            continue

        try:
            capdata = r.json()
        except requests.exceptions.JSONDecodeError as e:
            sys.stderr.write("Fehler beim Parsen der Rückgabe von '%s': %s\n" % ( url, e ))
            continue

        yield capdata



WARNINGS = {}

# CAP-Datensatz vom BBK herunterladen
for capdata in bbk_fetch(ARGS.bbk_url):
    for w in capdata:
        if w['identifier'] not in WARNINGS:
            WARNINGS[w['identifier']] = w

# Bereits heruntergeladenen CAP-Datensatz einlesen
for p in ARGS.bbk_file:
    with open(p) as f:
        # TODO: Fehlerbehandlung
        capdata = json.load(f)
        for w in capdata:
            if w['identifier'] not in WARNINGS:
                WARNINGS[w['identifier']] = w



#
# Warnungen filtern
#

# Gebietsschlüssel auf Plausibilität prüfen.
regioncodes = []
for r in ARGS.regions:
    if len(set(r) - set("0123456789")) > 0:
        sys.stderr.write("Ungültiger Gebietsschlüssel '%s': Nur Zahlen erlaubt." % r)
        continue
    if len(r) > 12:
        sys.stderr.write("Ungültiger Gebietsschlüssel '%s': Zu lang. Kürze auf 12 Stellen.\n" % r)
        regioncodes.append(r[0:12])
    elif len(r) in [ 2, 3, 5, 9, 12 ]:
        # Zu Kurze Regionalschlüssel ggf. erweitern
        regioncodes.append(r.ljust(12, "0"))
    else:
        sys.stderr.write("Ungültiger Gebietsschlüssel '%s': Zu kurz.\n" % r)
        continue

# Gebietsschlüssel verwerfen, die bereits durch übergeordnete erfasst sind.
regioncodes = area_filter_redundant(regioncodes)

# Alle übergeordneten Gebiete einbeziehen.
regioncodes_super = set()
for r in regioncodes:
    regioncodes_super |= area_superset(r)

# Aktuelle Uhrzeit für die Filterung.
NOW = datetime.datetime.now().astimezone(pytz.utc)

# Warnungen ohne Info-Element sind zulässig, aber damit können wir nichts
# anfangen.
WARNINGS = { identifier: w for identifier, w in WARNINGS.items() if 'info' in w }

WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)

# Alle Warnungen filtern.
WARNINGS_FILTER = {}
for identifier, w in WARNINGS.items():
    # Warnungen ohne Info-Element sind zulässig, aber damit können wir nichts
    # anfangen.
    if 'info' not in w:
        continue

    infos = []
    for info in w['info']:
        # Warnungen ohne Ortsbezug können wir nicht verarbeiten.
        if 'area' not in info:
            continue

        areas = []
        for area in info['area']:
            # Polygon in eine geometrische Datenstruktur überführen.
            if 'polygon' in area:
                multipolygon = ogr.Geometry(ogr.wkbMultiPolygon)
                for ringstr in area['polygon']:
                    ring = ogr.Geometry(ogr.wkbLinearRing)
                    for coords in ringstr.split():
                        x, y = coords.split(',')
                        ring.AddPoint(float(x), float(y))

                    poly = ogr.Geometry(ogr.wkbPolygon)
                    poly.AddGeometry(ring)
                    multipolygon.AddGeometry(poly)
                multipolygon.AssignSpatialReference(WGS84)

                area['polygon'] = multipolygon

            # TODO: Umkreise ebenfalls in das Polygon einbeziehen. Dazu muss
            # der Mittelpunkt zunächst in ein metrisches Bezugssystem
            # konvertiert werden.

            # Ohne Gebietsschlüssel können wir die Warnung nicht verarbeiten.
            # Ggf. könnten wir bei der Veröffentlichung von Polygonen auf
            # geometrische Überschneidungen mit den von uns spezifierten
            # Warngebieten prüfen.
            if 'geocode' not in area:
                continue

            # Eine Nachricht wird übernommen, wenn sie unterhalb der von uns
            # spezifizierten Gebiete liegt oder für eines der uns
            # übergeordneten Gebiete kodiert ist.
            geocodes = []
            for g in area['geocode']:
                geocode = g['value']
                geocode_super = area_superset(geocode)
                if geocode in regioncodes_super or \
                   len(geocode_super & regioncodes) > 0:
                    geocodes.append(g)

            if len(geocodes) == 0:
                continue
            area['geocode'] = geocodes
            areas.append(area)

        if len(areas) == 0:
            continue
        info['area'] = areas
        infos.append(info)

    # Zeitstempel kodieren
    if 'sent' in w:
        # Der Ausgabezeitpunkt muss immer enthalten sein. Aber programmieren
        # defensiv, um nicht bei invaliden Daten abzubrechen.
        w['sent'] = datetime.datetime.fromisoformat(w['sent']).astimezone(pytz.utc)
    for info in infos:
        if 'effective' in info:
            info['effective'] = datetime.datetime.fromisoformat(info['effective']).astimezone(pytz.utc)
        if 'onset' in info:
            info['onset'] = datetime.datetime.fromisoformat(info['onset']).astimezone(pytz.utc)
        if 'expires' in info:
            info['expires'] = datetime.datetime.fromisoformat(info['expires']).astimezone(pytz.utc)

    # Abgelaufene Meldungen filtern
    infos = [ info for info in infos if 'expires' not in info or info['expires'] < NOW ]

    # Warnungen, die uns nicht betreffen ignorieren wir.
    if len(infos) == 0:
        continue
    w['info'] = infos

    WARNINGS_FILTER[identifier] = w



#
# APRS-Meldung formatieren
#

FRAMES = []
IDX = 0

for w in WARNINGS_FILTER.values():
    if 'msgType' in w:
        cancel = w['msgType'].lower() == 'cancel'
    else:
        cancel = False

    for info in w['info']:
        # Einheitssymbol
        symbol = APRSSymbol('\\', '\'')

        # Position bestimmen
        if ARGS.no_position:
            pos = []
        else:
            for area in info['area']:
                pos = []
                if 'polygon' in area:
                    pos.append(area['polygon'].Centroid())
                elif 'geocode' in area:
                    for geocode in area['geocode']:
                        # TODO: Gebiet in Centroid auflösen
                        pass

        # Zeitpunkt bestimmen
        if ARGS.no_time:
            time = None
        if 'onset' in info:
            time = info['onset']
        elif 'effective' in info:
            time = info['effective']
        elif 'sent' in w:
            time = w['sent']
        else:
            time = None

        # Bei lang zurückliegenden Meldungen geben wir keinen Zeitpunkt an,
        # da wir bei APRS nur den Monatstag übermitteln können. Um
        # Eindeutigkeit sicherzustellen, werden zukünftige Meldungen eine Woche
        # im Voraus und vergangene Meldungen 3 Wochen im Nachgang mit einer
        # Zeitangabe versehen.
        if NOW - time >= datetime.timedelta(days = 21) or \
           NOW - time <= datetime.timedelta(days = -7):
            time = None

        # Meldungstext
        comment = ''
        if 'headline' in info:
            comment += info['headline']

        if comment.strip() == '':
            comment = None

        # APRS-Pakete erstellen
        if len(pos) == 0:
            # Bulletin

            # Ohne Meldungstext gibt es nichts zu warnen.
            if comment is None:
                continue

            packet = ':BLN0MOWAS:' + comment.replace('|', '').replace('~', '')

            FRAMES.append(
                APRSFrame(
                    ARGS.dstcall,
                    ARGS.mycall,
                    packet.encode(),
                    repeaters = [ AX25Address(addr) for addr in ARGS.digipath ]
                )
            )

        else:
            # Bake
            for p in pos:
                call = ARGS.beacon_prefix
                call += ('%d' % IDX)[len(call) - 9:]
                IDX += 1

                lat = (p.GetY() +  90.0) % 180 -  90.0
                lon = (p.GetX() + 180.0) % 360 - 180.0

                coord = APRSUncompressedCoordinates(
                    lat = APRSLatitude(lat),
                    lng = APRSLongitude(lon),
                    symbol = symbol
                )
                # coord = APRSCompressedCoordinates(
                #     lat = APRSCompressedLatitude(lat),
                #     lng = APRSCompressedLongitude(lon),
                #     symbol = symbol
                # )

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
                        packet += "Unspezifische MoWaS-Warnung"
                    else:
                        packet += "Unspezifische MoWaS-Entwarnung"
                else:
                    packet += comment

                FRAMES.append(
                    APRSFrame(
                        ARGS.dstcall,
                        ARGS.mycall,
                        packet.encode(),
                        repeaters = [ AX25Address(addr) for addr in ARGS.digipath ]
                    )
                )



#
# Frames per KISS über TNC oder TCP-Socket ausgeben
#

def kiss_data(frames, ports):
    kissstr = bytes()
    for p in ports:
        for f in frames:
            kissstr += b'\xc0'
            kissstr += bytes([ 16 * (p % 16) ])
            kissstr += bytes(f).replace(b'\xdb', b'\xdb\xdd').replace(b'\xc0', b'\xdb\xdc')
            kissstr += b'\xc0'

    return kissstr

if ARGS.kiss_serial is not None:
    with serial.Serial(ARGS.kiss_serial, ARGS.baudrate) as kissconn:
        kissconn.write(kiss_data(FRAMES, ARGS.kiss_port_serial))

if ARGS.kiss_tcp is not None:
    if ':' in ARGS.kiss_tcp:
        host, port = ARGS.kiss_tcp.split(':')[0:2]
    else:
        host = ARGS.kiss_tcp
        port = 8001

    sock = socket.socket()
    sock.connect(( host, port ))
    sock.shutdown(socket.SHUT_RD)
    sock.send(kiss_data(FRAMES, ARGS.kiss_port_tcp))
    sock.shutdown(socket.SHUT_WR)
    sock.close()
