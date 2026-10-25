"""Comandos de administração do sistema EAJ.

  python manage.py setup            cria .env (chave secreta aleatória), banco, dados iniciais e frames
  python manage.py create-admin     cria um administrador (a senha é digitada, nunca gravada no código)
  python manage.py prepare-frames   gera os quadros da loja a partir do vídeo (ou das imagens) da pasta FRAMES VIDEO
                                   opção --fps N para usar menos quadros por segundo
  python manage.py migrate          aplica migrations pendentes
  python manage.py run              servidor de desenvolvimento com recarga automática
  python manage.py test             roda os testes
"""
import argparse
import getpass
import json
import os
import re
import secrets
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV = BASE / '.env'


def ensure_env():
    if ENV.exists():
        return False
    example = (BASE / '.env.example').read_text(encoding='utf-8')
    content = re.sub(r'^SECRET_KEY=.*$', 'SECRET_KEY=' + secrets.token_urlsafe(48), example, flags=re.M)
    ENV.write_text(content, encoding='utf-8')
    return True


def app_ctx():
    from eaj import create_app
    app = create_app()
    return app, app.app_context()


def cmd_migrate():
    from eaj.db import get_db, migrate
    app, ctx = app_ctx()
    with ctx:
        applied = migrate(get_db())
    print('Migrations aplicadas:', ', '.join(applied) if applied else 'nenhuma (banco já atualizado)')


def cmd_seed():
    from eaj import seed
    from eaj.db import get_db
    app, ctx = app_ctx()
    with ctx:
        seed.run(get_db())
    print('Dados iniciais conferidos: 2 produtos oficiais, categorias, sócios (50% / 50%) e entrega a combinar.')


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', p.name)]


VIDEO_EXT = ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp')
# versões geradas: lg = tela grande (resolução nativa), md = telas menores e conexões lentas,
# pt = recorte vertical em resolução nativa para celular em pé (evita esticar uma imagem pequena)
VARIANTS = {'lg': (1920, 86), 'md': (960, 80), 'pt': (None, 86)}
PORTRAIT_FOCUS = 0.56          # posição horizontal do recorte vertical (onde está a ação nos frames)


def find_ffmpeg():
    import shutil
    exe = shutil.which('ffmpeg')
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


def extract_video(video, tmp, fps=None):
    import subprocess
    exe = find_ffmpeg()
    if not exe:
        sys.exit('Para extrair quadros de um vídeo instale o ffmpeg ou rode: pip install imageio-ffmpeg')
    cmd = [exe, '-hide_banner', '-loglevel', 'error', '-i', str(video)]
    if fps:
        cmd += ['-vf', f'fps={fps}']
    cmd += ['-vsync', '0', str(Path(tmp) / 'f_%05d.png')]
    subprocess.run(cmd, check=True)
    return sorted(Path(tmp).glob('f_*.png'))


