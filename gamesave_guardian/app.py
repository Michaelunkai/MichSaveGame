
#!/usr/bin/env python3
"""Universal Game Save Guardian: CLI, Tk GUI, and tiny web UI for game save backup/restore."""
from __future__ import annotations
import argparse, base64, dataclasses, datetime as dt, fnmatch, hashlib, html, json, os, re, shutil, sys, tarfile, tempfile, threading, time, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional

APP_NAME = "Universal Game Save Guardian"
DEFAULT_BACKUP_DIR_WIN = r"F:\backup\gamesaves"
MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"
ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MANIFEST = ROOT / "data" / "ludusavi_manifest.yaml"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "UniversalGameSaveGuardian"
CONFIG_FILE = CONFIG_DIR / "config.json"

@dataclasses.dataclass
class Source:
    path: Path
    reason: str
    tags: list[str]
    exists: bool
    file_count: int = 0
    byte_count: int = 0
    latest_write: float = 0.0

@dataclasses.dataclass
class GamePlan:
    game: str
    sources: list[Source]
    manifest_hit: bool
    detected_process: Optional[str] = None


def is_windows() -> bool:
    return os.name == "nt" or Path('/mnt/c/Windows').exists()

def win_to_wsl(path: str) -> str:
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', path)
    if m and os.name != 'nt':
        tail = m.group(2).replace('\\', '/')
        return f"/mnt/{m.group(1).lower()}/{tail}"
    return path

def wsl_to_win(path: str) -> str:
    m = re.match(r'^/mnt/([a-zA-Z])/(.*)$', path)
    if m:
        tail = m.group(2).replace('/', '\\')
        return f"{m.group(1).upper()}:\\{tail}"
    return path

def norm_path(p: str|Path) -> Path:
    return Path(win_to_wsl(str(p))).expanduser()

def load_config() -> dict:
    try: return json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
    except Exception: return {}

def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')

def backup_root() -> Path:
    cfg=load_config()
    return norm_path(cfg.get('backup_dir') or DEFAULT_BACKUP_DIR_WIN)

def set_backup_root(path: str) -> Path:
    p=norm_path(path); p.mkdir(parents=True, exist_ok=True)
    cfg=load_config(); cfg['backup_dir']=wsl_to_win(str(p)) if os.name!='nt' else str(p); save_config(cfg); return p

def safe_slug(text: str) -> str:
    s=re.sub(r'[^A-Za-z0-9._-]+','-',text.strip()).strip('-._')
    return s[:80] or 'game'

def iter_files(paths: Iterable[Path]):
    for root in paths:
        if root.is_file():
            yield root
        elif root.is_dir():
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d.lower() not in {'cache','shadercache','crashpad','logs','log','temp','tmp','__pycache__'}]
                for f in files:
                    yield Path(dirpath)/f

def summarize_path(p: Path) -> tuple[int,int,float]:
    count=size=0; latest=0.0
    if not p.exists(): return 0,0,0.0
    for f in iter_files([p]):
        try:
            st=f.stat(); count+=1; size+=st.st_size; latest=max(latest, st.st_mtime)
        except OSError: pass
    return count,size,latest

def placeholders() -> dict[str,str]:
    home = Path.home()
    userprofile = Path(os.environ.get('USERPROFILE') or win_to_wsl(r'C:\Users\micha'))
    docs = Path(os.environ.get('USERPROFILE', win_to_wsl(r'C:\Users\micha'))) / 'Documents'
    public = norm_path(r'C:\Users\Public\Documents')
    appdata = Path(os.environ.get('APPDATA') or (userprofile/'AppData/Roaming'))
    local = Path(os.environ.get('LOCALAPPDATA') or (userprofile/'AppData/Local'))
    locallow = userprofile/'AppData/LocalLow'
    saved = userprofile/'Saved Games'
    return {
        '<home>': str(home), '<winAppData>': str(appdata), '<winLocalAppData>': str(local),
        '<winLocalAppDataLow>': str(locallow), '<winDocuments>': str(docs), '<winPublic>': str(public),
        '<winSavedGames>': str(saved), '<base>': '', '<root>': '', '<storeUserId>': '*'
    }

