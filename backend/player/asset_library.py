"""Extract installed Unity artwork locally; no runtime templates or ownership claims.

The only input is this emulator's installed package. Reassemble numbered Unity
split files before decoding, keep every object identity distinct, and publish an
index atomically only after extraction. All binaries live under ignored local
calibration storage. Names from assets are data, never filesystem paths.
"""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import time
import zipfile

PACKAGE = "com.TechTreeGames.TheTower"
DECODER_VERSION = "1.25.3"
SCHEMA = 1
MAX_APK = 2 * 1024**3
MAX_UNPACKED = 3 * 1024**3


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.pending.json')
    temp.write_text(json.dumps(value, indent=1), encoding='utf-8')
    os.replace(temp, path)


def sha_file(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def installed(serial, shell=None):
    from device import adbclient
    shell = shell or adbclient.shell
    raw = shell(serial, f'pm path {PACKAGE}').decode(errors='replace')
    apks = []
    for row in raw.splitlines():
        if not row.startswith('package:'):
            continue
        path = row[8:].strip()
        if not path.startswith('/data/app/') or not path.endswith('.apk') or '\n' in path:
            raise RuntimeError('Game package returned an unsupported installation path')
        size = int(shell(serial, 'stat -c %s ' + shlex.quote(path)).decode().strip())
        if not 0 < size <= MAX_APK:
            raise RuntimeError('Installed APK size is outside supported bounds')
        apks.append({'path':path, 'size':size})
    if not apks:
        raise RuntimeError('The Tower is not installed on the selected emulator')
    info = shell(serial, f'dumpsys package {PACKAGE}').decode(errors='replace')
    def field(key):
        match = re.search(r'\b'+key+r'=([^\s]+)', info)
        return match.group(1) if match else 'unknown'
    identity = {'package':PACKAGE, 'version':field('versionName'), 'version_code':field('versionCode'),
                'serial':serial, 'apks':sorted(apks,key=lambda a:a['path']), 'decoder':DECODER_VERSION, 'schema':SCHEMA}
    identity['key'] = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:24]
    return identity


def unity_members(archive):
    """Yield complete Unity files. ZIP names are never extracted as paths."""
    groups = {}
    count = 0
    for item in archive.infolist():
        name = item.filename
        if not name.startswith('assets/bin/Data/') or item.is_dir() or '/Managed/' in name:
            continue
        parts = PurePosixPath(name).parts
        if '..' in parts or '\\' in name:
            raise ValueError('Unsafe Unity archive member')
        count += item.file_size
        if count > MAX_UNPACKED:
            raise ValueError('Unity assets exceed the extraction size limit')
        match = re.fullmatch(r'(.+)\.split(\d+)',name)
        base, index = (match[1],int(match[2])) if match else (name,None)
        group = groups.setdefault(base,{})
        if index in group:
            raise ValueError('Duplicate Unity archive member')
        group[index] = name
    for base, parts in groups.items():
        if None in parts:
            if len(parts)!=1:
                raise ValueError('Both split and complete Unity files found')
            yield base, archive.read(parts[None])
        else:
            if sorted(parts)!=list(range(len(parts))):
                raise ValueError(f'Incomplete numbered Unity asset: {PurePosixPath(base).name}')
            yield base, b''.join(archive.read(parts[i]) for i in range(len(parts)))


def load_unity(paths, progress, check_stop):
    try:
        import UnityPy
    except ImportError as e:
        raise RuntimeError('Install the application requirements to enable artwork extraction (UnityPy is missing)') from e
    if UnityPy.__version__ != DECODER_VERSION:
        raise RuntimeError(f'Artwork extraction requires UnityPy {DECODER_VERSION}; update application requirements')
    environment = UnityPy.Environment()
    loaded = {}
    for apk in paths:
        with zipfile.ZipFile(apk) as archive:
            members = unity_members(archive)
            for number, (name,data) in enumerate(members,1):
                check_stop()
                if number % 20 == 1:
                    progress(f'Decoding Unity asset files from {Path(apk).name}: {number} read')
                # Full archive identity avoids collisions; UnityPy registers
                # the basename too for inter-file texture resource references.
                digest=hashlib.sha256(data).hexdigest()
                if name in loaded:
                    if loaded[name]!=digest:
                        raise RuntimeError('Installed APKs contain conflicting Unity asset names')
                    continue
                loaded[name]=digest
                environment.load_file(data, name=name)
    return environment


