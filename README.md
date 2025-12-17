MoWaS-Anbindung für automatisch arbeitende Amateurfunkstellen
=============================================================

Dieses Projekt dient der Anbindung des Modularen Warnsystems der Bundesrepublik
Deutschland an diverse Amateurfunkdienste. Hierzu können mehrere Warnquellen an
mehrere Alarmierungskanäle (Senken) angebunden werden. Warnungen werden auf
Basis des Common Alerting Protocols (CAP) verarbeitet.

**ACHTUNG:** Dieses Projekt befindet sich im Entwicklungsstadium. Es ist noch
  nicht für den Produktiveinsatz freigegeben.


Funktionsweise
--------------

Warnungen werden periodisch von den konfigurierten Quellen abgerufen. Sie
werden anschließend auf die konfigurierten Senken verteilt. Hierbei lassen sich
geografische Filter einrichten, um nur für das Zielgebiet zutreffende Warnungen
zu behandeln. Warnungen werden nach einem konfigurierbaren Zeitschema
wiederholt, zunächst häufiger, wenn die Warnung ausgegeben wird und mit
zunehmend größerem Abstand, je älter die Warnung wird. Einmal abgerufene
Warnungen werden in einem lokalen Cache vorgehalten, sodass auch bei Ausfall
einer Anbindung an die Warnquellen weiterhin mit dem letzten Datenstand
alarmiert werden kann.

