import hashlib
import hmac
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import create_app
import messaging
import services
from storage import db, now, put_setting, secret_box, setting, stamp


class Workflows(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.path=Path(self.temporary.name)/'dizipy.sqlite'
        self.app=create_app({'TESTING':True,'DATABASE':self.path})
        self.owner=self.register('owner@example.com')
        with self.app.app_context():
            self.parish=dict(db().execute('SELECT * FROM parish').fetchone())
        self.public='/contribuir/'+self.parish['public_id']

    def tearDown(self):
        self.temporary.cleanup()

    def post(self,client,path,**data):
        with client.session_transaction() as state:
            csrf=state['csrf']
        return client.post(path,data={'csrf':csrf,**data})

    def register(self,email):
        client=self.app.test_client()
        client.get('/cadastro')
        response=self.post(client,'/cadastro',name='Paróquia '+email,email=email,password='test-password-123')
        self.assertEqual(response.status_code,302)
        client.get('/painel')
        return client

    def contribute(self,method,phone='11987654321',name='Maria',**extra):
        client=self.app.test_client()
        self.assertEqual(client.get(self.public).status_code,200)
        response=self.post(client,self.public,name=name,phone=phone,email='',amount='12,00',method=method,recurring_consent='on',consent='on',**extra)
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        return client,response.location,response.location.rsplit('/',1)[-1]

    def test_all_demo_methods_recurring_cancel_reports_and_sessions(self):
        clients=[]
        for index,method in enumerate(services.METHODS):
            client,path,donation_id=self.contribute(method,phone='1198765432'+str(index))
            self.assertEqual(client.get(path).status_code,200)
            self.post(client,path,action='pay')
            self.assertIn('Contribuição confirmada',client.get(path).get_data(as_text=True))
            self.post(client,path,action='pay')
            clients.append((client,path,donation_id))
        self.assertIn('R$ 48,00',self.owner.get('/painel').get_data(as_text=True))
        self.assertEqual(len(self.owner.get('/painel/exportar').get_data(as_text=True).splitlines()),5)
        for path in ('/fieis','/lembretes','/colaboradores','/integracoes','/plataforma','/atividade','/recorrencias'):
            self.assertEqual(self.owner.get(path).status_code,200,path)
        with self.app.app_context():
            recurring=db().execute('SELECT * FROM recurring ORDER BY created_at').fetchall()
            services.demo_cycles(services.next_month(now().date(),now().day),parish_id=self.parish['id'])
            services.demo_cycles(services.next_month(now().date(),now().day),parish_id=self.parish['id'])
            self.assertEqual(db().execute('SELECT COUNT(*) FROM donation').fetchone()[0],6)
            member=db().execute('SELECT * FROM member WHERE id=?',(recurring[0]['member_id'],)).fetchone()
            manage='/pessoa/'+member['manage_token']
            recurring_id=recurring[0]['id']
        client=clients[2][0]
        client.get(manage)
        self.post(client,manage,action='cancel',recurring_id=recurring_id)
        with self.app.app_context():
            self.assertEqual(db().execute('SELECT status FROM recurring WHERE id=?',(recurring_id,)).fetchone()[0],'cancelled')
        restarted=create_app({'TESTING':True,'DATABASE':self.path})
        retained=restarted.test_client()
        retained.set_cookie('session',self.owner.get_cookie('session').value)
        self.assertEqual(retained.get('/painel').status_code,200)
        self.assertEqual(self.owner.get('/plataforma/backup').status_code,302 if self.app.config.get('DATABASE_URL') else 200)

    def test_staff_permissions_tenant_isolation_and_csrf(self):
        self.owner.get('/colaboradores')
        self.post(self.owner,'/colaboradores',name='Secretaria',email='staff@example.com',password='test-password-123',permissions=['people'])
        staff=self.app.test_client()
        staff.get('/')
        self.post(staff,'/entrar',email='staff@example.com',password='test-password-123')
        self.assertEqual(staff.get('/fieis').status_code,200)
        self.assertEqual(staff.get('/painel/exportar').status_code,403)
        self.assertEqual(staff.get('/colaboradores').status_code,403)
        self.assertEqual(staff.get('/plataforma').status_code,403)
        self.assertEqual(staff.post('/fieis',data={}).status_code,400)
        donor,path,_=self.contribute('pix')
        self.assertEqual(staff.get(path).status_code,404)
        other=self.register('other@example.com')
        self.assertEqual(other.get(path).status_code,404)
        self.assertEqual(other.get('/plataforma/backup').status_code,403)
        with self.app.app_context():
            staff_id=db().execute('SELECT id FROM app_user WHERE email="staff@example.com"').fetchone()[0]
        self.post(self.owner,f'/colaboradores/{staff_id}',permissions=[])
        self.assertEqual(staff.get('/fieis').status_code,302)

    def test_messages_one_per_month_optout_and_inbound_signature(self):
        donor,path,_=self.contribute('pix')
        self.owner.get('/lembretes')
        self.post(self.owner,'/lembretes',day='31',time='09:00',body='Olá, {nome}, a {paroquia} agradece. {link}',enabled='on',methods=list(services.METHODS))
        with self.app.app_context():
            current=datetime(2026,2,28,10,0,tzinfo=now().tzinfo)
            self.assertEqual(messaging.enqueue_reminders(current),1)
            self.assertEqual(messaging.enqueue_reminders(current),0)
            messaging.send_queue()
            self.assertEqual(db().execute('SELECT status FROM message').fetchone()[0],'simulated')
            with db():
                put_setting('whatsapp_app_secret','test-secret',secret=True)
            body={'entry':[{'changes':[{'value':{'messages':[{'id':'msg_stop','from':'5511987654321','text':{'body':'SAIR'}}]}}]}]}
            raw=json.dumps(body).encode()
            signature='sha256='+hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
            self.assertTrue(messaging.valid_signature(raw,signature))
            self.assertFalse(messaging.valid_signature(raw,'bad'))
            messaging.process_inbound(body)
            messaging.process_inbound(body)
            self.assertEqual(messaging.enqueue_reminders(datetime(2026,3,31,10,0,tzinfo=now().tzinfo)),0)
            self.assertEqual(db().execute('SELECT COUNT(*) FROM inbound_message').fetchone()[0],1)

    def sandbox(self):
        with self.app.app_context(),db():
            put_setting('mode','sandbox')
            put_setting('asaas_key','dummy-key',self.parish['id'],secret=True)
            db().execute('UPDATE parish SET account_status="APPROVED" WHERE id=?',(self.parish['id'],))

    def test_card_and_pix_sandbox_persisted_webhook_queue(self):
        self.sandbox()
        for index,method in enumerate(('pix','card')):
            donor,path,donation_id=self.contribute(method,phone='1198765432'+str(index),document='24971563792')
            payment={'id':'pay_'+method,'billingType':services.BILLING[method],'value':12,'customer':'cus_test','externalReference':services.reference(donation_id),'status':'PENDING','invoiceUrl':'https://sandbox.asaas.com/i/test'}
            replies=[{'data':[]},{'id':'cus_test'},{'data':[]},payment]
            with patch('asaas.call',side_effect=replies) as api:
                self.assertEqual(self.post(donor,path,action='create').status_code,302)
                self.assertEqual(api.call_args.kwargs['data']['billingType'],services.BILLING[method])
            with self.app.app_context():
                token=setting('asaas_webhook_token',parish_id=self.parish['id'])
            hook='/webhooks/asaas/'+self.parish['public_id']
            event={'id':'evt_'+method,'event':'PAYMENT_RECEIVED','payment':{'id':payment['id']}}
            self.assertEqual(self.app.test_client().post(hook,json=event).status_code,401)
            for _ in range(2):
                self.assertEqual(self.app.test_client().post(hook,json=event,headers={'asaas-access-token':token}).status_code,200)
            with self.app.app_context(),patch('asaas.call',return_value={**payment,'status':'RECEIVED'}):
                services.work_events()
                self.assertEqual(db().execute('SELECT state FROM provider_event WHERE id=?',(event['id'],)).fetchone()[0],'processed')
                self.assertEqual(db().execute('SELECT status FROM donation WHERE id=?',(donation_id,)).fetchone()[0],'paid')
            self.assertIn('Contribuição confirmada',donor.get(path).get_data(as_text=True))
        self.assertIn('R$ 24,00',self.owner.get('/painel').get_data(as_text=True))

    def test_monthly_sandbox_contracts_and_cancel(self):
        self.sandbox()
        for index,method in enumerate(('card_monthly','pix_monthly')):
            donor,path,donation_id=self.contribute(method,phone='1198765432'+str(index),document='24971563792')
            with self.app.app_context():
                recurring_id=db().execute('SELECT recurring_id FROM donation WHERE id=?',(donation_id,)).fetchone()[0]
            if method=='card_monthly':
                payment={'id':'pay_monthly','billingType':'CREDIT_CARD','value':12,'customer':'cus_test','subscription':'sub_test','status':'PENDING','invoiceUrl':'https://sandbox.asaas.com/i/test'}
                replies=[{'data':[]},{'id':'cus_test'},{'data':[]},{'id':'sub_test'},{'data':[payment]}]
            else:
                authorization={'id':'auth_test','contractId':services.reference(recurring_id),'customerId':'cus_test','status':'CREATED','payload':'pix-test','subscriptionId':'sub_pix'}
                replies=[{'data':[]},{'id':'cus_test'},{'data':[]},authorization]
            with patch('asaas.call',side_effect=replies) as api:
                self.assertEqual(self.post(donor,path,action='create').status_code,302)
                posts=[call for call in api.call_args_list if call.args[1]=='POST']
                body=posts[-1].kwargs['data']
                self.assertEqual(body['value'],12)
                self.assertEqual(body.get('cycle') or body.get('frequency'),'MONTHLY')
            with self.app.app_context():
                item=db().execute('SELECT * FROM recurring WHERE id=?',(recurring_id,)).fetchone()
                self.assertIsNotNone(item['provider_id'])
                with patch('asaas.call',return_value={'deleted':True}):
                    services.cancel_recurring(item)
                self.assertEqual(db().execute('SELECT status FROM recurring WHERE id=?',(recurring_id,)).fetchone()[0],'cancelled')

    def test_migrate_previous_parish_password_and_pix(self):
        separate=Path(self.temporary.name)/'previous.sqlite'
        connection=sqlite3.connect(separate)
        connection.executescript('''CREATE TABLE parish(id INTEGER PRIMARY KEY,public_id TEXT UNIQUE,name TEXT,email TEXT UNIQUE,password_hash TEXT,methods TEXT,reminder_day INTEGER DEFAULT 10,reminder_time TEXT DEFAULT '09:00'); CREATE TABLE donation(id TEXT PRIMARY KEY,parish_id INTEGER,amount INTEGER,platform_fee INTEGER,method TEXT,created_at TEXT,paid_at TEXT); CREATE TABLE asaas_pix(donation_id TEXT PRIMARY KEY,payment_id TEXT UNIQUE);''')
        from werkzeug.security import generate_password_hash
        connection.execute('INSERT INTO parish VALUES(1,"old","Paróquia anterior","old@example.com",?,"pix",10,"09:00")',(generate_password_hash('test-password-123'),))
        connection.execute('INSERT INTO donation VALUES("legacy",1,1000,5,"pix","2026-11-10",NULL)')
        connection.execute('INSERT INTO asaas_pix VALUES("legacy","pay_old")')
        connection.commit()
        connection.close()
        app=create_app({'TESTING':True,'DATABASE':separate})
        client=app.test_client()
        client.get('/')
        self.post(client,'/entrar',email='old@example.com',password='test-password-123')
        self.assertEqual(client.get('/painel').status_code,200)
        with app.app_context():
            self.assertEqual(db().execute('SELECT payment_id FROM asaas_payment').fetchone()[0],'pay_old')
            self.assertEqual(db().execute('SELECT mode FROM donation').fetchone()[0],'sandbox')
        create_app({'TESTING':True,'DATABASE':separate})


if __name__=='__main__':
    unittest.main()
