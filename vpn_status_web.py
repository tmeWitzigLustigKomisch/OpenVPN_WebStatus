#!/usr/bin/env python3
"""
Flask‑basierte Web‑Anwendung zum Anzeigen von OpenVPN‑Sitzungen.

Dieses Skript stellt eine komfortable Oberfläche bereit, um sowohl den
momentanen Status des VPN‑Servers als auch eine aus historischen Daten
generierte Übersicht anzuzeigen. Es werden u. a. folgende Funktionen geboten:

* **Live‑Ansicht**:  Zeigt alle derzeit verbundenen Clients mit Benutzername,
  öffentlicher IP und Verbindungsbeginn an.
* **Historie**:  Ließt die von `vpn_session_history.py` erzeugte CSV‑Datei
  ein und gruppiert die Sessions nach Nutzer und Datum. Es ist möglich, nur
  aktive oder nur beendete Sessions zu filtern.
* **Statistikdiagramm**:  Visualisiert die Anzahl aktiver Verbindungen der
  letzten 24 Stunden in Form eines Linien‑/Flächendiagramms.
* **Benutzer‑Alias**:  Ermöglicht die Hinterlegung eines sprechenden Namens
  für jeden OpenVPN‑Benutzer in einer JSON‑Datei.
* **Löschfunktionen**:  Einzelne oder alle Logeinträge können über die
  Oberfläche gelöscht werden.
* **Excel‑Export**:  Download der gefilterten Historie als `.xlsx`.

Die Anwendung bindet per Default an `0.0.0.0` Port 8050. Aus Sicherheitsgründen
sollte der Zugriff nach Möglichkeit beschränkt werden (siehe README).
"""

# coding: utf-8
from flask import Flask, render_template_string, request, redirect, url_for, send_file
import os, csv, io, json
from datetime import datetime, timedelta
import openpyxl
import matplotlib

# Matplotlib im Kopf‑losen Modus verwenden, da der Server kein Display besitzt
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import base64
from collections import defaultdict

# ---- Pfade ----
# Sie können diese Konstanten anpassen, um Status‑Log, Session‑Log und Alias‑Datei
# an andere Orte zu legen. Die Pfade müssen für den ausführenden Benutzer lesbar
# bzw. beschreibbar sein.
STATUS_LOG = '/var/log/openvpn-status.log'
SESSION_LOG = '/var/log/openvpn-sessions.csv'
ALIASES_FILE = '/var/log/openvpn-aliases.json'

# Flask‑Applikation initialisieren
app = Flask(__name__)