def cmd_prepare_frames(fps=None):
    import tempfile
    from PIL import Image
    from eaj.config import Config
    src = Path(Config.FRAMES_SOURCE)
    videos = sorted([p for p in src.rglob('*') if p.suffix.lower() in VIDEO_EXT], key=lambda p: p.stat().st_mtime, reverse=True)
    tmp = tempfile.TemporaryDirectory(prefix='eaj-frames-')
    if videos:
        print(f'Extraindo quadros do vídeo {videos[0].name}...')
        files = extract_video(videos[0], tmp.name, fps)
        origin = f'vídeo {videos[0].name}'
    else:
        files = sorted([p for p in src.rglob('*') if p.suffix.lower() in IMAGE_EXT], key=natural_key)
        origin = 'imagens da pasta'
    if not files:
        print(f'Nenhum vídeo ou frame encontrado em {src}')
        return
    out = BASE / 'eaj' / 'static' / 'frames'
    for size in ('lg', 'md', 'pt', 'sm'):                     # "sm" é da versão antiga; só é limpo
        (out / size).mkdir(parents=True, exist_ok=True)
        for old in (out / size).glob('*.*'):
            old.unlink()
    (out / 'sm').rmdir()
    manifest = {'count': len(files), 'fps': fps, 'source': origin, **{k: [] for k in VARIANTS}}
    for i, f in enumerate(files, 1):
        im = Image.open(f).convert('RGB')
        if i == 1:
            manifest['width'], manifest['height'] = im.size
        for size, (maxw, q) in VARIANTS.items():
            if size == 'pt':
                w = round(im.height * 9 / 16)
                left = round((im.width - w) * PORTRAIT_FOCUS)
                v = im.crop((left, 0, left + w, im.height))
            else:
                v = im if im.width <= maxw else im.resize((maxw, round(im.height * maxw / im.width)), Image.LANCZOS)
            name = f'frame_{i:04d}.webp'
            v.save(out / size / name, 'WEBP', quality=q, method=5)
            manifest[size].append(f'frames/{size}/{name}')
        if i % 24 == 0 or i == len(files):
            print(f'  {i}/{len(files)}')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    sizes = {k: sum(p.stat().st_size for p in (out / k).glob('*.webp')) / 1048576 for k in VARIANTS}
    print('  tamanho total: ' + ', '.join(f'{k} {v:.1f} MB' for k, v in sizes.items()))

    # Foto do produto: recorte quadrado do último frame, onde aparece a embalagem oficial EAJ.
    last = Image.open(files[-1]).convert('RGB')
    s = min(last.size)
    cx = int(last.width * 0.485)
    left = max(0, min(last.width - s, cx - s // 2))
    # tamanho nativo do recorte: ampliar só perderia nitidez
    last.crop((left, 0, left + s, s)).save(
        BASE / 'eaj' / 'static' / 'img' / 'produto-eaj.jpg', 'JPEG', quality=92, optimize=True, progressive=True)
    tmp.cleanup()
    print(f'{len(files)} quadros preparados a partir de {origin} e foto do produto atualizada.')


def cmd_create_admin(args):
    from eaj.db import get_db, now
    from eaj.security import DEFAULT_PERMS, hash_password, password_problem
    from eaj.services import audit
    app, ctx = app_ctx()
    email = args.email or input('E-mail do administrador: ').strip()
    name = args.name or input('Nome: ').strip()
    role = args.role
    pw = os.environ.get('EAJ_ADMIN_PASSWORD')
    if not pw:
        pw = getpass.getpass('Senha (não aparece enquanto digita): ')
        if pw != getpass.getpass('Repita a senha: '):
            sys.exit('As senhas não conferem.')
    problem = password_problem(pw)
    if problem:
        sys.exit(problem)
    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email or ''):
        sys.exit('E-mail inválido.')
    with ctx:
        db = get_db()
        if db.execute('SELECT 1 FROM users WHERE email = ?', (email,)).fetchone():
            sys.exit('Já existe um usuário com esse e-mail.')
        cur = db.execute('INSERT INTO users (email, name, password_hash, role, permissions, created_at) VALUES (?,?,?,?,?,?)',
                         (email, name or email, hash_password(pw), role, ','.join(DEFAULT_PERMS[role]), now()))
        audit.log('criar_usuario', 'user', cur.lastrowid, user_id=None, via='manage.py', role=role)
    print(f'Usuário {email} criado com perfil {role}.')


def cmd_run(args):
    os.environ['LIVE_RELOAD'] = '1'
    from eaj import create_app
    app = create_app()
    app.run(host=args.host, port=args.port, debug=True, use_reloader=True)


def cmd_test():
    suite = unittest.defaultTestLoader.discover(str(BASE / 'tests'), top_level_dir=str(BASE))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def main():
    p = argparse.ArgumentParser(description='Administração do sistema EAJ Cafés Especiais')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('setup')
    sub.add_parser('migrate')
    sub.add_parser('seed')
    pf = sub.add_parser('prepare-frames')
    pf.add_argument('--fps', type=float, help='quadros por segundo extraídos do vídeo (padrão: todos)')
    ca = sub.add_parser('create-admin')
    ca.add_argument('--email'); ca.add_argument('--name')
    ca.add_argument('--role', default='admin', choices=['admin', 'analista'])
    r = sub.add_parser('run')
    r.add_argument('--host', default='127.0.0.1'); r.add_argument('--port', type=int, default=5000)
    sub.add_parser('test')
    args = p.parse_args()

    if args.cmd == 'setup':
        if ensure_env():
            print('Arquivo .env criado com uma SECRET_KEY aleatória.')
        cmd_migrate(); cmd_seed(); cmd_prepare_frames()
        print('\nPronto. Agora crie o administrador: python manage.py create-admin')
    elif args.cmd == 'migrate':
        cmd_migrate()
    elif args.cmd == 'seed':
        cmd_seed()
    elif args.cmd == 'prepare-frames':
        cmd_prepare_frames(args.fps)
    elif args.cmd == 'create-admin':
        cmd_create_admin(args)
    elif args.cmd == 'run':
        cmd_run(args)
    elif args.cmd == 'test':
        cmd_test()


if __name__ == '__main__':
    main()
