import csv
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO, StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import qrcode

import asaas
from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

METHODS = {
    "pix": "Pix avulso",
    "card": "Cartão avulso",
    "card_monthly": "Cartão recorrente",
    "pix_monthly": "Pix Automático",
}
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def now():
    return datetime.now(SAO_PAULO)


def money(cents):
    value = f"{Decimal(cents) / 100:,.2f}".translate(str.maketrans(".,", ",."))
    return f"R$ {value}"


def parse_amount(value):
    if not re.fullmatch(r"[0-9]{1,6}(?:[,.][0-9]{1,2})?", value):
        return None
    cents = int(Decimal(value.replace(",", ".")) * 100)
    return cents if cents > 0 else None


def matches_pix_payment(payment, donation):
    value = payment.get("value")
    return (
        payment.get("id") == donation["payment_id"]
        and payment.get("externalReference") == "dizipy-" + donation["id"]
        and payment.get("billingType") == "PIX"
        and type(value) in (int, float)
        and Decimal(str(value)) * 100 == donation["amount"]
    )


def create_app(config=None, *, webhook_only=False):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("DIZIPY_SECRET_KEY") or secrets.token_hex(32),
        DATABASE=Path(app.instance_path) / "dizipy.sqlite",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=16_384,
        ASAAS_API_KEY=os.environ.get("ASAAS_API_KEY", "").strip(),
        ASAAS_CUSTOMER_ID=os.environ.get("ASAAS_CUSTOMER_ID", "").strip(),
        ASAAS_WEBHOOK_TOKEN=os.environ.get("ASAAS_WEBHOOK_TOKEN", "").strip(),
    )
    if config:
        app.config.update(config)
    sandbox_enabled = bool(app.config["ASAAS_API_KEY"] and app.config["ASAAS_CUSTOMER_ID"])
    webhook_token = app.config["ASAAS_WEBHOOK_TOKEN"]
    if webhook_token and not re.fullmatch(r"\S{32,255}", webhook_token):
        raise ValueError("ASAAS_WEBHOOK_TOKEN deve ter de 32 a 255 caracteres sem espaços.")
    app.jinja_env.globals["webhook_enabled"] = bool(webhook_token)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    app.add_template_filter(money, "money")

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_error):
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    with app.app_context():
        db().executescript("""
            CREATE TABLE IF NOT EXISTS parish (
                id INTEGER PRIMARY KEY,
                public_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                methods TEXT NOT NULL,
                reminder_day INTEGER NOT NULL DEFAULT 10,
                reminder_time TEXT NOT NULL DEFAULT '09:00'
            );
            CREATE TABLE IF NOT EXISTS donation (
                id TEXT PRIMARY KEY,
                parish_id INTEGER NOT NULL REFERENCES parish(id),
                amount INTEGER NOT NULL CHECK (amount > 0),
                platform_fee INTEGER NOT NULL,
                method TEXT NOT NULL,
                created_at TEXT NOT NULL,
                paid_at TEXT
            );
            CREATE TABLE IF NOT EXISTS asaas_pix (
                donation_id TEXT PRIMARY KEY REFERENCES donation(id),
                payment_id TEXT UNIQUE
            );
            CREATE TABLE IF NOT EXISTS asaas_webhook_event (
                id TEXT PRIMARY KEY,
                donation_id TEXT NOT NULL REFERENCES donation(id)
            );
        """)

    @app.cli.command("asaas-cliente-teste")
    def asaas_test_customer():
        """Cria um cliente fictício no Sandbox e imprime seu identificador."""
        if not app.config["ASAAS_API_KEY"]:
            raise click.ClickException("Configure ASAAS_API_KEY antes de executar este comando.")
        try:
            customer = asaas.call(app.config["ASAAS_API_KEY"], "POST", "/customers", data={
                "name": "Cliente Teste Dizipy",
                "cpfCnpj": "24971563792",
                "notificationDisabled": True,
            })
        except asaas.AsaasError as error:
            raise click.ClickException(str(error)) from None
        customer_id = customer.get("id")
        if not isinstance(customer_id, str) or not re.fullmatch(r"cus_[A-Za-z0-9]+", customer_id):
            raise click.ClickException("O Asaas Sandbox não devolveu um identificador de cliente válido.")
        click.echo(customer_id)

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token
    app.jinja_env.globals["methods"] = METHODS

    @app.before_request
    def check_csrf():
        is_webhook = request.endpoint == "asaas_webhook"
        if webhook_only and not is_webhook:
            abort(404)
        if request.method == "POST" and not is_webhook:
            provided = request.form.get("csrf", "")
            expected = session.get("csrf", "")
            if not expected or not hmac.compare_digest(provided.encode(), expected.encode()):
                abort(400)

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def current_parish():
        return db().execute("SELECT * FROM parish WHERE id = ?", (session.get("parish_id"),)).fetchone()

    def public_parish(public_id):
        parish = db().execute("SELECT * FROM parish WHERE public_id = ?", (public_id,)).fetchone()
        if parish is None:
            abort(404)
        return parish

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "asaas_sandbox" if sandbox_enabled else "demo"}

    @app.get("/")
    def home():
        if current_parish():
            return redirect(url_for("dashboard"))
        return render_template("auth.html", register=False)

    @app.route("/cadastro", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not 2 <= len(name) <= 120 or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                flash("Informe o nome da paróquia e um e-mail válido para o teste.")
            elif not 10 <= len(password) <= 128:
                flash("A senha precisa ter entre 10 e 128 caracteres.")
            else:
                try:
                    with db() as connection:
                        parish_id = connection.execute(
                            "INSERT INTO parish (public_id, name, email, password_hash, methods) VALUES (?, ?, ?, ?, ?)",
                            (secrets.token_urlsafe(12), name, email, generate_password_hash(password), ",".join(METHODS)),
                        ).lastrowid
                except sqlite3.IntegrityError:
                    flash("Já existe uma conta com esse e-mail.")
                else:
                    session.clear()
                    session["parish_id"] = parish_id
                    return redirect(url_for("dashboard"))
        return render_template("auth.html", register=True)

    @app.post("/entrar")
    def login():
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        parish = db().execute("SELECT * FROM parish WHERE email = ?", (email,)).fetchone()
        if parish and len(password) <= 128 and check_password_hash(parish["password_hash"], password):
            session.clear()
            session["parish_id"] = parish["id"]
            return redirect(url_for("dashboard"))
        flash("E-mail ou senha inválidos.")
        return redirect(url_for("home"))

    @app.post("/sair")
    def logout():
        session.clear()
        return redirect(url_for("home"))

    @app.route("/painel", methods=["GET", "POST"])
    def dashboard():
        parish = current_parish()
        if parish is None:
            return redirect(url_for("home"))
        if request.method == "POST":
            chosen = set(request.form.getlist("methods"))
            day = request.form.get("day", "")
            hour = request.form.get("time", "")
            if not chosen <= METHODS.keys() or not re.fullmatch(r"(?:[1-9]|[12][0-9]|3[01])", day) or not re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", hour
            ):
                flash("Confira o dia, o horário e as formas de pagamento.")
            else:
                with db() as connection:
                    connection.execute(
                        "UPDATE parish SET methods = ?, reminder_day = ?, reminder_time = ? WHERE id = ?",
                        (",".join(method for method in METHODS if method in chosen), int(day), hour, parish["id"]),
                    )
                flash("Preferências salvas no modo de teste.")
            return redirect(url_for("dashboard"))

        # O contador e a lista devem refletir o mesmo estado quando chega um webhook.
        connection = db()
        connection.execute("BEGIN")
        totals = connection.execute("""
            SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS gross,
                   COALESCE(SUM(platform_fee), 0) AS fee
            FROM donation WHERE parish_id = ? AND paid_at IS NOT NULL
        """, (parish["id"],)).fetchone()
        if request.args.get("pix_status") == "1":
            return {"version": totals["count"]}
        donations = connection.execute("""
            SELECT donation.*, asaas_pix.donation_id IS NOT NULL AS sandbox_pix
            FROM donation LEFT JOIN asaas_pix ON asaas_pix.donation_id = donation.id
            WHERE parish_id = ? ORDER BY created_at DESC LIMIT 20
        """, (parish["id"],)).fetchall()
        connection.commit()
        return render_template("dashboard.html", parish=parish, totals=totals, donations=donations)

    @app.get("/painel/exportar")
    def export_donations():
        parish = current_parish()
        if parish is None:
            return redirect(url_for("home"))
        donations = db().execute("""
            SELECT * FROM donation WHERE parish_id = ? AND paid_at IS NOT NULL ORDER BY paid_at DESC
        """, (parish["id"],))
        output = StringIO()
        writer = csv.writer(output, delimiter=";")
        writer.writerow(["Identificador", "Forma", "Valor bruto", "Taxa Dizipy", "Valor líquido simulado", "Confirmado em"])
        for row in donations:
            writer.writerow([
                "dizipy-" + row["id"], METHODS[row["method"]], money(row["amount"]), money(row["platform_fee"]),
                money(row["amount"] - row["platform_fee"]), row["paid_at"],
            ])
        return Response(
            output.getvalue().encode("utf-8-sig"), mimetype="text/csv",
            headers={"Content-Disposition": 'attachment; filename="contribuicoes-dizipy.csv"'},
        )

    @app.get("/contribuir/<public_id>/qr")
    def parish_qr(public_id):
        public_parish(public_id)
        data = url_for("contribute", public_id=public_id, _external=True)
        buffer = BytesIO()
        qrcode.make(data).save(buffer, format="PNG")
        buffer.seek(0)
        return send_file(buffer, mimetype="image/png")

    @app.route("/contribuir/<public_id>", methods=["GET", "POST"])
    def contribute(public_id):
        parish = public_parish(public_id)
        enabled = parish["methods"].split(",")
        if request.method == "POST":
            amount = parse_amount(request.form.get("amount", "").strip())
            method = request.form.get("method", "")
            if amount is None or method not in METHODS or method not in enabled:
                flash("Escolha uma forma disponível e digite um valor válido.")
            else:
                donation_id = secrets.token_urlsafe(18)
                fee = int((Decimal(amount) * Decimal("0.005")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                with db() as connection:
                    connection.execute(
                        "INSERT INTO donation (id, parish_id, amount, platform_fee, method, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (donation_id, parish["id"], amount, fee, method, now().isoformat()),
                    )
                    if sandbox_enabled and method == "pix":
                        connection.execute("INSERT INTO asaas_pix (donation_id) VALUES (?)", (donation_id,))
                session["demo_donation"] = donation_id
                return redirect(url_for("demo_payment", donation_id=donation_id))
        return render_template("contribute.html", parish=parish, enabled=enabled, sandbox_enabled=sandbox_enabled)

    @app.route("/simulacao/<donation_id>", methods=["GET", "POST"])
    def demo_payment(donation_id):
        donation = db().execute("""
            SELECT donation.*, parish.name AS parish_name,
                   asaas_pix.donation_id IS NOT NULL AS sandbox_pix, asaas_pix.payment_id
            FROM donation JOIN parish ON parish.id = donation.parish_id
            LEFT JOIN asaas_pix ON asaas_pix.donation_id = donation.id WHERE donation.id = ?
        """, (donation_id,)).fetchone()
        if donation is None:
            abort(404)
        owns_checkout = session.get("demo_donation") == donation_id
        manages_sandbox_checkout = (
            donation["sandbox_pix"] and session.get("parish_id") == donation["parish_id"]
        )
        if not (owns_checkout or manages_sandbox_checkout):
            abort(404)
        if donation["sandbox_pix"]:
            if request.method == "GET" and request.args.get("pix_status") == "1":
                return {"version": int(donation["paid_at"] is not None)}
            return sandbox_payment(donation)
        if request.method == "POST":
            with db() as connection:
                mark_paid(connection, donation_id)
            return redirect(url_for("demo_payment", donation_id=donation_id))
        return render_template("payment.html", donation=donation)

    def mark_paid(connection, donation_id):
        connection.execute(
            "UPDATE donation SET paid_at = ? WHERE id = ? AND paid_at IS NULL",
            (now().isoformat(), donation_id),
        )

    @app.post("/webhooks/asaas")
    def asaas_webhook():
        if not webhook_token:
            abort(404)
        provided = request.headers.get("asaas-access-token", "")
        if not hmac.compare_digest(provided.encode(), webhook_token.encode()):
            abort(401)

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
            abort(400)
        if payload["event"] != "PAYMENT_RECEIVED":
            return {"received": True}

        event_id = payload.get("id")
        payment = payload.get("payment")
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 255 or not isinstance(payment, dict):
            abort(400)
        payment_id = payment.get("id")
        if not isinstance(payment_id, str) or not payment_id:
            abort(400)

        with db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM asaas_webhook_event WHERE id = ?", (event_id,)).fetchone():
                return {"received": True}
            donation = connection.execute("""
                SELECT donation.id, donation.amount, asaas_pix.payment_id
                FROM asaas_pix JOIN donation ON donation.id = asaas_pix.donation_id
                WHERE asaas_pix.payment_id = ?
            """, (payment_id,)).fetchone()
            if donation is not None:
                if payment.get("status") == "RECEIVED" and matches_pix_payment(payment, donation):
                    mark_paid(connection, donation["id"])
                    connection.execute(
                        "INSERT INTO asaas_webhook_event (id, donation_id) VALUES (?, ?)",
                        (event_id, donation["id"]),
                    )
                else:
                    app.logger.warning("Webhook do Asaas ignorado: dados divergentes da contribuição.")
        return {"received": True}

    def create_sandbox_charge(donation):
        reference = "dizipy-" + donation["id"]
        api_key = app.config["ASAAS_API_KEY"]
        customer_id = app.config["ASAAS_CUSTOMER_ID"]
        # Serializa tentativas de geração e recupera uma cobrança criada antes de uma falha local.
        with db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payment_id FROM asaas_pix WHERE donation_id = ?", (donation["id"],),
            ).fetchone()
            if row["payment_id"]:
                return
            result = asaas.call(api_key, "GET", "/payments", params={
                "externalReference": reference, "customer": customer_id, "limit": 2,
            })
            matches = result.get("data")
            if not isinstance(matches, list) or len(matches) > 1:
                raise asaas.AsaasError("Não foi possível identificar uma única cobrança no Sandbox.")
            payment = matches[0] if matches else asaas.call(api_key, "POST", "/payments", data={
                "customer": customer_id,
                "billingType": "PIX",
                "value": donation["amount"] / 100,
                "dueDate": now().date().isoformat(),
                "description": "Contribuição de teste Dizipy",
                "externalReference": reference,
            })
            if not isinstance(payment, dict) or payment.get("externalReference") != reference:
                raise asaas.AsaasError("A referência da cobrança retornada pelo Asaas diverge da contribuição.")
            payment_id = payment.get("id")
            if not isinstance(payment_id, str) or not re.fullmatch(r"pay_[A-Za-z0-9_]+", payment_id):
                raise asaas.AsaasError("O Asaas Sandbox não devolveu um identificador de cobrança válido.")
            connection.execute(
                "UPDATE asaas_pix SET payment_id = ? WHERE donation_id = ?", (payment_id, donation["id"]),
            )

    def sandbox_payment(donation):
        qr = None
        payment_id = donation["payment_id"]
        if not donation["paid_at"]:
            if not sandbox_enabled:
                flash("Configure o Asaas Sandbox para continuar com esta cobrança.")
            else:
                try:
                    if request.method == "POST":
                        if payment_id:
                            payment = asaas.call(app.config["ASAAS_API_KEY"], "GET", f"/payments/{payment_id}")
                            if not matches_pix_payment(payment, donation):
                                raise asaas.AsaasError("Os dados da cobrança divergem da contribuição no Dizipy.")
                            if payment.get("status") == "RECEIVED":
                                with db() as connection:
                                    mark_paid(connection, donation["id"])
                            else:
                                flash("O Asaas ainda não registrou o recebimento da cobrança.")
                        else:
                            create_sandbox_charge(donation)
                    elif payment_id:
                        qr = asaas.call(app.config["ASAAS_API_KEY"], "GET", f"/payments/{payment_id}/pixQrCode")
                except asaas.AsaasError as error:
                    flash(str(error))
                except sqlite3.OperationalError as error:
                    if "database is locked" not in str(error):
                        raise
                    flash("O Dizipy está finalizando outra tentativa. Tente novamente.")
        if request.method == "POST":
            return redirect(url_for("demo_payment", donation_id=donation["id"]))
        return render_template("payment.html", donation=donation, qr=qr, sandbox_enabled=sandbox_enabled)

    return app


def create_webhook_app():
    """Mantém a interface local fora do túnel usado pelo Asaas."""
    return create_app(webhook_only=True)