# ---------------------- Helpers ----------------------
def load_aliases():
    """
    Lese die Alias‑Datei und liefere ein Mapping von Benutzernamen zu Aliasnamen.

    In der optionalen JSON‑Datei `ALIASES_FILE` können Sie beliebige Schlüssel
    (OpenVPN‑Benutzernamen) und Werte (z. B. Realnamen, Abteilungsbezeichnungen)
    hinterlegen. Diese Funktion versucht, die Datei zu laden und gibt ein
    leeres Dictionary zurück, wenn die Datei fehlt oder nicht gelesen werden kann.

    :return: Dict mit Aliasnamen, z. B. {"flex‑RN13P": "Handy Flex"}
    """
    if not os.path.exists(ALIASES_FILE):
        return {}
    try:
        with open(ALIASES_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def parse_live_status():
    """
    Analyse des aktuellen OpenVPN‑Status zur Anzeige aktiver Verbindungen.

    Diese Funktion entspricht der Logik von `parse_status_log` im
    `vpn_session_history.py`, liefert die Daten jedoch in Listenform für die
    Darstellung im Web. Es werden nur die Felder benötigt, die in der
    Live‑Tabelle angezeigt werden: Benutzer, öffentliche IP (ohne Port) und
    Startzeit (`connected_since`).

    :return: Liste von Dicts, jeweils mit Schlüsseln `user`, `public_ip` und
             `connected_since`.
    """
    sessions = []
    if not os.path.exists(STATUS_LOG):
        return sessions
    with open(STATUS_LOG, 'r') as f:
        lines = [line.strip() for line in f]
    for line in lines:
        if line.startswith('CLIENT_LIST,'):
            parts = line.split(',')
            if len(parts) >= 8:
                cn = parts[1]                       # Benutzername
                real_addr = parts[2]                 # IP:Port
                ip = real_addr.split(':')[0]        # Nur IP ohne Port
                connected_since = parts[7]           # Startzeit
                sessions.append({
                    'user': cn,
                    'public_ip': ip,
                    'connected_since': connected_since,
                })
    return sessions

def parse_history():
    """
    Lade das CSV‑Protokoll und konvertiere es in eine sortierte Liste von Sessions.

    Jeder Eintrag im CSV wird um ein lesbares Dauer‑Feld (`duration_nice`) ergänzt,
    das aus der rohen Sekundenanzahl (`duration_s`) berechnet wird. Die Liste
    wird absteigend nach dem Startzeitpunkt sortiert, sodass neuere Einträge
    vorne stehen.

    :return: Liste von Dictionaries mit allen Feldern aus dem CSV plus
             `duration_nice`.
    """
    history = []
    if not os.path.exists(SESSION_LOG):
        return history
    with open(SESSION_LOG, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Lesbares Dauerfeld hinzufügen
            row['duration_nice'] = human_duration(row['duration_s'])
            history.append(row)
    # Nach Startzeit sortieren, neueste zuerst
    return sorted(history, key=lambda x: x['start'], reverse=True)

def human_duration(duration_s):
    """
    Konvertiere eine Dauer in Sekunden in eine menschenlesbare Darstellung.

    Für die History‑Ausgabe reicht die rohe Sekundenanzahl häufig nicht aus.
    Diese Funktion zerlegt die Dauer daher in Tage, Stunden, Minuten und
    Sekunden und gibt eine formattierte Zeichenkette zurück (z. B.
    "1 Tg, 2 Std, 5 Min, 30 Sek"). Falls die Eingabe leer oder nicht
    konvertierbar ist, wird ein leerer String zurückgegeben.

    :param duration_s: Dauer in Sekunden als String
    :return: Lesbare Dauer als String
    """
    try:
        s = int(duration_s)
    except Exception:
        return ""
    # Tage, Stunden, Minuten und Sekunden berechnen
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d} Tg")
    if h: parts.append(f"{h} Std")
    if m: parts.append(f"{m} Min")
    parts.append(f"{s} Sek")
    return ", ".join(parts)

def parse_filter(history, username, status):
    """
    Filtere die History nach Benutzername und Status.

    Der Benutzer kann in der Oberfläche einen spezifischen Benutzernamen
    auswählen und/oder nur aktive bzw. nur beendete Sessions anzeigen lassen.
    Diese Funktion implementiert die entsprechende Filterlogik.

    :param history: Komplette History‑Liste
    :param username: Gewählter Benutzer oder leerer String
    :param status: '', 'open' oder 'closed'
    :return: Gefilterte Liste
    """
    filtered = []
    for row in history:
        # Benutzerfilter: wenn gesetzt und ungleich, überspringen
        if username and username != row['user']:
            continue
        # Statusfilter für „nur aktiv“: nur Zeilen ohne Endzeit
        if status == 'open' and row['end'] != '':
            continue
        # Statusfilter für „nur beendet“: nur Zeilen mit Endzeit
        if status == 'closed' and row['end'] == '':
            continue
        filtered.append(row)
    return filtered

def group_history_by_user_day(history):
    """
    Gruppiere die History nach Benutzer und Tag und fasse Sessions zusammen.

    Für die tabellarische Darstellung im Web werden alle Sessions eines
    Benutzers an einem Tag zusammengefasst. Pro Gruppe wird die zuletzt
    gestartete Session (nach Startzeit sortiert) als `latest` herausgezogen,
    während alle weiteren Sessions dieses Tages unter `others` gesammelt werden.
    Dies ermöglicht ein kompaktes Layout mit Aufklappfunktion.

    :param history: Liste aller Sessions
    :return: Liste von Gruppen mit Schlüsseln `user`, `tag`, `latest` und `others`
    """
    groups = defaultdict(list)
    for row in history:
        dt = parse_dt(row['start'])
        # Datumsstring im Format YYYY‑MM‑DD ermitteln
        tag = dt.strftime('%Y-%m-%d') if dt else 'unbekannt'
        key = (row['user'], tag)
        groups[key].append(row)
    grouped = []
    for key, rows in groups.items():
        # Die Sessions dieses Benutzers/Tags nach Startzeit sortieren
        sorted_rows = sorted(rows, key=lambda x: x['start'], reverse=True)
        grouped.append({
            "user": key[0],
            "tag": key[1],
            "latest": sorted_rows[0],  # zuletzt gestartete Session
            "others": sorted_rows[1:],
        })
    # Gruppen nach Startzeit der neuesten Session sortieren
    grouped.sort(key=lambda g: g['latest']['start'], reverse=True)
    return grouped

def all_users(history):
    """
    Extrahiere aus der History eine sortierte Liste aller Benutzer.

    Diese Liste füllt das Dropdown für den Benutzerfilter in der Oberfläche.

    :param history: History‑Liste
    :return: Alphabetisch sortierte Liste der Benutzernamen
    """
    return sorted(set(row['user'] for row in history if row['user']))

def clear_history(selected_ids=None):
    """
    Lösche ausgewählte Einträge oder die komplette History aus dem CSV‑Log.

    Wird `selected_ids` nicht angegeben, wird eine neue, leere Datei mit nur
    der Kopfzeile erzeugt. Wenn `selected_ids` eine Liste von Session‑IDs
    enthält, werden nur diese Einträge aus der Datei entfernt. Die Session‑ID
    entspricht der Kombination `user|start` und muss mit dem aus dem Formular
    übermittelten Wert übereinstimmen.

    :param selected_ids: Liste der zu löschenden Session‑IDs oder `None`
    """
    if selected_ids is None:
        # Vollständiges Zurücksetzen: Datei mit nur Kopfzeile neu erstellen
        with open(SESSION_LOG, 'w') as f:
            f.write('user,public_ip,tunnel_ip,start,end,duration_s\n')
        return
    fieldnames = ['user', 'public_ip', 'tunnel_ip', 'start', 'end', 'duration_s']
    # Vorhandene Zeilen einlesen
    with open(SESSION_LOG, 'r') as f:
        rows = list(csv.DictReader(f))
    # Neue Datei schreiben; nur Zeilen behalten, die nicht ausgewählt wurden
    with open(SESSION_LOG, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            session_id = f"{row['user']}|{row['start']}"
            if session_id not in selected_ids:
                writer.writerow(row)

def create_graph_img(history, darkmode=True):
    """
    Erzeuge ein Base64‑PNG‑Bild, das die Anzahl aktiver Sessions pro Stunde der
    letzten 24 Stunden darstellt.

    Die Funktion erstellt 24 „Bins“ (Stunden‑Intervalle) rückwärts vom
    aktuellen Zeitpunkt. Für jedes Intervall wird gezählt, wie viele Sessions
    zu irgendeinem Zeitpunkt innerhalb des Intervalls aktiv waren. Startzeiten
    vor dem Ende des Intervalls und Endzeiten (oder jetzt, falls noch aktiv)
    nach dem Start des Intervalls werden berücksichtigt. Anschließend wird mit
    Matplotlib ein Liniendiagramm mit gefüllter Fläche erzeugt. Farben und
    Hintergrund wechseln abhängig vom Dark‑/Light‑Modus.

    :param history: Liste der protokollierten Sessions
    :param darkmode: True für dunkles Farbschema, False für helles Schema
    :return: Base64‑kodiertes PNG als String
    """
    now = datetime.now()
    # Stündliche Zeitabschnitte erzeugen: Liste von Zeitpunkten von -24h bis jetzt
    bins = [now - timedelta(hours=i) for i in range(24, -1, -1)]
    # X‑Achsenbeschriftungen (nur die Zeit, z. B. „13:00“)
    x_labels = [b.strftime('%H:%M') for b in bins[:-1]]
    y = []
    # Für jedes Intervall die Anzahl aktiver Sessions bestimmen
    for i in range(len(bins)-1):
        start = bins[i]
        end = bins[i+1]
        count = 0
        for row in history:
            t_start = parse_dt(row['start'])
            # Wenn die Session noch läuft, wird das Ende als „jetzt“ gesetzt
            t_end = parse_dt(row['end']) if row['end'] else now
            # Session zählt, wenn sie innerhalb des Intervalls aktiv war
            if t_start and t_start < end and t_end > start:
                count += 1
        y.append(count)
    # Neue Grafik erstellen; 10cm Breite, 2cm Höhe
    plt.figure(figsize=(10,2))
    # Farben abhängig vom Modus definieren
    bgcol = '#181c1f' if darkmode else '#ffffff'
    fgcol = '#ffffff' if darkmode else '#181c1f'
    gridcol = '#333333' if darkmode else '#cccccc'
    linecol = '#55ccff' if darkmode else '#1976d2'
    fillcol = '#2196f3' if darkmode else '#90caf9'
    x = list(range(len(x_labels)))
    plt.plot(x, y, marker='o', color=linecol)
    plt.fill_between(x, y, alpha=0.3, color=fillcol)
    plt.title("Verbindungen letzte 24h", color=fgcol)
    plt.xlabel("Uhrzeit", color=fgcol)
    plt.ylabel("Anzahl aktiv", color=fgcol)
    plt.xticks(x, x_labels, rotation=45, color=fgcol)
    plt.yticks(color=fgcol)
    plt.grid(True, color=gridcol)
    ax = plt.gca()
    ax.set_facecolor(bgcol)
    plt.gcf().patch.set_facecolor(bgcol)
    plt.tight_layout()
    # Bild in einen Bytes‑Puffer schreiben
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', facecolor=bgcol)
    plt.close()
    buf.seek(0)
    img_base64 = base64.b64encode(buf.getvalue()).decode()
    return img_base64

def parse_dt(s):
    """
    Versuche, ein Datum/Zeit‑String in ein `datetime`‑Objekt zu parsen.

    Diese Hilfsfunktion wird sowohl für Start‑ als auch Endzeiten genutzt. Sie
    akzeptiert sowohl ISO‑Format (`YYYY-MM-DD HH:MM:SS`) als auch das
    locale Format (`Mon Jan  2 HH:MM:SS YYYY`). Bei unbekanntem Format wird
    `None` zurückgegeben.

    :param s: Zeitstempel als String
    :return: `datetime` oder `None`
    """
    for fmt in ('%Y-%m-%d %H:%M:%S', '%a %b %d %H:%M:%S %Y'):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None

# ---------------------- HTML ----------------------
HTML = """
<!doctype html>
<title>OpenVPN Web Status</title>
<style>
body { background:#181c1f; color:#fff; font-family:sans-serif; margin:0; }
h1 { background:#263238; margin:0 0 1em 0; padding:1em; }
#autorefresh-controls { background:#23272b; padding:14px; margin:1em auto; width:95%; border-radius:8px; }
label, select, input[type=number] { font-size:15px; margin-right:12px;}
#history-controls {display:flex;align-items:center;justify-content:space-between;width:97%;margin:1em auto 0 auto;}
#history-controls > div {display:flex;align-items:center;}
#livebox { background:#27353a; border-radius:12px; margin-bottom:1.4em; width:95%; margin-left:auto; margin-right:auto;}
body.lightmode #livebox { background:#e3eaf3; }
table { border-collapse:collapse; width:95%; margin:1em auto;}
th, td { padding:7px 10px; border-bottom:1px solid #444;}
th { background:#263238; }
tr.active { background:#234; }
tr.closed { background:#222; color:#999; }
.btn-del { background:#c22; color:#fff; padding:8px 20px; border:none; border-radius:8px; font-size:15px; cursor:pointer;}
.btn-del:disabled {background:#555; color:#bbb;}
.btn-del:hover:enabled { background:#f44;}
input[type=checkbox].rowcheck {transform:scale(1.3);}
#selectall {transform:scale(1.3);}
.icon { width:1.1em; vertical-align:middle; margin-right:3px; }
.info-btn { background:none; border:none; cursor:pointer; font-size:18px; color:#9ec1ff; }
.info-btn:hover { color:#fff; }
.expandbtn {background:none;border:none;font-size:18px;cursor:pointer;color:#80d8ff;}
.expandbtn:hover {color:#fff;}
#filterbar { margin:1em 0 1.5em 0; text-align:center; width:97%; margin-left:auto; margin-right:auto;}
#filterbar select, #filterbar input { min-width:140px; }
#graphbox { width:97%; margin: 1em auto; background:#23272b; border-radius:12px; text-align:center; padding:1em;}
#download { background:#3574c0; color:#fff; padding:8px 18px; border:none; border-radius:8px; font-size:15px; cursor:pointer; margin-right: 10px;}
#download:hover { background:#62aaff;}
#modeSwitch { float:right; background:#333; color:#fff; padding:8px 14px; border-radius:8px; border:0; font-size:16px; cursor:pointer; margin-top:5px;}
#modeSwitch:hover { background:#2196f3; color:#fff;}
body.lightmode { background: #fff; color: #181c1f; }
body.lightmode h1, body.lightmode th { background: #e3eaf3; color: #181c1f; }
body.lightmode #autorefresh-controls, body.lightmode #graphbox { background: #f3f6fa; }
body.lightmode tr.closed { background: #eee; color: #555; }
body.lightmode tr.active { background: #d6e4ff; color: #111; }
body.lightmode .btn-del { background: #f88; color: #222; }
body.lightmode .btn-del:hover:enabled { background: #ffbbb0; }
body.lightmode .btn-del:disabled {background:#eee; color:#bbb;}
body.lightmode #download { background: #1976d2; }
body.lightmode #download:hover { background: #90caf9; color:#222;}
body.lightmode #modeSwitch { background:#eee; color:#333;}
body.lightmode .expandbtn { color:#1976d2;}
body.lightmode .expandbtn:hover { color:#222;}
.alias { color:#9ec1ff; font-size:0.95em; }
body.lightmode .alias { color:#415f9e; }

/* Modal */
.modal-backdrop {
  position: fixed; inset: 0; background: rgba(0,0,0,0.6); display:none; align-items:center; justify-content:center; z-index: 9999;
}
.modal {
  background: #222b33; color:#fff; border-radius: 10px; padding: 18px 20px; width: 520px; max-width: 94vw; box-shadow: 0 10px 40px rgba(0,0,0,0.5);
}
.modal h3 { margin-top:0; margin-bottom:10px; font-size: 20px; color:#90caf9;}
.modal .row { display:flex; gap:10px; margin:6px 0;}
.modal .row .k { width:160px; color:#9bb;}
.modal .row .v { flex:1; color:#eef;}
.modal .actions { text-align: right; margin-top: 12px; }
.modal .actions button { background:#3574c0; color:#fff; border:0; border-radius:8px; padding:8px 14px; cursor:pointer; }
.modal .actions button:hover { background:#62aaff; }
body.lightmode .modal { background:#fff; color:#222; }
body.lightmode .modal h3 { color:#1976d2; }
body.lightmode .modal .row .k { color:#445; }
body.lightmode .modal .row .v { color:#111; }
</style>
<button id="modeSwitch">🌙</button>
<h1>OpenVPN Web Status</h1>

<div id="autorefresh-controls">
  <label><input type="checkbox" id="autoReloadToggle"> Automatisch neu laden</label>
  <label>Intervall:
    <select id="reloadInterval">
      <option value="5">5s</option>
      <option value="10" selected>10s</option>
      <option value="30">30s</option>
      <option value="60">60s</option>
      <option value="120">2min</option>
    </select>
  </label>
</div>

<div id="graphbox">
    <img id="statimg" src="data:image/png;base64,{{graphimg}}" alt="Verbindungsstatistik" style="max-width:100%;">
</div>

<h2>Aktive Verbindungen (LIVE)</h2>
<div id="livebox">
<table>
    <tr>
        <th>&#128994;</th>
        <th>User</th>
        <th>Alias</th>
        <th>Öffentliche IP</th>
        <th>Verbunden seit</th>
        <th>Info</th>
    </tr>
    {% for s in live %}
    <tr class="active">
        <td><span class="icon" title="Online">&#128994;</span></td>
        <td>{{s.user}}</td>
        <td class="alias">{% if aliases.get(s.user) %}{{aliases.get(s.user)}}{% endif %}</td>
        <td>{{s.public_ip}}</td>
        <td>{{s.connected_since}}</td>
        <td>
          <button type="button" class="info-btn"
            data-type="live"
            data-user="{{s.user}}"
            data-alias="{{aliases.get(s.user,'')}}"
            data-public_ip="{{s.public_ip}}"
            data-start="{{s.connected_since}}"
          >ℹ️</button>
        </td>
    </tr>
    {% endfor %}
    {% if not live %}
    <tr><td colspan=6><i>Keine aktiven Verbindungen</i></td></tr>
    {% endif %}
</table>
</div>

<h2 style="margin-top:2em;margin-bottom:0.5em;">Verbindungs-History (gruppiert: User+Tag, mit Aufklappen)</h2>
<div id="history-controls">
    <div>
        <button id="download" type="button"
         onclick="window.location.href='{{url_for('download_excel', username=username, status=status)}}';">
         &#128190; Download Excel</button>
        <button id="delSelected" class="btn-del" disabled onclick="return submitSelDel();">Selektierte löschen</button>
    </div>
    <button id="delAll" class="btn-del" style="font-size:17px;padding:8px 18px 8px 18px;" title="Gesamte History löschen" onclick="return submitAllDel();">&#128465;</button>
</div>

<form method="GET" id="filterbar" action="{{url_for('home')}}" style="width:97%;margin:auto;">
    <select name="username">
        <option value="">(kein Filter)</option>
        {% for user in userlist %}
          <option value="{{user}}" {% if username == user %}selected{% endif %}>{{user}}</option>
        {% endfor %}
    </select>
    <select name="status">
        <option value="">alle</option>
        <option value="open" {% if status=='open' %}selected{% endif %}>nur aktiv</option>
        <option value="closed" {% if status=='closed' %}selected{% endif %}>nur beendet</option>
    </select>
    <button type="submit" style="padding:6px 16px;border-radius:8px;border:0;background:#333;color:#fff;">Filtern</button>
</form>

<form id="historyForm" method="POST">
<table id="historytbl">
    <tr>
        <th></th>
        <th><input type="checkbox" id="selectall"></th>
        <th>User</th>
        <th>Alias</th>
        <th>Tag</th>
        <th>Öffentliche IP</th>
        <th>Tunnel IP</th>
        <th>Start</th>
        <th>Ende</th>
        <th>Dauer</th>
        <th>Status</th>
        <th>Info</th>
    </tr>
    {% for g in groups %}
    <tr class="{{ 'active' if g['latest']['end']=='' else 'closed' }}">
        <td>
            {% if g['others'] %}
                <button class="expandbtn" onclick="toggleGroup('{{g.user}}_{{g.tag}}');return false;">&#x25B6;</button>
            {% endif %}
        </td>
        <td><input type="checkbox" class="rowcheck" name="rowselect" value="{{g.latest.user}}|{{g.latest.start}}"></td>
        <td>{{g.user}}</td>
        <td class="alias">{% if aliases.get(g.user) %}{{aliases.get(g.user)}}{% endif %}</td>
        <td>{{g.tag}}</td>
        <td>{{g.latest.public_ip}}</td>
        <td>{{g.latest.tunnel_ip}}</td>
        <td>{{g.latest.start}}</td>
        <td>{{g.latest.end if g.latest.end else 'läuft...'}}</td>
        <td>{{g.latest.duration_nice}}</td>
        <td>
            {% if g.latest.end == '' %}
                <span class="icon" title="Aktiv">&#128994;</span>
            {% else %}
                <span class="icon" title="Beendet">&#128308;</span>
            {% endif %}
        </td>
        <td>
          <button type="button" class="info-btn"
            data-type="history"
            data-user="{{g.latest.user}}"
            data-alias="{{aliases.get(g.latest.user,'')}}"
            data-public_ip="{{g.latest.public_ip}}"
            data-tunnel_ip="{{g.latest.tunnel_ip}}"
            data-start="{{g.latest.start}}"
            data-end="{{g.latest.end if g.latest.end else ''}}"
            data-duration="{{g.latest.duration_nice}}"
          >ℹ️</button>
        </td>
    </tr>
    {% for h in g.others %}
    <tr id="grp_{{g.user}}_{{g.tag}}" style="display:none;" class="{{ 'active' if h['end']=='' else 'closed' }}">
        <td></td>
        <td><input type="checkbox" class="rowcheck" name="rowselect" value="{{h.user}}|{{h.start}}"></td>
        <td>{{h.user}}</td>
        <td class="alias">{% if aliases.get(h.user) %}{{aliases.get(h.user)}}{% endif %}</td>
        <td></td>
        <td>{{h.public_ip}}</td>
        <td>{{h.tunnel_ip}}</td>
        <td>{{h.start}}</td>
        <td>{{h.end if h.end else 'läuft...'}}</td>
        <td>{{h.duration_nice}}</td>
        <td>
            {% if h.end == '' %}
                <span class="icon" title="Aktiv">&#128994;</span>
            {% else %}
                <span class="icon" title="Beendet">&#128308;</span>
            {% endif %}
        </td>
        <td>
          <button type="button" class="info-btn"
            data-type="history"
            data-user="{{h.user}}"
            data-alias="{{aliases.get(h.user,'')}}"
            data-public_ip="{{h.public_ip}}"
            data-tunnel_ip="{{h.tunnel_ip}}"
            data-start="{{h.start}}"
            data-end="{{h.end if h.end else ''}}"
            data-duration="{{h.duration_nice}}"
          >ℹ️</button>
        </td>
    </tr>
    {% endfor %}
    {% endfor %}
    {% if not groups %}
    <tr><td colspan=12><i>Noch keine History-Daten.</i></td></tr>
    {% endif %}
</table>
</form>
<form id="allDelForm" method="POST" style="display:none;"></form>

<!-- Modal -->
<div class="modal-backdrop" id="modalBackdrop">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle">
    <h3 id="modalTitle">Details</h3>
    <div id="modalBody"></div>
    <div class="actions">
      <button onclick="closeModal()">Schließen</button>
    </div>
  </div>
</div>

<script>
function confirmSelDel() {
    let checked = document.querySelectorAll('input.rowcheck:checked');
    if (checked.length == 0) return false;
    if (!confirm('Markierte Einträge wirklich löschen?')) return false;
    if (!confirm('Wirklich? Ausgewählte Zeilen unwiderruflich löschen?')) return false;
    return true;
}
function submitSelDel() {
    if (confirmSelDel()) {
        document.getElementById('historyForm').action = "{{url_for('clear_history_route')}}";
        document.getElementById('historyForm').submit();
    }
    return false;
}
function submitAllDel() {
    if (!confirm('Bist du sicher, dass du die gesamte History löschen willst?')) return false;
    if (!confirm('Wirklich ALLES löschen? Das kann NICHT rückgängig gemacht werden!')) return false;
    document.getElementById('allDelForm').action = "{{url_for('clear_history_route')}}";
    document.getElementById('allDelForm').submit();
    return false;
}
function toggleGroup(id) {
    let rows = document.querySelectorAll("#historytbl tr[id='grp_"+id+"']");
    for (let row of rows) {
        if (row.style.display === "none") row.style.display = "";
        else row.style.display = "none";
    }
}

// Enable/disable Button je nach Auswahl & Select All logic
function updateDelSelectedBtn() {
    let checkboxes = document.querySelectorAll('input.rowcheck');
    let checked = document.querySelectorAll('input.rowcheck:checked');
    document.getElementById('delSelected').disabled = checked.length == 0;
    let selectAll = document.getElementById('selectall');
    if (checked.length === 0) {
        selectAll.checked = false; selectAll.indeterminate = false;
    }
    else if (checked.length === checkboxes.length) {
        selectAll.checked = true; selectAll.indeterminate = false;
    }
    else selectAll.indeterminate = true;
}
document.addEventListener('change', function(e){
    if(e.target.classList.contains('rowcheck')) updateDelSelectedBtn();
});
document.getElementById('selectall').addEventListener('change', function(e){
    let checked = this.checked;
    document.querySelectorAll('input.rowcheck').forEach(cb => { cb.checked = checked; });
    updateDelSelectedBtn();
});
window.onload = updateDelSelectedBtn;

// Auto-Reload
const reloadToggle = document.getElementById('autoReloadToggle');
const reloadInterval = document.getElementById('reloadInterval');
function setReload() {
  let enabled = localStorage.getItem('reloadEnabled') === 'true';
  let interval = parseInt(localStorage.getItem('reloadInterval')) || 10;
  reloadToggle.checked = enabled;
  reloadInterval.value = interval;
}
function saveReloadSettings() {
  localStorage.setItem('reloadEnabled', reloadToggle.checked ? 'true' : 'false');
  localStorage.setItem('reloadInterval', reloadInterval.value);
}
let reloadTimer = null;
function setupAutoReload() {
  if (reloadTimer) clearTimeout(reloadTimer);
  if (reloadToggle.checked) {
    reloadTimer = setTimeout(() => { window.location.reload(); }, parseInt(reloadInterval.value) * 1000);
  }
}
reloadToggle.addEventListener('change', () => { saveReloadSettings(); setupAutoReload(); });
reloadInterval.addEventListener('change', () => { saveReloadSettings(); setupAutoReload(); });
setReload(); setupAutoReload();

// Theme Umschaltung + Diagramm reload
function setMode(mode) {
  if (mode === 'light') {
    document.body.classList.add('lightmode');
    document.getElementById('modeSwitch').innerText = '🌞';
  } else {
    document.body.classList.remove('lightmode');
    document.getElementById('modeSwitch').innerText = '🌙';
  }
  localStorage.setItem('openvpn_mode', mode);
}
function reloadStatImg() {
  let mode = document.body.classList.contains('lightmode') ? 'light' : 'dark';
  let img = document.getElementById('statimg');
  fetch('/statimg?mode=' + mode)
    .then(resp => resp.text())
    .then(dataurl => { img.src = dataurl; });
}
let mode = localStorage.getItem('openvpn_mode') || 'dark';
setMode(mode);
reloadStatImg();
document.getElementById('modeSwitch').onclick = function() {
  mode = (mode === 'dark') ? 'light' : 'dark';
  setMode(mode);
  reloadStatImg();
};

// ===== Modal (zentriert) =====
const modalBackdrop = document.getElementById('modalBackdrop');
const modalBody = document.getElementById('modalBody');
function closeModal(){ modalBackdrop.style.display='none'; modalBody.innerHTML=''; }
modalBackdrop.addEventListener('click', (e)=>{ if(e.target===modalBackdrop) closeModal(); });
document.addEventListener('keydown', (e)=>{ if(e.key==='Escape') closeModal(); });

function row(k,v){ const val=(v && String(v).trim()!=='')?v:'—'; return '<div class="row"><div class="k">'+k+'</div><div class="v">'+val+'</div></div>'; }

document.addEventListener('click', function(e){
  const btn = e.target.closest('.info-btn');
  if (!btn) return;
  e.preventDefault(); // Sicherheitsnetz: Keine Navigation

  const type = btn.dataset.type;
  const user = btn.dataset.user || '';
  const alias = btn.dataset.alias || '';
  const public_ip = btn.dataset.public_ip || '';
  const tunnel_ip = btn.dataset.tunnel_ip || '';
  const start = btn.dataset.start || '';
  const end = btn.dataset.end || '';
  const duration = btn.dataset.duration || '';

  let title = 'Details';
  let html = '';
  if (type === 'live') {
    title = 'Live‑Verbindung';
    html += row('User', user);
    html += row('Alias', alias);
    html += row('Öffentliche IP', public_ip);
    html += row('Verbunden seit', start);
    html += row('Status', 'Aktiv');
  } else {
    title = 'Historische Verbindung';
    html += row('User', user);
    html += row('Alias', alias);
    html += row('Öffentliche IP', public_ip);
    html += row('Tunnel IP', tunnel_ip);
    html += row('Start', start);
    html += row('Ende', end || 'läuft…');
    html += row('Dauer', duration);
    html += row('Status', end ? 'Beendet' : 'Aktiv');
  }

  document.getElementById('modalTitle').innerText = title;
  modalBody.innerHTML = html;
  modalBackdrop.style.display = 'flex';
});
</script>
"""

@app.route('/', methods=['GET'])
def home():
    """
    Startseite der Web‑Applikation.

    Diese Route rendert die HTML‑Vorlage und füllt sie mit dynamischen Daten:
    * `history` enthält alle Sessions aus dem CSV‑Log.
    * Über die Query‑Parameter `username` und `status` können Filter angewendet
      werden. Leere Strings bedeuten „kein Filter“.
    * Die Liste `groups` enthält nach Benutzer und Tag gruppierte Sessions
      inklusive der Aufklapp‑Logik.
    * `userlist` versorgt das Dropdown des Benutzerfilters.
    * `aliases` liefert Aliasnamen aus der JSON‑Datei.
    * `graphimg` ist ein Base64‑String mit dem 24‑Stunden‑Diagramm.

    :return: Gerenderter HTML‑String
    """
    history = parse_history()
    username = request.args.get('username', '').strip()
    status = request.args.get('status', '').strip()
    filtered_history = parse_filter(history, username, status)
    groups = group_history_by_user_day(filtered_history)
    userlist = all_users(history)
    aliases = load_aliases()
    graphimg = create_graph_img(history, darkmode=True)
    return render_template_string(
        HTML, live=parse_live_status(), groups=groups,
        username=username, status=status, graphimg=graphimg,
        userlist=userlist, aliases=aliases
    )

@app.route('/statimg')
def statimg():
    """
    Endpunkt für das Statistikbild.

    Die JavaScript‑Funktion in der HTML‑Vorlage ruft diesen Endpunkt auf, um
    abhängig vom aktuellen Theme (dark/light) eine Grafik als Data‑URL
    zurückzubekommen. Es wird keine eigene HTML‑Seite gerendert, sondern
    lediglich der Base64‑String des Bildes ausgegeben.

    :query mode: "dark" (Standard) oder "light"
    :return: String im Format "data:image/png;base64,..."
    """
    mode = request.args.get('mode', 'dark')
    darkmode = (mode != 'light')
    history = parse_history()
    img_data = create_graph_img(history, darkmode=darkmode)
    return f"data:image/png;base64,{img_data}"

@app.route('/clear_history', methods=['POST'])
def clear_history_route():
    """
    Formularverarbeitung für das Löschen von History‑Einträgen.

    Wird das Formular mit ausgewählten Zeilen abgeschickt (`rowselect`), ruft
    diese Funktion `clear_history` mit diesen IDs auf. Ist keine Auswahl
    vorhanden, wird die gesamte History geleert. Anschließend wird auf die
    Startseite umgeleitet.

    :return: Redirect zur Startseite
    """
    selected = request.form.getlist('rowselect')
    if selected:
        clear_history(selected_ids=selected)
    else:
        clear_history()
    return redirect(url_for('home'))

@app.route('/download_excel', methods=['GET'])
def download_excel():
    """
    Generiere einen Excel‑Export der gefilterten Sessions.

    Abhängig von den URL‑Parametern `username` und `status` wird die History
    gefiltert und anschließend in ein neues Arbeitsblatt geschrieben. Jede
    Zeile enthält zusätzlich den Alias (falls vorhanden) sowie sowohl die
    Rohdauer als auch die menschenlesbare Dauer. Der Dateiname spiegelt die
    gewählten Filter wider (z. B. `openvpn-history_flex-RN13P_open.xlsx`).

    :return: Eine `.xlsx`‑Datei als Download
    """
    username = request.args.get('username', '').strip()
    status = request.args.get('status', '').strip()
    history = parse_history()
    filtered = parse_filter(history, username, status)
    output = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "OpenVPN History"
    headers = ['User', 'Alias', 'Öffentliche IP', 'Tunnel IP', 'Start', 'Ende', 'Dauer (s)', 'Dauer (lesbar)']
    ws.append(headers)
    aliases = load_aliases()
    for row in filtered:
        ws.append([
            row['user'],
            aliases.get(row['user'], ''),
            row['public_ip'],
            row['tunnel_ip'],
            row['start'],
            row['end'],
            row['duration_s'],
            row['duration_nice']
        ])
    # Dateiname dynamisch anhand der Filter bauen
    parts = ["openvpn-history"]
    if username:
        parts.append(username)
    if status:
        parts.append(status)
    if not username and not status:
        parts.append("all")
    filename = "_".join(parts) + ".xlsx"

    wb.save(output)
    output.seek(0)
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name=filename)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8050)
