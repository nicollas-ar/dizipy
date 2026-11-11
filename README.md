# Dizipy — versão local integrada

Painel para paróquias, fiéis, contribuições e lembretes. Esta versão permite testar a experiência inteira em demonstração, sem ngrok e sem movimentar dinheiro. Ainda não é a versão homologada para uso real pela paróquia.

## Abrir no Windows

1. Encerre as janelas antigas do Flask antes de atualizar. Faça uma cópia da pasta `instance` se já tiver dados de teste importantes.
2. Atualize o código da branch `asaas-sandbox`.
3. Dê dois cliques em **iniciar.bat** dentro da pasta do projeto. Python deve estar instalado. A primeira execução instala as dependências e precisa de internet.
4. O navegador abre em http://127.0.0.1:5000. Mantenha a janela do inicializador aberta durante o uso.

O inicializador sobe o painel, o receptor de eventos e a agenda juntos. Para encerrar, pressione Ctrl+C. Se as portas 5000 ou 5001 estiverem ocupadas, encerre os processos antigos antes de tentar novamente.

No Linux/macOS: crie `.venv`, instale `requirements.txt` e execute `.venv/bin/python run_local.py`.

## Primeiro percurso completo

- Cadastre uma paróquia fictícia ou entre com o cadastro local antigo. A primeira conta proprietária administra também a plataforma.
- Em **Plataforma**, mantenha contribuição e WhatsApp em demonstração.
- Abra o link público da paróquia. Teste Pix avulso, cartão avulso, cartão mensal e Pix Automático mensal. Os botões de simulação permitem acompanhar pagamento e falha sem conta bancária.
- Nas formas mensais, confirme a autorização. Abra o link pessoal do fiel para conferir histórico e cancelar a recorrência.
- Em **Recorrências**, avance o próximo ciclo de demonstração. O calendário real não precisa ser alterado.
- Cadastre fiéis com consentimento para lembretes. Em **Lembretes**, edite mensagem, dia e horário e processe o mês. Veja as mensagens simuladas; repetir o processamento não duplica o lembrete mensal.
- Confira o painel financeiro, filtros e CSV. Crie um colaborador com permissões limitadas e confira seu acesso.
- Feche e reabra: dados, configurações e sessões persistem no computador.

No celular, use o IPv4 do computador com `http://IP:5000`, na mesma rede privada. Abra o painel por esse endereço antes de compartilhar seu link. O Windows pode pedir autorização de rede privada.

## O que está incluído

Cadastro de paróquias, conta proprietária, colaboradores com permissões, cadastro de fiéis, portal pessoal, quatro jornadas de contribuição, recorrências, cancelamentos, histórico e exportação. A taxa prevista do Dizipy é 0,5%, com valores arredondados em centavos. O painel distingue previsão e tarifas retornadas pelo provedor.

O receptor guarda eventos antes de responder e a agenda os processa com verificação no Asaas. Há deduplicação de eventos, cobranças por período e mensagens por mês; falhas podem ser revisadas em Atividade. O acesso é separado por paróquia, formulários usam CSRF e configurações secretas ficam cifradas no disco.

## Conectores externos: validação ainda necessária

**Asaas Sandbox:** cada proprietário configura sua chave em Integrações e verifica a situação da conta. A aprovação geral é consultada no Asaas antes de liberar novas cobranças de teste. A habilitação de Pix Automático depende da conta. Em Plataforma, mude novas contribuições para Sandbox; os registros antigos conservam o ambiente em que foram criados.

O painel escuta na porta 5000 e o receptor na 5001. Para homologar eventos externos, exponha apenas o receptor, por exemplo com um túnel HTTPS apontando para `http://127.0.0.1:5001`. Configure o endereço do receptor em Plataforma e copie a URL completa e o token individual em Integrações. Cadastre eventos de cobrança, assinatura, autorização Pix Automático e situação cadastral. Mantenha aplicativo e túnel abertos.

O endereço público do aplicativo é separado do receptor: links de contribuição enviados ao fiel devem apontar para o painel, não para a porta exclusiva de webhooks.

Pix avulso já foi validado anteriormente em Sandbox. As jornadas novas de cartão mensal e Pix Automático estão implementadas, mas precisam de homologação externa: autorização bancária, pagamento inicial, cobrança seguinte, cancelamento e estorno. Um Pix Automático sem vínculo conhecido é sinalizado em Atividade para conciliação, sem inventar confirmação financeira.

O Split é solicitado para avulsos e assinaturas de cartão quando há carteira da plataforma configurada. **O primeiro pagamento por QR do Pix Automático não suporta Split na integração documentada.** A cobrança efetiva da taxa nesse caso permanece pendente de solução e homologação; a previsão local não representa transferência realizada.

**WhatsApp:** utiliza um número empresarial central do Dizipy e a API oficial da Meta. A demonstração não envia mensagens. Para envio externo, configure número, ID, token, App Secret, versão da API e modelo aprovado em Plataforma. O modelo usa quatro parâmetros de corpo: nome do fiel, paróquia, texto configurado e link. A aprovação desse conteúdo precisa ser validada com a Meta.

O webhook Meta é `/webhooks/whatsapp`, com verificação GET e assinatura HMAC nas notificações. Para receber lembretes reais, o fiel deve consentir e confirmar o telefone enviando `ATIVAR` com seu código pelo link pessoal. `SAIR` cancela os lembretes do número. Uma resposta incerta de envio não é reenviada automaticamente, evitando duplicidade. Credenciais, aprovação do modelo e entrega real ainda precisam ser homologadas.

## Dados e cópias de segurança

Os dados ficam em `instance`; mantenha essa pasta fora do Git. As chaves `local.key` e `session.key` devem acompanhar o banco: sem elas, configurações cifradas não podem ser recuperadas. Não compartilhe backups com terceiros.

O inicializador faz uma cópia diária e mantém as sete últimas em `instance/backups`. O administrador da plataforma pode baixar um backup completo. Para restaurar, encerre o aplicativo, guarde a pasta atual e extraia banco e chaves na pasta de dados, preservando o nome do banco usado pela instalação. Consulte `DIZIPY_DATABASE` se estiver usando caminho personalizado. Nunca restaure com o servidor aberto.

O esquema anterior é migrado preservando cadastros e contribuições. Antes da primeira atualização, mantenha uma cópia independente dos dados antigos.

## Verificação de desenvolvimento

Execute `.venv/bin/python -m unittest discover -s tests -v` (Windows: `.venv\Scripts\python.exe`). Os testes cobrem quatro jornadas de demonstração, recorrência, cancelamento, persistência, migração, permissões, isolamento, CSRF, agenda, consentimento e eventos Sandbox com respostas simuladas. Testes simulados não substituem homologação com os provedores.

Antes de disponibilizar à paróquia: concluir homologação dos quatro meios e WhatsApp, definir cobrança efetiva da taxa, preparar hospedagem e banco de produção, proteger credenciais, validar restauração de backup e realizar piloto acompanhado. Produção permanece desabilitada nesta versão.