def export_images(environment, folder, progress, check_stop):
    images = folder/'images'
    images.mkdir(parents=True,exist_ok=True)
    objects = [o for o in environment.objects if o.type.name in ('Sprite','Texture2D')]
    if not objects:
        raise RuntimeError('No Unity artwork found in the installed package')
    records, errors = [], []
    for number,obj in enumerate(objects,1):
        check_stop()
        name = obj.peek_name() or ''
        identity = f'{obj.assets_file.name}:{obj.path_id}:{obj.type.name}'
        record = {'id':hashlib.sha256(identity.encode()).hexdigest()[:24], 'name':name,
                  'type':obj.type.name,'source_file':obj.assets_file.name,'path_id':obj.path_id}
        try:
            data=obj.parse_as_object()
            if obj.type.name=='Texture2D' and (getattr(data,'m_Width',1)==0 or getattr(data,'m_Height',1)==0):
                errors.append(dict(record,kind='empty_placeholder',error='Empty texture placeholder: no source pixels in the installed package'))
                continue
            image=data.image.convert('RGBA')
            if not 0 < image.width*image.height <= 64*1024**2:
                raise ValueError('Texture dimensions outside supported bounds')
            stream=io.BytesIO()
            image.save(stream,format='PNG',compress_level=3)
            payload=stream.getvalue()
            digest=hashlib.sha256(payload).hexdigest()
            path=images/(digest+'.png')
            if not path.exists() or sha_file(path)!=digest:
                temp=path.with_suffix('.pending')
                temp.write_bytes(payload)
                os.replace(temp,path)
            record.update(file='images/'+path.name, sha256=digest, width=image.width,height=image.height)
            records.append(record)
        except Exception as e:
            errors.append(dict(record,error=str(e)[:220]))
        if number % 20 == 0 or number==len(objects):
            progress(f'Extracting artwork: {number}/{len(objects)} objects checked; {len(records)} decoded',
                     units={'completed':number,'total':len(objects),'label':'Image objects'})
    if not records:
        raise RuntimeError('None of the installed artwork could be decoded')
    return records,errors


def valid_index(folder,index):
    if index.get('schema')!=SCHEMA or index.get('decoder')!=DECODER_VERSION or not index.get('images'):
        return False
    # Validate every distinct image once; object aliases share content files.
    files={r.get('file'):r.get('sha256') for r in index['images']}
    for name,digest in files.items():
        if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest) or name != f'images/{digest}.png':
            return False
        path=folder/name
        if not path.is_file() or sha_file(path)!=digest:
            return False
    return True


def acquire(calibration_folder, serial, progress, check_stop):
    from device import adbclient
    identity=installed(serial)
    folder=Path(calibration_folder)/'asset_library'/identity['key']
    folder.mkdir(parents=True,exist_ok=True)
    progress(f"Reading installed game {identity['version']}")
    paths=[]
    for number,apk in enumerate(identity['apks']):
        check_stop()
        target=folder/f'package_{number}.apk'
        stamp=target.with_suffix('.sha256')
        if not (target.is_file() and stamp.is_file() and target.stat().st_size==apk['size'] and sha_file(target)==stamp.read_text()):
            temp=target.with_suffix('.pending')
            last=[0.0]
            def transferred(count):
                now=time.monotonic()
                if now-last[0] >= .5 or count==apk['size']:
                    last[0]=now
                    progress(f"Reading installed package {number+1}/{len(identity['apks'])}: {count//1048576}/{apk['size']//1048576} MB",
                             units={'completed':count,'total':apk['size'],'label':'Package bytes'})
            with temp.open('wb') as sink:
                size=adbclient.exec_to(serial,'cat '+shlex.quote(apk['path']),sink,progress=transferred,check_stop=check_stop)
            if size!=apk['size'] or not zipfile.is_zipfile(temp):
                raise RuntimeError('Installed package copy was incomplete; retry extraction')
            os.replace(temp,target)
            stamp.write_text(sha_file(target))
        paths.append(target)
    try:
        index=json.loads((folder/'index.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):
        index={}
    if index.get('installation')==identity and valid_index(folder,index):
        progress(f"Using {len(index['images'])} verified local image objects from game {identity['version']}")
        return folder,index
    progress('Decoding the installed Unity assets, including numbered file parts')
    environment=load_unity(paths,progress,check_stop)
    records,errors=export_images(environment,folder,progress,check_stop)
    index={'schema':SCHEMA,'decoder':DECODER_VERSION,'installation':identity,'created_at':time.time(),
           'images':records,'errors':errors,'unique_images':len({r['file'] for r in records}),
           'package_hashes':[sha_file(p) for p in paths]}
    check_stop()
    atomic_json(folder/'index.json',index)
    return folder,index


def map_targets(index, definitions):
    """Explicit source names, never guessed ownership or fuzzy filename matches."""
    result={}
    for rel,spec in definitions.items():
        names={n.casefold() for n in spec['names']}
        matches=[r for r in index['images'] if r['name'].casefold() in names and r['type']=='Sprite']
        if not matches:
            matches=[r for r in index['images'] if r['name'].casefold() in names and r['type']=='Texture2D']
        # Duplicate sprite/texture aliases are not different appearances.
        matches=list({r['sha256']:r for r in matches}.values())
        result[rel]={'status':'mapped' if matches else 'missing_source','candidates':matches,
                     'screen':spec.get('screen'),'verification':'pending'}
    return result