def steam_roots() -> list[Path]:
    roots=[]
    for raw in [r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam', r'D:\Steam', r'E:\Steam', r'F:\Steam']:
        p=norm_path(raw)
        if p.exists(): roots.append(p)
    # Steam library folders
    for r in list(roots):
        f=r/'steamapps'/'libraryfolders.vdf'
        if f.exists():
            txt=f.read_text(errors='ignore')
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                pp=norm_path(m.group(1).replace('\\\\','\\'))
                if pp.exists() and pp not in roots: roots.append(pp)
    return roots

def load_manifest_text() -> str:
    if BUNDLED_MANIFEST.exists() and BUNDLED_MANIFEST.stat().st_size > 1000:
        return BUNDLED_MANIFEST.read_text(encoding='utf-8', errors='replace')
    import urllib.request
    txt=urllib.request.urlopen(MANIFEST_URL, timeout=30).read().decode('utf-8', errors='replace')
    BUNDLED_MANIFEST.parent.mkdir(parents=True, exist_ok=True); BUNDLED_MANIFEST.write_text(txt, encoding='utf-8')
    return txt

def manifest_block(game: str) -> tuple[Optional[str], list[str]]:
    txt=load_manifest_text()
    # exact and loose case-insensitive title block from YAML top-level
    titles=[game, game.title(), game.replace('_',' '), game.replace('-',' ')]
    lines=txt.splitlines()
    for i,line in enumerate(lines):
        if not line.startswith(' ') and line.strip().rstrip(':').strip('"').lower() in {t.lower() for t in titles}:
            block=[]
            for j in range(i+1, len(lines)):
                if lines[j] and not lines[j].startswith(' '): break
                block.append(lines[j])
            return line.strip().rstrip(':').strip('"'), block
    # substring fallback
    low=game.lower()
    for i,line in enumerate(lines):
        if not line.startswith(' ') and low in line.strip().rstrip(':').strip('"').lower():
            block=[]
            for j in range(i+1, len(lines)):
                if lines[j] and not lines[j].startswith(' '): break
                block.append(lines[j])
            return line.strip().rstrip(':').strip('"'), block
    return None, []

def extract_manifest_sources(game: str) -> tuple[str, list[tuple[str,list[str]]], Optional[str]]:
    title, block = manifest_block(game)
    if not block: return game, [], None
    paths=[]; appid=None; in_files=False; cur_tags=[]
    for line in block:
        if re.match(r'^  files:\s*$', line): in_files=True; continue
        if re.match(r'^  [a-zA-Z].*:\s*$', line) and not line.startswith('    '): in_files=False
        if in_files:
            m=re.match(r'^    "?([^":]+(?:/[^"]*)?)"?:\s*$', line)
            if m and '<' in m.group(1):
                paths.append((m.group(1), []))
        sm=re.match(r'^    id:\s*(\d+)', line)
        if sm: appid=sm.group(1)
    return title or game, paths, appid

def expand_manifest_path(pattern: str, game: str, appid: Optional[str], install_dir: Optional[Path]=None) -> list[Path]:
    ph=placeholders(); out=pattern
    if install_dir:
        ph['<base>']=str(install_dir); ph['<root>']=str(install_dir.parent)
    expanded=[]
    if '<root>' in out and appid:
        for sr in steam_roots():
            expanded.append(out.replace('<root>', str(sr)).replace('<storeUserId>', '*'))
    else:
        expanded=[out]
    results=[]
    for item in expanded:
        for k,v in ph.items(): item=item.replace(k,v)
        item=win_to_wsl(item)
        if '*' in item:
            results.extend(Path(p) for p in fnmatch.filter([str(x) for x in Path(item).parent.parent.glob('*/*')], item) if Path(p).exists())
            results.extend(Path(p) for p in Path('/').glob(item.lstrip('/')) if Path(p).exists()) if item.startswith('/mnt/') else None
        else:
            results.append(Path(item))
    return list(dict.fromkeys(results))

def detect_running_game() -> tuple[Optional[str], Optional[Path]]:
    if os.name == 'nt':
        try:
            import subprocess
            out=subprocess.check_output(['powershell','-NoProfile','-Command','Get-Process | ? {$_.Path -and ($_.Path -match "games|steam|GOG|Epic") -and ($_.ProcessName -notmatch "steam|epic|gog")} | Select -First 1 ProcessName,Path | ConvertTo-Json -Compress'], text=True, timeout=8)
        except Exception: return None,None
    else:
        ps=Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
        if not ps.exists(): return None,None
        import subprocess
        try:
            out=subprocess.check_output([str(ps),'-NoProfile','-Command','Get-Process | ? {$_.Path -and ($_.Path -match "games|steam|GOG|Epic") -and ($_.ProcessName -notmatch "steam|epic|gog")} | Select -First 1 ProcessName,Path | ConvertTo-Json -Compress'], text=True, timeout=8)
        except Exception: return None,None
    try:
        obj=json.loads(out) if out.strip() else None
        if isinstance(obj, list): obj=obj[0] if obj else None
        if obj: return obj.get('ProcessName'), norm_path(obj.get('Path')).parent
    except Exception: pass
    return None,None

def extra_crack_sources(install_dir: Optional[Path]) -> list[Path]:
    res=[]
    if not install_dir: return res
    ini=install_dir/'steam_emu.ini'
    if ini.exists():
        txt=ini.read_text(errors='ignore')
        m=re.search(r'Game data is stored at\s+([^\r\n]+)', txt)
        if m:
            raw=m.group(1).strip().replace('%SystemDrive%', 'C:')
            res.append(norm_path(raw))
    return res

def heuristic_sources(game: str, install_dir: Optional[Path]=None) -> list[Path]:
    terms=[t for t in re.split(r'[^A-Za-z0-9]+', game) if len(t)>2]
    variants={game, game.replace(' ','') , game.replace(' ','_'), game.replace(' ','-')}
    if install_dir: variants.add(install_dir.name)
    roots=[Path.home()/ 'Documents'/'My Games', Path.home()/'Saved Games', Path.home()/'AppData/Local', Path.home()/'AppData/LocalLow', Path.home()/'AppData/Roaming', norm_path(r'C:\Users\Public\Documents\Steam')]
    if os.name!='nt':
        roots=[norm_path(str(r)) for r in roots]
    found=[]
    for root in roots:
        if not root.exists(): continue
        try:
            for dirpath, dirs, files in os.walk(root):
                depth=len(Path(dirpath).relative_to(root).parts)
                if depth>5: dirs[:] = []
                low=dirpath.lower()
                if any(v.lower() in low for v in variants) or all(t.lower() in low for t in terms[:2]):
                    found.append(Path(dirpath)); dirs[:] = []
        except Exception: pass
    return found

def build_plan(game: Optional[str]=None) -> GamePlan:
    proc, install_dir = detect_running_game()
    target = game or proc or ''
    if not target and install_dir: target=install_dir.name
    if not target: raise SystemExit('No game specified and no running game process detected. Pass --game "Game Name".')
    # prettify process name
    if target and re.match(r'^[A-Za-z0-9]+$', target):
        target=re.sub(r'(?<!^)([A-Z])', r' \1', target).strip()
    title, manifest_paths, appid = extract_manifest_sources(target)
    raw=[]
    for pat,tags in manifest_paths:
        raw.extend((p, 'ludusavi manifest: '+pat, tags) for p in expand_manifest_path(pat,title,appid,install_dir))
    raw.extend((p, 'Steam/RUNE emulator save path from steam_emu.ini', ['save']) for p in extra_crack_sources(install_dir))
    raw.extend((p, 'heuristic save root match', ['save','heuristic']) for p in heuristic_sources(title, install_dir))
    dedup={}
    for p,reason,tags in raw:
        dedup[str(p.resolve() if p.exists() else p)] = (p,reason,tags)
    sources=[]
    for p,reason,tags in dedup.values():
        c,b,l=summarize_path(p)
        sources.append(Source(p,reason,tags,p.exists(),c,b,l))
    sources.sort(key=lambda s: (not s.exists, -s.latest_write, -s.byte_count, str(s.path).lower()))
    return GamePlan(title or target, sources, bool(manifest_paths), proc)

def safe_copy_file(src: Path, dst: Path) -> None:
    """Copy file contents and preserve metadata when the filesystem allows it.

    Windows-mounted drives in WSL sometimes reject chmod/utime metadata writes
    during shutil.copy2. In that case, keep the important payload bytes by
    falling back to copyfile.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        shutil.copyfile(src, dst)

def copy_tree(src: Path, dst: Path) -> None:
    if src.is_file():
        safe_copy_file(src,dst); return
    for f in iter_files([src]):
        rel=f.relative_to(src); target=dst/rel; safe_copy_file(f,target)

def backup(game: Optional[str], destination: Optional[str], include_missing=False) -> Path:
    plan=build_plan(game)
    root=norm_path(destination) if destination else backup_root(); root.mkdir(parents=True, exist_ok=True)
    ts=dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    out=root/f"{safe_slug(plan.game)}-{ts}"
    payload=out/'payload'; payload.mkdir(parents=True)
    entries=[]
    for i,s in enumerate([x for x in plan.sources if x.exists and x.file_count>0] or ([x for x in plan.sources if x.exists] if include_missing else [])):
        label=f"source-{i+1:02d}-{safe_slug(s.path.name or 'root')}"
        dst=payload/label
        copy_tree(s.path,dst)
        entries.append(dataclasses.asdict(s) | {'path': str(s.path), 'payload': str(dst.relative_to(out)), 'restore_to': wsl_to_win(str(s.path))})
    meta={'app':APP_NAME,'version':'1.0.0','game':plan.game,'created_at':dt.datetime.now().isoformat(),'detected_process':plan.detected_process,'manifest_hit':plan.manifest_hit,'sources':entries}
    (out/'backup_manifest.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    with tarfile.open(out.with_suffix('.tar.gz'), 'w:gz') as tar: tar.add(out, arcname=out.name)
    return out

def restore_target_for(original: str, target_root: Optional[str]) -> Path:
    if not target_root:
        return norm_path(original)
    root = norm_path(target_root)
    # Recreate a portable drive/root-relative layout for restoring onto another PC.
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', original)
    if m:
        return root / m.group(1).upper() / Path(m.group(2).replace('\\', '/'))
    p = Path(original)
    if p.is_absolute():
        parts = [part for part in p.parts if part not in ('/', '\\')]
        return root.joinpath(*parts)
    return root / p

def restore(backup_dir: str, target_root: Optional[str]=None, dry_run=False) -> list[str]:
    b=norm_path(backup_dir); meta=json.loads((b/'backup_manifest.json').read_text(encoding='utf-8'))
    actions=[]
    safety_root=b/('restore-safety-backup-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S'))
    for src in meta.get('sources',[]):
        payload=b/src['payload']
        dest=restore_target_for(src['restore_to'], target_root)
        actions.append(f"{payload} -> {dest}")
        if dry_run: continue
        if dest.exists(): copy_tree(dest, safety_root/safe_slug(str(dest)))
        if dest.is_file(): dest.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        copy_tree(payload,dest)
    return actions

def cmd_discover(args):
    plan=build_plan(args.game)
    print(json.dumps({'game':plan.game,'running_process':plan.detected_process,'manifest_hit':plan.manifest_hit,'sources':[dataclasses.asdict(s)|{'path':str(s.path),'latest_write_iso':dt.datetime.fromtimestamp(s.latest_write).isoformat() if s.latest_write else None} for s in plan.sources]}, indent=2))

def cmd_backup(args): print(str(backup(args.game, args.destination)))
def cmd_restore(args): print('\n'.join(restore(args.backup, args.target_root, args.dry_run)))
def cmd_config(args):
    if args.set_default: print(set_backup_root(args.set_default))
    else: print(json.dumps(load_config() | {'effective_backup_dir': str(backup_root())}, indent=2))

def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    win=tk.Tk(); win.title(APP_NAME); win.geometry('850x620')
    game=tk.StringVar(); dest=tk.StringVar(value=wsl_to_win(str(backup_root()))); backup_path=tk.StringVar()
    log=scrolledtext.ScrolledText(win); log.pack(side='bottom', fill='both', expand=True)
    def say(x): log.insert('end', x+'\n'); log.see('end')
    frm=tk.Frame(win); frm.pack(fill='x', padx=8, pady=8)
    tk.Label(frm,text='Game (blank = running game)').grid(row=0,column=0,sticky='w'); tk.Entry(frm,textvariable=game,width=55).grid(row=0,column=1,sticky='ew')
    tk.Label(frm,text='Default backup folder').grid(row=1,column=0,sticky='w'); tk.Entry(frm,textvariable=dest,width=55).grid(row=1,column=1,sticky='ew')
    tk.Button(frm,text='Browse',command=lambda: dest.set(filedialog.askdirectory() or dest.get())).grid(row=1,column=2)
    tk.Label(frm,text='Backup folder to restore').grid(row=2,column=0,sticky='w'); tk.Entry(frm,textvariable=backup_path,width=55).grid(row=2,column=1,sticky='ew')
    tk.Button(frm,text='Browse',command=lambda: backup_path.set(filedialog.askdirectory() or backup_path.get())).grid(row=2,column=2)
    def do_discover():
        try: say(json.dumps(dataclasses.asdict(build_plan(game.get() or None)), default=str, indent=2))
        except Exception as e: messagebox.showerror('Discover failed', str(e))
    def do_backup():
        try: set_backup_root(dest.get()); p=backup(game.get() or None, dest.get()); backup_path.set(str(p)); say('Backup created: '+str(p))
        except Exception as e: messagebox.showerror('Backup failed', str(e))
    def do_restore():
        try: say('\n'.join(restore(backup_path.get(), None, False))); say('Restore complete')
        except Exception as e: messagebox.showerror('Restore failed', str(e))
    for text,cmd in [('Discover saves',do_discover),('Create backup',do_backup),('Restore selected backup',do_restore)]: tk.Button(frm,text=text,command=cmd).grid(sticky='ew', pady=4)
    win.mainloop()

class Web(BaseHTTPRequestHandler):
    def do_GET(self):
        qs={}
        if '?' in self.path:
            from urllib.parse import parse_qs, urlparse
            qs=parse_qs(urlparse(self.path).query)
        game=qs.get('game',[''])[0]
        body=f"<h1>{APP_NAME}</h1><form><input name=game placeholder='Game name, blank=running'><button>Discover</button></form>"
        if game or self.path.startswith('/discover'):
            try: body += '<pre>'+html.escape(json.dumps(dataclasses.asdict(build_plan(game or None)), default=str, indent=2))+'</pre>'
            except Exception as e: body += '<pre>'+html.escape(str(e))+'</pre>'
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body.encode())

def serve(port:int):
    http=ThreadingHTTPServer(('127.0.0.1', port), Web); print(f'Web UI http://127.0.0.1:{port}'); webbrowser.open(f'http://127.0.0.1:{port}'); http.serve_forever()

def main(argv=None):
    p=argparse.ArgumentParser(description=APP_NAME)
    sub=p.add_subparsers(required=True)
    d=sub.add_parser('discover'); d.add_argument('--game'); d.set_defaults(func=cmd_discover)
    b=sub.add_parser('backup'); b.add_argument('--game'); b.add_argument('--destination'); b.set_defaults(func=cmd_backup)
    r=sub.add_parser('restore'); r.add_argument('backup'); r.add_argument('--target-root'); r.add_argument('--dry-run', action='store_true'); r.set_defaults(func=cmd_restore)
    c=sub.add_parser('config'); c.add_argument('--set-default'); c.set_defaults(func=cmd_config)
    g=sub.add_parser('gui'); g.set_defaults(func=lambda a: launch_gui())
    w=sub.add_parser('web'); w.add_argument('--port',type=int,default=8765); w.set_defaults(func=lambda a: serve(a.port))
    args=p.parse_args(argv); args.func(args)
if __name__=='__main__': main()
