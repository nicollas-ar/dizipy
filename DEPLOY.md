# Colocar o piloto do Dizipy no ar

O piloto começa com dados fictícios e pagamentos simulados. O site já pode receber HTTPS e webhooks pelo próprio Render, sem ngrok. Esta versão não movimenta dinheiro real.

## 1. Resend, Render e Neon

Crie uma conta no [Resend](https://resend.com/) e gere uma chave com permissão de envio. Para testar com o próprio e-mail da conta Resend, use `onboarding@resend.dev` e use o mesmo e-mail em `DIZIPY_ADMIN_EMAIL`. Para enviar códigos a colaboradores com outros e-mails, verifique um domínio seu no Resend e use um remetente desse domínio.

| Variável no Render | Valor |
| --- | --- |
| `DATABASE_URL` | URL PostgreSQL completa do Neon, com TLS |
| `DIZIPY_ADMIN_EMAIL` | E-mail que criará a conta administradora |
| `RESEND_API_KEY` | Chave de envio do Resend |
| `DIZIPY_EMAIL_FROM` | Remetente de teste do Resend ou endereço de domínio verificado |
| `DIZIPY_HOSTED` | `1`, configurado pelo Blueprint |
| `DIZIPY_SECRET_KEY` | Gerada pelo Blueprint para sessão e proteção dos códigos |
| `DIZIPY_ENCRYPTION_KEY` | Gerada pelo Blueprint para cifrar configurações |
| `DIZIPY_SETUP_TOKEN` | Gerado pelo Blueprint para o primeiro cadastro |

Cole os segredos diretamente no Render. O Asaas e a Meta são configurados dentro do Dizipy, nos passos abaixo. O modo local com SQLite continua disponível. Para testar e-mail localmente, forneça as duas variáveis do Resend antes de abrir o app.

1. No [Neon](https://console.neon.tech/), selecione um banco **vazio e exclusivo do Dizipy**. Não reutilize o banco do site da paróquia. Abra **Connect** e copie a URL completa que começa com `postgresql://`, mantendo os parâmetros TLS.
2. No [Render](https://dashboard.render.com/), abra **New → Blueprint**. Selecione `nicollas-ar/dizipy`, branch `asaas-sandbox`, e o arquivo `render.yaml`. A configuração cria um serviço web de piloto no plano Free, sem criar um banco ou tarefa paga no Render.
3. Preencha `DATABASE_URL` com a URL do Neon e `DIZIPY_ADMIN_EMAIL` com seu e-mail. Preencha também as duas variáveis do Resend da tabela. O Blueprint gera as três chaves de sessão, criptografia e cadastro. Mantenha essas chaves no Render, especialmente `DIZIPY_ENCRYPTION_KEY`: perder ou mudar essa chave torna as configurações cifradas ilegíveis.
4. Depois de o serviço ficar **Live**, abra seu endereço `https://…onrender.com/health`. Deve retornar `status: ok`. Abra `/cadastro` e cadastre a paróquia com o CNPJ da entidade responsável, o mesmo e-mail e o valor de `DIZIPY_SETUP_TOKEN` da aba **Environment**. Esse cadastro só funciona uma vez. Confirme o código que chegar por e-mail. A confirmação é exigida a cada novo login na hospedagem.
5. No Dizipy, abra **Plataforma** e confira o endereço público HTTPS. Deixe o endereço separado do receptor vazio: no Render, painel e webhooks usam o mesmo domínio. Confira uma contribuição em demonstração antes de mudar para o Sandbox.

Se preferir usar um Web Service do Dizipy já criado, selecione a mesma branch. Use `pip install -r requirements.txt` no build e `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` na inicialização. Copie as variáveis listadas no Blueprint, gere valores aleatórios fortes para as três chaves e configure `/health` como caminho de verificação. Não altere outro site da paróquia.

## Conferir o cadastro da paróquia

No Dizipy, abra **Revisão cadastral**. Consulte o comprovante no [portal da Receita Federal](https://solucoes.receita.fazenda.gov.br/Servicos/cnpjreva/) e confira o nome empresarial, a situação ativa e o vínculo com a paróquia. Se o CNPJ for da mitra ou da diocese, confirme com a secretaria que aquela é a entidade responsável. Registre o resultado no painel. Essa consulta manual dispensa outra conta de API. O fluxo Sandbox continua disponível para testes enquanto o cadastro é conferido.

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
