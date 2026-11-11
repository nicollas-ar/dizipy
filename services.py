"""Cobranças, recorrências e eventos. Operações externas limitadas ao Sandbox."""
import calendar
import hashlib
import json
import re
import secrets
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote, urlsplit

from flask import current_app

import asaas
from storage import audit, db, now, setting, stamp

METHODS = {'pix':'Pix avulso', 'card':'Cartão avulso', 'card_monthly':'Cartão mensal', 'pix_monthly':'Pix Automático'}
BILLING = {'pix':'PIX', 'card':'CREDIT_CARD', 'card_monthly':'CREDIT_CARD', 'pix_monthly':'PIX'}
LABELS = {'pending':'Pendente','paid':'Pago','failed':'Não aprovado','cancelled':'Cancelado','refunded':'Estornado','disputed':'Em contestação','active':'Ativa','awaiting':'Aguardando autorização','expired':'Expirada','refused':'Recusada','demo':'Demonstração','APPROVED':'Verificada','PENDING':'Pendente','queued':'Na fila','simulated':'Simulada','sent':'Enviada','delivered':'Entregue','read':'Lida','unknown':'Conferir envio','ignored':'Ignorado','processed':'Processado'}


def money(cents):
    value = f'{Decimal(cents or 0)/100:,.2f}'.translate(str.maketrans('.,', ',.'))
    return 'R$ ' + value


def parse_amount(value):
    if not re.fullmatch(r'[0-9]{1,6}(?:[,.][0-9]{1,2})?', str(value)):
        return None
    amount = int(Decimal(str(value).replace(',','.')) * 100)
    return amount if amount > 0 else None


