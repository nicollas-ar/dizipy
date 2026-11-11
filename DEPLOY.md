# Colocar o piloto do Dizipy no ar

O piloto começa com dados fictícios e pagamentos simulados. O site já pode receber HTTPS e webhooks pelo próprio Render, sem ngrok. Esta versão não movimenta dinheiro real.

## 1. Render + Neon

1. No [Neon](https://console.neon.tech/), selecione um banco **vazio e exclusivo do Dizipy**. Não reutilize o banco do site da paróquia. Abra **Connect** e copie a URL completa que começa com `postgresql://`, mantendo os parâmetros TLS.
2. No [Render](https://dashboard.render.com/), abra **New → Blueprint**. Selecione `nicollas-ar/dizipy`, branch `asaas-sandbox`, e o arquivo `render.yaml`. A configuração cria um serviço web de piloto no plano Free, sem criar um banco ou tarefa paga no Render.
3. Preencha `DATABASE_URL` com a URL do Neon e `DIZIPY_ADMIN_EMAIL` com seu e-mail. O Blueprint gera as três chaves de sessão, criptografia e cadastro. Mantenha essas chaves no Render, especialmente `DIZIPY_ENCRYPTION_KEY`: perder ou mudar essa chave torna as configurações cifradas ilegíveis.
4. Depois de o serviço ficar **Live**, abra seu endereço `https://…onrender.com/health`. Deve retornar `status: ok`. Abra `/cadastro` e crie sua paróquia fictícia com o mesmo e-mail e o valor de `DIZIPY_SETUP_TOKEN` da aba **Environment**. Esse cadastro só funciona uma vez.
5. No Dizipy, abra **Plataforma** e confira o endereço público HTTPS. Deixe o endereço separado do receptor vazio: no Render, painel e webhooks usam o mesmo domínio. Confira uma contribuição em demonstração antes de mudar para o Sandbox.

Se preferir usar um Web Service do Dizipy já criado, selecione a mesma branch. Use `pip install -r requirements.txt` no build e `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` na inicialização. Copie as variáveis listadas no Blueprint, gere valores aleatórios fortes para as três chaves e configure `/health` como caminho de verificação. Não altere outro site da paróquia.

## 2. Asaas Sandbox

1. Em **Integrações**, salve a chave do **Asaas Sandbox** e use **Verificar conta no Asaas**.
2. Em **Asaas Sandbox → Integrações → Webhooks**, use a URL completa e o token mostrados em **Integrações** no Dizipy. Para o primeiro teste selecione `PAYMENT_RECEIVED` e ative a fila. Depois inclua os eventos relevantes dos demais fluxos conforme a homologação. Um webhook antigo do ngrok não aponta para o Render.
3. Em **Plataforma**, selecione **Asaas Sandbox**. Crie um Pix avulso de teste, simule o pagamento no Sandbox e verifique o HTTP `200` no log e a confirmação no Dizipy. O `200` confirma a entrega; confira o processamento em **Atividade** e o estado no painel.
4. Homologue cartão avulso e os dois meios mensais separadamente. O código ainda usa a API Sandbox; uma chave de produção não ativa cobranças reais.

## 3. WhatsApp da Meta

1. Em **Meta for Developers → Meus apps**, crie o app **Dizipy** com o caso de uso WhatsApp. Comece com o número de teste e somente seu próprio telefone como destinatário autorizado.
2. No Dizipy, em **Plataforma**, configure o ID do número, número remetente, versão da API, token e App Secret. Mantenha os valores secretos nos próprios painéis.
3. Na Meta, configure o callback `https://SEU-DOMINIO.onrender.com/webhooks/whatsapp` com o token de verificação mostrado em **Plataforma**, e assine o campo `messages` da conta de WhatsApp vinculada. Valide a recepção antes de habilitar envios no Dizipy.
4. Para validar o lembrete real, configure um modelo aprovado com os quatro parâmetros descritos em Plataforma, confirme o opt-in do seu telefone e use somente um cadastro de teste. O número definitivo será do Dizipy.

O plano Free do Render pode suspender o serviço sem tráfego. Webhooks e lembretes podem atrasar. Para entrega periódica mesmo com o site suspenso, configure um Cron Job com `python jobs.py` e as mesmas chaves e banco. O Cron é opcional e tem cobrança própria; não é criado pelo Blueprint. Antes do piloto paroquial, homologue provedores, backup/recuperação e entrega de lembretes.
