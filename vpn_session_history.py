#!/usr/bin/env python3
"""
Dieses Skript dient als Helfer zur Protokollierung von OpenVPN‑Sitzungen.

Es wird die Status‑Datei des OpenVPN‑Servers ausgelesen (z. B. die unter
`STATUS_LOG` angegebene `openvpn-status.log`). Für jede aktuell aktive Verbindung
wird eine eindeutige Session‑ID gebildet (Benutzername und Startzeit). Das
CSV‑Protokoll (`SESSION_LOG`) enthält Zeilen mit Benutzer, öffentlicher IP,
Tunnel‑IP, Start‑ und Endzeit sowie der Dauer der Verbindung in Sekunden.

Der Aufruf dieses Skripts kann regelmäßig via Cron erfolgen. Es vergleicht den
aktuellen Status mit den bereits bekannten Sitzungen und trägt Endzeiten ein,
wenn eine Session nicht mehr aktiv ist. Neue Sessions werden hinzugefügt.

Alle Funktionen und Variablen sind ausführlich auf Deutsch kommentiert, um
einen leichten Einstieg zu ermöglichen.
"""

import os
import csv
from datetime import datetime

# Pfad zur OpenVPN‑Status‑Datei. Diese wird von der OpenVPN‑Server‑Konfiguration
# mit der Direktive `status` definiert. Unter Linux liegt sie häufig unter
# `/var/log/openvpn-status.log` oder `/etc/openvpn/openvpn-status.log`.
STATUS_LOG = '/var/log/openvpn-status.log'

# Pfad zur CSV‑Datei, in der die Sitzungen protokolliert werden. Wenn die Datei
# noch nicht existiert, wird sie mit Kopfzeile neu angelegt.
SESSION_LOG = '/var/log/openvpn-sessions.csv'

def parse_status_log(path):
    """
    Lese die OpenVPN‑Status‑Datei ein und extrahiere die aktiven Sessions.

    Die Status‑Datei besteht aus zeilenweiser CSV‑ähnlicher Ausgabe des
    OpenVPN‑Servers. Interessant für uns sind Zeilen, die mit `CLIENT_LIST,`
    beginnen. Jede dieser Zeilen enthält Informationen zur aktuellen Verbindung:

    * `parts[1]` – Common Name (der Benutzername des VPN‑Clients)
    * `parts[2]` – Real Address (öffentliche IP mit Port des Clients)
    * `parts[3]` – Virtual Address (Tunnel‑IP des Clients)
    * `parts[7]` – Connected Since (Startzeit der Verbindung im Format
      „YYYY‑MM‑DD HH:MM:SS“)

    Es wird eine eindeutige Session‑ID aus Benutzername und Startzeit
    zusammengesetzt. Die Startzeit (im Originalformat) wird im Session‑Dict
    abgelegt. Dieses Dict wird später verwendet, um neue und bestehende Sessions
    zu erkennen.

    :param path: Dateisystempfad zur Status‑Datei
    :return: Dictionary, bei dem der Schlüssel die Session‑ID ist und der Wert
             ein weiteres Dict mit User, öffentlicher IP, Tunnel‑IP und Startzeit
    """
    sessions = {}
    # Wenn die Status‑Datei nicht existiert, gibt es keine aktiven Sessions.
    if not os.path.exists(path):
        return sessions
    # Datei komplett einlesen und Zeilenumbrüche entfernen
    with open(path, 'r') as f:
        lines = [line.strip() for line in f]
    # Jede Zeile analysieren
    for line in lines:
        # Nur Zeilen, die mit „CLIENT_LIST,“ beginnen, beschreiben eine aktive Session
        if line.startswith('CLIENT_LIST,'):
            parts = line.split(',')
            # Erwartet werden mindestens acht Einträge, ansonsten ignorieren
            if len(parts) >= 8:
                cn = parts[1]            # Common Name des Clients
                real_addr = parts[2]      # Öffentliche IP inklusive Port
                tunnel_ip = parts[3]      # Tunnel‑IP
                connected_since = parts[7] # Startzeit der Verbindung
                # Öffentliche IP ohne Port extrahieren
                public_ip = real_addr.split(':')[0]
                # Eindeutige Session‑ID aus Benutzer und Startzeit bilden
                session_id = f"{cn}|{connected_since}"
                # Session‑Details in Dictionary ablegen
                sessions[session_id] = {
                    'user': cn,
                    'public_ip': public_ip,
                    'tunnel_ip': tunnel_ip,
                    'start': connected_since,
                }
    return sessions

def load_previous_sessions(path):
    """
    Lade bereits protokollierte Sessions aus dem CSV‑Log.

    Das CSV‑Log enthält neben aktiven auch bereits beendete Sitzungen. Um
    abzugleichen, welche Sessions noch laufen, wird jede Zeile eingelesen und
    anhand derselben Logik wie bei `parse_status_log` eine Session‑ID
    (Benutzername und Startzeit) generiert. Das zurückgegebene Dictionary
    erlaubt einen schnellen Abgleich zwischen alten und neuen Daten.

    :param path: Dateipfad zum CSV‑Log
    :return: Dictionary der bereits bekannten Sessions
    """
    prev = {}
    # Wenn das Log noch nicht existiert, gibt es keine vorherigen Sessions
    if not os.path.exists(path):
        return prev
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Session‑ID besteht aus User und Startzeit
            session_id = f"{row['user']}|{row['start']}"
            prev[session_id] = row
    return prev

