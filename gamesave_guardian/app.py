
#!/usr/bin/env python3
"""MichSaveGame: premium local CLI and browser app for save backup, restore, and cleanup."""
from __future__ import annotations
import argparse, base64, dataclasses, datetime as dt, fnmatch, hashlib, html, json, os, queue, re, secrets, shutil, socket, sys, tarfile, tempfile, threading, time, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable, Optional

APP_NAME = "MichSaveGame"
DEFAULT_BACKUP_DIR_WIN = r"F:\backup\gamesaves"
MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"
ROOT = Path(__file__).resolve().parents[1]
BUNDLED_MANIFEST = ROOT / "data" / "ludusavi_manifest.yaml"
CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "MichSaveGame"
CONFIG_FILE = CONFIG_DIR / "config.json"
API_TOKEN = secrets.token_urlsafe(24)

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


def human_size(n:int)->str:
    units=['B','KB','MB','GB','TB']; v=float(n)
    for u in units:
        if v<1024 or u==units[-1]: return f'{v:.1f} {u}' if u!='B' else f'{int(v)} B'
        v/=1024

def iso(ts:float)->str|None:
    return dt.datetime.fromtimestamp(ts).isoformat(timespec='seconds') if ts else None

def visible_drives()->list[Path]:
    if os.name=='nt':
        return [Path(f'{c}:\\') for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if Path(f'{c}:\\').exists()]
    return [p for p in Path('/mnt').iterdir() if p.is_dir() and len(p.name)==1] if Path('/mnt').exists() else []

def windows_profiles()->list[Path]:
    roots=[]; env=os.environ.get('USERPROFILE')
    if env: roots.append(norm_path(env))
    users=norm_path(r'C:\Users')
    if users.exists():
        roots += [p for p in users.iterdir() if p.is_dir() and p.name.lower() not in {'public','default','default user','all users'}]
    micha=norm_path(r'C:\Users\micha')
    if micha.exists(): roots.append(micha)
    seen=set(); out=[]
    for p in roots:
        k=str(p).lower()
        if p.exists() and k not in seen: seen.add(k); out.append(p)
    return out

def all_save_roots()->list[Path]:
    roots=[]
    for u in windows_profiles():
        roots += [u/'Documents', u/'Documents'/'My Games', u/'Saved Games', u/'AppData'/'Roaming', u/'AppData'/'Local', u/'AppData'/'LocalLow', u/'OneDrive'/'Documents', u/'OneDrive'/'Saved Games']
    roots += [norm_path(r'C:\Users\Public\Documents'), norm_path(r'C:\ProgramData')]
    for s in steam_roots(): roots += [s/'userdata', s/'steamapps'/'compatdata']
    seen=set(); out=[]
    for r in roots:
        k=str(r).lower()
        if r.exists() and k not in seen: seen.add(k); out.append(r)
    return out

def save_like(files:list[str], dirname:str='')->bool:
    exts={'.sav','.save','.slot','.profile','.sgf','.ess','.rpgsave','.sol'}
    low=dirname.lower()
    if low in {'save','saves','saved games','savegames','savegame','savedata','save data'}: return True
    # Folders named "Profiles", "Remote" and "User Data" are common in browsers,
    # launchers and developer tools, so only treat them as saves when the payload
    # itself contains save-shaped files.
    return any(Path(f).suffix.lower() in exts or 'save' in f.lower() for f in files[:80])

def source_score(s:Source)->int:
    score=30; low=str(s.path).lower(); reason=s.reason.lower()
    if 'manifest' in reason: score+=45
    if any(x in low for x in ['saved games','my games','appdata','userdata','remote','steam/rune']): score+=20
    if s.file_count: score+=10
    if s.latest_write and s.latest_write>time.time()-120*86400: score+=10
    if s.byte_count>5*1024*1024*1024: score-=20
    return max(1,min(100,score))

def source_json(s:Source)->dict:
    return dataclasses.asdict(s)|{'path':str(s.path),'path_windows':wsl_to_win(str(s.path)),'latest_write_iso':iso(s.latest_write),'size_human':human_size(s.byte_count),'confidence':source_score(s)}

def installed_game_hints()->list[dict]:
    games=[]
    for sr in steam_roots():
        for acf in (sr/'steamapps').glob('appmanifest_*.acf'):
            txt=acf.read_text(errors='ignore')
            name=re.search(r'"name"\s+"([^"]+)"',txt); installdir=re.search(r'"installdir"\s+"([^"]+)"',txt); appid=re.search(r'"appid"\s+"(\d+)"',txt)
            if name: games.append({'title':name.group(1),'platform':'Steam','appid':appid.group(1) if appid else None,'install_path':str(sr/'steamapps'/'common'/(installdir.group(1) if installdir else name.group(1)))})
    epic=norm_path(r'C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests')
    if epic.exists():
        for item in epic.glob('*.item'):
            try:
                obj=json.loads(item.read_text(errors='ignore')); title=obj.get('DisplayName') or obj.get('AppName')
                if title: games.append({'title':title,'platform':'Epic','appid':obj.get('CatalogItemId'),'install_path':win_to_wsl(obj.get('InstallLocation') or '')})
            except Exception: pass
    for d in visible_drives():
        for base in [d/'GOG Games', d/'Games', d/'games', d/'FitGirl Repacks']:
            if base.exists():
                for child in base.iterdir():
                    if child.is_dir() and child.name.lower() not in {'_backup','tools','download','downloads'}:
                        games.append({'title':child.name,'platform':'Standalone','appid':None,'install_path':str(child)})
    seen=set(); out=[]
    for g in games:
        key=(g['title'].lower(),str(g.get('install_path','')).lower())
        if key not in seen: seen.add(key); out.append(g)
    return out

def infer_game_from_path(p:Path, root:Path)->str:
    try: parts=list(p.relative_to(root).parts)
    except Exception: parts=list(p.parts)
    bad={'save','saves','saved games','savegames','profiles','remote','userdata','data'}
    for part in reversed(parts[:-1] or parts):
        if part.lower() not in bad and not re.fullmatch(r'\d+',part): return part
    return p.name or 'Unknown Game'

