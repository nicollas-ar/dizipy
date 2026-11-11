# Hospedar o Dizipy

O cadastro de igrejas é público e consulta o CNPJ no Serpro depois de confirmar o e-mail do responsável. O Render fornece HTTPS para o site e os webhooks. O serviço hospedado cria cobranças na API de produção do Asaas.

## 1. Resend, Serpro, Render e Neon

Contrate a [Consulta CNPJ básica do Serpro](https://apicenter.estaleiro.serpro.gov.br/documentacao/consulta-cnpj/) para obter Consumer Key e Consumer Secret de produção. Crie uma conta no [Resend](https://resend.com/) e gere uma chave com permissão de envio. Para testar com o próprio e-mail da conta Resend, use `onboarding@resend.dev` e use o mesmo e-mail em `DIZIPY_ADMIN_EMAIL`. Para enviar códigos a colaboradores com outros e-mails, verifique um domínio seu no Resend e use um remetente desse domínio.

| Variável no Render | Valor |
| --- | --- |
| `DATABASE_URL` | URL PostgreSQL completa do Neon, com TLS |
| `DIZIPY_ADMIN_EMAIL` | E-mail que criará a conta administradora |
| `SERPRO_CONSUMER_KEY` e `SERPRO_CONSUMER_SECRET` | Credenciais de produção da Consulta CNPJ básica |
| `RESEND_API_KEY` | Chave de envio do Resend |
| `DIZIPY_EMAIL_FROM` | Remetente de teste do Resend ou endereço de domínio verificado |
| `DIZIPY_HOSTED` | `1`, configurado pelo Blueprint |
| `DIZIPY_SECRET_KEY` | Gerada pelo Blueprint para sessão e proteção dos códigos |
| `DIZIPY_ENCRYPTION_KEY` | Gerada pelo Blueprint para cifrar configurações |
| `DIZIPY_SETUP_TOKEN` | Gerado pelo Blueprint para o primeiro cadastro |

Cole os segredos diretamente no Render. O Asaas e a Meta são configurados dentro do Dizipy, nos passos abaixo. O modo local com SQLite continua disponível. Para testar e-mail localmente, forneça as duas variáveis do Resend antes de abrir o app.

1. No [Neon](https://console.neon.tech/), selecione um banco **vazio e exclusivo do Dizipy**. Não reutilize o banco do site da paróquia. Abra **Connect** e copie a URL completa que começa com `postgresql://`, mantendo os parâmetros TLS.
2. No [Render](https://dashboard.render.com/), abra **New → Blueprint**. Selecione `nicollas-ar/dizipy`, branch `producao`, e o arquivo `render.yaml`. A configuração cria um serviço web no plano Free, sem criar um banco ou tarefa paga no Render.
3. Preencha `DATABASE_URL` com a URL do Neon e `DIZIPY_ADMIN_EMAIL` com seu e-mail. Preencha também as variáveis do Resend e do Serpro da tabela. O Blueprint gera as três chaves de sessão, criptografia e cadastro. Mantenha essas chaves no Render, especialmente `DIZIPY_ENCRYPTION_KEY`: perder ou mudar essa chave torna as configurações cifradas ilegíveis.
4. Depois de o serviço ficar **Live**, abra seu endereço `https://…onrender.com/health`. Deve retornar `status: ok`. Abra `/cadastro?administrador=1` e cadastre a paróquia com o CNPJ da entidade responsável, o mesmo e-mail e o valor de `DIZIPY_SETUP_TOKEN` da aba **Environment**. O código inicial é exclusivo do cadastro da plataforma. Outras igrejas usam `/cadastro` com o e-mail do responsável, senha e CNPJ. Confirme o código que chegar por e-mail. A confirmação é exigida a cada novo login na hospedagem.
5. No Dizipy, abra **Plataforma** e confira o endereço público HTTPS. Deixe o endereço separado do receptor vazio: no Render, painel e webhooks usam o mesmo domínio. Confira o endereço antes de entregar os links de contribuição às igrejas.

Se preferir usar um Web Service do Dizipy já criado, selecione a mesma branch. Use `pip install -r requirements.txt` no build e `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120` na inicialização. Copie as variáveis listadas no Blueprint, gere valores aleatórios fortes para as três chaves e configure `/health` como caminho de verificação. Não altere outro site da paróquia.

## Conferir o cadastro da paróquia

No Dizipy, abra **Revisão cadastral**. Consulte o comprovante no [portal da Receita Federal](https://solucoes.receita.fazenda.gov.br/Servicos/cnpjreva/) e confira o nome empresarial, a situação ativa e o vínculo com a paróquia. Se o CNPJ for da mitra ou da diocese, confirme com a secretaria que aquela é a entidade responsável. O Serpro precisa confirmar a inscrição ativa. Registre também a revisão do vínculo e da autorização do responsável no painel. A página de contribuições fica indisponível enquanto o cadastro está em revisão.

## 2. Asaas de produção

1. Cada igreja abre ou utiliza uma conta de produção do Asaas no CNPJ revisado no Dizipy e conclui a aprovação cadastral no Asaas.
2. No Dizipy, em **Integrações**, vincule a chave de produção dessa conta. O Dizipy consulta o CNPJ e o identificador da conta antes de guardar a chave. A chave é inserida apenas pelo responsável no painel da igreja.
3. No painel da mesma conta Asaas, adicione a URL e o token de webhook exibidos em **Integrações**. Ative eventos de pagamentos, assinaturas, autorizações do Pix Automático e situação cadastral.
4. A página pública fica disponível para cobranças após a revisão da igreja e a aprovação da conta vinculada. As contribuições entram na própria conta da igreja. A disponibilidade de cada meio depende da habilitação no Asaas.

O serviço hospedado usa chaves, webhooks e relatórios separados do Sandbox. Para cobrar a taxa do Dizipy nas modalidades compatíveis, configure a carteira Asaas de produção da plataforma. O split de 0,5% é solicitado em pagamentos avulsos e na assinatura de cartão. A integração atual de Pix Automático fica sem esse split.

## 3. WhatsApp da Meta

1. Em **Meta for Developers → Meus apps**, crie o app **Dizipy** com o caso de uso WhatsApp. Comece com o número de teste e somente seu próprio telefone como destinatário autorizado.
2. No Dizipy, em **Plataforma**, configure o ID do número, número remetente, versão da API, token e App Secret. Mantenha os valores secretos nos próprios painéis.
3. Na Meta, configure o callback `https://SEU-DOMINIO.onrender.com/webhooks/whatsapp` com o token de verificação mostrado em **Plataforma**, e assine o campo `messages` da conta de WhatsApp vinculada. Valide a recepção antes de habilitar envios no Dizipy.
4. Para validar o lembrete real, configure um modelo aprovado com os quatro parâmetros descritos em Plataforma, confirme o opt-in do seu telefone e use somente um cadastro de teste. O número definitivo será do Dizipy.

O plano Free do Render pode suspender o serviço sem tráfego. Webhooks e lembretes podem atrasar. Para entrega periódica mesmo com o site suspenso, configure um Cron Job com `python jobs.py` e as mesmas chaves e banco. O Cron é opcional e tem cobrança própria; não é criado pelo Blueprint. Os lembretes agendados dependem de um serviço ativo ou de uma tarefa programada.