def append_session_to_log(path, data, is_new_file):
    """
    Nicht mehr verwendete Hilfsfunktion zum Anhängen einzelner Sessions.

    Diese Funktion war ursprünglich dazu gedacht, einzelne Datensätze an ein
    bestehendes CSV anzuhängen. In der aktuellen Implementierung wird das
    komplette CSV in einem Rutsch neu geschrieben (siehe `main`). Sie bleibt
    dennoch für eventuelle Erweiterungen erhalten.

    :param path: Pfad zur CSV‑Datei
    :param data: Dictionary mit den Daten einer einzelnen Session
    :param is_new_file: Flag, ob das CSV neu erstellt wird (Schreiben des Headers)
    """
    fieldnames = ['user', 'public_ip', 'tunnel_ip', 'start', 'end', 'duration_s']
    file_exists = os.path.exists(path)
    with open(path, 'a', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        # Wenn das File neu ist, schreibe Kopfzeile
        if is_new_file or not file_exists:
            writer.writeheader()
        writer.writerow(data)

def parse_openvpn_time(s):
    """
    Versuche, einen Zeitstempel aus der OpenVPN‑Logdatei in ein `datetime` zu
    konvertieren.

    OpenVPN kann Zeitstempel in unterschiedlichen Formaten ausgeben. Das
    Status‑Log verwendet im Regelfall das ISO‑Format `%Y-%m-%d %H:%M:%S`, aber
    manche Installationen nutzen ein lokales Format wie `Mon Feb  7 12:34:56 2023`.
    Diese Funktion iteriert über bekannte Formate und gibt das erste gültige
    `datetime`‑Objekt zurück. Bei unbekannten Formaten wird `None` geliefert.

    :param s: Zeitstempel als Zeichenkette
    :return: `datetime`‑Objekt oder `None` bei Fehler
    """
    for fmt in ('%Y-%m-%d %H:%M:%S', '%a %b %d %H:%M:%S %Y'):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

def main():
    """
    Haupteinstiegspunkt: vergleicht den aktuellen OpenVPN‑Status mit dem
    bestehenden CSV‑Protokoll und aktualisiert dieses.

    Ablauf:

    1. Aktuelle Uhrzeit erfassen, um Endzeiten für abgelaufene Sessions zu
       setzen.
    2. Aktive Sessions aus dem Status‑Log lesen (`parse_status_log`).
    3. Bisher bekannte Sessions aus dem CSV laden (`load_previous_sessions`).
    4. Jede alte Session prüfen:
       * Wenn sie bereits eine Endzeit besitzt, bleibt sie unverändert.
       * Wenn sie noch aktiv ist (existiert auch im aktuellen Status), bleibt sie
         unverändert.
       * Wenn sie im Status nicht mehr vorhanden ist, wird sie beendet: Endzeit
         auf „jetzt“ setzen und Dauer berechnen.
    5. Alle neuen Sessions (nur im Status, noch nicht im CSV) ans Log anhängen.
    6. Das komplette Log mit Kopfzeile neu in die CSV schreiben.

    Das Schreiben des gesamten Logs (statt Anhängen einzelner Zeilen) vereinfacht
    die Behandlung von beendeten Sessions und vermeidet Duplikate.
    """
    now = datetime.now()
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    # Aktuelle Sessions aus dem Status‑File lesen
    sessions = parse_status_log(STATUS_LOG)
    # Bisher bekannte Sessions laden
    prev_sessions = load_previous_sessions(SESSION_LOG)
    is_new_file = not os.path.exists(SESSION_LOG)
    new_log = []

    # Set aller aktuell offenen Sessions zur schnellen Prüfung
    still_open = set(sessions.keys())
    # Bestehende Einträge durchgehen
    for session_id, row in prev_sessions.items():
        if row['end'] != '':
            # Bereits abgeschlossene Session beibehalten
            new_log.append(row)
        elif session_id in sessions:
            # Session ist noch aktiv – unverändert übernehmen
            new_log.append(row)
            still_open.discard(session_id)
        else:
            # Session war offen, ist jetzt aber verschwunden – daher beenden
            start_time = row['start']
            end_time = now_str
            t1 = parse_openvpn_time(start_time)
            t2 = parse_openvpn_time(end_time)
            # Differenz in Sekunden berechnen, falls das Parsen gelingt
            duration = int((t2 - t1).total_seconds()) if t1 and t2 else ''
            row['end'] = end_time
            row['duration_s'] = duration
            new_log.append(row)

    # Alle Sessions aus dem aktuellen Status, die nicht im Log existieren,
    # müssen neu angelegt werden (neu gestartete Verbindungen)
    for session_id, sess in sessions.items():
        if session_id not in prev_sessions:
            new_log.append({
                'user': sess['user'],
                'public_ip': sess['public_ip'],
                'tunnel_ip': sess['tunnel_ip'],
                'start': sess['start'],
                'end': '',
                'duration_s': ''
            })

    # Das gesamte Log erneut schreiben. Das Überschreiben der Datei ermöglicht
    # eine saubere Sortierung und verhindert alte, nicht mehr genutzte Einträge.
    fieldnames = ['user', 'public_ip', 'tunnel_ip', 'start', 'end', 'duration_s']
    with open(SESSION_LOG, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in new_log:
            writer.writerow(row)

if __name__ == '__main__':
    main()
