# Dizipy

Primeira versão local para testar o painel da paróquia e a jornada de contribuição simulada. Use nomes, e-mails e senhas de teste. A aplicação roda com SQLite e funciona sem chaves de API.

## Rodar no Windows

Na pasta do projeto, no PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m flask --app app run
```

Abra http://127.0.0.1:5000. No Linux ou macOS, use `python3 -m venv .venv` e troque `.\.venv\Scripts\python.exe` por `.venv/bin/python`.

## Testar no celular

1. Na sua rede Wi-Fi privada, rode o mesmo comando Flask com `--host 0.0.0.0`.
2. No Windows, use `ipconfig` para achar o IPv4 do computador na rede Wi-Fi. Abra o painel pelo endereço da rede, por exemplo `http://192.168.1.20:5000`, para que o QR Code contenha um endereço que o celular alcance.
3. Cadastre uma paróquia fictícia, escaneie o QR Code com o celular na mesma rede, escolha o valor e simule a confirmação.
4. Confira a contribuição no painel e exporte o CSV. No modo de teste, o valor líquido desconta só a taxa simulada da plataforma.

## Testar Pix no Asaas Sandbox

Na pasta do projeto, pare o Flask com `Ctrl+C` e atualize as dependências. Digite a chave do Sandbox somente quando o PowerShell pedir. Ela fica disponível aos processos iniciados nessa janela.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:ASAAS_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Chave do Asaas Sandbox' -AsSecureString)).Password
$env:ASAAS_CUSTOMER_ID = .\.venv\Scripts\python.exe -m flask --app app asaas-cliente-teste
.\.venv\Scripts\python.exe -m flask --app app run --host 0.0.0.0
```

O comando `asaas-cliente-teste` cria um cadastro fictício no Sandbox com as notificações desabilitadas. Execute-o uma vez e guarde o identificador `cus_...` se quiser reutilizá-lo. Em outra sessão, defina `$env:ASAAS_CUSTOMER_ID = 'cus_...'` com o identificador guardado.

1. Abra o link de contribuição no celular pela rede privada e escolha **Pix avulso · Asaas Sandbox**. Digite um valor de teste, como `10,00`, e clique em **Gerar Pix de teste**.
2. No painel do Asaas Sandbox, abra a cobrança usando a referência `pay_...` mostrada no Dizipy e simule a confirmação do pagamento.
3. No Dizipy, toque em **Consultar pagamento**. Quando a API indicar o recebimento, atualize o painel da paróquia para ver **Pago**. Os totais e o CSV passam a incluir essa contribuição, com a mesma taxa simulada do protótipo.

A URL da API aponta apenas para o Sandbox. O Pix usa um cliente fictício compartilhado neste ambiente de teste. A consulta manual continua disponível.

## Testar a confirmação automática por webhook

Use o Asaas Sandbox e mantenha a chave da API e o ID de cliente já configurados. No mesmo PowerShell, pare o Flask com `Ctrl+C` e rode na pasta do projeto:

```powershell
$env:ASAAS_WEBHOOK_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
Start-Process -FilePath (Resolve-Path .\.venv\Scripts\python.exe).Path -ArgumentList '-m flask --app app:create_webhook_app run --host 127.0.0.1 --port 5001 --no-debugger --no-reload'
```

Com o [ngrok para Windows](https://ngrok.com/download/windows) instalado e configurado com o token da sua conta ngrok, abra outro PowerShell e rode:

```powershell
ngrok http http://127.0.0.1:5001
```

O túnel expõe o receptor da porta 5001. O painel e o checkout continuam na porta 5000 da rede local. No Asaas Sandbox, vá em **Menu do usuário → Integrações → Webhooks → Criar Webhook**.

- Em **URL**, use o endereço HTTPS do ngrok com `/webhooks/asaas` no final.
- Depois de preencher a URL, rode `Set-Clipboard -Value $env:ASAAS_WEBHOOK_TOKEN` no PowerShell original e cole o valor em **Auth Token**. É um token próprio do webhook.
- Preencha o nome e o e-mail de alerta, mantenha o webhook e a fila ativos e selecione os eventos `PAYMENT_RECEIVED` e `PAYMENT_CONFIRMED`.

Salve o webhook e inicie o painel no PowerShell original:

```powershell
.\.venv\Scripts\python.exe -m flask --app app run --host 0.0.0.0
```

Crie uma cobrança Pix nova no Dizipy e simule a confirmação no Asaas. Com a página de pagamento ou o painel aberto, aguarde alguns segundos até aparecer **Pago**. As telas consultam o banco local. Se você estiver editando as preferências do painel, salve ou recarregue a página para ver a mudança. Nos **Logs de Webhooks** do Asaas, a entrega correspondente deve retornar HTTP 200.

Ao encerrar o teste, desative o webhook temporário no Asaas e feche o túnel. Para reutilizá-lo, mantenha o mesmo token configurado no Asaas e em `ASAAS_WEBHOOK_TOKEN`, e atualize a URL se ela mudar.

## Testar cartão avulso no Sandbox

Antes de atualizar, pare os dois processos Flask (painel na porta 5000 e receptor na porta 5001). Atualize a branch e reinicie cada processo na mesma janela em que suas variáveis já estão configuradas. A migração do banco local preserva as cobranças Pix existentes.

1. No webhook do Asaas Sandbox, mantenha `PAYMENT_RECEIVED` marcado e acrescente `PAYMENT_CONFIRMED`. Salve.
2. No link de contribuição, selecione **Cartão avulso · Asaas Sandbox**, informe um valor de teste e clique em **Gerar pagamento com cartão**.
3. Abra **Pagar com cartão no Asaas Sandbox**. A fatura abre em outra aba. Use somente dados fictícios e um dos [cartões oficiais de teste do Asaas](https://docs.asaas.com/docs/testando-pagamento-com-cart%C3%A3o-de-cr%C3%A9dito).
4. Para testar aprovação, use `4539656048392220`, validade futura, CCV `123` e nome fictício. Para testar recusa em outra cobrança, use `5184019740373151`.
5. Volte à aba do Dizipy para acompanhar a confirmação automática. O painel e o CSV incluem somente as contribuições confirmadas. Uma recusa deve manter a contribuição pendente.

O cartão avulso é confirmado quando o Asaas informar `CONFIRMED` ou `RECEIVED`. A confirmação do pagamento e a disponibilidade do saldo são etapas distintas. O Pix continua exigindo `RECEIVED`. O líquido do painel permanece simulado e desconta somente a taxa Dizipy do protótipo.

A fatura de teste aceita somente um endereço HTTPS do domínio `sandbox.asaas.com`. Os dados do cartão são preenchidos no Asaas. O cliente fictício configurado no Sandbox é reutilizado neste protótipo.

## Escopo desta versão

O QR Code abre o simulador web. Sem as variáveis do Asaas, as quatro formas usam o simulador local. Com o Sandbox configurado, Pix avulso e cartão avulso usam a API do Asaas. O painel salva o dia e horário do lembrete, mas o envio ainda não está ligado.

O próximo passo inclui conectar Meta e IA, adicionar permissões por colaborador, ativação após verificação da conta de recebimento e execução dos lembretes com a regra de contato mensal. O piloto usa PostgreSQL no Neon e um workspace separado no Render. Esta versão local é exclusivamente para testes fictícios.