def summarize_path_fast(p:Path, max_files:int=600)->tuple[int,int,float]:
    count=size=0; latest=0.0
    if not p.exists(): return 0,0,0.0
    try:
        for f in iter_files([p]):
            try:
                st=f.stat(); count+=1; size+=st.st_size; latest=max(latest,st.st_mtime)
            except OSError: pass
            if count>=max_files: break
    except Exception: pass
    return count,size,latest

def discover_all_games(refresh=False, max_depth=5)->list[dict]:
    cache=CONFIG_DIR/'discovery-cache-v2.json'
    started=time.time(); root_budget=25.0
    if not refresh and cache.exists():
        try:
            obj=json.loads(cache.read_text(encoding='utf-8'))
            if time.time()-obj.get('created_unix',0)<3600: return obj['games']
        except Exception: pass
    games={}
    def add(title, platform, source:Source, install_path=None, manifest_hit=False):
        blacklist={'docker','powershell','windowspowershell','windows','mozilla','internet-explorer','claude','netlify','everything','flingtrainer','microsoft','google','chrome','edge','nodejs','python','npm','hermes','user-data','user','user-pinned','content','lib','output','man','start-menu','inetcache','oneauth','saved','config','dist','title','player','ludusavi','token-optimizer','savedgames','goldberg-steamemu-saves','gse-saves','steamemu','rune','codex','nvidia-corporation','wlanservice','wlansvc','updateframework','firefox','snapshots','chocolatey','chocolateyinstall','cosmos','cef','deno','migrationdata','open-interpreter','jszip','startup64','agents','artificial-intelligence','artificial_intelligence','test'}
        slug=safe_slug(title).lower()
        if slug in blacklist or re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', slug) or any(x in slug for x in ['docker','powershell','mozilla','internet-explorer','chrome','microsoft','python','cache','start-menu','user-data']):
            return
        key=slug
        rec=games.setdefault(key, {'title':title,'platform':platform,'install_path':install_path,'manifest_hit':manifest_hit,'sources':[]})
        if manifest_hit: rec['manifest_hit']=True
        if install_path and not rec.get('install_path'): rec['install_path']=install_path
        if platform!='Heuristic': rec['platform']=platform
        if str(source.path).lower() not in {s['path'].lower() for s in rec['sources']}: rec['sources'].append(source_json(source))
    # Store libraries give game names/install roots quickly. Avoid per-game manifest parsing here;
    # the broad save-root pass below is much faster and finds real existing saves.
    for hint in installed_game_hints():
        for sp in extra_crack_sources(norm_path(hint['install_path']) if hint.get('install_path') else None):
            c,b,l=summarize_path_fast(sp)
            s=Source(sp,'Store/emulator config declared save location',['save','store'],sp.exists(),c,b,l)
            if s.exists and s.file_count>0: add(hint['title'],hint['platform'],s,hint.get('install_path'),False)
    for root in all_save_roots():
        try:
            visited=0
            for dirpath, dirs, files in os.walk(root):
                visited+=1
                if visited>6000 or time.time()-started>root_budget:
                    dirs[:]=[]; break
                p=Path(dirpath); depth=len(p.relative_to(root).parts)
                dirs[:]=[d for d in dirs if d.lower() not in {'cache','shadercache','crashpad','crash_reports','logs','log','temp','tmp','__pycache__','screenshots','captures','webcache','gpu_cache','profiles','profile','user data','node_modules','.git','packages'}]
                if depth>max_depth: dirs[:]=[]
                if depth and save_like(files,p.name):
                    c,b,l=summarize_path_fast(p)
                    if c>0:
                        s=Source(p,f'All-PC fast save scan under {wsl_to_win(str(root))}',['heuristic','save-like'],True,c,b,l)
                        if source_score(s)>=45: add(infer_game_from_path(p,root),'Heuristic',s,None,False); dirs[:]=[]
        except Exception: pass
    out=[]
    for rec in games.values():
        rec['sources'].sort(key=lambda s:(-s['confidence'],-s.get('latest_write',0),-s.get('byte_count',0)))
        rec['file_count']=sum(s['file_count'] for s in rec['sources']); rec['byte_count']=sum(s['byte_count'] for s in rec['sources']); rec['size_human']=human_size(rec['byte_count'])
        rec['latest_write']=max([s['latest_write'] for s in rec['sources']] or [0]); rec['latest_write_iso']=iso(rec['latest_write']); rec['confidence']=max([s['confidence'] for s in rec['sources']] or [0]); rec['location_count']=len(rec['sources'])
        out.append(rec)
    out.sort(key=lambda g:(-g['latest_write'],-g['confidence'],g['title'].lower()))
    CONFIG_DIR.mkdir(parents=True,exist_ok=True); cache.write_text(json.dumps({'created_unix':time.time(),'games':out},indent=2),encoding='utf-8')
    return out

def plan_from_record(rec:dict)->GamePlan:
    sources=[]
    for s in rec['sources']:
        sources.append(Source(norm_path(s['path']), s.get('reason','selected source'), s.get('tags',[]), s.get('exists',True), s.get('file_count',0), s.get('byte_count',0), s.get('latest_write',0)))
    return GamePlan(rec['title'],sources,rec.get('manifest_hit',False),None)

def backup_record(rec:dict,destination=None)->Path:
    return backup_plan(plan_from_record(rec), destination)

