"""Dizipy local. Execute iniciar.bat para abrir a aplicação e a agenda."""
import base64
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import date
from functools import wraps
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import quote

import click
import qrcode
from cryptography.fernet import Fernet
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import asaas
from database import IntegrityError
import messaging
import verification
from cnpj_registry import Registry, RegistryUnavailable
import services
from storage import audit, close_db, db, durable_secret, initialize, now, put_setting, secret_box, setting, stamp

METHODS=services.METHODS
PERMISSIONS={'finance':'Consultar e exportar contribuições','people':'Gerenciar fiéis','reminders':'Editar os lembretes'}


def create_app(config=None, *, webhook_only=False):
    app=Flask(__name__)
    instance=Path(app.instance_path)
    app.config.update(DATABASE_URL=os.environ.get('DATABASE_URL',''),HOSTED=os.environ.get('DIZIPY_HOSTED')=='1',ADMIN_EMAIL=os.environ.get('DIZIPY_ADMIN_EMAIL','').strip().lower(),DATABASE=os.environ.get('DIZIPY_DATABASE') or instance/'dizipy.sqlite',SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',MAX_CONTENT_LENGTH=262144,ASAAS_CUSTOMER_ID=os.environ.get('ASAAS_CUSTOMER_ID',''),TESTING=False)
    app.config.from_mapping(RESEND_API_KEY=os.environ.get('RESEND_API_KEY',''),
                            DIZIPY_EMAIL_FROM=os.environ.get('DIZIPY_EMAIL_FROM',''))
    if config:
        app.config.update(config)
    if app.config['HOSTED']:
        required = ('DATABASE_URL','DIZIPY_ENCRYPTION_KEY','DIZIPY_SECRET_KEY','DIZIPY_ADMIN_EMAIL','DIZIPY_SETUP_TOKEN')
        if any(not os.environ.get(name) for name in required):
            raise RuntimeError('Configure as variáveis de banco, chaves e cadastro inicial do render.yaml.')
        app.config['LOCAL_ENCRYPTION_KEY']=base64.urlsafe_b64encode(hashlib.sha256(os.environ['DIZIPY_ENCRYPTION_KEY'].encode()).digest())
        app.config['SECRET_KEY']=os.environ['DIZIPY_SECRET_KEY']
        app.config['SESSION_COOKIE_SECURE']=True
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
    else:
        data_dir=Path(app.config['DATABASE']).parent
        data_dir.mkdir(parents=True,exist_ok=True)
        app.config.setdefault('LOCAL_ENCRYPTION_KEY',durable_secret(data_dir/'local.key',Fernet.generate_key()))
        app.config['SECRET_KEY']=app.config.get('SECRET_KEY') or os.environ.get('DIZIPY_SECRET_KEY') or durable_secret(data_dir/'session.key',secrets.token_bytes(32)).hex()
    app.config['EMAIL_VERIFICATION'] = app.config['HOSTED'] or bool(app.config['RESEND_API_KEY'])
    if app.config['EMAIL_VERIFICATION'] and not all(app.config[name] for name in ('RESEND_API_KEY','DIZIPY_EMAIL_FROM')):
        raise RuntimeError('Configure RESEND_API_KEY e DIZIPY_EMAIL_FROM para enviar códigos de acesso.')
    if app.config['HOSTED']:
        registry_key = os.environ.get('SERPRO_CONSUMER_KEY')
        registry_secret = os.environ.get('SERPRO_CONSUMER_SECRET')
        if not registry_key or not registry_secret:
            raise RuntimeError('Configure SERPRO_CONSUMER_KEY e SERPRO_CONSUMER_SECRET.')
        app.extensions['cnpj_registry'] = Registry(registry_key, registry_secret)
    app.extensions['secrets']=Fernet(app.config['LOCAL_ENCRYPTION_KEY'])
    app.teardown_appcontext(close_db)
    with app.app_context():
        initialize()
    app.add_template_filter(services.money,'money')
    app.add_template_filter(lambda value: services.LABELS.get(value,value),'label')
    app.add_template_filter(lambda value: str(value or '')[:16].replace('T',' '),'datetime')

    def csrf_token():
        if 'csrf' not in session:
            session['csrf']=secrets.token_urlsafe(32)
        return session['csrf']

    def current_user():
        if app.config['EMAIL_VERIFICATION'] and session.get('email_verified') is not True:
            return None
        if 'current_user' not in g:
            g.current_user=db().execute('SELECT * FROM app_user WHERE id=? AND active=1',(session.get('user_id'),)).fetchone()
        return g.current_user

    def current_parish():
        user=current_user()
        return db().execute('SELECT * FROM parish WHERE id=?',(user['parish_id'],)).fetchone() if user else None

    def allowed(permission):
        user=current_user()
        return bool(user and (user['role']=='owner' or permission in json.loads(user['permissions'])))

    def require(permission=None):
        def decorate(function):
            @wraps(function)
            def wrapped(*args,**kwargs):
                if current_user() is None:
                    return redirect(url_for('home'))
                if permission=='owner' and current_user()['role']!='owner':
                    abort(403)
                if permission and permission!='owner' and not allowed(permission):
                    abort(403)
                return function(*args,**kwargs)
            return wrapped
        return decorate

    def parish_by_public(public_id):
        row=db().execute('SELECT * FROM parish WHERE public_id=?',(public_id,)).fetchone()
        if row is None:
            abort(404)
        return row

    def local_admin():
        user=current_user()
        if not user or not user['platform_admin']:
            abort(403)

    @app.context_processor
    def template_context():
        return {'csrf_token':csrf_token,'methods':METHODS,'permissions':PERMISSIONS,'user':current_user(),'can':allowed,'current_parish':current_parish(),'mode':services.mode_for_new(),'whatsapp_mode':setting('whatsapp_mode','demo'),'provider_url':services.public_url,'labels':services.LABELS}

    @app.before_request
    def security_checks():
        is_hook=request.endpoint in ('asaas_webhook','whatsapp_webhook')
        if webhook_only and not is_hook:
            abort(404)
        if request.method=='POST' and not is_hook:
            provided=request.form.get('csrf','')
            expected=session.get('csrf','')
            if not expected or not hmac.compare_digest(provided.encode(),expected.encode()):
                abort(400,description='A sessão expirou. Recarregue a página e tente novamente.')

    @app.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def friendly_error(error):
        return render_template('error.html',error=error),error.code

    @app.get('/health')
    def health():
        return {'status':'ok','mode':services.mode_for_new()}

    @app.get('/')
    def home():
        return redirect(url_for('dashboard')) if current_user() else render_template('auth.html',register=False)

    @app.get('/cookies')
    def cookies():
        return render_template('cookies.html')

    def finish_password_step(user_id):
        session.clear()
        if not app.config['EMAIL_VERIFICATION']:
            session['user_id']=user_id
            return redirect(url_for('dashboard'))
        user=db().execute('SELECT * FROM app_user WHERE id=? AND active=1',(user_id,)).fetchone()
        if user is None:
            return redirect(url_for('home'))
        challenge=verification.issue_code(user)
        if challenge is None:
            flash('O envio do código não foi confirmado. Aguarde um minuto e tente entrar novamente.')
            return redirect(url_for('home'))
        session['email_challenge']=[user_id,challenge]
        return redirect(url_for('verify_email'))

    @app.route('/entrar/verificar',methods=['GET','POST'])
    def verify_email():
        pending=session.get('email_challenge')
        if not pending:
            return redirect(url_for('home'))
        if request.method=='POST':
            user_id,challenge=pending
            user=db().execute('SELECT * FROM app_user WHERE id=? AND active=1',(user_id,)).fetchone()
            if user and verification.verify_code(user,challenge,request.form.get('code','').strip()):
                session.clear()
                session['user_id']=user_id
                session['email_verified']=True
                parish = current_parish()
                if app.config['HOSTED'] and user['role']=='owner' and parish['cnpj_status']!='verified':
                    check_parish_cnpj(parish)
                    return redirect(url_for('parish_registration'))
                return redirect(url_for('dashboard'))
            flash('Código incorreto, expirado ou sem tentativas restantes. Se precisar de outro código, entre novamente.')
        return render_template('verify_email.html')

    @app.route('/cadastro',methods=['GET','POST'])
    def register():
        if request.method=='POST':
            name=request.form.get('name','').strip()
            email=request.form.get('email','').strip().lower()
            if app.config['HOSTED'] and email==app.config['ADMIN_EMAIL']:
                supplied=request.form.get('setup_token','').encode()
                expected=os.environ['DIZIPY_SETUP_TOKEN'].encode()
                if not hmac.compare_digest(supplied, expected):
                    abort(403,description='Use o cadastro de administrador e o código inicial do Render.')
            password=request.form.get('password','')
            raw_cnpj=request.form.get('cnpj','').strip()
            try:
                cnpj=verification.normalize_cnpj(raw_cnpj) if raw_cnpj or app.config['HOSTED'] else None
            except ValueError as error:
                flash(str(error))
                return render_template('auth.html',register=True,hosted=app.config['HOSTED'],admin_setup=request.args.get('administrador')=='1')
            if not 2<=len(name)<=120 or not valid_email(email) or not 10<=len(password)<=128:
                flash('Informe um nome, e-mail válido e senha de 10 a 128 caracteres.')
            else:
                try:
                    with db() as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        if app.config['HOSTED']:
                            rate_key='signup:'+hashlib.sha256((request.remote_addr or '').encode()).hexdigest()
                            rate=connection.execute('SELECT * FROM login_attempt WHERE key=?',(rate_key,)).fetchone()
                            current=time.time()
                            attempts=rate['failures'] if rate and rate['blocked_until']>current else 0
                            if attempts>=5:
                                abort(429,description='Limite de cadastros atingido. Tente novamente mais tarde.')
                            until=rate['blocked_until'] if attempts else current+3600
                            connection.execute(
                                'INSERT INTO login_attempt VALUES(?,?,?) ON CONFLICT(key) DO UPDATE '
                                'SET failures=excluded.failures,blocked_until=excluded.blocked_until',
                                (rate_key,attempts+1,until))
                        password_hash=generate_password_hash(password)
                        parish_id=connection.execute('INSERT INTO parish(public_id,name,email,password_hash,methods,cnpj) VALUES(?,?,?,?,?,?)',(secrets.token_urlsafe(12),name,email,password_hash,','.join(METHODS),cnpj)).lastrowid
                        first=not connection.execute('SELECT 1 FROM app_user').fetchone()
                        user_id=connection.execute("INSERT INTO app_user(parish_id,name,email,password_hash,role,platform_admin) VALUES(?,?,?,?,'owner',?)",(parish_id,name,email,password_hash,int(email==app.config['ADMIN_EMAIL'] if app.config['HOSTED'] else first))).lastrowid
                        put_setting('asaas_webhook_token',secrets.token_hex(32),parish_id,secret=True)
                        audit(parish_id,'parish_registered',user_id=user_id)
                except IntegrityError:
                    flash('Este e-mail já está cadastrado.')
                else:
                    return finish_password_step(user_id)
        return render_template('auth.html',register=True,hosted=app.config['HOSTED'],admin_setup=request.args.get('administrador')=='1')

    @app.post('/entrar')
    def login():
        email=request.form.get('email','').strip().lower()
        password=request.form.get('password','')
        key=hashlib.sha256(((request.remote_addr or '')+'|'+email).encode()).hexdigest()
        attempt=db().execute('SELECT * FROM login_attempt WHERE key=?',(key,)).fetchone()
        if attempt and attempt['blocked_until']>time.time():
            abort(429,description='Muitas tentativas. Aguarde alguns minutos.')
        user=db().execute('SELECT * FROM app_user WHERE email=? AND active=1',(email,)).fetchone()
        if user and len(password)<=128 and check_password_hash(user['password_hash'],password):
            with db():
                db().execute('DELETE FROM login_attempt WHERE key=?',(key,))
            return finish_password_step(user['id'])
        with db():
            failures=(attempt['failures'] if attempt else 0)+1
            db().execute('INSERT INTO login_attempt VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,blocked_until=excluded.blocked_until',(key,failures,time.time()+300 if failures>=8 else 0))
        flash('E-mail ou senha inválidos.')
        return redirect(url_for('home'))

    @app.post('/sair')
    def logout():
        session.clear()
        return redirect(url_for('home'))

    def report_rows():
        parish=current_parish()
        start=request.args.get('inicio','')
        end=request.args.get('fim','')
        for value in (start,end):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    abort(400,description='Período inválido.')
        query='SELECT d.*,m.name AS member_name,a.payment_id FROM donation d LEFT JOIN member m ON m.id=d.member_id LEFT JOIN asaas_payment a ON a.donation_id=d.id WHERE d.parish_id=?'
        params=[parish['id']]
        if start:
            query+=' AND substr(d.created_at,1,10)>=?'
            params.append(start)
        if end:
            query+=' AND substr(d.created_at,1,10)<=?'
            params.append(end)
        return db().execute(query+' ORDER BY d.created_at DESC,d.id',params).fetchall()

    @app.route('/painel',methods=['GET','POST'])
    @require()
    def dashboard():
        parish=current_parish()
        if request.method=='POST':
            if not allowed('reminders'):
                abort(403)
            save_reminders(parish)
            return redirect(url_for('dashboard'))
        rows=report_rows() if allowed('finance') else []
        paid=[row for row in rows if row['status']=='paid']
        totals={'gross':sum(row['amount'] for row in paid),'fee':sum(row['platform_fee'] for row in paid),'count':len(paid),'pending':sum(row['status']=='pending' for row in rows)}
        version=hashlib.sha256(json.dumps([(row['id'],row['status'],row['settled_at']) for row in rows]).encode()).hexdigest()[:16]
        if request.args.get('pix_status')=='1':
            return {'version':version}
        people=db().execute('SELECT COUNT(*) FROM member WHERE parish_id=? AND active=1',(parish['id'],)).fetchone()[0]
        recurring_count=db().execute("SELECT COUNT(*) FROM recurring WHERE parish_id=? AND status='active'",(parish['id'],)).fetchone()[0]
        return render_template('dashboard.html',parish=parish,donations=rows[:50],totals=totals,people=people,recurring_count=recurring_count,version=version)

    @app.get('/painel/exportar')
    @require('finance')
    def export_donations():
        output=StringIO()
        writer=csv.writer(output,delimiter=';')
        writer.writerow(['Referência','Contribuinte','Forma','Ambiente','Status','Bruto','Taxa Dizipy prevista','Tarifa Asaas informada','Líquido estimado','Confirmado em'])
        for row in report_rows():
            writer.writerow([csv_safe(row['id']),csv_safe(row['member_name'] or ''),METHODS[row['method']],row['mode'],services.LABELS.get(row['status'],row['status']),services.money(row['amount']),services.money(row['platform_fee']),services.money(row['provider_fee']) if row['provider_fee'] is not None else '',services.money(row['amount']-row['platform_fee']-(row['provider_fee'] or 0)),row['paid_at'] or ''])
        return Response(output.getvalue().encode('utf-8-sig'),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="contribuicoes-dizipy.csv"'})

    @app.route('/fieis',methods=['GET','POST'])
    @require('people')
    def people():
        parish=current_parish()
        if request.method=='POST':
            try:
                with db():
                    member_id=save_member(parish['id'])
                    audit(parish['id'],'member_saved',str(member_id),current_user()['id'])
                flash('Cadastro salvo. A adesão ao WhatsApp é confirmada pelo próprio fiel.')
            except ValueError as error:
                flash(str(error))
            return redirect(url_for('people'))
        members=db().execute('SELECT * FROM member WHERE parish_id=? ORDER BY active DESC,name',(parish['id'],)).fetchall()
        return render_template('people.html',members=members)

    @app.post('/fieis/<int:member_id>/atualizar')
    @require('people')
    def edit_member(member_id):
        member=db().execute('SELECT * FROM member WHERE id=? AND parish_id=?',(member_id,current_parish()['id'])).fetchone()
        if not member:
            abort(404)
        with db():
            action=request.form.get('action')
            if action=='optout':
                db().execute('UPDATE member SET opted_out_at=? WHERE id=?',(stamp(),member_id))
                db().execute("UPDATE message SET status='cancelled' WHERE member_id=? AND status='queued'",(member_id,))
            elif action=='archive':
                db().execute('UPDATE member SET active=1-active WHERE id=?',(member_id,))
            audit(member['parish_id'],'member_updated',action or '',current_user()['id'])
        return redirect(url_for('people'))

    def save_reminders(parish):
        day=request.form.get('day','')
        hour=request.form.get('time','')
        chosen=set(request.form.getlist('methods'))
        body=request.form.get('body',parish['reminder_body']).strip()
        if not re.fullmatch(r'(?:[1-9]|[12][0-9]|3[01])',day) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',hour) or not chosen<=METHODS.keys() or not 10<=len(body)<=700:
            flash('Confira dia, horário, mensagem e formas de pagamento.')
            return
        with db():
            db().execute('UPDATE parish SET reminder_day=?,reminder_time=?,reminder_body=?,reminder_enabled=?,methods=? WHERE id=?',(int(day),hour,body,int(request.form.get('enabled')=='on'),','.join(method for method in METHODS if method in chosen),parish['id']))
            audit(parish['id'],'reminders_updated',user_id=current_user()['id'])
        flash('Preferências salvas.')

    @app.route('/lembretes',methods=['GET','POST'])
    @require('reminders')
    def reminders():
        parish=current_parish()
        if request.method=='POST':
            save_reminders(parish)
            return redirect(url_for('reminders'))
        sample={'name':'Maria','manage_token':'exemplo'}
        messages=db().execute('SELECT message.*,member.name FROM message JOIN member ON member.id=message.member_id WHERE message.parish_id=? ORDER BY message.id DESC LIMIT 50',(parish['id'],)).fetchall()
        return render_template('reminders.html',parish=parish,preview=messaging.reminder_text(parish,sample),messages=messages)

    @app.post('/lembretes/processar')
    @require('reminders')
    def queue_reminders():
        count=messaging.enqueue_reminders(force_parish=current_parish()['id'])
        messaging.send_queue()
        flash(f'{count} lembrete(s) adicionado(s) para este mês. Os cadastros já processados não são repetidos.')
        return redirect(url_for('reminders'))

    @app.route('/colaboradores',methods=['GET','POST'])
    @require('owner')
    def staff():
        parish=current_parish()
        if request.method=='POST':
            email=request.form.get('email','').strip().lower()
            name=request.form.get('name','').strip()
            password=request.form.get('password','')
            permissions=set(request.form.getlist('permissions'))
            if not valid_email(email) or not 2<=len(name)<=120 or not 10<=len(password)<=128 or not permissions<=PERMISSIONS.keys():
                flash('Confira nome, e-mail, senha e permissões.')
            else:
                try:
                    with db():
                        db().execute("INSERT INTO app_user(parish_id,name,email,password_hash,role,permissions) VALUES(?,?,?,?,'staff',?)",(parish['id'],name,email,generate_password_hash(password),json.dumps(sorted(permissions))))
                        audit(parish['id'],'staff_created',email,current_user()['id'])
                    flash('Colaborador cadastrado.')
                except IntegrityError:
                    flash('Esse e-mail já está em uso.')
            return redirect(url_for('staff'))
        users=db().execute("SELECT * FROM app_user WHERE parish_id=? AND role='staff' ORDER BY name",(parish['id'],)).fetchall()
        return render_template('staff.html',users=users)

    @app.post('/colaboradores/<int:user_id>')
    @require('owner')
    def update_staff(user_id):
        target=db().execute("SELECT * FROM app_user WHERE id=? AND parish_id=? AND role='staff'",(user_id,current_parish()['id'])).fetchone()
        if not target:
            abort(404)
        permissions=set(request.form.getlist('permissions'))
        if not permissions<=PERMISSIONS.keys():
            abort(400)
        with db():
            db().execute('UPDATE app_user SET active=?,permissions=? WHERE id=?',(int(request.form.get('active')=='on'),json.dumps(sorted(permissions)),user_id))
            password=request.form.get('password','')
            if password:
                if not 10<=len(password)<=128:
                    abort(400)
                db().execute('UPDATE app_user SET password_hash=? WHERE id=?',(generate_password_hash(password),user_id))
            audit(target['parish_id'],'staff_updated',str(user_id),current_user()['id'])
        return redirect(url_for('staff'))

    def check_parish_cnpj(parish):
        registry=app.extensions.get('cnpj_registry')
        if not registry or not parish['cnpj']:
            return
        cnpj=parish['cnpj']
        now=int(time.time())
        with db() as connection:
            # Reserve the attempt before calling the paid API.
            result=connection.execute(
                'UPDATE parish SET cnpj_checked_at=? WHERE id=? AND cnpj=? '
                "AND cnpj_status='pending' AND (cnpj_checked_at IS NULL OR "
                "(cnpj_registry_status IS NULL AND cnpj_checked_at<=?) OR "
                "(cnpj_registry_status IN ('inactive','not_found') AND cnpj_checked_at<=?))",
                (now,parish['id'],cnpj,now-600,now-86400))
            if not result.rowcount:
                return
        try:
            status,name=registry.lookup(cnpj)
        except RegistryUnavailable:
            flash('A consulta de CNPJ está indisponível. Tente novamente em 10 minutos.')
            return
        with db() as connection:
            connection.execute(
                'UPDATE parish SET cnpj_registry_status=?,cnpj_legal_name=? '
                "WHERE id=? AND cnpj=? AND cnpj_checked_at=? AND cnpj_status='pending'",
                (status,name,parish['id'],cnpj,now))

    @app.route('/cadastro-paroquial',methods=['GET','POST'])
    @require('owner')
    def parish_registration():
        parish=current_parish()
        if request.method=='POST':
            try:
                cnpj=verification.normalize_cnpj(request.form.get('cnpj',''))
            except ValueError as error:
                flash(str(error))
            else:
                with db() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute(
                        "UPDATE parish SET cnpj=?,cnpj_status='pending',cnpj_legal_name=NULL,"
                        "cnpj_reviewed_at=NULL,cnpj_registry_status=NULL WHERE id=? AND (cnpj IS NULL OR cnpj<>? OR cnpj_status='rejected')",
                        (cnpj,parish['id'],cnpj))
                updated=current_parish()
                check_parish_cnpj(updated)
                flash('CNPJ salvo. Confira a situação cadastral abaixo.')
            return redirect(url_for('parish_registration'))
        return render_template('parish_registration.html',parish=parish)

    @app.route('/plataforma/cadastros',methods=['GET','POST'])
    @require()
    def review_registrations():
        local_admin()
        if request.method=='POST':
            parish_id=request.form.get('parish_id',type=int)
            cnpj=request.form.get('cnpj','')
            decision=request.form.get('decision')
            legal_name=request.form.get('legal_name','').strip()
            if decision not in ('verified','rejected') or request.form.get('checked')!='on':
                abort(400)
            if decision=='verified' and not 2<=len(legal_name)<=200:
                abort(400,description='Informe o nome empresarial que consta no comprovante.')
            with db() as connection:
                connection.execute('BEGIN IMMEDIATE')
                if app.config['HOSTED'] and decision=='verified':
                    record=connection.execute('SELECT cnpj_registry_status FROM parish WHERE id=? AND cnpj=?',(parish_id,cnpj)).fetchone()
                    if not record or record['cnpj_registry_status']!='active':
                        abort(400,description='A consulta ao Serpro precisa confirmar o CNPJ ativo.')
                result=connection.execute(
                    'UPDATE parish SET cnpj_status=?,cnpj_legal_name=?,cnpj_reviewed_at=? '
                    "WHERE id=? AND cnpj=? AND cnpj_status='pending'",
                    (decision,legal_name if decision=='verified' else None,stamp(),parish_id,cnpj))
                if result.rowcount:
                    audit(parish_id,'cnpj_reviewed',decision,current_user()['id'])
            flash('Revisão registrada.' if result.rowcount else 'Cadastro alterado. Atualize a página e confira o CNPJ.')
            return redirect(url_for('review_registrations'))
        parishes=db().execute('SELECT * FROM parish WHERE cnpj IS NOT NULL ORDER BY name').fetchall()
        return render_template('review_registrations.html',parishes=parishes)

    @app.route('/integracoes',methods=['GET','POST'])
    @require('owner')
    def integrations():
        parish=current_parish()
        if request.method=='POST':
            action=request.form.get('action','save')
            if action=='check':
                try:
                    status=services.api(parish['id'],'GET','/myAccount/status/')
                    with db():
                        db().execute('UPDATE parish SET account_status=?,account_id=? WHERE id=?',(status.get('general','PENDING'),status.get('id'),parish['id']))
                        audit(parish['id'],'account_checked',status.get('general','PENDING'),current_user()['id'])
                    flash('Situação cadastral atualizada pelo Asaas Sandbox.')
                except asaas.AsaasError as error:
                    flash(str(error))
            else:
                with db():
                    key=request.form.get('asaas_key','').strip()
                    if key:
                        put_setting('asaas_key',key,parish['id'],secret=True)
                        db().execute("UPDATE parish SET account_status='PENDING',account_id=NULL WHERE id=?",(parish['id'],))
                    put_setting('pix_key',request.form.get('pix_key','').strip(),parish['id'],secret=True)
                    if not setting('asaas_webhook_token',parish_id=parish['id']):
                        put_setting('asaas_webhook_token',secrets.token_hex(32),parish['id'],secret=True)
                    audit(parish['id'],'payment_integration_updated',user_id=current_user()['id'])
                flash('Configuração salva localmente. Confira a situação da conta.')
            return redirect(url_for('integrations'))
        token=setting('asaas_webhook_token',parish_id=parish['id']) or setting('asaas_webhook_token')
        return render_template('integrations.html',parish=parish,has_key=bool(services.api_key(parish['id'])),hook_token=token,hook_path='/webhooks/asaas/'+parish['public_id'])

    @app.route('/plataforma',methods=['GET','POST'])
    @require()
    def platform():
        local_admin()
        if request.method=='POST':
            new_mode=request.form.get('mode','demo')
            whatsapp_mode=request.form.get('whatsapp_mode','demo')
            public=request.form.get('public_url','').strip().rstrip('/')
            webhook=request.form.get('webhook_url','').strip().rstrip('/')
            if webhook and not re.fullmatch(r'https://[^\s/?#]+(?::[0-9]+)?',webhook):
                flash('Informe um endereço HTTPS válido para o receptor.')
                return redirect(url_for('platform'))
            if new_mode not in ('demo','sandbox') or whatsapp_mode not in ('demo','cloud') or (public and not re.fullmatch(r'https?://[^\s/?#]+(?::[0-9]+)?',public)):
                flash('Confira o ambiente e o endereço público sem caminhos adicionais.')
                return redirect(url_for('platform'))
            with db():
                for name in ('public_url','webhook_url','whatsapp_phone_id','whatsapp_version','whatsapp_template','whatsapp_language','whatsapp_number','platform_wallet'):
                    value=request.form.get(name,'').strip()
                    if len(value)>500:
                        abort(400)
                    put_setting(name,value)
                put_setting('mode',new_mode)
                put_setting('whatsapp_mode',whatsapp_mode)
                for name in ('whatsapp_token','whatsapp_app_secret'):
                    value=request.form.get(name,'').strip()
                    if value:
                        put_setting(name,value,secret=True)
                audit(current_parish()['id'],'platform_settings_updated',new_mode,current_user()['id'])
            flash('Configurações centrais salvas. A configuração de produção será feita após a homologação.')
            return redirect(url_for('platform'))
        keys=('public_url','webhook_url','whatsapp_phone_id','whatsapp_version','whatsapp_template','whatsapp_language','whatsapp_number','platform_wallet')
        settings={name:setting(name) for name in keys}
        checks={'asaas':bool(setting('asaas_key')),'whatsapp':bool(setting('whatsapp_token')),'public':bool(setting('public_url'))}
        return render_template('platform.html',hosted=app.config['HOSTED'],settings=settings,checks=checks,verify_token=setting('whatsapp_verify_token'))

    @app.get('/plataforma/backup')
    @require()
    def backup():
        local_admin()
        # O arquivo inclui todas as paróquias. Só o administrador da plataforma tem acesso.
        if app.config.get('DATABASE_URL'):
            flash('O banco está no Neon. Use a recuperação e exportação do projeto no painel Neon. Guarde também as chaves do ambiente Render.')
            return redirect(url_for('platform'))
        import tempfile
        import zipfile
        with tempfile.TemporaryDirectory() as folder:
            snapshot=Path(folder)/'dizipy.sqlite'
            target=sqlite3.connect(snapshot)
            db().backup(target)
            target.close()
            buffer=BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot,'dizipy.sqlite')
                for name in ('local.key','session.key'):
                    source=Path(app.config['DATABASE']).parent/name
                    if source.exists():
                        archive.write(source,name)
        buffer.seek(0)
        return send_file(buffer,mimetype='application/zip',as_attachment=True,download_name='dizipy-backup.zip')

    @app.get('/atividade')
    @require('owner')
    def activity():
        parish=current_parish()
        events=db().execute('SELECT id,event,state,error,created_at FROM provider_event WHERE parish_id=? ORDER BY created_at DESC LIMIT 50',(parish['id'],)).fetchall()
        history=db().execute('SELECT * FROM audit WHERE parish_id=? ORDER BY id DESC LIMIT 50',(parish['id'],)).fetchall()
        return render_template('activity.html',events=events,history=history)

    @app.post('/atividade/reprocessar')
    @require('owner')
    def retry_events():
        with db():
            db().execute("UPDATE provider_event SET state='pending',attempts=0,error='' WHERE parish_id=? AND state='failed'",(current_parish()['id'],))
        services.work_events()
        flash('Eventos pendentes reprocessados. Confira o resultado abaixo.')
        return redirect(url_for('activity'))

    @app.get('/recorrencias')
    @require('finance')
    def recurring_list():
        items=db().execute('SELECT r.*,m.name FROM recurring r LEFT JOIN member m ON m.id=r.member_id WHERE r.parish_id=? ORDER BY r.created_at DESC',(current_parish()['id'],)).fetchall()
        return render_template('recurring.html',items=items)

    @app.post('/recorrencias/<recurring_id>/cancelar')
    @require('owner')
    def cancel_subscription(recurring_id):
        item=db().execute('SELECT * FROM recurring WHERE id=? AND parish_id=?',(recurring_id,current_parish()['id'])).fetchone()
        if not item:
            abort(404)
        try:
            services.cancel_recurring(item)
            flash('Recorrência cancelada. O histórico dos pagamentos permanece disponível.')
        except asaas.AsaasError as error:
            flash(str(error))
        return redirect(url_for('recurring_list'))

    @app.post('/recorrencias/simular-ciclo')
    @require('owner')
    def simulate_cycle():
        today=now().date()
        future=services.next_month(today,today.day)
        services.demo_cycles(future,parish_id=current_parish()['id'])
        flash('Próximo ciclo de demonstração gerado. As novas contribuições aguardam confirmação na tela de pagamento.')
        return redirect(url_for('recurring_list'))

    @app.get('/contribuir/<public_id>/qr')
    def parish_qr(public_id):
        parish_by_public(public_id)
        url=services.public_url(url_for('contribute',public_id=public_id))
        if url.startswith('/'):
            url=request.host_url.rstrip('/')+url
        return qr_response(url)

    def save_member(parish_id, existing=None, public=False):
        name=request.form.get('name','').strip()
        phone=messaging.normalize_phone(request.form.get('phone',''))
        email=request.form.get('email','').strip().lower()
        document=re.sub(r'\D','',request.form.get('document',''))
        if not 2<=len(name)<=120 or (email and not valid_email(email)) or (request.form.get('phone','').strip() and not phone):
            raise ValueError('Confira nome, e-mail e telefone com DDD.')
        if document and len(document) not in (11,14):
            raise ValueError('Informe CPF com 11 dígitos ou CNPJ com 14 dígitos.')
        duplicate=db().execute('SELECT id FROM member WHERE parish_id=? AND phone=? AND phone!=""',(parish_id,phone)).fetchone()
        if duplicate and (not existing or duplicate['id']!=existing['id']):
            raise ValueError('Esse telefone já está cadastrado. Use seu link pessoal ou peça ajuda à secretaria.')
        consent=request.form.get('consent')=='on'
        encrypted=secret_box().encrypt(document.encode()).decode() if document else ''
        if existing:
            # O telefone confirmado não pode ser substituído por um link público.
            if public and phone!=existing['phone']:
                raise ValueError('Para alterar seu telefone, entre em contato com a secretaria.')
            db().execute('UPDATE member SET name=?,email=?,document=CASE WHEN ?!="" THEN ? ELSE document END WHERE id=?',(name,email,encrypted,encrypted,existing['id']))
            if consent:
                db().execute('UPDATE member SET consent_at=COALESCE(consent_at,?),opted_out_at=NULL,phone_verified_at=CASE WHEN ?="demo" THEN COALESCE(phone_verified_at,?) ELSE phone_verified_at END WHERE id=?',(stamp(),setting('whatsapp_mode'),stamp(),existing['id']))
            return existing['id']
        return db().execute('INSERT INTO member(parish_id,name,phone,email,document,manage_token,consent_at,created_at,phone_verified_at) VALUES(?,?,?,?,?,?,?,?,?)',(parish_id,name,phone,email,encrypted,secrets.token_urlsafe(24),stamp() if consent else None,stamp(),stamp() if consent and setting('whatsapp_mode')=='demo' else None)).lastrowid

    @app.route('/contribuir/<public_id>',methods=['GET','POST'])
    def contribute(public_id):
        parish=parish_by_public(public_id)
        if app.config['HOSTED'] and parish['cnpj_status']!='verified':
            abort(404,description='Esta página de contribuições ainda está em configuração.')
        token=request.args.get('pessoa') or session.get('member_tokens',{}).get(str(parish['id']))
        member=db().execute('SELECT * FROM member WHERE manage_token=? AND parish_id=?',(token,parish['id'])).fetchone() if token else None
        enabled=[method for method in parish['methods'].split(',') if method in METHODS]
        mode=services.mode_for_new()
        if request.method=='POST':
            amount=services.parse_amount(request.form.get('amount','').strip())
            method=request.form.get('method','')
            if amount is None or method not in enabled:
                flash('Escolha a forma disponível e informe um valor válido.')
            elif method.endswith('monthly') and request.form.get('recurring_consent')!='on':
                flash('Confirme a autorização da contribuição mensal antes de continuar.')
            elif mode=='sandbox' and parish['account_status']!='APPROVED':
                flash('A paróquia precisa verificar a conta recebedora antes de aceitar contribuições no Sandbox.')
            else:
                try:
                    with db() as connection:
                        member_id=save_member(parish['id'],member,public=True)
                        recurring_id=None
                        period=None
                        if method.endswith('monthly'):
                            recurring_id=secrets.token_urlsafe(18)
                            today=now().date()
                            period=today.isoformat()
                            connection.execute('INSERT INTO recurring(id,parish_id,member_id,method,amount,mode,next_due,day,consent_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(recurring_id,parish['id'],member_id,method,amount,mode,services.next_month(today,today.day).isoformat(),today.day,stamp(),stamp()))
                        donation_id=services.new_donation(parish['id'],amount,method,member_id,recurring_id,period,mode)
                        member=connection.execute('SELECT * FROM member WHERE id=?',(member_id,)).fetchone()
                    tokens=dict(session.get('member_tokens',{}))
                    tokens[str(parish['id'])]=member['manage_token']
                    session['member_tokens']=tokens
                    session['demo_donation']=donation_id
                    return redirect(url_for('demo_payment',donation_id=donation_id))
                except IntegrityError:
                    flash('Este cadastro já foi utilizado. Volte ao seu link pessoal ou tente novamente.')
                except ValueError as error:
                    flash(str(error))
        return render_template('contribute.html',parish=parish,member=member,enabled=enabled)

    def accessible_donation(donation_id):
        donation=db().execute('SELECT d.*,p.name AS parish_name,p.public_id,a.payment_id,m.manage_token FROM donation d JOIN parish p ON p.id=d.parish_id LEFT JOIN asaas_payment a ON a.donation_id=d.id LEFT JOIN member m ON m.id=d.member_id WHERE d.id=?',(donation_id,)).fetchone()
        if donation is None:
            abort(404)
        user=current_user()
        owner=user and user['parish_id']==donation['parish_id'] and allowed('finance')
        tokens=session.get('member_tokens',{})
        personal=donation['manage_token'] and tokens.get(str(donation['parish_id']))==donation['manage_token']
        if not (owner or personal or session.get('demo_donation')==donation_id):
            abort(404)
        return donation

    @app.route('/simulacao/<donation_id>',methods=['GET','POST'])
    def demo_payment(donation_id):
        donation=accessible_donation(donation_id)
        version=donation['status']+'|'+str(donation['settled_at'] or '')
        if request.method=='GET' and request.args.get('pix_status')=='1':
            return {'version':version}
        if request.method=='POST':
            action=request.form.get('action','')
            try:
                if donation['mode']=='demo':
                    services.simulate(donation_id,action or 'pay')
                elif action=='create' or not donation['payment_id']:
                    services.ensure_charge(donation_id)
                else:
                    services.refresh_charge(donation_id)
            except (asaas.AsaasError,ValueError) as error:
                flash(str(error))
            return redirect(url_for('demo_payment',donation_id=donation_id))
        recurring=db().execute('SELECT * FROM recurring WHERE id=?',(donation['recurring_id'],)).fetchone() if donation['recurring_id'] else None
        user=current_user()
        show_personal=bool(session.get('demo_donation')==donation_id or session.get('member_tokens',{}).get(str(donation['parish_id']))==donation['manage_token'] or (user and user['role']=='owner'))
        return render_template('payment.html',donation=donation,recurring=recurring,version=version,show_personal=show_personal)

    @app.get('/simulacao/<donation_id>/qr')
    def payment_qr(donation_id):
        donation=accessible_donation(donation_id)
        if not donation['qr_payload']:
            abort(404)
        return qr_response(donation['qr_payload'])

    @app.route('/pessoa/<token>',methods=['GET','POST'])
    def member_portal(token):
        member=db().execute('SELECT m.*,p.name AS parish_name,p.public_id FROM member m JOIN parish p ON p.id=m.parish_id WHERE m.manage_token=?',(token,)).fetchone()
        if not member:
            abort(404)
        tokens=dict(session.get('member_tokens',{}))
        tokens[str(member['parish_id'])]=token
        session['member_tokens']=tokens
        if request.method=='POST':
            action=request.form.get('action')
            if action=='optout':
                with db():
                    db().execute('UPDATE member SET opted_out_at=? WHERE id=?',(stamp(),member['id']))
                    db().execute("UPDATE message SET status='cancelled' WHERE member_id=? AND status='queued'",(member['id'],))
                flash('Lembretes cancelados.')
            elif action=='cancel':
                recurring=db().execute('SELECT * FROM recurring WHERE id=? AND member_id=?',(request.form.get('recurring_id'),member['id'])).fetchone()
                if not recurring:
                    abort(404)
                try:
                    services.cancel_recurring(recurring)
                    flash('Contribuição mensal cancelada.')
                except asaas.AsaasError as error:
                    flash(str(error))
            return redirect(url_for('member_portal',token=token))
        donations=db().execute('SELECT * FROM donation WHERE member_id=? ORDER BY created_at DESC LIMIT 30',(member['id'],)).fetchall()
        recurring=db().execute('SELECT * FROM recurring WHERE member_id=? ORDER BY created_at DESC',(member['id'],)).fetchall()
        central=setting('whatsapp_number')
        optin_url='https://wa.me/'+central+'?text='+quote('ATIVAR '+token) if re.fullmatch(r'\d{10,15}',central) else ''
        return render_template('member.html',member=member,donations=donations,recurring=recurring,optin_url=optin_url)

    @app.post('/webhooks/asaas')
    @app.post('/webhooks/asaas/<public_id>')
    def asaas_webhook(public_id=None):
        parish=parish_by_public(public_id) if public_id else None
        token=(setting('asaas_webhook_token',parish_id=parish['id']) if parish else '') or setting('asaas_webhook_token')
        supplied=request.headers.get('asaas-access-token','')
        if not token or not hmac.compare_digest(supplied.encode(),token.encode()):
            abort(401)
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or not isinstance(body.get('id'),str) or not 1<=len(body['id'])<=255 or not isinstance(body.get('event'),str):
            abort(400)
        if parish is None:
            payment=body.get('payment',{})
            mapped=db().execute('SELECT p.* FROM parish p JOIN donation d ON d.parish_id=p.id JOIN asaas_payment a ON a.donation_id=d.id WHERE a.payment_id=?',(payment.get('id'),)).fetchone() if isinstance(payment,dict) else None
            if not mapped and isinstance(payment,dict):
                mapped=db().execute('SELECT p.* FROM parish p JOIN recurring r ON r.parish_id=p.id WHERE r.provider_subscription=?',(payment.get('subscription'),)).fetchone()
            parish=mapped
            if not parish:
                return {'received':True,'ignored':True}
        # Persiste somente os campos necessários à conciliação, sem dados de cartão.
        minimal={'id':body['id'],'event':body['event'],'dateCreated':body.get('dateCreated'),'accountStatus':body.get('accountStatus',{})}
        for resource in ('payment','subscription','authorization'):
            value=body.get(resource)
            if isinstance(value,dict):
                minimal[resource]={key:value[key] for key in ('id','deleted','status') if key in value}
        with db():
            db().execute('INSERT OR IGNORE INTO provider_event(id,parish_id,event,payload,created_at) VALUES(?,?,?,?,?)',(body['id'],parish['id'],body['event'],json.dumps(minimal),stamp()))
        return {'received':True}

    @app.route('/webhooks/whatsapp',methods=['GET','POST'])
    def whatsapp_webhook():
        if request.method=='GET':
            supplied=request.args.get('hub.verify_token','')
            expected=setting('whatsapp_verify_token')
            if request.args.get('hub.mode')=='subscribe' and expected and hmac.compare_digest(supplied.encode(),expected.encode()):
                return Response(request.args.get('hub.challenge',''),mimetype='text/plain')
            abort(403)
        if not messaging.valid_signature(request.get_data(),request.headers.get('X-Hub-Signature-256','')):
            abort(401)
        body=request.get_json(silent=True)
        if not isinstance(body,dict):
            abort(400)
        messaging.process_inbound(body)
        return {'received':True}

    @app.cli.command('asaas-cliente-teste')
    def asaas_test_customer():
        key=setting('asaas_key')
        if not key:
            raise click.ClickException('Configure a chave do Sandbox em Integrações.')
        try:
            customer=asaas.call(key,'POST','/customers',data={'name':'Cliente Teste Dizipy','cpfCnpj':'24971563792','notificationDisabled':True})
        except asaas.AsaasError as error:
            raise click.ClickException(str(error)) from None
        click.echo(customer['id'])

    @app.cli.command('processar-agenda')
    def process_schedule():
        from runner import tick
        tick(app)
        click.echo('Agenda processada.')

    return app


def create_webhook_app():
    return create_app(webhook_only=True)


def valid_email(value):
    return len(value)<=254 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value)


def csv_safe(value):
    value=str(value)
    return "'"+value if value and value[0] in '=+-@\t\r\n' else value


def qr_response(value):
    buffer=BytesIO()
    qrcode.make(value).save(buffer,format='PNG')
    buffer.seek(0)
    return send_file(buffer,mimetype='image/png')
