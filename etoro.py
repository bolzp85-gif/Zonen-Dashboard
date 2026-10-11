"""Read-only eToro market-data adapter. No trading or portfolio endpoints."""
import uuid
import requests
import pandas as pd
from engine import TF, clean_candles

class FeedError(Exception):
    pass

class Client:
    def __init__(self, api_key, user_key):
        self.keys = {'x-api-key':api_key.strip(), 'x-user-key':user_key.strip()}

    def get(self, path, params=None):
        if not path.startswith('/market-data/'):
            raise FeedError('Nur Marktdatenabrufe erlaubt.')
        try:
            r = requests.get('https://public-api.etoro.com/api/v1'+path,
                             headers={**self.keys,'x-request-id':str(uuid.uuid4())},params=params,
                             timeout=(5,20),allow_redirects=False)
        except requests.RequestException:
            raise FeedError('Verbindung fehlgeschlagen oder Zeitüberschreitung. Erneut versuchen.') from None
        errors = {401:'Schlüsselpaar ungültig oder abgelaufen.',403:'Zugriff abgelehnt. Marktdaten-Berechtigung und API-Freigabe prüfen.',429:'Abruflimit erreicht. Mindestens 60 Sekunden warten.',404:'API-Endpunkt oder Instrument nicht gefunden.'}
        if r.status_code != 200:
            raise FeedError(f'HTTP {r.status_code}: '+errors.get(r.status_code,'eToro-Abruf fehlgeschlagen.'))
        try:
            return r.json()
        except ValueError:
            raise FeedError('eToro hat kein gültiges JSON geliefert.') from None

    def search(self, symbol):
        data = self.get('/market-data/search',{'internalSymbolFull':symbol, 'fields':'instrumentId,internalSymbolFull,displayname', 'pageSize':100,'pageNumber':1})
        if not isinstance(data,dict) or not isinstance(data.get('items'),list):
            raise FeedError('Unbekanntes Antwortformat bei der Instrumentsuche.')
        return [x for x in data['items'] if isinstance(x,dict) and 'instrumentId' in x and 'internalSymbolFull' in x]

    def candles(self, instrument_id, tf, count):
        interval, minutes = TF[tf]
        if not 40 <= count <= 1000 or int(instrument_id) <= 0:
            raise FeedError('Ungültige Instrument-ID oder Kerzenanzahl.')
        data = self.get(f'/market-data/instruments/{int(instrument_id)}/history/candles/desc/{interval}/{count}')
        if not isinstance(data,dict) or data.get('interval') != interval:
            raise FeedError('Die gelieferte Zeitebene stimmt nicht mit der Anfrage überein.')
        return parse_candles(data, instrument_id, minutes)


def parse_candles(data, instrument_id, minutes, now=None):
    if not isinstance(data,dict) or not isinstance(data.get('candles'),list):
        raise FeedError('Keine Kerzendaten oder unbekanntes Antwortformat.')
    groups = [g for g in data['candles'] if isinstance(g,dict) and str(g.get('instrumentId'))==str(instrument_id)]
    if len(groups)!=1 or not isinstance(groups[0].get('candles'),list) or not groups[0]['candles']:
        raise FeedError('Keine eindeutig zugeordneten Kerzendaten für dieses Instrument.')
    records = groups[0]['candles']
    if any(str(r.get('instrumentID',instrument_id)) != str(instrument_id) for r in records):
        raise FeedError('Instrument-ID in Kerzen widersprüchlich.')
    try:
        return clean_candles(pd.DataFrame(records).rename(columns={'fromDate':'time'}),minutes,now)
    except (ValueError,TypeError,KeyError):
        raise FeedError('Kerzendaten ungültig; Analyse angehalten.') from None