def backup_plan(plan:GamePlan,destination=None)->Path:
    root=norm_path(destination) if destination else backup_root(); root.mkdir(parents=True,exist_ok=True)
    sources=[s for s in plan.sources if s.exists and s.file_count>0]
    if not sources: raise SystemExit(f'No backupable source for {plan.game}')
    ts=dt.datetime.now().strftime('%Y%m%d-%H%M%S'); out=root/f"{safe_slug(plan.game)}-{ts}"; payload=out/'payload'; payload.mkdir(parents=True)
    entries=[]
    for i,s in enumerate(sources):
        label=f"source-{i+1:02d}-{safe_slug(s.path.name or 'root')}"; dst=payload/label; copy_tree(s.path,dst)
        entries.append(source_json(s)|{'payload':str(dst.relative_to(out)),'restore_to':wsl_to_win(str(s.path))})
    meta={'app':APP_NAME,'version':'2.0.0','game':plan.game,'created_at':dt.datetime.now().isoformat(timespec='seconds'),'detected_process':plan.detected_process,'manifest_hit':plan.manifest_hit,'sources':entries}
    (out/'backup_manifest.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    with tarfile.open(out.with_suffix('.tar.gz'),'w:gz') as tar: tar.add(out,arcname=out.name)
    return out

def list_backups(root=None)->list[dict]:
    base=norm_path(root) if root else backup_root(); out=[]
    if not base.exists(): return out
    for mf in base.glob('*/backup_manifest.json'):
        try:
            meta=json.loads(mf.read_text(encoding='utf-8'))
            sources=meta.get('sources',[])
            size=sum(int(src.get('byte_count') or 0) for src in sources)
            if not size:
                size=mf.stat().st_size
            out.append({'game':meta.get('game'),'path':str(mf.parent),'path_windows':wsl_to_win(str(mf.parent)),'created_at':meta.get('created_at'),'size':size,'size_human':human_size(size),'sources':len(sources)})
        except Exception: pass
    return sorted(out,key=lambda x:x.get('created_at') or '',reverse=True)

def verify_backup(backup_dir)->dict:
    b=norm_path(backup_dir); meta=json.loads((b/'backup_manifest.json').read_text(encoding='utf-8'))
    missing=[]; mismatched=[]; checked=0
    for src in meta.get('sources',[]):
        payload=b/src['payload']
        expected=src.get('file_count',0)
        if not payload.exists():
            missing.append(str(payload)); continue
        actual_files=list(iter_files([payload])); checked+=len(actual_files)
        if expected and len(actual_files)<expected:
            mismatched.append(f"{payload}: expected at least {expected} files, found {len(actual_files)}")
        if src.get('byte_count') is not None:
            actual_bytes=sum((f.stat().st_size for f in actual_files if f.exists()),0)
            if actual_bytes < int(src.get('byte_count') or 0):
                mismatched.append(f"{payload}: expected at least {src.get('byte_count')} bytes, found {actual_bytes}")
    return {'ok':not missing and not mismatched,'checked_files':checked,'missing':missing,'mismatched':mismatched}


def c_drive_root() -> Path:
    return norm_path('C:/')

LEFTOVER_DIRS = {'cache', 'logs', 'log', 'temp', 'tmp', 'crash', 'crashes', 'crash_reports', 'shadercache', 'webcache', 'gpu_cache'}
PROTECTED_CLEANUP_TERMS = {'windows', 'program files', 'program files (x86)', 'users', 'system32', 'syswow64', 'desktop', 'documents', 'downloads'}

def game_terms(game: str) -> list[str]:
    return [t.lower() for t in re.split(r'[^A-Za-z0-9]+', game) if len(t) >= 3]

def game_variants(game: str) -> set[str]:
    compact = re.sub(r'[^A-Za-z0-9]+', '', game).lower()
    dashed = safe_slug(game).lower().replace('-', '')
    return {game.lower(), safe_slug(game).lower(), compact, dashed}

def cleanup_scan_roots() -> list[Path]:
    roots=[]
    c = c_drive_root()
    for u in windows_profiles():
        roots += [u/'AppData'/'LocalLow', u/'Saved Games', u/'Documents'/'My Games', u/'Documents', u/'AppData'/'Roaming', u/'AppData'/'Local']
    roots += [c/'ProgramData', c/'Users'/'Public'/'Documents']
    seen=set(); out=[]
    for root in roots:
        key=str(root).lower()
        if root.exists() and key not in seen:
            seen.add(key); out.append(root)
    return out

def is_cleanup_candidate(path: Path, root: Path, game: str) -> bool:
    try:
        rel = path.relative_to(root)
    except Exception:
        return False
    if not rel.parts:
        return False
    low_path = str(path).lower()
    leaf = path.name.lower()
    terms = game_terms(game)
    variants = game_variants(game)
    compact_path = re.sub(r'[^a-z0-9]+', '', low_path)
    if leaf in PROTECTED_CLEANUP_TERMS or str(path).lower() in {str(c_drive_root()).lower(), str(root).lower()}:
        return False
    matched = any(v and v in low_path for v in variants) or any(v and v in compact_path for v in variants) or (terms and all(t in low_path for t in terms[:2]))
    if not matched:
        return False
    protected_bits = ['\\windows\\', '/windows/', '\\program files\\', '/program files/', '\\program files (x86)\\', '/program files (x86)/']
    return not any(bit in low_path for bit in protected_bits)

def c_path_to_win(path: Path) -> str:
    try:
        rel = path.relative_to(c_drive_root())
        return 'C:\\' + str(rel).replace('/', '\\')
    except Exception:
        return wsl_to_win(str(path))

def cleanup_candidate_json(source: Source, root: Path, game: str) -> dict:
    payload = source_json(source)
    payload['path_windows'] = c_path_to_win(source.path)
    payload['id'] = hashlib.sha256(str(source.path).lower().encode('utf-8')).hexdigest()[:16]
    payload['root'] = str(root)
    payload['root_windows'] = c_path_to_win(root)
    payload['delete_safe'] = is_cleanup_candidate(source.path, root, game)
    payload['category'] = 'save leftovers' if save_like([f.name for f in source.path.iterdir() if f.is_file()] if source.path.is_dir() else [], source.path.name) else 'game data leftovers'
    return payload

def discover_game_leftovers(game: str, max_depth: int = 6) -> dict:
    game = (game or '').strip()
    if not game:
        return {'ok': False, 'error': 'Game name is required', 'game': game, 'count': 0, 'candidates': []}
    candidates=[]
    started=time.time(); budget=14.0
    for root in cleanup_scan_roots():
        if time.time() - started > budget:
            break
        try:
            visited=0
            for dirpath, dirs, files in os.walk(root):
                if time.time() - started > budget:
                    dirs[:] = []
                    break
                visited += 1
                if visited > 9000:
                    dirs[:] = []
                    break
                pth = Path(dirpath)
                try:
                    depth = len(pth.relative_to(root).parts)
                except Exception:
                    depth = 0
                dirs[:] = [d for d in dirs if d.lower() not in {'.git','node_modules','packages','winsxs','servicing','installer'}]
                if depth > max_depth:
                    dirs[:] = []
                if depth and is_cleanup_candidate(pth, root, game):
                    count, size, latest = summarize_path_fast(pth, max_files=1200)
                    if count > 0:
                        src = Source(pth, f'C-drive leftover scan under {wsl_to_win(str(root))}', ['cleanup','c-drive','leftover'], True, count, size, latest)
                        candidates.append(cleanup_candidate_json(src, root, game))
                    dirs[:] = []
        except Exception:
            pass
    by_id={c['id']: c for c in candidates if c.get('delete_safe')}
    ordered=sorted(by_id.values(), key=lambda c:(-c.get('latest_write',0), -c.get('byte_count',0), c.get('path','').lower()))
    return {'ok': True, 'game': game, 'count': len(ordered), 'candidates': ordered, 'default_quarantine_dir': wsl_to_win(str(backup_root() / '_cleanup-quarantine'))}

def remove_path(path: Path) -> None:
    if path.is_file() or path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)

def cleanup_game_leftovers(game: str, execute: bool = False, candidate_ids: Optional[list[str]] = None) -> dict:
    discovery = discover_game_leftovers(game)
    if not discovery.get('ok'):
        return discovery
    candidates = discovery['candidates']
    wanted = set([c['id'] for c in candidates] if candidate_ids is None else candidate_ids)
    selected = [c for c in candidates if c['id'] in wanted and c.get('delete_safe')]
    ts = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    quarantine = backup_root() / '_cleanup-quarantine' / f"{safe_slug(game)}-{ts}"
    actions=[]
    if execute:
        quarantine.mkdir(parents=True, exist_ok=True)
    for c in selected:
        src = norm_path(c['path'])
        qdst = quarantine / c['id'] / safe_slug(src.name or 'leftover')
        actions.append({'id': c['id'], 'path': c['path'], 'path_windows': c['path_windows'], 'files': c['file_count'], 'size_human': c['size_human'], 'would_quarantine_to': wsl_to_win(str(qdst))})
        if execute and src.exists():
            copy_tree(src, qdst)
            remove_path(src)
    if execute:
        (quarantine/'cleanup_manifest.json').write_text(json.dumps({'app': APP_NAME, 'version': '3.0.0', 'game': game, 'created_at': dt.datetime.now().isoformat(timespec='seconds'), 'actions': actions}, indent=2), encoding='utf-8')
    return {'ok': True, 'game': game, 'mode': 'deleted' if execute else 'preview', 'count': len(selected), 'candidates': selected, 'actions': actions, 'quarantine_path': str(quarantine), 'quarantine_path_windows': wsl_to_win(str(quarantine))}

def cmd_discover(args):
    if getattr(args,'all',False):
        games=discover_all_games(refresh=args.refresh)
        print(json.dumps({'count':len(games),'games':games},indent=2) if args.json else '\n'.join([f"Found {len(games)} games/save groups"]+[f"- {g['title']} [{g['platform']}] {g['location_count']} locations {g['size_human']} confidence={g['confidence']}" for g in games]))
        return
    plan=build_plan(args.game)
    print(json.dumps({'game':plan.game,'running_process':plan.detected_process,'manifest_hit':plan.manifest_hit,'sources':[source_json(s) for s in plan.sources]}, indent=2))

def cmd_backup(args):
    if getattr(args,'all',False):
        games=discover_all_games(refresh=args.refresh); outs=[backup_record(g,args.destination) for g in games if g.get('sources')]
        print(json.dumps({'backups':[str(x) for x in outs],'backups_windows':[wsl_to_win(str(x)) for x in outs]},indent=2)); return
    print(str(backup(args.game, args.destination)))
def cmd_restore(args): print('\n'.join(restore(args.backup, args.target_root, args.dry_run or getattr(args,'preview',False))))
def cmd_config(args):
    if args.set_default: print(set_backup_root(args.set_default))
    else: print(json.dumps(load_config() | {'effective_backup_dir': str(backup_root()), 'effective_backup_dir_windows': wsl_to_win(str(backup_root()))}, indent=2))
def cmd_list_backups(args): print(json.dumps({'backups':list_backups(args.root)},indent=2))
def cmd_verify(args): print(json.dumps(verify_backup(args.backup),indent=2))
def cmd_cache(args):
    cache=CONFIG_DIR/'discovery-cache-v2.json'
    started=time.time(); root_budget=25.0
    if args.clear and cache.exists(): cache.unlink(); print('Discovery cache cleared')
    else: print(str(cache))


def api_discover(refresh: bool = False) -> dict:
    games = discover_all_games(refresh=refresh)
    return {'ok': True, 'generated_at': dt.datetime.now().isoformat(timespec='seconds'), 'default_backup_dir': wsl_to_win(str(backup_root())), 'count': len(games), 'games': games}

def api_backups(root: str | None = None) -> dict:
    backups = list_backups(root)
    return {'ok': True, 'count': len(backups), 'backups': backups, 'default_backup_dir': wsl_to_win(str(backup_root()))}

def api_backup_selected(payload: dict) -> dict:
    destination = payload.get('destination') or wsl_to_win(str(backup_root()))
    titles = payload.get('titles') or []
    if not titles:
        return {'ok': False, 'error': 'No games selected'}
    discovered = {g.get('title'): g for g in discover_all_games(refresh=False)}
    selected = [discovered[t] for t in titles if t in discovered]
    if not selected:
        return {'ok': False, 'error': 'No discovered games matched the requested selection'}
    outputs = [backup_record(game, destination) for game in selected]
    return {'ok': True, 'backups': [{'path': str(p), 'path_windows': wsl_to_win(str(p))} for p in outputs]}


def api_leftovers(game: str) -> dict:
    return discover_game_leftovers(game)