def fee(amount):
    return int((Decimal(amount) * Decimal('0.005')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def next_month(value, day):
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def api_key(parish_id):
    if current_app.config['HOSTED']:
        return setting('asaas_live_key', parish_id=parish_id)
    direct=setting('asaas_key', parish_id=parish_id)
    first=db().execute('SELECT MIN(id) FROM parish').fetchone()[0]
    return direct or (setting('asaas_key') if parish_id==first else '')


def api(parish_id, method, path, **kwargs):
    key = api_key(parish_id)
    if not key:
        raise asaas.AsaasError('Configure a conta recebedora do Asaas em Integrações.')
    if current_app.config['HOSTED']:
        return asaas.call(key, method, path, production=True, **kwargs)
    return asaas.call(key, method, path, **kwargs)


def reference(value):
    return 'dizipy-' + value


def provider_path(value):
    return quote(str(value), safe='')


def invoice_url(value):
    if not isinstance(value, str):
        return ''
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ''
    hosts = ('asaas.com', 'www.asaas.com') if current_app.config['HOSTED'] else ('sandbox.asaas.com',)
    return value if parsed.scheme == 'https' and parsed.netloc in hosts else ''


def public_url(path):
    base = (setting('webhook_url') or setting('public_url')).rstrip('/') if path.startswith('/webhooks/') else setting('public_url').rstrip('/')
    return base + path if base else path


def mode_for_new():
    return 'production' if current_app.config['HOSTED'] else setting('mode', 'demo')


def check_payment_environment(mode):
    if (mode == 'production') != current_app.config['HOSTED']:
        raise asaas.AsaasError('Esta cobrança pertence a outro ambiente do Asaas.')


def live_ready(parish):
    return (parish['cnpj_status']=='verified' and parish['live_account_status']=='APPROVED'
            and bool(parish['live_account_id']) and parish['live_account_cnpj']==parish['cnpj'])


def new_donation(parish_id, amount, method, member_id=None, recurring_id=None, period=None, mode=None):
    donation_id = secrets.token_urlsafe(18)
    mode = mode or mode_for_new()
    platform_fee = 0 if mode=='production' else fee(amount)
    db().execute('INSERT INTO donation(id,parish_id,amount,platform_fee,method,created_at,member_id,recurring_id,mode,period) VALUES(?,?,?,?,?,?,?,?,?,?)', (donation_id,parish_id,amount,platform_fee,method,stamp(),member_id,recurring_id,mode,period))
    return donation_id


def customer_for(donation):
    member = db().execute('SELECT * FROM member WHERE id=? AND parish_id=?', (donation['member_id'], donation['parish_id'])).fetchone()
    if member is None:
        legacy = current_app.config.get('ASAAS_CUSTOMER_ID','')
        if legacy:
            return legacy
        raise asaas.AsaasError('Cadastre os dados do contribuinte para criar a cobrança de teste.')
    if donation['mode']=='production':
        account = db().execute('SELECT live_account_id FROM parish WHERE id=?',(donation['parish_id'],)).fetchone()
        account_hash = hashlib.sha256(('production:'+account['live_account_id']).encode()).hexdigest()
    else:
        account_hash = hashlib.sha256(api_key(donation['parish_id']).encode()).hexdigest()
    saved = db().execute('SELECT provider_id FROM customer WHERE member_id=? AND account_hash=?', (member['id'],account_hash)).fetchone()
    if saved:
        return saved['provider_id']
    document = current_app.extensions['secrets'].decrypt(member['document'].encode()).decode() if member['document'] else ''
    if not document:
        raise asaas.AsaasError('Informe o CPF ou CNPJ do contribuinte.')
    ext = f'dizipy-member-{member["id"]}'
    result = api(donation['parish_id'], 'GET', '/customers', params={'externalReference':ext,'limit':2})
    items = result.get('data', [])
    if len(items)>1:
        raise asaas.AsaasError('Há mais de um cliente com esta referência. Confira no Asaas.')
    record = items[0] if items else api(donation['parish_id'],'POST','/customers',data={'name':member['name'],'cpfCnpj':document,'externalReference':ext,'notificationDisabled':True})
    customer_id = record.get('id')
    if not isinstance(customer_id,str) or not customer_id:
        raise asaas.AsaasError('O Asaas não retornou o identificador do cliente.')
    db().execute('INSERT INTO customer VALUES(?,?,?) ON CONFLICT(member_id,account_hash) DO UPDATE SET provider_id=excluded.provider_id',(member['id'],account_hash,customer_id))
    return customer_id


def split_for(amount):
    wallet = setting('platform_live_wallet' if current_app.config['HOSTED'] else 'platform_wallet')
    return [{'walletId':wallet,'fixedValue':fee(amount)/100,'externalReference':'dizipy-fee'}] if wallet and fee(amount)>0 else []


def list_records(parish_id, path, params):
    offset = 0
    while True:
        result = api(parish_id,'GET',path,params={**params,'limit':100,'offset':offset})
        records = result.get('data')
        if not isinstance(records,list):
            raise asaas.AsaasError('Resposta de listagem inválida do Asaas.')
        yield from records
        if not result.get('hasMore'):
            break
        if not records:
            raise asaas.AsaasError('O Asaas devolveu uma página vazia incompleta.')
        offset += len(records)
        if offset > 10000:
            raise asaas.AsaasError('Listagem extensa demais para a conciliação local.')


def ensure_charge(donation_id):
    """Uma tentativa por vez; referências estáveis recuperam criações com resposta perdida."""
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT d.*,p.account_status,p.live_account_status,p.live_account_id,p.live_account_cnpj,p.cnpj,p.cnpj_status,a.payment_id FROM donation d JOIN parish p ON p.id=d.parish_id LEFT JOIN asaas_payment a ON a.donation_id=d.id WHERE d.id=?',(donation_id,)).fetchone()
        if row is None:
            raise asaas.AsaasError('Contribuição não encontrada.')
        if row['mode']=='demo' or row['status'] in ('paid','cancelled','refunded'):
            return
        check_payment_environment(row['mode'])
        if row['mode']=='production' and not live_ready(row):
            raise asaas.AsaasError('A igreja precisa de uma conta Asaas aprovada e vinculada ao CNPJ conferido.')
        if row['payment_id']:
            return
        if row['mode']=='sandbox' and row['account_status'] != 'APPROVED':
            raise asaas.AsaasError('Verifique a conta recebedora em Integrações antes de gerar cobranças no Sandbox.')
        customer_id = row['customer_id'] or customer_for(row)
        connection.execute('UPDATE donation SET customer_id=? WHERE id=?',(customer_id,donation_id))
        if row['recurring_id']:
            recurring = connection.execute('SELECT * FROM recurring WHERE id=?',(row['recurring_id'],)).fetchone()
            ensure_recurring(recurring, row, customer_id)
            return
        records = list(list_records(row['parish_id'],'/payments',{'externalReference':reference(row['id']),'customer':customer_id}))
        if len(records)>1:
            raise asaas.AsaasError('Há cobranças duplicadas no Asaas. Confira a referência antes de prosseguir.')
        body = {'customer':customer_id,'billingType':BILLING[row['method']],'value':row['amount']/100,'dueDate':now().date().isoformat(),'externalReference':reference(row['id']),'description':'Contribuição Dizipy'}
        split = split_for(row['amount'])
        if split:
            body['split']=split
        payment = records[0] if records else api(row['parish_id'],'POST','/payments',data=body)
        bind_payment(row,payment)
        connection.execute('UPDATE donation SET split_applied=? WHERE id=?',(int(bool(payment.get('split'))),row['id']))


def ensure_recurring(recurring, donation, customer_id):
    parish_id = donation['parish_id']
    if recurring['status']=='cancelled':
        raise asaas.AsaasError('Esta recorrência foi cancelada.')
    if recurring['method']=='card_monthly':
        matches = list(list_records(parish_id,'/subscriptions',{'externalReference':reference(recurring['id']),'customer':customer_id}))
        if len(matches)>1:
            raise asaas.AsaasError('Assinaturas duplicadas no Asaas. Confira a referência.')
        if recurring['provider_id']:
            sub_id = recurring['provider_id']
        else:
            body = {'customer':customer_id,'billingType':'CREDIT_CARD','value':recurring['amount']/100,'nextDueDate':now().date().isoformat(),'cycle':'MONTHLY','description':'Contribuição mensal Dizipy','externalReference':reference(recurring['id'])}
            split = split_for(recurring['amount'])
            if split:
                body['split']=split
            result = matches[0] if matches else api(parish_id,'POST','/subscriptions',data=body)
            sub_id = result.get('id')
            if not isinstance(sub_id,str) or not sub_id:
                raise asaas.AsaasError('O Asaas não retornou o identificador da assinatura.')
            db().execute('UPDATE recurring SET provider_id=?,provider_subscription=?,customer_id=? WHERE id=?',(sub_id,sub_id,customer_id,recurring['id']))
        payments = list(list_records(parish_id,'/payments',{'subscription':sub_id}))
        if not payments:
            raise asaas.AsaasError('A assinatura foi criada. Aguarde alguns instantes e consulte novamente para abrir a primeira fatura.')
        payments.sort(key=lambda item:(item.get('dueDate',''),item.get('id','')))
        bind_payment(donation,payments[0],recurring_reference=True)
    else:
        if recurring['provider_id']:
            result = api(parish_id,'GET','/pix/automatic/authorizations/'+provider_path(recurring['provider_id']))
        else:
            candidates = list(list_records(parish_id,'/pix/automatic/authorizations',{'customerId':customer_id}))
            matches = [item for item in candidates if item.get('contractId')==reference(recurring['id'])]
            if len(matches)>1:
                raise asaas.AsaasError('Autorizações duplicadas no Asaas. Confira o contrato.')
            body = {'customerId':customer_id,'frequency':'MONTHLY','contractId':reference(recurring['id']),'startDate':now().date().isoformat(),'value':recurring['amount']/100,'description':'Contribuição mensal Dizipy','paymentCreationMode':'SUBSCRIPTION','retryPolicy':'NOT_ALLOWED','immediateQrCode':{'expirationSeconds':3600,'originalValue':donation['amount']/100,'description':'Primeira contribuição Dizipy'}}
            pix_key = setting('pix_key',parish_id=parish_id) if not current_app.config['HOSTED'] else ''
            if pix_key:
                body['immediateQrCode']['pixKey']=pix_key
            result = matches[0] if matches else api(parish_id,'POST','/pix/automatic/authorizations',data=body)
            if not isinstance(result.get('id'),str) or not result['id']:
                raise asaas.AsaasError('O Asaas não retornou a autorização Pix.')
            db().execute('UPDATE recurring SET provider_id=?,customer_id=? WHERE id=?',(result['id'],customer_id,recurring['id']))
        update_authorization(recurring, result)
        db().execute('UPDATE donation SET qr_payload=? WHERE id=?',(result.get('payload') or '',donation['id']))


def bind_payment(donation, payment, recurring_reference=False):
    payment_id = payment.get('id')
    value = payment.get('value')
    if not isinstance(payment_id,str) or not payment_id or type(value) not in (float,int) or not Decimal(str(value)).is_finite() or Decimal(str(value))*100 != donation['amount'] or payment.get('billingType') != BILLING[donation['method']]:
        raise asaas.AsaasError('Valor ou forma de pagamento divergente da contribuição.')
    if not recurring_reference and payment.get('externalReference') != reference(donation['id']):
        raise asaas.AsaasError('Referência da cobrança divergente da contribuição.')
    existing = db().execute('SELECT payment_id FROM asaas_payment WHERE donation_id=?',(donation['id'],)).fetchone()
    if existing and existing['payment_id'] and existing['payment_id']!=payment_id:
        raise asaas.AsaasError('Esta contribuição já está associada a outra cobrança.')
    db().execute('INSERT INTO asaas_payment(donation_id,payment_id) VALUES(?,?) ON CONFLICT(donation_id) DO UPDATE SET payment_id=excluded.payment_id',(donation['id'],payment_id))
    db().execute('UPDATE donation SET invoice_url=?,customer_id=COALESCE(customer_id,?) WHERE id=?',(invoice_url(payment.get('invoiceUrl')),payment.get('customer'),donation['id']))


def update_payment(donation, payment):
    value = payment.get('value')
    expected = db().execute('SELECT payment_id FROM asaas_payment WHERE donation_id=?',(donation['id'],)).fetchone()
    if not expected or expected['payment_id']!=payment.get('id') or type(value) not in (int,float) or not Decimal(str(value)).is_finite() or Decimal(str(value))*100!=donation['amount'] or payment.get('billingType')!=BILLING[donation['method']]:
        raise asaas.AsaasError('O evento não corresponde ao valor ou à forma de pagamento registrados.')
    if donation['mode']=='production' and isinstance(payment.get('split'),list):
        applied=0
        for part in payment['split']:
            if isinstance(part,dict) and part.get('externalReference')=='dizipy-fee':
                fixed=part.get('fixedValue')
                if type(fixed) in (float,int) and Decimal(str(fixed)).is_finite() and 0<fixed<=donation['amount']/100:
                    applied+=int(Decimal(str(fixed))*100)
        db().execute('UPDATE donation SET platform_fee=?,split_applied=? WHERE id=?',
                     (min(applied,donation['amount']),int(bool(applied)),donation['id']))
    state = payment.get('status')
    if state=='RECEIVED' or (state=='CONFIRMED' and donation['method'] in ('card','card_monthly')):
        if donation['status'] in ('refunded','disputed'):
            return
        db().execute("UPDATE donation SET status='paid',paid_at=COALESCE(paid_at,?),settled_at=CASE WHEN ?='RECEIVED' THEN COALESCE(settled_at,?) ELSE settled_at END,remote_state=? WHERE id=?",(stamp(),state,stamp(),state,donation['id']))
        if donation['recurring_id'] and donation['method']=='card_monthly':
            db().execute("UPDATE recurring SET status='active' WHERE id=? AND cancelled_at IS NULL",(donation['recurring_id'],))
    elif state=='REFUNDED':
        db().execute("UPDATE donation SET status='refunded',refunded_at=?,remote_state=? WHERE id=?",(stamp(),state,donation['id']))
    elif state in ('CHARGEBACK_REQUESTED','CHARGEBACK_DISPUTE','AWAITING_CHARGEBACK_REVERSAL'):
        db().execute("UPDATE donation SET status='disputed',remote_state=? WHERE id=?",(state,donation['id']))
    elif payment.get('deleted') or state=='DELETED':
        db().execute("UPDATE donation SET status='cancelled',remote_state=? WHERE id=? AND paid_at IS NULL",(state,donation['id']))
    net = payment.get('netValue')
    if type(net) in (int,float) and Decimal(str(net)).is_finite():
        provider_fee = donation['amount']-int(Decimal(str(net))*100)
        db().execute('UPDATE donation SET provider_fee=? WHERE id=?',(provider_fee,donation['id']))


def update_authorization(recurring, record):
    statuses={'ACTIVE':'active','CREATED':'awaiting','CANCELLED':'cancelled','REFUSED':'refused','EXPIRED':'expired'}
    status=statuses.get(record.get('status'))
    if not status:
        return
    if recurring['cancelled_at'] and status!='cancelled':
        return
    db().execute('UPDATE recurring SET status=?,provider_subscription=COALESCE(?,provider_subscription),cancelled_at=CASE WHEN ?="cancelled" THEN COALESCE(cancelled_at,?) ELSE cancelled_at END WHERE id=?',(status,record.get('subscriptionId'),status,stamp(),recurring['id']))


def refresh_charge(donation_id):
    donation=db().execute('SELECT d.*,a.payment_id FROM donation d LEFT JOIN asaas_payment a ON a.donation_id=d.id WHERE d.id=?',(donation_id,)).fetchone()
    if donation['mode']=='demo':
        return
    check_payment_environment(donation['mode'])
    if not donation['payment_id']:
        ensure_charge(donation_id)
        return
    payment=api(donation['parish_id'],'GET','/payments/'+provider_path(donation['payment_id']))
    with db():
        update_payment(donation,payment)
        db().execute('UPDATE donation SET invoice_url=? WHERE id=?',(invoice_url(payment.get('invoiceUrl')),donation_id))
        if donation['method']=='pix' and donation['status']=='pending' and payment.get('status') in ('PENDING','OVERDUE'):
            qr=api(donation['parish_id'],'GET','/payments/'+provider_path(donation['payment_id'])+'/pixQrCode')
            db().execute('UPDATE donation SET qr_payload=? WHERE id=?',(qr.get('payload',''),donation_id))


def simulate(donation_id, action):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        donation=connection.execute('SELECT * FROM donation WHERE id=?',(donation_id,)).fetchone()
        if donation['mode']!='demo':
            raise ValueError('A simulação só pode alterar contribuições de demonstração.')
        if action=='pay' and donation['status'] in ('pending','failed'):
            connection.execute("UPDATE donation SET status='paid',paid_at=COALESCE(paid_at,?),settled_at=? WHERE id=?",(stamp(),stamp(),donation_id))
            if donation['recurring_id']:
                connection.execute("UPDATE recurring SET status='active' WHERE id=? AND cancelled_at IS NULL",(donation['recurring_id'],))
        elif action=='fail' and donation['status']=='pending':
            connection.execute("UPDATE donation SET status='failed' WHERE id=?",(donation_id,))
        elif action=='refund' and donation['status']=='paid':
            connection.execute("UPDATE donation SET status='refunded',refunded_at=? WHERE id=?",(stamp(),donation_id))
        audit(donation['parish_id'],'demo_payment',action)


def cancel_recurring(recurring):
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        current=connection.execute('SELECT * FROM recurring WHERE id=?',(recurring['id'],)).fetchone()
        if current['status']=='cancelled':
            return
        if current['mode'] in ('sandbox','production') and current['provider_id']:
            check_payment_environment(current['mode'])
            prefix='/subscriptions/' if current['method']=='card_monthly' else '/pix/automatic/authorizations/'
            path=prefix+provider_path(current['provider_id'])
            remote=api(current['parish_id'],'GET',path)
            if not remote.get('deleted') and remote.get('status')!='CANCELLED':
                api(current['parish_id'],'DELETE',path)
        connection.execute("UPDATE recurring SET status='cancelled',cancelled_at=? WHERE id=?",(stamp(),current['id']))
        connection.execute("UPDATE donation SET status='cancelled' WHERE recurring_id=? AND status IN ('pending','failed')",(current['id'],))
        audit(current['parish_id'],'recurring_cancelled',current['id'])


def process_event(event):
    payload=json.loads(event['payload'])
    parish_id=event['parish_id']
    environment=payload.get('_environment','sandbox')
    if (environment=='production') != current_app.config['HOSTED']:
        return
    kind=payload['event']
    payment=payload.get('payment')
    if kind.startswith('ACCOUNT_STATUS_'):
        account=api(parish_id,'GET','/myAccount/status/')
        status=account.get('general')
        if status:
            if environment=='production':
                db().execute('UPDATE parish SET live_account_status=? WHERE id=? AND live_account_id=?',(status,parish_id,account.get('id')))
            else:
                db().execute('UPDATE parish SET account_status=? WHERE id=?',(status,parish_id))
        return
    if kind.startswith('PIX_AUTOMATIC_RECURRING_AUTHORIZATION_'):
        record=payload.get('authorization',{})
        recurring=db().execute('SELECT * FROM recurring WHERE parish_id=? AND provider_id=? AND mode=?',(parish_id,record.get('id'),environment)).fetchone()
        if recurring:
            # Consulta o estado atual para impedir reativação por um evento atrasado.
            current=api(parish_id,'GET','/pix/automatic/authorizations/'+provider_path(record['id']))
            update_authorization(recurring,current)
        return
    if kind.startswith('SUBSCRIPTION_'):
        record=payload.get('subscription',{})
        recurring=db().execute('SELECT * FROM recurring WHERE parish_id=? AND provider_subscription=? AND mode=?',(parish_id,record.get('id'),environment)).fetchone()
        if recurring and (kind=='SUBSCRIPTION_DELETED' or record.get('deleted')):
            db().execute("UPDATE recurring SET status='cancelled',cancelled_at=COALESCE(cancelled_at,?) WHERE id=?",(stamp(),recurring['id']))
        return
    if not isinstance(payment,dict) or not isinstance(payment.get('id'),str):
        return
    # Reconsulta na conta autenticada, conciliando eventos fora de ordem e estornos.
    payment=api(parish_id,'GET','/payments/'+provider_path(payment['id']))
    donation=db().execute('SELECT d.* FROM donation d JOIN asaas_payment a ON a.donation_id=d.id WHERE a.payment_id=? AND d.parish_id=? AND d.mode=?',(payment['id'],parish_id,environment)).fetchone()
    if donation is None:
        recurring=db().execute('SELECT * FROM recurring WHERE parish_id=? AND provider_subscription=? AND mode=?',(parish_id,payment.get('subscription'),environment)).fetchone()
        if recurring is None:
            ext=payment.get('externalReference','')
            if isinstance(ext,str) and ext.startswith('dizipy-'):
                donation=db().execute('SELECT * FROM donation WHERE id=? AND parish_id=? AND mode=?',(ext[7:],parish_id,environment)).fetchone()
        else:
            period=payment.get('dueDate')
            if not isinstance(period,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',period):
                raise ValueError('Cobrança recorrente sem vencimento válido.')
            existing=db().execute('SELECT * FROM donation WHERE recurring_id=? AND period=?',(recurring['id'],period)).fetchone()
            if existing:
                donation=existing
            else:
                initial=db().execute('SELECT * FROM donation WHERE recurring_id=? AND id NOT IN (SELECT donation_id FROM asaas_payment) ORDER BY created_at LIMIT 1',(recurring['id'],)).fetchone()
                if initial:
                    db().execute('UPDATE donation SET period=? WHERE id=?',(period,initial['id']))
                    donation=initial
                else:
                    donation_id=new_donation(parish_id,recurring['amount'],recurring['method'],recurring['member_id'],recurring['id'],period,environment)
                    donation=db().execute('SELECT * FROM donation WHERE id=?',(donation_id,)).fetchone()
        if donation:
            bind_payment(donation,payment,recurring_reference=bool(donation['recurring_id']))
    if donation:
        update_payment(donation,payment)
    elif db().execute("SELECT 1 FROM recurring WHERE parish_id=? AND customer_id=? AND method='pix_monthly' AND mode=?",(parish_id,payment.get('customer'),environment)).fetchone():
        raise ValueError('Pix Automático recebido sem vínculo de assinatura. Confira a autorização e reprocesse este evento em Atividade.')


def work_events():
    events=db().execute("SELECT * FROM provider_event WHERE state IN ('pending','failed') AND attempts<5 ORDER BY created_at LIMIT 20").fetchall()
    for event in events:
        try:
            with db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                current=connection.execute('SELECT * FROM provider_event WHERE id=?',(event['id'],)).fetchone()
                if current['state']=='processed':
                    continue
                process_event(current)
                connection.execute("UPDATE provider_event SET state='processed',processed_at=?,error='',attempts=attempts+1 WHERE id=?",(stamp(),event['id']))
        except (asaas.AsaasError,ValueError) as error:
            with db():
                db().execute("UPDATE provider_event SET state='failed',attempts=attempts+1,error=? WHERE id=?",(str(error)[:300],event['id']))


def demo_cycles(today=None,parish_id=None):
    today=today or now().date()
    with db() as connection:
        connection.execute('BEGIN IMMEDIATE')
        recurring=connection.execute("SELECT * FROM recurring WHERE mode='demo' AND status='active' AND next_due<=? AND (? IS NULL OR parish_id=?)",(today.isoformat(),parish_id,parish_id)).fetchall()
        for item in recurring:
            due=date.fromisoformat(item['next_due'])
            cycles=0
            while due<=today and cycles<24:
                period=due.isoformat()
                if not connection.execute('SELECT 1 FROM donation WHERE recurring_id=? AND period=?',(item['id'],period)).fetchone():
                    new_donation(item['parish_id'],item['amount'],item['method'],item['member_id'],item['id'],period,'demo')
                due=next_month(due,item['day'])
                cycles+=1
            connection.execute('UPDATE recurring SET next_due=? WHERE id=?',(due.isoformat(),item['id']))
