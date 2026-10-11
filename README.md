# Zonen-Dashboard
eToro 

# Prüfprotokoll – Version 0.1.0

Datum: 11.10.2026. Python 3.12. Abhängigkeiten in requirements.txt fixiert.

Ergebnis: 8 automatisierte Tests bestanden (pytest, 4,88 Sekunden).

1. Laufende Kerze ausgeschlossen; widersprüchliche OHLC-Daten abgewiesen.
2. Auf allen vier Zeitebenen stimmen die bis zum jeweiligen Zeitpunkt bekannten
   Zonen zwischen Teilhistorie und Gesamthistorie überein (drei Schnitte).
3. Dokumentiertes verschachteltes Kerzenformat verarbeitet; Reihenfolge sortiert;
   falsches Instrument und fehlende Kerzen abgewiesen.
4. Einzelner Schluss außerhalb löst keinen bestätigten Bruch aus; zwei Schlüsse
   bestätigen ihn. Spätere Erholung lässt die Zone nicht still wieder aufleben.
5. Adapter verweigert Nicht-Marktdatenpfade; Fehlermeldungen enthalten keine Schlüssel.
6. Synthetische Zeitebenen beruhen auf demselben 5Min-Pfad.
7. Streamlit-Demostart und fehlende Secrets mit AppTest geprüft, ohne Ausnahme.
8. Passwort, Instrumentsuche und Auswertung mit simuliertem eToro-Adapter durchlaufen.

Keine Prüfung mit echten eToro-Schlüsseln oder authentifizierten Live-Daten.
Keine Bereitstellung auf GitHub/Streamlit durchgeführt.
Keine Rendite-, Trefferquoten- oder vollständige historische Strategieverifikation.
Keine manuelle Browser-/Mobilgeräte-Sichtprüfung; UI funktional mit Streamlit AppTest geprüft.
Die Einschränkungen der Erstversion sind in START_HIER.md und der App dokumentiert.

# eToro Zonenradar – Streamlit Cloud

Version 0.1.0 · erste lauffähige Analyseversion · 11.10.2026

## 1. Dateien auf GitHub ablegen

ZIP entpacken. Ein eigenes privates GitHub-Repository anlegen, beispielsweise
`etoro-zonenradar`. Die Inhalte des Ordners dort hochladen, insbesondere:

- app.py
- engine.py
- etoro.py
- requirements.txt

Die Dateien sollten direkt im Repository-Hauptverzeichnis liegen.
`.streamlit/config.toml` ist die optionale Farbschema-Konfiguration.
Die enthaltene `secrets.example.toml` enthält nur Platzhalter.
Niemals echte Schlüssel in diese Datei oder eine andere GitHub-Datei eintragen.

## 2. Streamlit-App anlegen

https://share.streamlit.io öffnen und mit GitHub verbinden.
„Create app“ wählen, das Repository und den Branch (üblicherweise `main`) auswählen.
Als „Main file path“ `app.py` angeben. In „Advanced settings“ Python **3.12** wählen.
Wenn du den ganzen Ordner hochgeladen hast, lautet der Pfad entsprechend
`etoro_zonenradar/app.py`; die Variante ohne zusätzlichen Ordner ist einfacher.

## 3. Schlüssel hinterlegen

Unter „Advanced settings → Secrets“ beim Anlegen oder später unter
„App settings → Secrets“ Folgendes einfügen und die Platzhalter dort ersetzen:

```toml
ETORO_API_KEY = "DEIN_PUBLIC_API_KEY"
ETORO_USER_KEY = "DEIN_USER_KEY"
APP_PASSWORD = "DEIN_EIGENES_LANGES_PASSWORT"
```

Das Dashboard-Passwort frei wählen, mindestens 16 Zeichen, nicht dein eToro-Passwort.
Public API Key und User Key sind zwei verschiedene Angaben. eToro-Zugang mit
Leserechten und der für Kursabrufe erforderlichen Marktdaten-Freigabe verwenden.
Die App ruft ausschließlich Marktdaten ab und benötigt keine Trading-Schreibrechte.
Schlüssel nicht im Chat, im Repository oder in Screenshots teilen.

Unter App settings / Sharing die App zusätzlich auf **privat** stellen und den
Zugriff auf dich beschränken. Das eingebaute Passwort ist eine Zusatzbarriere,
kein Ersatz für die Zugriffskontrolle von Streamlit. Die gespeicherten Schlüssel
gehören dem Betreiber der App; es handelt sich nicht um eine Mehrbenutzer-Brokeranmeldung.

## 4. Start und Verbindungstest

