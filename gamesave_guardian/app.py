
#!/usr/bin/env python3
"""Universal Game Save Guardian: CLI, Tk GUI, and tiny web UI for game save backup/restore."""
from __future__ import annotations
import argparse, base64, dataclasses, datetime as dt, fnmatch, hashlib, html, json, os, queue, re, shutil, sys, tarfile, tempfile, threading, time, webbrowser
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
    if low in {'save','saves','saved games','savegames','profiles','remote','userdata'}: return True
    return any(Path(f).suffix.lower() in exts or 'save' in f.lower() or 'profile' in f.lower() for f in files[:80])

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
        blacklist={'docker','powershell','windowspowershell','windows','mozilla','internet-explorer','claude','netlify','everything','flingtrainer','microsoft','google','chrome','edge','nodejs','python','npm','hermes'}
        slug=safe_slug(title).lower()
        if slug in blacklist or any(x in slug for x in ['docker','powershell','mozilla','internet-explorer','chrome','microsoft','python']):
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
                dirs[:]=[d for d in dirs if d.lower() not in {'cache','shadercache','crashpad','logs','log','temp','tmp','__pycache__','screenshots','captures','webcache','gpu_cache'}]
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
            meta=json.loads(mf.read_text(encoding='utf-8')); size=sum(f.stat().st_size for f in mf.parent.rglob('*') if f.is_file())
            out.append({'game':meta.get('game'),'path':str(mf.parent),'path_windows':wsl_to_win(str(mf.parent)),'created_at':meta.get('created_at'),'size':size,'size_human':human_size(size),'sources':len(meta.get('sources',[]))})
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

def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    win=tk.Tk(); win.title(APP_NAME); win.geometry('1220x780'); win.configure(bg='#0b1020')
    style=ttk.Style(win); style.theme_use('clam')
    style.configure('.',background='#0b1020',foreground='#e5eefc',fieldbackground='#111827',font=('Segoe UI',10))
    style.configure('Treeview',background='#111827',fieldbackground='#111827',foreground='#e5eefc',rowheight=34,borderwidth=0)
    style.configure('Treeview.Heading',background='#172033',foreground='#e5eefc',font=('Segoe UI Semibold',10),padding=8)
    style.map('Treeview',background=[('selected','#1e3a8a')]); style.configure('Accent.TButton',background='#38bdf8',foreground='#06111f',padding=(14,9),font=('Segoe UI Semibold',10)); style.configure('Ghost.TButton',background='#172033',foreground='#e5eefc',padding=(12,8))
    games=[]; selected=set(); q=queue.Queue(); dest=tk.StringVar(value=wsl_to_win(str(backup_root()))); status=tk.StringVar(value='Ready. Discover Saves scans all visible drives/users/store manifests/save roots.'); progress=tk.DoubleVar(value=0); search=tk.StringVar()
    header=tk.Frame(win,bg='#0b1020'); header.pack(fill='x',padx=24,pady=(20,10)); tk.Label(header,text='Save Guardian',bg='#0b1020',fg='#e5eefc',font=('Segoe UI Semibold',27)).pack(side='left'); tk.Label(header,text='professional all-PC game-save backup and restore',bg='#0b1020',fg='#94a3b8',font=('Segoe UI',11)).pack(side='left',padx=16,pady=(10,0))
    main=tk.Frame(win,bg='#0b1020'); main.pack(fill='both',expand=True,padx=24,pady=10); side=tk.Frame(main,bg='#111827',width=280); side.pack(side='left',fill='y'); side.pack_propagate(False); content=tk.Frame(main,bg='#0b1020'); content.pack(side='left',fill='both',expand=True,padx=(18,0))
    tk.Label(side,text='Backup destination',bg='#111827',fg='#94a3b8',font=('Segoe UI Semibold',9)).pack(anchor='w',padx=18,pady=(20,6)); ttk.Entry(side,textvariable=dest).pack(fill='x',padx=18); ttk.Button(side,text='Browse',style='Ghost.TButton',command=lambda: dest.set(filedialog.askdirectory() or dest.get())).pack(fill='x',padx=18,pady=8)
    def run_bg(label,fn):
        status.set(label); progress.set(10)
        def work():
            try: q.put(('ok',fn()))
            except Exception as e: q.put(('err',e))
        threading.Thread(target=work,daemon=True).start()
    def populate():
        term=search.get().lower().strip(); tree.delete(*tree.get_children())
        for g in games:
            if term and term not in g['title'].lower() and term not in g['platform'].lower(): continue
            tree.insert('', 'end', iid=g['title'], values=('☑' if g['title'] in selected else '☐',g['title'],g['platform'],g['location_count'],g['size_human'],g.get('latest_write_iso') or '—',str(g['confidence'])+'%'))
    def poll():
        nonlocal games
        try:
            k,v=q.get_nowait(); progress.set(100)
            if k=='err': status.set('Failed'); messagebox.showerror('Operation failed',str(v))
            elif isinstance(v,list): games=v; status.set(f'Discovered {len(games)} games/save groups. Select games and back them up.'); populate(); update_stats()
            else: status.set(str(v)); messagebox.showinfo('Done',str(v))
        except queue.Empty:
            if 0<progress.get()<94: progress.set(progress.get()+1.2)
        win.after(180,poll)
    def update_stats():
        vals=[str(len(games)),str(sum(g['location_count'] for g in games)),human_size(sum(g['byte_count'] for g in games)),(max([g['latest_write'] for g in games] or [0]))]
        vals[3]=(iso(vals[3]) or '—').replace('T',' ')
        for lab,val in zip(cards,vals): cards[lab].config(text=val)
    ttk.Button(side,text='Discover Saves',style='Accent.TButton',command=lambda: run_bg('Scanning all PC save locations...',lambda: discover_all_games(refresh=True))).pack(fill='x',padx=18,pady=(22,8))
    ttk.Button(side,text='Backup Selected',style='Accent.TButton',command=lambda: run_bg('Backing up selected games...',lambda: '\n'.join(wsl_to_win(str(backup_record(g,dest.get()))) for g in games if g['title'] in selected) or 'Nothing selected')).pack(fill='x',padx=18,pady=8)
    ttk.Button(side,text='Backup Browser',style='Ghost.TButton',command=lambda: messagebox.showinfo('Backups','\n'.join(f"{b['created_at']}  {b['game']}  {b['path_windows']}" for b in list_backups(dest.get())) or 'No backups found')).pack(fill='x',padx=18,pady=8)
    ttk.Progressbar(side,variable=progress).pack(side='bottom',fill='x',padx=18,pady=8); tk.Label(side,textvariable=status,wraplength=230,justify='left',bg='#111827',fg='#94a3b8').pack(side='bottom',fill='x',padx=18,pady=18)
    stats=tk.Frame(content,bg='#0b1020'); stats.pack(fill='x'); cards={}
    for lab in ['Games','Locations','Total size','Latest']:
        card=tk.Frame(stats,bg='#111827',padx=18,pady=13); card.pack(side='left',fill='x',expand=True,padx=(0,12)); tk.Label(card,text=lab.upper(),bg='#111827',fg='#94a3b8',font=('Segoe UI Semibold',8)).pack(anchor='w'); v=tk.Label(card,text='—',bg='#111827',fg='#e5eefc',font=('Segoe UI Semibold',18)); v.pack(anchor='w'); cards[lab]=v
    tools=tk.Frame(content,bg='#0b1020'); tools.pack(fill='x',pady=12); ttk.Entry(tools,textvariable=search).pack(side='left',fill='x',expand=True); ttk.Button(tools,text='Filter',style='Ghost.TButton',command=populate).pack(side='left',padx=8); ttk.Button(tools,text='Select Visible',style='Ghost.TButton',command=lambda: (selected.update(tree.get_children()),populate())).pack(side='left')
    pane=tk.PanedWindow(content,bg='#0b1020',sashwidth=8); pane.pack(fill='both',expand=True); left=tk.Frame(pane,bg='#111827'); right=tk.Frame(pane,bg='#111827'); pane.add(left,minsize=650); pane.add(right,minsize=330)
    cols=('sel','game','platform','loc','size','latest','conf'); tree=ttk.Treeview(left,columns=cols,show='headings');
    for col,txt,w in [('sel','✓',42),('game','Game',280),('platform','Platform',100),('loc','Locations',80),('size','Size',95),('latest','Latest save',150),('conf','Confidence',95)]: tree.heading(col,text=txt); tree.column(col,width=w)
    tree.pack(fill='both',expand=True); detail=tk.Text(right,bg='#111827',fg='#e5eefc',relief='flat',wrap='word',font=('Consolas',10),padx=14,pady=14); detail.pack(fill='both',expand=True); detail.insert('end','Select a game to inspect every save path and why it was chosen.\n')
    def click(e):
        iid=tree.identify_row(e.y)
        if not iid: return
        if tree.identify_column(e.x)=='#1': selected.symmetric_difference_update({iid}); populate()
        g=next((x for x in games if x['title']==iid),None); detail.delete('1.0','end')
        if g:
            detail.insert('end',f"{g['title']}\nPlatform: {g['platform']}\nConfidence: {g['confidence']}%\nFiles: {g['file_count']}  Size: {g['size_human']}\nInstall: {g.get('install_path') or 'unknown'}\n\n")
            for i,s in enumerate(g['sources'],1): detail.insert('end',f"{i}. {s['path_windows']}\n   {s['file_count']} files, {s['size_human']}, confidence {s['confidence']}%\n   {s['reason']}\n   latest: {s.get('latest_write_iso') or 'unknown'}\n\n")
    tree.bind('<ButtonRelease-1>',click); poll(); win.mainloop()