Folgende Warnquellen stehen zur Verfügung.

 - Öffentliche CAP-Daten des Bundesamts für Bevölkerungsschutz und
   Katastrophenhilfe
 - MoWaS-Schnittelle des Deutschen Amateurradio Clubs
   (https://mowas.notfunk.radio/)

Warnungen aus unterschiedlichen Quellen, die die gleiche CAP-Kennung haben,
werden als eine Warnung betrachtet. Es ist also möglich mehrere Quellen
anzubinden ohne Gefahr zu laufen, Warnungen mehrfach auszugeben.

Folgende Senken stehen zur Verfügung:

 - APRS-Meldungen per KISS-TNC über eine serielle Schnittstelle
 - APRS-Meldungen per KISS-TNS über eine TCP-Verbindung
 - APRS-Meldungen per Telnet-Protokoll
 - geplant: Sprachansagen auf SVXlink-Relais


Installation
------------

Zum Betrieb ist nur das Skript `mowas.py` notwendig. Das Skript
`mowas-geodata.py` wird nur einmal zur Aufbereitung von Geodaten benötigt.
Zweckmäßiger Weise installiert man beide Skript als ausfühbare Dateien nach
`/usr/bin/` bzw. `/usr/local/bin`.

```
# cp mowas.py /usr/local/bin
# cp mowas-geodata.py /usr/local/bin
```

Die Skripte erfordern die in `requirements.txt` aufgeführten Python-Pakete.
Stehen diese nicht allesamt über die Distribution zur Verfügung, kann man
alternativ wie folgt eine virtuelle Python-Umgebung einrichten, z.B. unter
`/opt/mowas`. Jedes andere Verzeichnis ist aber ebenfalls dafür geeignet.

```
# python -m venv /opt/mowas
# source /opt/mowas/bin/active
# pip install -U -r requirements.txt
```

Besonderes Augenmerk ist bei einer virtuellen Python-Umgebung für das
Python-Paket `gdal` erforderlich. Es erfordert die entsprechenden
`libgdal`-C-Biliothek, die über die Distribution installiert werden muss. Dabei
muss die Version des Python-`gdal`-Pakets zur Version der `libgdal`-Biliothek
passen. Ggf. ist in `requirements.txt` die Version von `gdal` entsprechend
einzuschränken.

Mit dem `source`-Befehl wird die Python-Umgebung betreten. Will man das Skript
ohne vorherigen `source`-Befehl aufrufen, muss man stattdessen folgenden
Befehl verwenden.

```
$ /opt/mowas/bin/python ./mowas.py
```

Weiterhin werden noch Datenverzeichnis benötigt. Die Geodaten werden unter
`/usr/share/mowas/` abgelegt.

```
# mkdir /usr/share/mowas
# mkdir /usr/share/mowas/geodata
# ./mowas-geodata.py -i DE_VG5000.gpkg -o /usr/share/mowas/geodata/mowas.gpkg
```

Die Quelldaten, lassen sich über https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-5-000-000-stand-01-01-vg5000-01-01.html
herunterladen. Für die genauen Hintergründe sei auf den Abschnitt
[Geodaten](#geodata) verwiesen.

Temporäre Daten sollten unter `/var/cache/mowas` abgelegt werden.

```
# mkdir /var/cache/mowas
# mkdir /var/cache/mowas/darc
```

Die Konfiguration muss unter `/etc/mowas.yml` angelegt werden. Eine
beispielhafte Konfiguration sieht wie folgt aus. Es werden die
DARC-Warn-Schnittelle und die öffentlichen Warndatensätze des BBK angebunden
und für den Bereich um die Stadt Chemnitz per APRS-Baken gewarnt.

```yaml
logging:
  level: 'INFO'
  file: '/var/log/mowas.log'
  console: true

geodata:
  path: '/usr/share/mowas/geodata/mowas.gpkg'

cache:
  path: '/var/cache/mowas/cache.json'

source:
  darc:
    DARC:
      dir_json: '/var/cache/mowas/darc/'
      dir_cap: '/var/cache/mowas/darc/'
      dir_audio: '/var/cache/mowas/darc/'
      fetch_internet: true
      fetch_hamnet: false
  bbk_url:
    MOWAS:
      url: 'https://warnung.bund.de/bbk.mowas/gefahrendurchsagen.json'
    KATWARN:
      url: 'https://warnung.bund.de/bbk.katwarn/warnmeldungen.json'
    BIWAP:
      url: 'https://warnung.bund.de/bbk.biwapp/warnmeldungen.json'
    DWD:
      url: 'https://warnung.bund.de/bbk.dwd/unwetter.json'
    LHP:
      url: 'https://warnung.bund.de/bbk.lhp/hochwassermeldungen.json'

target:
  aprs_kiss_tcp:
    DB0CSD:
      schedule:
        10m: '1m'
        1h: '5m'
        1d: '10m'
      filter:
        geocodes:
          - '145210000000'
          - '145220000000'
          - '145240000000'
      aprs:
        mycall: 'DB0CSD'
        digipath:
          - 'WIDE1-1'
        truncate_comment: true
        beacon:
          prefix: 'MWC'
        bulletin:
          id: '0MWC'
      remote:
        host: 'localhost'
        port: 8001
      kiss:
        ports:
          - 0
```

Eine detaillierte Beschreibung aller Einstellmöglichkeiten folgt weiter unten.

Damit der Dienst im Hintergrund ausgeführt wird, kann unter
`/etc/systemd/system/mowas.service` eine systemd-Unit angelegt werden.

```
[Unit]
Description=MoWaS-Alarmierung

[Service]
ExecStart=/usr/local/bin/station.py
# alternativ: ExecStart=/opt/mowas/bin/python /usr/local/bin/station.py
Type=exec

[Install]
WantedBy=multi-user.target
```

Diese muss dann beim Hochfahren nur noch automatisch gestartet werden.

```
# systemctl daemon-reload
# systemctl enable mowas
# systemctl start mowas
```

Für das Logfile `/var/log/mowas.log` empfiehlt es sich unter `/etc/lograte.d/mowas`
eine entsprechende Regel einzurichten, die alte Logdaten rotiert.

```
/var/log/mowas.log {
	copytruncate
	rotate 31
	daily
	compress
	missingok
	notifempty
}
```


Allgemeine Konfiguration
------------------------

Alle Funktionen werden über eine YAML-Datei unter `/etc/mowas.yml`
konfiguriert.

### Logging

Über den Abschnitt `logging` wird gesteuert, wie detailliert Statusausgaben
erfolgen sollen.

```yaml
logging:
  level: 'INFO'
  console: true
  file: '/var/log/mowas.log'
```

| Einstellung | Typ    | Standardwert   | Bedeutung |
|:----------- | -------| -------------- |:--------- |
| `level`     | String | *erforderlich* | Log-Level: `ERROR`, `WARNING`, `INFO` oder `DEBUG`|
| `console`   | Bool   | 'true'         | Log auf Standardausgabe ausgeben |
| `file`      | String | leer           | Log in diese Datei ausgeben |

Es stehen die Log-Level `ERROR`, `WARNING`, `INFO` und `DEBUG` zur Verfügung.
Nachrichten die mind. so kritisch wie `level` sind, werden ausgegeben. Die
Ausgabe erfolgt auf dem Terminal wen `console` auf `true` gesetzt wird und in
die Datei `file`, sofern dieser Parameter angegeben ist.

### Cache

Die Warnungen aus allen Quellen werden in einem Cache zusammengeführt, der im
Dateisystem gespeichert wird.

```yaml
cache:
  path: '/var/cache/mowas/cache.json'
  purge: '31d'
```

| Einstellung | Typ        | Standardwert   | Bedeutung |
|:----------- | ---------- | -------------- |:--------- |
| `path`      | String     | *erforderlich* | Cache-Datei |
| `purge`     | Zeitangabe | '31d'          | Zeitraum, nach dem Warnungen gelöscht werden |

Der Parameter `path` legt den Speicherort des Caches fest. Der Parameter
`purge` legt fest, nach welcher Zeit eine Warnung aus dem Cache gelöscht wird.
Dabei wird berücksichtigt, dass sich Warnungen gegenseitig referenzieren
können. Es ist sichergestellt, dass nur Warnungen gelöscht werden, die älter
als die eingestellt Frist sind **und** nicht durch eine jüngere Nachricht
referenziert werden.

Für die Zeiträume können folgende Einheiten angegeben werden:

 * `m` für Minuten
 * `h` für Stunden
 * `d` für Tage
 * `w` für Wochen

Zahlen ohne Einheitenangabe werden als Minuten interpretiert.

### Geodaten

Für APRS-Meldungen muss Warnungen ein Ortsbezug zugewiesen werden. Dieser kann
im CAP-Datensatz kodiert sein. Ist dort keine Positionsangabe vorhanden, kann
diese anhand des Regionalschlüssels bestimmt werden.

Jeder Gebietskörperschaft ist ein eindeutiger Regionalschlüssel zugeordnet.
Die Rohdaten hierzu werden vom Bundesamt für Kartographie und Geodäsie als
VG5000-Datensatz unter https://gdz.bkg.bund.de/index.php/default/verwaltungsgebiete-1-5-000-000-stand-01-01-vg5000-01-01.html
veröffentlicht. Mit dem Programm `mowas-geodata.py` kann für jedes
Verwaltungsgebiet eine Referenzposition abgeleitet werden.

```
$ ./mowas-geodata.py -i DE_VG5000.gpkg -o mowas.gpkg
Lade 'DE_VG5000.gpkg'.
  Lade Layer 'vg5000_sta'.
    1 Regionen mit 1 Features geladen.
  Lade Layer 'vg5000_lan'.
    16 Regionen mit 16 Features geladen.
  Lade Layer 'vg5000_rbz'.
    19 Regionen mit 19 Features geladen.
  Lade Layer 'vg5000_krs'.
    400 Regionen mit 400 Features geladen.
  Lade Layer 'vg5000_vwg'.
    4593 Regionen mit 4593 Features geladen.
  Lade Layer 'vg5000_gem'.
    10957 Regionen mit 10957 Features geladen.
Konvertiere 15772 Regionen. Das dauert etwas.
```

Die Referenzpositionen werden in der Datei `mowas.gpkg` gespeichert, die durch
die Konfigurationseinstellung

```yaml
geodata:
  path: '/usr/share/mowas/geodata/mowas.gpkg'
```

geladen wird. Der VG5000-Datensatz wird jährlich aktualisiert und muss
entsprechend aktuell gehalten werden.

Die Nutzung dieser Daten ist optional. Werden keine Referenzpositionen geladen,
dann erfolgt ggf. keine APRS-Alarmierung mit Ortsbezug, wenn die jeweilige
Warnung nicht selbst eine Positionsangabe enthält.


Quellen
-------

Warnquellen werden im Abschnitt `source` konfiguriert. Dieser Abschnitt hat
eine weitere zweistufige Untergliederung.

```yaml
source:
  TYP1:
    NAME1:
      EINSTELLLUNG: WERT
      # ...
  TYP2:
    NAME2:
      EINSTELLLUNG: WERT
      # ...
    NAME2:
      EINSTELLLUNG: WERT
      # ...
```

Direkt unterhalb von `source` werden die verschiedenen Treiber für die
Warnquellen definiert. Hier wird für `TYP1` und `TYP2` der jeweilige Name des
Treiber eingesetzt. Welche Treiber unterstützt sind, ist weiter unten
beschrieben.

Für jeden Treiber können mehrere Quellen angebunden werden, die mit frei
gewählten Namen `NAME1`, `NAME2`, ... benannt werden müssen. Der Name muss
innerhalb der Treiberschnitts eindeutig sein. Quellen verschiedener Treiber
können aber gleich benannt werden sollen. Wenn z.B. Meldungen des modularen
Warnsystems des Bundes sowohl über den DARC e.V. als Warnmultiplikator als auch
über die öffentliche JSON-Schnittstelle des BBK abgerufen werden, ist folgende
Konstruktion möglich.

```yaml
source:
  darc:
    MOWAS:
      # ...
  bbk_url:
    MOWAS:
      # ...
```

Hierbei wird der Quellenname `MOWAS` mehrfach benutzt.

Innerhalb der Quelle können weitere Einstellungen festgelegt werden. Diese sind
treibabhängig und weiter unten im Detail beschrieben. Nicht alle vorgesehenen
Einstellungen müssen festgelegt werden. Lässt man eine Einstellung weg, greift
ihr Standardwert. Einstellungen, für die keine Standardwerte definiert sind,
müssen stets konfiguriert werden. Sie auszulassen, stellt einen
Konfigurationsfehler dar.

### BBK

Das BBK stellt CAP-Datensätze verschiedener Warnsysteme als JSON-Dateien zum
Download bereit. Für die Auswertung dieser Dateien sind zwei Treiber
vorgesehen. Ein Treiber ruft die Warnungen eigenständig per HTTP(S) vom BBK ab.
Ein anderer Treiber wertet ein JSON-File an einem vorgegebenem Pfad regelmäßig
aus, welches extern bereitgestellt wird.

#### JSON-Abruf

Treibername: `bbk_url`

| Einstellung | Typ    | Standardwert   | Bedeutung |
|:----------- | ------ | -------------- |:--------- |
| `url`       | String | *erforderlich* | abzurufende URL |

Der Treiber ruft bei jedem Durchlauf die angegebene URL ab und verarbeitet die
enthaltenen Warnungen.

#### JSON-Files

Treibername: `bbk_file`

| Einstellung | Typ    | Standardwert   | Bedeutung |
|:----------- | ------ | -------------- |:--------- |
| `path`      | String | *erforderlich* | einzulesende Datei |

Der Treiber liest bei jedem Durchlauf die angegebene Datei ein und verarbeitet
die enthaltenen Warnungen.

#### Beispiel

Mit folgende Konfiguration werden die Warnsysteme MoWaS, KatWarn, Biwap sowie
die Wetterwarnungen des DWD und die Hochwassermeldungen der
Landeshochwasserstellen abgerufen.

```yaml
source:
  bbk_url:
    MOWAS:
      url: 'https://warnung.bund.de/bbk.mowas/gefahrendurchsagen.json'
    KATWARN:
      url: 'https://warnung.bund.de/bbk.katwarn/warnmeldungen.json'
    BIWAP:
      url: 'https://warnung.bund.de/bbk.biwapp/warnmeldungen.json'
    DWD:
      url: 'https://warnung.bund.de/bbk.dwd/unwetter.json'
    LHP:
      url: 'https://warnung.bund.de/bbk.lhp/hochwassermeldungen.json'
```

### DARC

Treibername: `darc`

Der DARC e.V. ist ein sog. Warnmultiplikator des BBK und hat Zugriff auf
qualitätsgesicherte Warndaten, die er an seine Mitglieder weitergibt. Das
Verfahren hierzu ist unter https://mowas.notfunk.radio/ beschrieben. Die
Warnungen werden per Push-Verfahren als JSON-Datensatz auf das Warnsystem
ausgeliefert. Sie enthalten nur die notwendigsten Daten. Der vollständige
CAP-Datensatz kann als XML-Datei vom DARC entweder über das öffentliche
Internet oder das HAMNET heruntergeladen werden. Ferner werden dort Audio-Files
bereitgestellt, die den Warntext wiedergeben.

| Einstellung      | Typ                | Standardwert   | Bedeutung |
|:---------------- | ------------------ | -------------- |:--------- |
| `dir_json`       | String             | *erforderlich* | Verzeichnis in denen Push-Meldungen abgelegt werden |
| `dir_cap`        | String             | *erforderlich* | Verzeichnis in die CAP-Datensätze heruntergeladen werden |
| `dir_audio`      | String oder `null` | `null`         | Verzeicnnis in die Audio-Datensätze heruntergeladen werden |
| `fetch_internet` | Bool               | `false`        | CAP- und Audio-Daten aus dem öffentlichen Internet herunterladen |
| `fetch_hamnet`   | Bool               | `false`        | CAP- und Audio-Daten aus dem HAMNET herunterladen |

Das Eingabeverzeichnis der Push-Meldungen muss durch `dir_json` angegeben
werden. Es können nur Meldungen verarbeitet werden, wenn für diese ein
CAP-Datensatz heruntergeladen werden kann. Deshalb ist `dir_cap` ebenfalls eine
Pflichteinstellung. Es kann sich um ein temporäres Verzeichnis handeln.
Audiodaten sind nur bei Anbindung von Sprechfunkrelais etc. notwendig. Wird
dieser Parameter weggelassen, erfolgt kein Download der Audio-Files. Für alle
Parameter kann auch das selbe Verzeichnis verwendet werden.

Es werden sowohl Quell-URLs im öffentlichen Internet als auch im HAMNET
angegeben. Je nachdem, ob das Warnsystem ans Internet oder ans HAMNET
angebunden ist, muss mind. einer der beiden Parameter auf `true` gesetzt
werden. Es können auch beide Parameter aktiviert werden. Es ist jedoch ein
Konfigurationsfehler, wenn beide Parameter `false` sind, da dann keine Daten
heruntergeladen werden können. Beim Download werden alle Quellen in zufälliger
Reihenfolge abgefragt, bis die erstbeste Quelle ein Ergebnis liefert.

#### Beispiel

```yaml
source:
  darc:
    MOWAS:
      dir_json: '/tmp/darc/'
      dir_cap: '/var/cache/mowas/darc/'
      fetch_internet: true
      fetch_hamnet: false
```


Senken
------

Warnsenken werden im Abschnitt `target` konfiguriert. Dieser Abschnitt hat die
gleiche zweistufige Struktur wie die Warnquellen: auf oberster Ebene werden
Ausgabetreiber definiert, anschließend die jeweiligen Ziele für den Treiber.

```yaml
target:
  TYP1:
    NAME1:
      EINSTELLLUNG: WERT
      # ...
  TYP2:
    NAME2:
      EINSTELLLUNG: WERT
      # ...
    NAME2:
      EINSTELLLUNG: WERT
      # ...
```

Für jeden Treiber gibt es eine Reihe allgemeiner Einstellungen, die für jeden
Treiber gleich sind. Sie sind daher nur einmal beschrieben. Ferner besitzt
jeder Treiber noch eine Reihe speizfischer Einstellungen, die für den
jeweiligen Treiber beschrieben sind. Aufgrund der Fülle von
Einstellmöglichkeiten, sind die einzelnen Parameter ggf. wieder durch eigene
Unterabschnitte gegliedert.

### Allgemeine Einstellungen

#### Wiederholungsrhythmus

Es ist nicht unedingt ausreichende eine Warnung nur zum Ausgabezeitpunkt
auszusenden, da die Zielgruppe mitunter nicht ständig empfangsbereit ist. Aus
diesem Grund ergibt es Sinn, Warnungen regelmäßig zu wiederholen. Im Abschnitt
`schedule` kann ein Wiederholungsschema festgelegt. Am besten wird dies an
einem Beispiel deutlich.

```yaml
target:
  TYP:
    NAME:
      schedule:
        10m: '1m'
        1h: '5m'
        1d: '10m'
```

Es werden jeweils zwei Zeitspannen angegeben: Ein Zeitraum und das
Wiederholungsintervall. Im vorliegenden Beispiel, werden Warnungen in den
ersten 10 Minuten nach der ersten Alarmierung jede Minute wiederholt. Nach
Ablauf von 10 Minuten bis zu einer Stunde nach Erstalarmierung erfolgt die
Alarmierung nur noch alle 6 Minuten. Bis zu einem Tag nach Erstalarmierung wird
dann nur noch alle 10 Minuten alarmiert. Nach Ablauf von einem Tag erfolgt
keine Alarmierung mehr.

Die gestufe Alarmierung soll neue Warnungen möglichst oft wiederholen und aber
das Funkmedium nicht unnötig belasten, je älter die Warnung ist.

Wird für eine Warnung eine Aktualisierung bzw. Entwarnung ausgegeben, setzt
dies das Wiederholungsschema zurück. D.h. nach Aktualisierungen oder Entwarnung
werden zunächst wieder häufiger übertragen und phasen dann durch längere
Widerholungsintervalle aus.

#### Filter

Warnungen werden i.A. für das gesamte Warngebiet bereitgestellt. Aber nicht
jede Warnung ist für eine bestimmte Amateurfunkstelle relevant. Deshalb besteht
die Möglichkeit die Warnungen sowohl räumlich als auch örtlich zu filtern. Der
Filter wird in einem eigenen Abschnitt `filter` definiert.

```yaml
target:
  TYP:
    NAME:
      filter:
        # ...
```

| Einstellung | Typ               | Standardwert   | Bedeutung |
|:----------- | ----------------- | -------------- |:--------- |
| `geocodes`  | Liste von Strings | *erforderlich* | Regionalbereiche der Meldungen |
| `max_age`   | Zeitangabe        | `4h`           | Maximales Alter einer Warnung |

Bei `geocodes` handelt es sich um eine Liste amtlicher Regionalschlüssel. Jede
Gebietskörperschaft (Republik, Bundesland, Regierungsbezirk, Kreis, Gemeinde,
...) verfügt über einen eigenen hirarchischen Regionalschlüssel. Eine Warnung
wird vom Filter erfasst, wenn *mindestens ein* konfigurierter
Regionalschlüssel ...

 * dem Regionalschlüssel der Warnung entspricht oder
 * dem Regionalschlüssel der Warnung übergeordnet ist (d.h. wenn im Filter der
   Schlüssel eines Landkreises eingetragen ist, werden auch Warnungen für alle
   Gemeinden in diesem Landkreis weiterverarbeitet) oder
 * dem Regionalschlüssel der Warnung nachgeordnet ist (d.h. wenn im Filter der
   Schlüssel eines Landkreises eingetragen ist, werden auch Warnungen für das
   übergeordnete Bundesland weiterverarbeitet).

Neben dem örtlichen Kriterium muss die Warnung auch neu genug sein, um
verarbeitet zu werden. Standardmäßig werden nur Warnungen verarbeitet, die
jünger als 4 Stunden sind. Auf diese Weise wird verhindert, dass beim
erstmaligen Start des Warndienstes oder nach einem längeren Ausfall erst alle
zurückliegenden Warnungen aussendet, die durch ihr Alter ggf. gar nicht mehr
relevant sind. Es zählt nur der Zeitraum zwischen Ausgabe und Erstverarbeitung
der Warnung. Der Wiederholungsrhythmus wird dadurch nicht eingeschränkt.

Beispiel:

```yaml
target:
  TYP:
    NAME:
      filter:
        geocodes:
          - '14511'
          - '14521'
        max_age: '8h'
```

In diesem Fall wird das Warngebiet auf die Stadt Chemnitz (ARS 14511) und den
Erzgebirgskreis (ARS 14521) festgelegt. Damit werden z.B. folgenden Warnungen
ausgegeben:

 * Warnungen für die Stadt Chemnitz, da diese im Filter direkt erfasst ist
 * Warnungen für die Gemeinde Raschau-Markersbach (ARS 145210500), da diese dem
   Erzgebirgskreis untergeordnet ist.
 * Warnungen für das Bundesland Sachsen (ARS 14), da diese sowohl der Stadt
   Chemnitz und dem Erzgebirgskreis übergeordnet ist.

Warnungen für den Landkreis Altenburger Land (ARS 16077) werden hingegen nicht
verarbeitet, obwohl dieser geografisch recht Nahe gelegen ist.

Regionalschlüssel für Gebietskörperschaften können unter
https://opengovtech.de/ars/ recherchiert werden.

### APRS

APRS ist ein Funkmeldesystem mit dem sowohl Positionsmeldungen (sog. Baken) als
auch allgemeine Nachrichten (sog. Bulletins) ausgesendet werden können.
Positionsmeldungen stellen den überwiegenden Hauptanwendungsfall von APRS da.
Der APRS-Treiber untertützt die Alarmierung sowohl per Positionsmeldungen als
auch per Bulletins. Baken werden an der geografischen Stelle platziert, die in
der Warnung kodiert ist als Kommentartext für die Bake wird der Warntext
kodiert. Bei Bulletins wird nur der Warntext ausgesendet. Generell
funktionieren APRS-Warnungen nur, wenn der Empfänger zum Zeitpunkt der
Aussendung auf Empfang ist. Eine Zwischenspeicherung oder ein Abruf von
Meldungen erfolgt nicht.

Für die Anbindung von APRS-Sendern stehen folgende Möglichkeiten bereit.

 * Serielles KISS-TNC
 * Netzwerkbasiertes KISS-TNC per TCP-Anbindung
 * Telnet-Anbindung eines APRS-IS-Servers

Jedes Protokoll ist als eigenständiger Treiber implementiert. Die
APRS-Einstellungen dieser Treiber sind einheitlich und werden unten
beschrieben. Zusätzlich bietet jeder Treiber noch spezifische Einstellungen,
die in der jeweiligen Beschreibung für den Treiber dokumentiert sind.

Aufgrund der Vielfalt der Einstellmöglichkeiten, ist die Konfiguration selbst
wieder hierarchisch aufgebaut. Im folgenden Beispiel besitzt die Einstellung
`beacon` z.B. die Untereinstellung `enabled`.


```yaml
target:
  TYP:
    NAME:
      aprs:
        dstcall: '...'
        mycall: '...'
        digipath:
          # ...
          # ...
        beacon:
          enabled: true
          # ...
        bulletin:
          mode: 'always'
          # ...
```

Folgende Einstellungen sind für jeden APRS-Treiber möglich. Untereinstellungen
werden durch `.` getrennt.

| Einstellung         | Typ               | Standardwert   | Bedeutung |
|:------------------- | ----------------- | -------------- |:--------- |
| `dstcall`           | String            | `APMOWA`       | Zielrufzeichen |
| `mycall`            | String            | *erforderlich* | eigenes Rufzeichen |
| `digipath`          | Liste von Strings | `WIDE1-1`      | Digi-Pfad |
| `truncate_comment`  | Bool              | `true`         | Kommentare kürzen, um APRS-Spezifikation einzuhalten |
| `beacon.enabled`    | Bool              | `true`         | Positionsmeldungen senden |
| `beacon.prefix`     | String            | `MOWA`         | Präfix für die Objektkennung von Positionsmeldungen |
| `beacon.time`       | Bool              | `true`         | Zeitstempel in der Positionsmeldung kodieren |
| `beacon.compressed` | Bool              | `false`        | komprimiertes Format für Zeitstempel anwenden |
| `beacon.max_areas`  | Zahl              | `0`            | Höchstanzahl an Teilgebieten, die alarmiert werden |
| `bulletin.mode`     | String            | `fallback`     | Festlegungen, wann Bulletins gesendet werden: `never`, `fallback`, `always` |
| `bulletin.id`       | String            | `0MOWAS`       | Kennung für Bulletin-Meldungen |

Das eigene Rufzeichen muss in `mycall` eingetragen werden. Dies kann auch eine
AX.25-SSID enthalten. Das Zielrufzeichen `dstcall` wird für APRS nicht genutzt
und kennzeichnet die verwendete APRS-Software. Es sollte beim Standardwert
belassen werden.

Der Digipath legt fest, wie oft APRS-Pakete von Digipeatern wiederholt werden
sollen. Mit `WIDE1-1` wird jedes Paket genau einmal wiederholt. Die Einstellung
muss stets als Liste angegeben werden.

```yaml
target:
  TYP:
    NAME:
      aprs:
        digipath:
          - 'WIDE1-1'
```

Mit dem Digipath `WIDE1-1,WIDE2-2` würde jedes Paket bis zu drei mal wiederholt
werden. Die Einstellung wäre wie folgt zu kodieren.

```yaml
target:
  TYP:
    NAME:
      aprs:
        digipath:
          - 'WIDE1-1'
          - 'WIDE2-2'
```

Die Digipath-Einstellung sollte das Warngebiet abdecken, aber nicht zu hoch
gewählt sein, um das Netzwerk nicht unnötig zu sättigen.

Für APRS-Pakete ist eine Höchstlänge festgelegt. Überschreitet ein Warntext
dieses Limit, wird der Text entsprechend abgeschnitten, wenn `truncate_comment`
auf `true` gesetzt ist. Die sichert die Einhaltung des APRS-Standards. Bei
der Einstellung `false` wird das Längenlimit ignoriert und der vollständige
Text übermittelt. Es ist dann aber nicht mehr garantiert, dass alle
APRS-Empfänger die Warnung korrekt verarbeiten können.

Die Einstellung `beacon` ist nur für Positionsbaken relevant. Mit
`beacon.enabled` kann die Aussendung von Baken aktiviert (Standardeinstellung)
oder deaktiviert werden. Baken werden als sog. APRS-Item ausgessendet.

Jede Bake enthält eine eindeutige Kennung. Diese besteht aus einem Präfix, aus
einer laufenden Nummer. Die Warnung `MOWA1` besteht aus dem Präfix `MOWA` und
der Nummer `1`. Zudem kann eine Warnung mehrere Teilgebiete enthalten. Diese
werden durch angehängte Buchstaben nummeriert. `MOWA2A` ist das erste
Teilgebiet (`A`) der Warnung `2`. Entsprechend wäre `MOWA2B` das zweite
Teilgebiet usw. Nummern werden erst wiederverwendet, wenn die zugehörige
Warnung nach Ablauf der Wartezeit aus dem Cache entfernt wird.

Der Präfix wird mittels `beacon.prefix` festgelegt. Hierbei ist zu beachten,
dass jede Instanz dieses Programms eigene laufende Nummern vergibt. Damit bei
benachbarten Installationen keine Überschneidungen der Bakenkennung auftreten,
wird dringend empfohlen eine eindeutiges Präfix festzulegen. Dies kann z.B.
`MW` für (MoWaS) gefolgt vom KFZ-Unterscheidungszeichen des Betriebsorts sein,
z.B. `MWC` für eine Installation in Chemnitz, `MWERZ` für eine Installation
im Erzgebirgskreis etc. Der Präfix sollte nicht länger als 5 Zeichen sein. So
verbleibt noch genug Platz für die laufende Nummer, da max. 9 Zeichen für eine
Bake zur Verfügung stehen.

Ist `beacon.time` auf `true` gesetzt, wird in der APRS-Bake ein Zeitstempel
kodiert. Liegt der Warnzeitpunkt mehr als 21 Tage in der Vergangenheit oder
7 Tage in der Zukunft (Warnungen über zukünftige Ereignisse sind technisch
möglich), wird kein Zeitstempel kodiert. So wird Eindeutigkeit gerantiert, da
bei sich bei APRS-Baken nur der Monatstag angeben lässt.

Ist `beacon.compressed` auf `true` gesetzt, werden die Positionskoordinaten in
einem komprimierten Format kodiert. Dieses spart Übertragungszeit, die
Koordinaten sind dann aber nicht mehr menschenlesbar.

Eine Warnung kann mehrere Teilgebiete enthalten. Für den Mittelpunkt jedes
Teilgebiets wird eine eigene Bake ausgesendet. Mit dem Wert `max_areas` wird
ein Höchstwert für die Anzahl an Teilgebieten festgelegt. Enthält eine Warnung
mehr Teilgebiete als dieser Schwellwert, werden alle Teilgebiete zu einem
einzigen Gebiet zusammengefasst und nur eine einzige Bake ausgesendet.
Hierdurch wird eine Überlastung des APRS-Netzwerks verhindert. Mit dem
Standardwert 0 werden Teilgebiete niemals zusammengefasst.

Neben Baken können auch APRS-Bulletins versendet werden. Dies sind Nachrichten
an alle aktiven Empfängen. Hierfür ist der Abschnitt `bulletin` relevant. Die
Einstellung `bulletin.mode` legt fest, wann ein solches Bulletin versendet
wird. Sie kann die drei Werte `never`, `fallback` oder `always` annehmen. Beim
Wert `never` erfolgt keine Aussendung eines Bulletins. Beim Wert `always` wird
für jede Warnung ein Bulletin versendet. Beim Wert `fallback` hängt es davon
ab, ob für diese Warnung bereits eine Bake gesendet wurde. Wenn bereits per
Bake alarmiert wurde, wird kein Bulletin gesendet. Konnte jedoch noch per Bake
alarmiert werden, wird ein Bulletin gesendet. Eine Bake kann z.B. dann
entfallen, wenn in der Warnung keine Position kodiert war oder die Postion aus
welchem Grund auch immer nicht ermittelt werden konnte.

Jedes Bulletin enthält ebenfalls ein spezifisches Präfix, welches mit einer
Ziffer beginnt und dann aus bis zu 5 Buchstaben besteht. Es sollten die selben
Regeln wir für den Baken-Präfix angewendet werden. Die Wahl der Ziffer ist
beliebig.

#### Serielles KISS-TNC

Der Treibername für die Anbindung eines seriellen AX.25 KISS-TNCs lautet
`aprs_kiss_serial`. Das KISS-Modem muss entsprechend mit einem APRS-Transceiver
verbunden sein.

Die Konfiguration erfolgt in Ergänzung der allgemeinen Einstellungen wie folgt.

```yaml
target:
  aprs_kiss_serial:
    NAME:
      # Allgemeine Einstellungen
      serial:
        device: '/dev/ttyUSB0'
        baud: 115200
        cmd_up: ''
        cmd_down: ''
        cmd_pre: ''
        cmd_post: ''
      kiss:
        ports:
          - 0
```

Folgende Parameter stehen zur Verfügung

| Einstellung       | Typ              | Standardwert   | Bedeutung |
|:----------------- | ---------------- | -------------- |:--------- |
| `serial.device`   | String           | *erforderlich* | Positionsmeldungen senden |
| `serial.baud`     | Zahl             | 115200         | Präfix für die Objektkennung von Positionsmeldungen |
| `serial.cmd_up`   | Binär-String     | leer           | Kommando, welches zur Initialisierung an das TNC geschickt wird |
| `serial.cmd_down` | Binär-String     | leer           | Kommando, welches beim Beenden an das TNC geschickt wird |
| `serial.cmd_pre`  | Binär-String     | leer           | Kommando, welches vor einem Sendezyklus an das TNC geschickt wird |
| `serial.cmd_post` | Binär-String     | leer           | Kommando, welches nach einem Sendezyklus an das TNC geschickt wird |
| `kiss.ports`      | Liste von Zahlen | leer           | KISS-Ports, über die gesendet werden soll |

Mit `serial.device` und `serial.baud` werden die Schnittstellenparameter des
KISS-Modems angegeben. Jedes KISS-Moden kann mehrere Ports ansteuern. Diese
sind mit Zahlen bei 0 beginnend durchnummiert. In der Liste `kiss.ports` wird
angegeben, welche Ports mit Daten bespielt werden sollen.

Ggf. muss das Modem zunächst initialisiert werden. Mit den `cmd`-Parametern
können Kommandos in Hexadezimalschreibweise konfiguriert werden, die zu
bestimmten Zeitpunkten ausgeführt werden.

 * `serial.cmd_up` → einmalig bei Initialisierung der Software
 * `serial.cmd_down` → einmalig bei Beendigung der Software
 * `serial.cmd_pre` → vor einem Sendezyklus
 * `serial.cmd_post` → nach einem Sendezyklus

I.d.R. ist dies notwendig, um ein TNC in den KISS-Modus zu versetzen. Mit
folgender Konfiguration, wird ein SCS DSP-TNC initialisiert.

```yaml
target:
  aprs_kiss_serial:
    NAME:
      serial:
        cmd_up: '1b404b0d'
        cmd_down: 'dbdbc0ffc0'
```

`cmd_up` stellt sicher, dass das TNC in den KISS-Modus wechselt. Mit `cmd_down`
wird der KISS-Modus verlassen.

#### TCP-KISS-TNC

Der Treibername für die Anbindung eines netzwerkbasierten AX.25 KISS-TNCs
lautet `aprs_kiss_tcp`. Die Ansteuerung erfolgt per TCP-Protokoll. Dieses
Konstrukt kann z.B. von Vorteil sein, um APRS-Pakete in einen bestehenden
Digipeater einzuspielen.

Die Konfiguration erfolgt in Ergänzung der allgemeinen Einstellungen wie folgt.


```yaml
target:
  aprs_kiss_tnc:
    NAME:
      remote:
        host: 'localhost'
        port: 8001
      kiss:
        ports:
          - 0
```

Folgende Parameter stehen zur Verfügung

| Einstellung   | Typ              | Standardwert   | Bedeutung |
|:------------- | ---------------- | -------------- |:--------- |
| `remote.host` | String           | *erforderlich* | Hostname des KISS-TNCs |
| `remote.port` | Zahl             | *erforderlich* | TCP-Port des KISS-TNCs |
| `kiss.ports`  | Liste von Zahlen | leer           | KISS-Ports, über die gesendet werden soll |

Mit `remote.host` und `remote.port` werden die Netzwerkadresse (Hostname oder
IP-Adresse inkl. TCP-Port) des KISS-Modems angegeben. Die KISS-Parameter sind
identisch zum seriellen KISS-Modem.

#### Telnet

Mit dem Telnet-Treiber ist eine direkte Anbindung an das APRS-IS-Netzwerk
möglich. Der Treibername lautet `aprs_telnet`.

Die Konfiguration erfolgt in Ergänzung der allgemeinen Einstellungen wie folgt.

```yaml
target:
  aprs_telnet:
    NAME:
      remote:
        host: 'euro.aprs2.net'
        user: '...'
        pass: '...'
```

Folgende Parameter stehen zur Verfügung

| Einstellung   | Typ    | Standardwert   | Bedeutung |
|:------------- | ------ | -------------- |:--------- |
| `remote.host` | String | *erforderlich* | Hostname des APRS-Servers |
| `remote.port` | Zahl   | 14580          | TCP-Port des APRS-Servers |
| `remote.user` | String | *erforderlich* | Nutzername für die Anmeldung am Server |
| `remote.pass` | String | leer           | Nutzerabhängigker Pass-Code für die Anmeldung am Server |

Mit `remote.host` und `remote.port` werden die Netzwerkadresse (Hostname oder
IP-Adresse inkl. TCP-Port) des APRS-Server angegeben. Öffentlich Server sind
unter https://www.aprs2.net/ aufgeführt. Für Deutschland empfiehlt sich
`euro.aprs2.net` als Einstiegspunkt.

APRS-Server erfordern eine Anmeldung mit Nutzernamen (`remote.user`) und einem
optionalen Pass-Code (`remote.pass`). Als Nutzername wird das Rufzeichen der
einliefernden Amateurfunkstelle verwendet. Der Pass-Code dient zum Nachweis,
dass man Funkamateur ist. Server verlangen ihn in aller Regel, wenn man Pakete
einliefern will. Er berechnet sich statisch aus dem Nutzernamen (siehe z.B.
https://apps.magicbug.co.uk/passcode/) und stellt keinen harten
Sicherheitsmechanismus dar.


Anwendungsbeispiel
------------------

### TCP-KISS-TNC mit Direwolf

Mit Direwolf (https://github.com/wb2osz/direwolf) lässt sich ein einfaches
Soundkarten-Modem als Test-Setup zum Einsatz bringen. Mit folgender
Konfiguration wird Direwolf als TCP-KISS-Modem auf Port 8001 aktiv.

```
ADEVICE0 hw:0,0
ARATE 48000
ACHANNELS 1

KISSPORT 8001

CHANNEL 0
MYCALL DB0XXX
MODEM 1200
```

Direwolf kann interaktiv von der Shell getartet werden.

```
$ direwolf -c direwolf.conf
Dire Wolf version 1.7
Includes optional support for:  gpsd hamlib cm108-ptt dns-sd
Warning: Could not open 'symbols-new.txt'.
The "new" OVERLAID character information will not be available.

Reading config file /etc/direwolf.conf
Audio device for both receive and transmit: default  (channels 0 & 1)
Channel 0: 1200 baud, AFSK 1200 & 2200 Hz, A+, 44100 sample rate.
Channel 1: 1200 baud, AFSK 1200 & 2200 Hz, A+, 44100 sample rate.
Note: PTT not configured for channel 0. (Ignore this if using VOX.)
Note: PTT not configured for channel 1. (Ignore this if using VOX.)
Ready to accept AGW client application 0 on port 8000 ...
Ready to accept KISS TCP client application 0 on port 8001 ...
DNS-SD: Avahi: Failed to create Avahi client: Daemon not running
```

Anschließend konfiguriert man das MoWaS-Tool wie folgt, um bundesweit alle
Warnungen per APRS auszugeben. Die Ausgabe aller verfügbaren Warnungen sollte
jedoch nur in einem Test-Setup erfolgen.

```yaml
source:
  bbk_url:
    MOWAS:
      url: 'https://warnung.bund.de/bbk.mowas/gefahrendurchsagen.json'

cache:
  path: 'cache/cache.json'

target:
  aprs_kiss_tcp:
    DIREWOLF:
      schedule:
        10m: '1m'
        1h: '5m'
        1d: '10m'
      filter:
        geocodes:
          - '0'
      aprs:
        mycall: 'DB0XXX'
        beacon:
          prefix: 'MOWA'
        bulletin:
          id: '0MOWA'
      remote:
        host: 'localhost'
        port: 8001
      kiss:
        ports:
          - 0
```

Startet man das MoWaS-Tool, sollte Direwolf nun binnen kürzester Zeit eine
Reihe von Paketen über die Soundkarte ausgeben.

```
$ ./mowas.py -c mowas.yml
```
