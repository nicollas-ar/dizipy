from flask import current_app
"""Agenda mensal e envio pelo número empresarial central do Dizipy."""
import calendar
import hashlib
import hmac
import re
import unicodedata

import requests
from storage import db, now, setting, stamp
from services import public_url, live_ready


def normalize_phone(value):
    value=re.sub(r'\D','',str(value))
    if len(value) in (10,11):
        value='55'+value
    return value if re.fullmatch(r'55\d{10,11}',value) else ''


def reminder_text(parish,member):
    link=public_url('/contribuir/'+parish['public_id']+'?pessoa='+member['manage_token'])
    return parish['reminder_body'].replace('{nome}',member['name']).replace('{paroquia}',parish['name']).replace('{link}',link)


def enqueue_reminders(today=None,force_parish=None):
    current=today or now()
    period=current.strftime('%Y-%m')
    created=0
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        parishes=connection.execute('SELECT * FROM parish WHERE reminder_enabled=1').fetchall()
        for parish in parishes:
            day=min(parish['reminder_day'],calendar.monthrange(current.year,current.month)[1])
            due=current.replace(day=day,hour=int(parish['reminder_time'][:2]),minute=int(parish['reminder_time'][3:]),second=0,microsecond=0)
            if force_parish!=parish['id'] and current<due:
                continue
            if force_parish is not None and force_parish!=parish['id']:
                continue
            members=connection.execute('SELECT * FROM member WHERE parish_id=? AND active=1 AND consent_at IS NOT NULL AND opted_out_at IS NULL AND phone!=""',(parish['id'],)).fetchall()
            for member in members:
                mode=setting('whatsapp_mode','demo')
                if mode=='cloud':
                    ready=live_ready(parish) if current_app.config['HOSTED'] else parish['account_status']=='APPROVED'
                    if not member['phone_verified_at'] or not ready:
                        continue
                result=connection.execute('INSERT OR IGNORE INTO message(parish_id,member_id,period,body,phone,mode,created_at) VALUES(?,?,?,?,?,?,?)',(parish['id'],member['id'],period,reminder_text(parish,member),member['phone'],mode,stamp()))
                created+=result.rowcount
    return created


def send_queue():
    records=db().execute("SELECT id FROM message WHERE status='queued' ORDER BY id LIMIT 20").fetchall()
    for record in records:
        with db() as connection:
            connection.execute('BEGIN IMMEDIATE')
            item=connection.execute('SELECT * FROM message WHERE id=?',(record['id'],)).fetchone()
            if item['status']!='queued':
                continue
            member=connection.execute('SELECT * FROM member WHERE id=?',(item['member_id'],)).fetchone()
            if not member['active'] or member['opted_out_at'] or not member['consent_at']:
                connection.execute("UPDATE message SET status='cancelled' WHERE id=?",(item['id'],))
                continue
            if item['mode']=='demo':
                connection.execute("UPDATE message SET status='simulated',sent_at=? WHERE id=?",(stamp(),item['id']))
                continue
            if not member['phone_verified_at']:
                connection.execute("UPDATE message SET status='cancelled',error='Telefone sem confirmação de adesão' WHERE id=?",(item['id'],))
                continue
            connection.execute("UPDATE message SET status='sending' WHERE id=?",(item['id'],))
        # Uma resposta inconclusiva não é reenviada automaticamente, evitando mensagem duplicada.
        try:
            provider_id=send_template(item,member)
        except (ValueError,requests.RequestException) as error:
            with db():
                db().execute("UPDATE message SET status='unknown',error=? WHERE id=?",(str(error)[:240],item['id']))
        else:
            with db():
                db().execute("UPDATE message SET status='sent',provider_id=?,sent_at=? WHERE id=?",(provider_id,stamp(),item['id']))


def send_template(item,member):
    token=setting('whatsapp_token')
    phone_id=setting('whatsapp_phone_id')
    version=setting('whatsapp_version','v23.0')
    template=setting('whatsapp_template')
    if not token or not re.fullmatch(r'\d+',phone_id) or not re.fullmatch(r'v\d+\.\d+',version) or not re.fullmatch(r'[a-z0-9_]+',template):
        raise ValueError('Complete a integração central de WhatsApp na administração da plataforma.')
    if not setting('public_url').startswith('https://'):
        raise ValueError('O WhatsApp exige o endereço público HTTPS do Dizipy configurado.')
    parish=db().execute('SELECT * FROM parish WHERE id=?',(item['parish_id'],)).fetchone()
    link=public_url('/contribuir/'+parish['public_id']+'?pessoa='+member['manage_token'])
    snippet=parish['reminder_body'].replace('{nome}',member['name']).replace('{paroquia}',parish['name']).replace('{link}',link)
    parameters=[{'type':'text','text':text} for text in (member['name'],parish['name'],snippet,link)]
    body={'messaging_product':'whatsapp','to':item['phone'],'type':'template','template':{'name':template,'language':{'code':setting('whatsapp_language','pt_BR')},'components':[{'type':'body','parameters':parameters}]}}
    response=requests.post(f'https://graph.facebook.com/{version}/{phone_id}/messages',json=body,headers={'Authorization':'Bearer '+token},timeout=(4,15),allow_redirects=False)
    if response.status_code not in (200,201):
        raise ValueError(f'Meta respondeu HTTP {response.status_code}. Confira os registros da conta empresarial.')
    try:
        provider_id=response.json()['messages'][0]['id']
    except (ValueError,KeyError,IndexError,TypeError):
        raise ValueError('A Meta não retornou um identificador de mensagem.') from None
    return provider_id


def valid_signature(raw,signature):
    secret=setting('whatsapp_app_secret')
    if not secret:
        return False
    expected='sha256='+hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,signature)


def process_inbound(payload):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        for entry in payload.get('entry',[]):
            for change in entry.get('changes',[]):
                value=change.get('value',{})
                for status in value.get('statuses',[]):
                    remote_id=status.get('id')
                    remote_status=status.get('status')
                    if remote_status in ('sent','delivered','read','failed'):
                        current=connection.execute('SELECT * FROM message WHERE provider_id=?',(remote_id,)).fetchone()
                        ranks={'sending':0,'unknown':0,'sent':1,'delivered':2,'read':3,'failed':4}
                        if current and ranks.get(remote_status,0)>=ranks.get(current['status'],0):
                            connection.execute('UPDATE message SET status=? WHERE id=?',(remote_status,current['id']))
                for message in value.get('messages',[]):
                    message_id=message.get('id')
                    if not isinstance(message_id,str) or connection.execute('SELECT 1 FROM inbound_message WHERE id=?',(message_id,)).fetchone():
                        continue
                    phone=normalize_phone(message.get('from',''))
                    text=message.get('text',{}).get('body','').strip()
                    folded=unicodedata.normalize('NFKD',text).encode('ascii','ignore').decode().upper()
                    if folded in ('SAIR','PARAR','CANCELAR LEMBRETES') and phone:
                        connection.execute('UPDATE member SET opted_out_at=? WHERE phone=?',(stamp(),phone))
                        connection.execute("UPDATE message SET status='cancelled' WHERE phone=? AND status='queued'",(phone,))
                    if folded.startswith('ATIVAR ') and phone:
                        token=text.split(' ',1)[1].strip()
                        connection.execute('UPDATE member SET phone_verified_at=?,consent_at=COALESCE(consent_at,?),opted_out_at=NULL WHERE manage_token=? AND phone=?',(stamp(),stamp(),token,phone))
                    connection.execute('INSERT INTO inbound_message VALUES(?,?)',(message_id,stamp()))