„Deploy“ auswählen. Die App startet im deutlich gekennzeichneten synthetischen
Demomodus. Links „eToro · API“ wählen, mit dem Dashboard-Passwort entsperren,
ein eToro-Symbol eingeben und „Instrument suchen / Verbindung testen“ anklicken.
Den exakten Treffer anhand Symbol, Name und Instrument-ID auswählen.
Insbesondere Standard-, Währungs- und 24/7-Varianten nicht verwechseln.

Alle vier Zeitebenen werden geladen. Unter „Daten & Methode“ Beginn, letzten
Kerzenschluss und Kerzenzahl prüfen. Den letzten 5Min-Schluss mit derselben
abgeschlossenen Kerze im eToro-Chart vergleichen. Es ist kein Live-Bid/Ask-Kurs.

## 5. Was die Version kann

- Unterstützung/Widerstand durch bestätigte Wendepunkte.
- Preisbasierte Angebots-/Nachfragebereiche nach kompakten Basen und Impulsen.
- Vermutete Liquiditätsbereiche an ähnlichen Hochs/Tiefs.
- 4H/1H/15Min/5Min, jeweils separat und mit sichtbaren Überschneidungen.
- Besuchszählung, bestätigter Bruch, mögliche Sweeps mit Zeitstempel.
- Interaktiver Chart, tabellarische Übersicht und CSV-Export.
- Nur abgeschlossene Kerzen, keine vorgezogene Pivot-Erkennung.
- Eindeutig gekennzeichnete synthetische Demo ohne API-Zugang.
- Optionale Aktualisierung alle 60 Sekunden bei aktiver Sitzung.

## 6. Grenzen und Betriebsweise

Die Regeln und Punktwerte sind heuristische Ausgangswerte, kein nachgewiesener
Handelsvorteil. Es gibt kein echtes Orderbuch und keine Bestätigung versteckter
Stops. Volumen wird nicht als zentralisiertes Handelsvolumen ausgegeben.
Überlappende Zonen bleiben als einzelne begründete Beobachtungen erhalten;
es findet keine automatische Verschmelzung oder Addition ihrer Punktwerte statt.

Ein Bruch benötigt zwei Schlusskurse jenseits der Zone mit ATR-Puffer. Gebrochene
Zonen bleiben gebrochen. Rollenwechsel und bestätigte Retests sind noch nicht
modelliert. Auch Vortages-/Session-Level, persistentes Kursarchiv, vollständiger
Strategie-Backtest und externe Push-Alarme sind nicht Bestandteil dieser Version.

Maximal 1000 Kerzen pro Zeitrahmen je Abruf. Der tatsächlich verfügbare Zeitraum
wird angezeigt. Das ist keine langjährige Intraday-Historie. Das gleitende
Datenfenster kann alte Zonen verlieren; es gibt noch keine dauerhafte Zonendatenbank.
Zeitlücken werden gezählt, nicht automatisch aufgefüllt. Ohne Handelskalender
kann das Programm Marktpausen und Feeddaten-Ausfälle nicht sicher unterscheiden.

Streamlit Community Cloud kann schlafen oder neu starten. Geschlossener Browser
bedeutet keinen zugesicherten Analysebetrieb. Keine dauerhafte Überwachung,
keine verlässliche Hintergrundalarmierung. Für diese Funktionen wäre später ein
separater Dienst mit dauerhafter Speicherung nötig.

## Fehlerhilfe

- 401: Beide Schlüssel auf Vollständigkeit, Vertauschung und Gültigkeit prüfen.
- 403: Marktdaten-Freigabe/API-Zugriff im eToro-Portal prüfen. Ein Zugriffsschutz
  seitens eToro kann ebenfalls eine Rolle spielen; keine Umgehung implementiert.
- 429: Mindestens eine Minute warten. Mehrere Tabs vermeiden.
- Keine Suchtreffer: Genaues eToro-Symbol verwenden; Instrumentnamen sind nicht immer Ticker.
- Keine frischen Daten am Wochenende: Kann eine reguläre Marktpause sein.
- Ungültiges Antwortformat: Kein stiller Ersatz durch Demo. Ohne Geheimnisse den
  angezeigten Fehler, das Symbol und die betroffene Zeitebene zur Prüfung melden.

## Lokaler Start (optional)

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Tests: `python -m pip install pytest`, danach `python -m pytest tests -q`.

## Prüfstand

Siehe PRUEFUNG.md. Die API-Anbindung ist nach offizieller Dokumentation implementiert,
aber ohne deine Zugangsdaten nicht gegen dein eToro-Konto getestet. Es ist noch
keine bereitgestellte Cloud-App und keine statistisch validierte Tradingstrategie.

## Offizielle Referenzen

- https://api-portal.etoro.com/api-reference/market-data/get-instrument-candle-history
- https://api-portal.etoro.com/api-reference/market-data/search-for-instruments
- https://builders.etoro.com/get-started
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management
- https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings
