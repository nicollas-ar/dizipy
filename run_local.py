"""Um processo inicia painel, receptor e agenda, usando os mesmos dados locais."""
import os
import sqlite3
import threading
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

from waitress import create_server
from app import create_app, create_webhook_app
from runner import tick


def daily_backup(app):
    folder=Path(app.config['DATABASE']).parent
    backups=folder/'backups'
    backups.mkdir(exist_ok=True)
    target=backups/(datetime.now().strftime('%Y-%m-%d')+'.zip')
    if target.exists():
        return
    temporary=backups/'snapshot.sqlite'
    source=sqlite3.connect(app.config['DATABASE'])
    destination=sqlite3.connect(temporary)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    pending=target.with_suffix('.tmp')
    with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.write(temporary,'dizipy.sqlite')
        for name in ('local.key','session.key'):
            if (folder/name).exists():
                archive.write(folder/name,name)
    temporary.unlink()
    os.replace(pending,target)
    for old in sorted(backups.glob('*.zip'))[:-7]:
        old.unlink()


def main():
    app=create_app()
    try:
        main_server=create_server(app,host='0.0.0.0',port=5000,threads=6)
        hook_server=create_server(create_webhook_app(),host='127.0.0.1',port=5001,threads=4)
    except OSError:
        print('Não foi possível abrir as portas 5000 e 5001. Encerre as janelas antigas do Flask antes de iniciar o Dizipy.')
        raise SystemExit(1)
    daily_backup(app)
    stopped=threading.Event()
    def schedule():
        while not stopped.is_set():
            try:
                tick(app)
            except Exception:
                app.logger.exception('A agenda encontrou uma falha. O próximo ciclo tentará continuar.')
            stopped.wait(15)
    threading.Thread(target=hook_server.run,daemon=True).start()
    threading.Thread(target=schedule,daemon=True).start()
    print('\nDizipy aberto em http://127.0.0.1:5000')
    print('Painel e agenda ativos. O receptor está na porta 5001.')
    print('O modo de demonstração funciona sem ngrok ou contas externas.')
    print('Para testar no celular, use o IPv4 deste computador com :5000.')
    print('Mantenha esta janela aberta. Pressione Ctrl+C para encerrar.\n')
    if os.environ.get('DIZIPY_NO_BROWSER')!='1':
        threading.Timer(1,lambda:webbrowser.open('http://127.0.0.1:5000')).start()
    try:
        main_server.run()
    except KeyboardInterrupt:
        pass
    finally:
        stopped.set()
        main_server.close()
        hook_server.close()


if __name__=='__main__':
    main()
