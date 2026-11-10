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

## Escopo desta versão

O QR Code abre o simulador web. As quatro formas são opções de teste e geram apenas um comprovante simulado, inclusive quando o usuário escolhe um meio recorrente. O painel salva o dia e horário do lembrete, mas o envio ainda não está ligado.

O próximo passo inclui conectar Meta, Asaas e IA, adicionar permissões por colaborador, ativação após verificação da conta de recebimento e execução dos lembretes com a regra de contato mensal. O piloto usa PostgreSQL no Neon e um workspace separado no Render. Esta versão local é exclusivamente para testes fictícios.