WEB_CSS='body{margin:0;background:radial-gradient(circle at top left,#12355b,#08111f 40%,#070b14);color:#e5eefc;font-family:Inter,Segoe UI,system-ui}.wrap{max-width:1180px;margin:auto;padding:34px}.card{background:rgba(15,23,42,.86);border:1px solid rgba(148,163,184,.18);border-radius:24px;padding:22px;margin:16px 0;box-shadow:0 24px 70px rgba(0,0,0,.3)}h1{font-size:42px;letter-spacing:-.04em}.btn{background:linear-gradient(135deg,#38bdf8,#a78bfa);padding:12px 18px;border-radius:14px;color:#06111f;text-decoration:none;font-weight:800}table{width:100%;border-collapse:collapse}td,th{padding:13px;border-bottom:1px solid rgba(148,163,184,.14);text-align:left}th{color:#94a3b8;font-size:12px;text-transform:uppercase}.pill{border-radius:999px;background:#172554;color:#bfdbfe;padding:4px 9px;font-weight:700}'
class Web(BaseHTTPRequestHandler):
    def do_GET(self):
        games=discover_all_games() if self.path.startswith('/discover') else []
        body=f"<html><head><title>{APP_NAME}</title><style>{WEB_CSS}</style></head><body><div class=wrap><h1>{APP_NAME}</h1><p>Polished local web dashboard for all-PC game save discovery and backups.</p><a class=btn href='/discover'>Discover all saves</a>"
        if games:
            body+=f"<div class=card><b>{len(games)}</b> games/save groups • {human_size(sum(g['byte_count'] for g in games))}</div><div class=card><table><tr><th>Game</th><th>Platform</th><th>Locations</th><th>Size</th><th>Latest</th><th>Confidence</th></tr>"
            for g in games[:300]: body+=f"<tr><td>{html.escape(g['title'])}</td><td><span class=pill>{html.escape(g['platform'])}</span></td><td>{g['location_count']}</td><td>{g['size_human']}</td><td>{g.get('latest_write_iso') or '—'}</td><td>{g['confidence']}%</td></tr>"
            body+='</table></div>'
        body+='</div></body></html>'
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.end_headers(); self.wfile.write(body.encode())
def serve(port:int):
    http=ThreadingHTTPServer(('127.0.0.1', port), Web); print(f'Web UI http://127.0.0.1:{port}'); webbrowser.open(f'http://127.0.0.1:{port}'); http.serve_forever()

def main(argv=None):
    p=argparse.ArgumentParser(description=APP_NAME); sub=p.add_subparsers(required=True)
    d=sub.add_parser('discover',aliases=['discover-all']); d.add_argument('--game'); d.add_argument('--all',action='store_true'); d.add_argument('--json',action='store_true'); d.add_argument('--refresh',action='store_true'); d.set_defaults(func=cmd_discover)
    b=sub.add_parser('backup'); b.add_argument('--game'); b.add_argument('--destination'); b.add_argument('--all',action='store_true'); b.add_argument('--refresh',action='store_true'); b.set_defaults(func=cmd_backup)
    r=sub.add_parser('restore'); r.add_argument('backup'); r.add_argument('--target-root'); r.add_argument('--dry-run', action='store_true'); r.add_argument('--preview',action='store_true'); r.set_defaults(func=cmd_restore)
    c=sub.add_parser('config'); c.add_argument('--set-default'); c.set_defaults(func=cmd_config)
    lb=sub.add_parser('list-backups'); lb.add_argument('--root'); lb.set_defaults(func=cmd_list_backups)
    v=sub.add_parser('verify'); v.add_argument('backup'); v.set_defaults(func=cmd_verify)
    sc=sub.add_parser('scan-cache'); sc.add_argument('--clear',action='store_true'); sc.set_defaults(func=cmd_cache)
    g=sub.add_parser('gui'); g.set_defaults(func=lambda a: launch_gui())
    w=sub.add_parser('web'); w.add_argument('--port',type=int,default=8765); w.set_defaults(func=lambda a: serve(a.port))
    args=p.parse_args(argv)
    if len(sys.argv)>1 and sys.argv[1]=='discover-all': setattr(args,'all',True)
    args.func(args)
if __name__=='__main__': main()
