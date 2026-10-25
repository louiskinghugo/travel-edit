"""旅行 Zine 素材清點、影片抽幀與圖片時間表預覽。說明見 references/自動化.md。"""
import argparse, hashlib, json, math, re, subprocess, tempfile, zipfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageOps

PIC={'.jpg','.jpeg','.png','.webp'}
def natural(p):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)',str(p))]
def run(args, cwd=None):
    r=subprocess.run([str(x) for x in args],capture_output=True,cwd=cwd)
    log=r.stderr.decode('utf8',errors='replace')
    if r.returncode: raise RuntimeError(log[-6000:])
    return log

def unpack(source,out):
    out.mkdir(parents=True,exist_ok=False)
    records=[]
    for n,z in enumerate(sorted(source.glob('*.zip')),1):
        target=out/f'zip-{n:02d}'
        with zipfile.ZipFile(z) as a:
            if sum(x.file_size for x in a.infolist())>2_000_000_000: raise ValueError('展開大小超過 2 GB，停止')
            for x in a.infolist():
                if '\\' in x.filename or not (target/x.filename).resolve().is_relative_to(target.resolve()):
                    raise ValueError('不安全 ZIP 路徑')
                if (x.external_attr>>16)&0o170000==0o120000: raise ValueError('不接受符號連結')
            a.extractall(target)
        for f in sorted(target.rglob('*'),key=natural):
            if f.is_file():
                rec={'來源':z.name,'路徑':str(f.resolve()),'大小':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
                if f.suffix.lower() in PIC:
                    with Image.open(f) as im: rec['尺寸']=im.size
                records.append(rec)
        make_sheets([f for f in sorted(target.rglob('*'),key=natural) if f.suffix.lower() in PIC],out,f'zip-{n:02d}')
    for f in sorted(source.glob('*.mp4')):
        records.append({'路徑':str(f.resolve()),'大小':f.stat().st_size,'sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
    (out/'inventory.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')

def make_sheets(files,out,prefix,labels=None):
    for start in range(0,len(files),12):
        sheet=Image.new('RGB',(1280,math.ceil(min(12,len(files)-start)/4)*280),'#dddddd')
        for j,f in enumerate(files[start:start+12]):
            with Image.open(f) as raw: im=ImageOps.contain(ImageOps.exif_transpose(raw).convert('RGB'),(310,245))
            x=j%4*320;y=j//4*280;sheet.paste(im,(x,y))
            ImageDraw.Draw(sheet).text((x+5,y+250),labels[start+j] if labels else f'{start+j:02d}',fill='black')
        sheet.save(out/f'{prefix}-{start//12+1:02d}.jpg')

def inspect_video(ffmpeg,source,out,interval):
    if interval<=0: raise ValueError('間隔須大於零')
    out.mkdir(parents=True,exist_ok=False)
    # 讀取格式，不將 ffmpeg 只探測輸入時的非零結束碼視為影片損壞。
    r=subprocess.run([str(ffmpeg),'-hide_banner','-i',str(source)],capture_output=True)
    meta=r.stderr.decode('utf8',errors='replace');(out/'metadata.txt').write_text(meta,encoding='utf8')
    m=re.search(r'Duration: (\d+):(\d+):([\d.]+)',meta)
    if not m: raise ValueError('無法讀取影片時長')
    duration=int(m[1])*3600+int(m[2])*60+float(m[3]);labels=[];files=[]
    for n,t in enumerate(range(0,math.ceil(duration),interval)):
        f=out/f'frame-{n:04d}.jpg'
        run([ffmpeg,'-hide_banner','-loglevel','error','-n','-ss',t,'-i',source,'-frames:v','1','-vf','scale=640:-2',f])
        if f.exists(): files.append(f);labels.append(f'{t//60:02d}:{t%60:02d}')
    make_sheets(files,out,'video',labels)
    log=run([ffmpeg,'-hide_banner','-i',source,'-vf',"select='gt(scene,0.25)',showinfo",'-an','-f','null','-'])
    candidates=[float(x) for x in re.findall(r'pts_time:([\d.]+)',log)]
    (out/'scene-candidates.json').write_text(json.dumps({'說明':'畫面變化候選，須人工判讀，不是鏡頭計數','秒':candidates},ensure_ascii=False,indent=2),encoding='utf8')
    if 'Audio:' in meta:
        log=run([ffmpeg,'-hide_banner','-i',source,'-af','volumedetect','-vn','-f','null','-'])
        (out/'audio-measurement.txt').write_text(log,encoding='utf8')

def render(ffmpeg,plan_path,out):
    plan=json.loads(plan_path.read_text(encoding='utf-8-sig'))
    width=int(plan.get('寬',960));height=int(plan.get('高',720));fps=int(plan.get('每秒影格',30))
    if width<=0 or height<=0 or width%2 or height%2 or not 1<=fps<=60: raise ValueError('請使用正偶數尺寸及 1–60 fps')
    if out.exists(): raise FileExistsError('輸出已存在，請換檔名')
    shots=plan['分鏡']
    if not shots: raise ValueError('沒有分鏡')
    for shot in shots:
        f=(plan_path.parent/shot['圖片']).resolve()
        if not f.is_file() or f.suffix.lower() not in PIC: raise ValueError('圖片不存在或格式不支援')
        if not 0<float(shot['秒數'])<=120: raise ValueError('單張時長需為 0–120 秒')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='zine-',dir=out.parent) as tmp:
        d=Path(tmp)
        for n,shot in enumerate(shots):
            f=(plan_path.parent/shot['圖片']).resolve()
            # Pillow 統一 EXIF 方向、色彩與畫布，避免影片直接拉伸跨頁。
            with Image.open(f) as raw: im=ImageOps.contain(ImageOps.exif_transpose(raw).convert('RGB'),(width,height))
            canvas=Image.new('RGB',(width,height),plan.get('背景','#eee9df'))
            canvas.paste(im,((width-im.width)//2,(height-im.height)//2));canvas.save(d/f'{n:04d}.png')
            run([ffmpeg,'-hide_banner','-loglevel','error','-n','-loop','1','-i',d/f'{n:04d}.png','-t',shot['秒數'],'-r',fps,'-c:v','libx264','-pix_fmt','yuv420p','-an',d/f'{n:04d}.mp4'])
        (d/'concat.txt').write_text(''.join(f"file '{n:04d}.mp4'\n" for n in range(len(shots))),encoding='ascii')
        run([ffmpeg,'-hide_banner','-loglevel','error','-n','-f','concat','-safe','1','-i','concat.txt','-c','copy','-movflags','+faststart',out.resolve()],cwd=d)
    print('已產生無聲硬切預覽：'+str(out))

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='mode',required=True)
    u=s.add_parser('unpack',help='安全解壓與圖片索引');u.add_argument('source',type=Path);u.add_argument('output',type=Path)
    v=s.add_parser('inspect',help='影片時間索引與量測');v.add_argument('source',type=Path);v.add_argument('output',type=Path);v.add_argument('--ffmpeg',required=True,type=Path);v.add_argument('--interval',type=int,default=6)
    r=s.add_parser('render',help='依圖片時間表輸出無聲預覽');r.add_argument('plan',type=Path);r.add_argument('output',type=Path);r.add_argument('--ffmpeg',required=True,type=Path)
    a=p.parse_args()
    if a.mode=='unpack':unpack(a.source,a.output)
    elif a.mode=='inspect':inspect_video(a.ffmpeg,a.source,a.output,a.interval)
    else:render(a.ffmpeg,a.plan,a.output)
if __name__=='__main__':main()
