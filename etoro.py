"""eToro market-data-only adapter for Zonenradar v0.5.0.

All requests are GET and allowlisted. The response is always verified to belong
where possible to the selected instrument. No trading or portfolio endpoints.
No credentials are persisted, logged or exported.
"""
from __future__ import annotations
from typing import Any
from uuid import uuid4
import pandas as pd
import requests
from engine import TF, clean_candles

BASE = 'https://public-api.etoro.com'

class FeedError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _instrument_records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for field in ('results', 'items', 'instruments', 'data'):
            value = payload.get(field)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def parse_candles(payload: Any, instrument_id: int | None = None, minutes: int = 5, now=None) -> pd.DataFrame:
    """Parse nested eToro v1 candles as CLOSED UTC OHLC, sorted ascending.

    Raise rather than silently selecting a different instrument or contradictory
    candles. Provider volume is intentionally ignored (CFD feed != venue volume).
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('candles'), list):
        raise FeedError('Ungültiges Kerzenformat: candles-Liste fehlt.')
    rows = []
    for block in payload['candles']:
        if not isinstance(block, dict):
            continue
        if instrument_id is not None and block.get('instrumentId') is not None:
            try:
                if int(block['instrumentId']) != int(instrument_id):
                    raise FeedError('Kerzendaten gehören zu einem anderen Instrument.')
            except (ValueError, TypeError):
                raise FeedError('Ungültige Instrument-ID in Kerzenantwort.') from None
        candles = block.get('candles')
        items = candles if isinstance(candles,list) else [block]
        for row in items:
            if isinstance(row,dict):
                rows.append(row)
    if not rows:
        raise FeedError('Die eToro-Antwort enthält keine Kerzen.')
    data = pd.DataFrame(rows)
    time_col = next((x for x in ('fromDate','date','time') if x in data.columns),None)
    if time_col is None or not all(x in data.columns for x in ('open','high','low','close')):
        raise FeedError('Kerzenformat erfordert fromDate und Open/High/Low/Close.')
    frame = data.rename(columns={time_col:'time'})[['time','open','high','low','close']]
    try:
        parsed = clean_candles(frame,minutes,now=now)
    except (ValueError,TypeError) as e:
        raise FeedError(f'Kerzendaten können nicht ausgewertet werden: {e}') from None
    if parsed.empty:
        raise FeedError('Keine abgeschlossenen Kerzen vorhanden.')
    return parsed

class Client:
    def __init__(self, api_key: str, user_key: str, session=None, timeout: int=15):
        if not str(api_key).strip() or not str(user_key).strip():
            raise FeedError('Öffentlicher API-Schlüssel und User-Key fehlen.')
        self._api_key = str(api_key)
        self._user_key = str(user_key)
        self.session = session or requests.Session()
        self.timeout = int(timeout)

    def get(self, *_args, **_kwargs):
        """No arbitrary endpoint access: always use search/candles allowlist."""
        raise FeedError('Nur schreibgeschützte Such- und Kursdaten-Endpunkte erlaubt.')

    def _get(self, endpoint: str, params: dict | None = None) -> Any:
        allowed = (endpoint in ('/api/v2/market-data/instruments','/api/v1/market-data/search')
                   or (endpoint.startswith('/api/v1/market-data/instruments/')
                       and '/history/candles/desc/' in endpoint))
        if not allowed:
            raise FeedError('Der angeforderte API-Endpunkt ist nicht freigegeben.')
        headers = {'x-api-key':self._api_key,'x-user-key':self._user_key,
                   'x-request-id':str(uuid4()),'Accept':'application/json'}
        try:
            response = self.session.get(BASE + endpoint, params=params,headers=headers,timeout=self.timeout)
        except requests.RequestException as e:
            raise FeedError(f'Netzwerkfehler/Timeout bei Marktdaten: {type(e).__name__}') from None
        if response.status_code != 200:
            reason={400:'Ungültige Anfrage',401:'Authentifizierung fehlgeschlagen',
                    403:'Keine Berechtigung',404:'Ressource nicht gefunden',
                    422:'Ungültige Such- oder Pfadparameter',429:'API-Anfragelimit erreicht'}.get(
                    response.status_code,'Serverfehler')
            # Never relay raw response body or headers; they could contain secrets.
            raise FeedError(f'eToro HTTP {response.status_code}: {reason}',response.status_code)
        try:
            return response.json()
        except ValueError:
            raise FeedError('eToro-Antwort ist kein gültiges JSON.') from None

    def search(self,symbol:str) -> list[dict]:
        query = symbol.strip()
        if not query or len(query)>60:
            raise FeedError('Bitte ein gültiges Symbol (bis 60 Zeichen) eingeben.')
        searches = [('/api/v2/market-data/instruments',{'symbols':query}),
                    ('/api/v1/market-data/search',{'internalSymbolFull':query,
                     'fields':'instrumentId,internalSymbolFull,displayname','pageNumber':1,'pageSize':20})]
        errors=[]
        for endpoint,params in searches:
            try:
                items=_instrument_records(self._get(endpoint,params))
            except FeedError as e:
                if e.status in (401,403,429,None):
                    raise
                errors.append(str(e))
                continue
            matches=[]
            for item in items:
                name=str(item.get('internalSymbolFull') or item.get('symbol') or '').strip()
                raw_id=item.get('instrumentId',item.get('instrumentID'))
                try:
                    ident=int(raw_id)
                except (ValueError,TypeError):
                    continue
                if name.casefold()==query.casefold() and ident>0:
                    matches.append({'instrumentId':ident,'internalSymbolFull':name,
                                    'displayname':str(item.get('displayname') or item.get('displayName') or name)})
            if matches:
                unique={m['instrumentId']:m for m in matches}
                return list(unique.values())
        raise FeedError('Kein eindeutiger, exakter Instrumenttreffer für '+query+'. '+
                        '; '.join(errors)[:200])

    def candles(self,instrument_id:int,tf:str,count:int=400) -> pd.DataFrame:
        if tf not in TF:
            raise FeedError('Unbekannte Zeitebene.')
        if not (1<=int(instrument_id)<=10**10 and 40<=int(count)<=1000):
            raise FeedError('Instrument-ID oder Kerzenanzahl ungültig.')
        interval,minutes=TF[tf]
        endpoint=(f'/api/v1/market-data/instruments/{int(instrument_id)}/history/'
                  f'candles/desc/{interval}/{int(count)}')
        d=parse_candles(self._get(endpoint),int(instrument_id),minutes)
        if len(d)<40:
            raise FeedError(f'Nur {len(d)} abgeschlossene Kerzen in {tf}; mindestens 40 benötigt.')
        return d