def api_cleanup_leftovers(payload: dict) -> dict:
    game = str(payload.get('game') or '').strip()
    execute = bool(payload.get('execute'))
    candidate_ids = payload.get('candidate_ids') if isinstance(payload.get('candidate_ids'), list) else None
    return cleanup_game_leftovers(game, execute=execute, candidate_ids=candidate_ids)

def render_app_shell() -> str:
    default_dir = html.escape(wsl_to_win(str(backup_root())))
    api_token = html.escape(API_TOKEN)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{APP_NAME} — Beautiful, safe cleanup + saves</title>
<style>
:root{{--bg:#050712;--bg2:#08111f;--card:rgba(13,22,39,.78);--card2:rgba(20,31,55,.86);--line:rgba(148,163,184,.18);--text:#eef6ff;--muted:#9fb0ca;--brand:#67e8f9;--brand2:#a78bfa;--hot:#fb7185;--good:#34d399;--warn:#fbbf24;--shadow:0 30px 100px rgba(0,0,0,.52)}}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;overflow:hidden;color:var(--text);font-family:Inter,Segoe UI,system-ui,sans-serif;background:radial-gradient(circle at 8% 0,rgba(103,232,249,.28),transparent 34%),radial-gradient(circle at 82% 4%,rgba(167,139,250,.24),transparent 32%),linear-gradient(135deg,var(--bg),var(--bg2));}}button,input{{font:inherit}} .app{{height:100vh;display:grid;grid-template-columns:324px 1fr;gap:22px;padding:22px}} .glass{{background:var(--card);border:1px solid var(--line);box-shadow:var(--shadow);backdrop-filter:blur(20px);border-radius:30px}} aside{{padding:24px;display:flex;flex-direction:column;gap:16px}} .brand{{display:flex;gap:14px;align-items:center}} .logo{{width:58px;height:58px;border-radius:20px;background:conic-gradient(from 220deg,var(--brand),var(--brand2),#60a5fa,var(--brand));display:grid;place-items:center;color:#02111e;font-size:30px;font-weight:1000;box-shadow:0 20px 60px rgba(103,232,249,.32)}} h1{{font-size:24px;line-height:1;margin:0}} .sub,.muted{{color:var(--muted);font-size:13px;line-height:1.45}} .badge{{display:inline-flex;gap:8px;align-items:center;border:1px solid var(--line);background:rgba(255,255,255,.06);padding:8px 11px;border-radius:999px;color:#cffafe;font-size:12px;font-weight:800}} .label{{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#7dd3fc;font-weight:900;margin-top:8px}} input{{width:100%;border:1px solid var(--line);background:rgba(5,10,20,.72);color:var(--text);border-radius:17px;padding:14px 15px;outline:0}} input:focus{{border-color:rgba(103,232,249,.9);box-shadow:0 0 0 4px rgba(103,232,249,.13)}} .btn{{width:100%;border:1px solid var(--line);background:rgba(22,34,58,.9);color:var(--text);border-radius:17px;padding:14px 16px;font-weight:900;cursor:pointer;transition:.18s transform,.18s filter,.18s box-shadow;text-align:left}} .btn:hover{{transform:translateY(-1px);filter:brightness(1.1);box-shadow:0 14px 34px rgba(0,0,0,.24)}} .primary{{background:linear-gradient(135deg,var(--brand),var(--brand2));color:#03131e}} .danger{{background:linear-gradient(135deg,#fb7185,#f97316);color:#180306}} .good{{background:linear-gradient(135deg,#34d399,#67e8f9);color:#032018}} .main{{display:grid;grid-template-rows:auto auto 1fr;gap:18px;min-width:0}} .hero{{padding:26px 30px;display:flex;justify-content:space-between;align-items:flex-start;gap:24px}} .eyebrow{{color:#67e8f9;font-weight:900;letter-spacing:.14em;text-transform:uppercase;font-size:12px}} .title{{font-size:42px;line-height:.98;font-weight:1000;letter-spacing:-.04em;margin:8px 0}} .lead{{max-width:920px;color:#cbd5e1;font-size:16px;line-height:1.6}} .metrics{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px}} .metric{{padding:16px 18px}} .metric span{{display:block;color:var(--muted);font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}} .metric strong{{display:block;font-size:26px;margin-top:7px}} .workspace{{display:grid;grid-template-columns:minmax(560px,1.25fr) minmax(380px,.75fr);gap:18px;min-height:0}} .panel{{overflow:hidden;display:flex;flex-direction:column;min-height:0}} .toolbar{{display:grid;grid-template-columns:1fr auto auto;gap:10px;padding:16px;border-bottom:1px solid var(--line)}} table{{width:100%;border-collapse:collapse}} th{{text-align:left;color:#93c5fd;font-size:11px;text-transform:uppercase;letter-spacing:.12em;padding:12px 16px;background:rgba(255,255,255,.03)}} td{{padding:14px 16px;border-top:1px solid rgba(148,163,184,.1);font-size:13px;vertical-align:top}} tbody tr{{cursor:pointer;transition:.15s background}} tbody tr:hover,tbody tr.selected{{background:rgba(103,232,249,.08)}} .game{{font-weight:900;font-size:15px}} .pill{{display:inline-flex;border:1px solid rgba(103,232,249,.25);background:rgba(103,232,249,.1);padding:5px 9px;border-radius:999px;font-size:11px;font-weight:850;color:#cffafe}} .confidence{{width:92px;height:8px;background:rgba(148,163,184,.18);border-radius:999px;overflow:hidden}} .confidence i{{display:block;height:100%;background:linear-gradient(90deg,var(--good),var(--brand),var(--brand2))}} .scroll{{overflow:auto;min-height:0}} .detail{{padding:20px;display:grid;gap:14px}} .source{{padding:14px;border-radius:20px;background:rgba(255,255,255,.055);border:1px solid rgba(148,163,184,.14);margin-bottom:10px}} code{{display:block;white-space:normal;word-break:break-all;color:#e0f2fe;margin:8px 0;font-size:12px}} .console{{min-height:105px;max-height:150px;overflow:auto;padding:14px;background:#020617;color:#a7f3d0;border-radius:20px;font-family:ui-monospace,Consolas,monospace;font-size:12px}} .toast{{position:fixed;right:24px;bottom:24px;opacity:0;transform:translateY(12px);transition:.25s;background:#ecfeff;color:#03212a;border-radius:18px;padding:14px 18px;font-weight:900;box-shadow:0 20px 60px rgba(0,0,0,.34)}} .toast.show{{opacity:1;transform:none}} .pulse{{width:10px;height:10px;border-radius:50%;display:inline-block;background:var(--good);box-shadow:0 0 0 6px rgba(52,211,153,.15);margin-right:8px}} @media(max-width:1100px){{body{{overflow:auto}}.app{{height:auto;grid-template-columns:1fr}}.workspace,.metrics{{grid-template-columns:1fr}}}}
</style></head><body><div id="app-shell" class="app"><aside class="glass"><div class="brand"><div class="logo">M</div><div><h1>MichSaveGame</h1><div class="sub">Former SaveVault Command Center • Universal Game Save Guardian engine</div></div></div><span class="badge">🛡️ local-only • preview-first • quarantined deletes</span><div class="label">Backup destination</div><input id="destination" value="{default_dir}"/><button class="btn primary" data-action="discover" id="discoverBtn">⚡ Discover saves on this PC</button><button class="btn good" id="backupBtn">⬢ Backup selected saves</button><div class="label">C-drive cleanup</div><input id="cleanupGame" placeholder="Game name to clean, e.g. Edge of Eternity"/><button class="btn" id="leftoversBtn">🔎 Find C-drive leftovers</button><button class="btn danger" id="deleteLeftoversBtn">🧹 Delete C-drive leftovers safely</button><button class="btn" id="backupsBtn">Restore Preview / Backups</button><button class="btn" id="clearBtn">Clear selection</button><div class="source"><b><span class="pulse"></span><span id="statusTitle">Ready</span></b><div class="sub" id="statusText">Choose discover, backup, or cleanup. Deletes are backed up to quarantine first.</div></div><div class="console" id="console">Activity Timeline\n</div></aside><main class="main"><section class="hero glass"><div><div class="eyebrow">Beautiful, safe cleanup + save backup</div><div class="title">Protect saves. Remove old game junk. Restore anywhere.</div><div class="lead">MichSaveGame discovers live save locations, creates verifiable backups, previews restore targets, and finds C-drive leftovers for any game you name. Cleanup is intentionally safe: preview first, exact paths visible, server recomputes candidates, and every deletion is quarantined before removal.</div></div></section><section class="metrics"><div class="metric glass"><span>Games found</span><strong id="mGames">—</strong></div><div class="metric glass"><span>Save locations</span><strong id="mLocations">—</strong></div><div class="metric glass"><span>Total save size</span><strong id="mSize">—</strong></div><div class="metric glass"><span>Latest save</span><strong id="mLatest">—</strong></div><div class="metric glass"><span>Leftovers</span><strong id="mLeftovers">—</strong></div></section><section class="workspace"><div class="panel glass"><div class="toolbar"><input id="search" placeholder="Search games, platforms, paths..."/><button class="btn" id="selectAllBtn" style="width:auto">Select visible</button><button class="btn" id="jsonBtn" style="width:auto">Export JSON</button></div><div class="scroll"><table><thead><tr><th></th><th>Game / evidence</th><th>Platform</th><th>Locations</th><th>Size</th><th>Latest</th><th>Confidence</th></tr></thead><tbody id="gamesBody"><tr><td colspan="7">Loading...</td></tr></tbody></table></div></div><div class="panel glass"><div class="detail"><h2 id="detailTitle">Restore Preview</h2><div class="sub" id="detailSub">Backups and cleanup candidates will appear here.</div><div id="sources"></div></div></div></section></main><div class="toast" id="toast"></div></div>
<script>
const API_TOKEN='{api_token}'; let games=[]; let selectedGames=new Set(); let currentFilter=''; let leftovers=[]; const $=id=>document.getElementById(id);
function toast(msg){{const t=$('toast'); t.textContent=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),3400)}}
function log(msg){{$('console').textContent=new Date().toLocaleTimeString()+'  '+msg+'\\n'+$('console').textContent.slice(0,2200)}}
function status(title,text){{$('statusTitle').textContent=title; $('statusText').textContent=text; log(title+' — '+text)}}
function escapeHtml(s){{return String(s??'').replace(/[&<>"']/g,m=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[m]))}} function escapeAttr(s){{return escapeHtml(s).replace(/`/g,'&#96;')}}
const authHeaders=()=>({{'X-UGSG-Token':API_TOKEN}}); const jsonHeaders=()=>({{'Content-Type':'application/json','X-UGSG-Token':API_TOKEN}});
function humanBytes(n){{let u=['B','KB','MB','GB','TB'],i=0,v=n||0;while(v>=1024&&i<u.length-1){{v/=1024;i++}}return i?`${{v.toFixed(1)}} ${{u[i]}}`:`${{v}} B`}} function fmtLatest(x){{return x?String(x).replace('T',' '):'—'}}
function filtered(){{const q=currentFilter.toLowerCase();return games.filter(g=>!q||g.title.toLowerCase().includes(q)||g.platform.toLowerCase().includes(q)||JSON.stringify(g.sources||[]).toLowerCase().includes(q))}}
function updateMetrics(){{$('mGames').textContent=games.length;$('mLocations').textContent=games.reduce((a,g)=>a+(g.location_count||0),0);$('mSize').textContent=humanBytes(games.reduce((a,g)=>a+(g.byte_count||0),0));$('mLatest').textContent=fmtLatest(games.map(g=>g.latest_write_iso).filter(Boolean).sort().pop());$('mLeftovers').textContent=leftovers.length||'—'}}
function renderGames(){{const rows=filtered();$('gamesBody').innerHTML=rows.length?rows.map(g=>`<tr class="${{selectedGames.has(g.title)?'selected':''}}" data-title="${{escapeAttr(g.title)}}"><td>${{selectedGames.has(g.title)?'☑':'☐'}}</td><td><div class="game">${{escapeHtml(g.title)}}</div><div class="muted">${{escapeHtml((g.sources||[])[0]?.reason||'Discovered save group')}}</div></td><td><span class="pill">${{escapeHtml(g.platform||'Heuristic')}}</span></td><td>${{g.location_count||0}}</td><td>${{g.size_human||humanBytes(g.byte_count)}}</td><td>${{fmtLatest(g.latest_write_iso)}}</td><td><div class="confidence"><i style="width:${{g.confidence||0}}%"></i></div><div class="muted">${{g.confidence||0}}%</div></td></tr>`).join(''):`<tr><td colspan="7"><div class="source"><b>No matching games.</b><div class="sub">Try clearing the filter or run Discover again.</div></div></td></tr>`;document.querySelectorAll('tbody tr[data-title]').forEach(r=>r.onclick=()=>selectGame(r.dataset.title));}}
function selectGame(title){{const g=games.find(x=>x.title===title); if(!g)return; if(selectedGames.has(title))selectedGames.delete(title);else selectedGames.add(title); $('cleanupGame').value=title; renderGames(); renderGameDetail(g)}}
function renderGameDetail(g){{$('detailTitle').textContent=g.title;$('detailSub').textContent=`${{g.platform}} • ${{g.location_count}} save locations • ${{g.size_human}} • confidence ${{g.confidence}}%`;$('sources').innerHTML=(g.sources||[]).map(s=>`<div class="source"><code>${{escapeHtml(s.path_windows||s.path)}}</code><span class="pill">${{s.confidence}}% confidence</span> <span class="pill">${{escapeHtml(s.size_human)}}</span><p class="sub">${{escapeHtml(s.reason||'Detected save location')}}</p><p class="muted">${{s.file_count}} files • latest ${{fmtLatest(s.latest_write_iso)}}</p></div>`).join('')||'<div class="source">No source details.</div>'}}
function renderLeftovers(data){{leftovers=data.candidates||[]; updateMetrics(); $('detailTitle').textContent=`Delete all leftovers: ${{escapeHtml(data.game||'game')}}`; $('detailSub').textContent=`${{leftovers.length}} C-drive candidate folders found. Review paths; actual deletion quarantines first.`; $('sources').innerHTML=leftovers.map(c=>`<div class="source"><b>${{escapeHtml(c.category||'leftover')}}</b><code>${{escapeHtml(c.path_windows||c.path)}}</code><span class="pill">${{escapeHtml(c.size_human)}}</span> <span class="pill">${{c.file_count}} files</span><p class="sub">${{escapeHtml(c.reason)}} • quarantine before delete</p></div>`).join('')||'<div class="source">No C-drive leftovers found for that game.</div>'}}
async function discover(refresh=true){{status('Scanning','Indexing drives, users, stores and save roots...'); const res=await fetch('/api/discover?refresh='+(refresh?'1':'0'),{{headers:authHeaders()}}); const data=await res.json(); games=data.games||[]; selectedGames.clear(); updateMetrics(); renderGames(); status('Discovery complete',`${{games.length}} game/save groups found`); toast(`Found ${{games.length}} save groups`)}}
async function loadBackups(){{const data=await (await fetch('/api/backups',{{headers:authHeaders()}})).json(); $('detailTitle').textContent='Restore Preview'; $('detailSub').textContent=`${{data.count}} backups found in ${{data.default_backup_dir}}`; $('sources').innerHTML=(data.backups||[]).map(b=>`<div class="source"><b>${{escapeHtml(b.game||'Unknown')}}</b><code>${{escapeHtml(b.path_windows||b.path)}}</code><span class="pill">${{escapeHtml(b.size_human)}}</span><p class="muted">${{escapeHtml(b.created_at)}} • ${{escapeHtml(b.sources)}} sources</p></div>`).join('')||'<div class="source">No backups found yet.</div>'; status('Backup browser','Loaded restore-preview backup list')}}
async function backupSelected(){{const selectedTitles=[...selectedGames]; if(!selectedTitles.length){{toast('Select at least one game first');return}} status('Backing up',`Creating backups for ${{selectedTitles.length}} games...`); const res=await fetch('/api/backup',{{method:'POST',headers:jsonHeaders(),body:JSON.stringify({{destination:$('destination').value,titles:selectedTitles}})}}); const data=await res.json(); if(!data.ok){{toast(data.error||'Backup failed');status('Backup failed',data.error||'Unknown error');return}} toast('Backup complete'); status('Backup complete',data.backups.map(b=>b.path_windows).join(' • ')); await loadBackups()}}
async function findLeftovers(){{const game=$('cleanupGame').value.trim(); if(!game){{toast('Type a game name or select one first');return}} status('Scanning C drive',`Looking for leftovers for ${{game}}...`); const data=await (await fetch('/api/leftovers?game='+encodeURIComponent(game),{{headers:authHeaders()}})).json(); renderLeftovers(data); status('Cleanup preview',`${{data.count||0}} leftover folders found for ${{game}}`)}}
async function deleteLeftovers(){{const game=$('cleanupGame').value.trim(); if(!game){{toast('Type a game name first');return}} if(!leftovers.length){{toast('Run Find C-drive leftovers first');return}} if(!confirm('Delete the previewed leftover folders for '+game+'? They will be copied to quarantine first.')) return; status('Deleting safely','Quarantining then removing previewed C-drive leftovers...'); const res=await fetch('/api/delete-leftovers',{{method:'POST',headers:jsonHeaders(),body:JSON.stringify({{game,execute:true,candidate_ids:leftovers.map(c=>c.id)}})}}); const data=await res.json(); renderLeftovers(data); toast(`Deleted ${{data.count||0}} leftover folders`); status('Cleanup complete',`${{data.count||0}} folders quarantined at ${{data.quarantine_path_windows||''}}`)}}
$('discoverBtn').onclick=()=>discover(true);$('backupBtn').onclick=backupSelected;$('backupsBtn').onclick=loadBackups;$('leftoversBtn').onclick=findLeftovers;$('deleteLeftoversBtn').onclick=deleteLeftovers;$('selectAllBtn').onclick=()=>{{filtered().forEach(g=>selectedGames.add(g.title));renderGames();toast('Visible games selected')}};$('clearBtn').onclick=()=>{{selectedGames.clear();renderGames();toast('Selection cleared')}};$('jsonBtn').onclick=()=>{{const blob=new Blob([JSON.stringify({{games,leftovers}},null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='MichSaveGame-discovery.json';a.click()}};$('search').oninput=e=>{{currentFilter=e.target.value;renderGames()}};loadBackups();discover(true);
</script></body></html>'''

class Web(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        if self.headers.get('X-UGSG-Token') != API_TOKEN:
            return False
        origin = self.headers.get('Origin')
        if origin and not (origin.startswith('http://127.0.0.1:') or origin.startswith('http://localhost:')):
            return False
        return True
    def _json(self, obj: dict, status_code: int = 200) -> None:
        data = json.dumps(obj, indent=2).encode('utf-8')
        self.send_response(status_code); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def _html(self, body: str) -> None:
        data = body.encode('utf-8')
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == '/api/discover':
                if not self._authorized():
                    self._json({'ok': False, 'error': 'Unauthorized local request'}, 403); return
                qs = urllib.parse.parse_qs(parsed.query); self._json(api_discover(refresh=qs.get('refresh', ['0'])[0] == '1'))
            elif parsed.path == '/api/backups':
                if not self._authorized():
                    self._json({'ok': False, 'error': 'Unauthorized local request'}, 403); return
                self._json(api_backups())
            elif parsed.path == '/api/leftovers':
                if not self._authorized():
                    self._json({'ok': False, 'error': 'Unauthorized local request'}, 403); return
                qs = urllib.parse.parse_qs(parsed.query); self._json(api_leftovers(qs.get('game', [''])[0]))
            elif parsed.path in ('/', '/discover', '/app'): self._html(render_app_shell())
            else: self._json({'ok': False, 'error': 'Not found'}, 404)
        except Exception as exc: self._json({'ok': False, 'error': str(exc)}, 500)
    def do_POST(self):
        try:
            if not self._authorized():
                self._json({'ok': False, 'error': 'Unauthorized local request'}, 403); return
            length = int(self.headers.get('Content-Length') or '0'); payload = json.loads(self.rfile.read(length).decode('utf-8') or '{}')
            if self.path == '/api/backup': self._json(api_backup_selected(payload))
            elif self.path == '/api/delete-leftovers': self._json(api_cleanup_leftovers(payload))
            else: self._json({'ok': False, 'error': 'Not found'}, 404)
        except Exception as exc: self._json({'ok': False, 'error': str(exc)}, 500)

def local_app_url(port: int) -> str:
    return f'http://127.0.0.1:{port}/app'

def port_looks_like_michsavegame(port: int) -> bool:
    try:
        with urllib.request.urlopen(local_app_url(port), timeout=1.5) as response:
            return b'MichSaveGame' in response.read(12000)
    except Exception:
        return False

def available_port(preferred: int, fallbacks: Iterable[int] | None = None) -> int:
    candidates = [preferred] + list(fallbacks or [8787, 8877, 8995, 52117, 52118, 52119])
    for candidate in candidates:
        if port_looks_like_michsavegame(candidate):
            return candidate
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(('127.0.0.1', candidate))
            return candidate
        except OSError:
            continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])

def serve(port:int, open_browser: bool = True):
    tried=[]
    for requested in [port, 8787, 8877, 8995, 52117, 52118, 52119, 0]:
        selected_port = requested if requested == 0 else available_port(requested)
        url = local_app_url(selected_port)
        if port_looks_like_michsavegame(selected_port):
            print(f'MichSaveGame already running {url}', flush=True)
            if open_browser: webbrowser.open(url)
            return
        try:
            http=ThreadingHTTPServer(('127.0.0.1', selected_port), Web)
            actual_port = int(http.server_address[1])
            url = local_app_url(actual_port)
            print(f'MichSaveGame local app {url}', flush=True)
            if open_browser: webbrowser.open(url)
            http.serve_forever()
            return
        except OSError as exc:
            tried.append(f'{selected_port}:{exc}')
            continue
    raise OSError('Could not bind MichSaveGame local server. Tried ' + '; '.join(tried))

def launch_gui():
    serve(8765, open_browser=True)

def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        argv = ['gui']
    p=argparse.ArgumentParser(description=APP_NAME); sub=p.add_subparsers(required=True)
    d=sub.add_parser('discover',aliases=['discover-all']); d.add_argument('--game'); d.add_argument('--all',action='store_true'); d.add_argument('--json',action='store_true'); d.add_argument('--refresh',action='store_true'); d.set_defaults(func=cmd_discover)
    b=sub.add_parser('backup'); b.add_argument('--game'); b.add_argument('--destination'); b.add_argument('--all',action='store_true'); b.add_argument('--refresh',action='store_true'); b.set_defaults(func=cmd_backup)
    r=sub.add_parser('restore'); r.add_argument('backup'); r.add_argument('--target-root'); r.add_argument('--dry-run', action='store_true'); r.add_argument('--preview',action='store_true'); r.set_defaults(func=cmd_restore)
    c=sub.add_parser('config'); c.add_argument('--set-default'); c.set_defaults(func=cmd_config)
    lb=sub.add_parser('list-backups'); lb.add_argument('--root'); lb.set_defaults(func=cmd_list_backups)
    v=sub.add_parser('verify'); v.add_argument('backup'); v.set_defaults(func=cmd_verify)
    sc=sub.add_parser('scan-cache'); sc.add_argument('--clear',action='store_true'); sc.set_defaults(func=cmd_cache)
    cl=sub.add_parser('cleanup'); cl.add_argument('--game',required=True); cl.add_argument('--execute',action='store_true'); cl.set_defaults(func=lambda a: print(json.dumps(cleanup_game_leftovers(a.game, execute=a.execute), indent=2)))
    g=sub.add_parser('gui'); g.set_defaults(func=lambda a: launch_gui())
    w=sub.add_parser('web'); w.add_argument('--port',type=int,default=8765); w.set_defaults(func=lambda a: serve(a.port))
    args=p.parse_args(argv)
    if argv and argv[0]=='discover-all': setattr(args,'all',True)
    args.func(args)
if __name__=='__main__': main()
