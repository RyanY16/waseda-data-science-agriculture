"""Download and mosaic official GSI estimated-depth tiles; do not infer numeric depths."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import math,json,io,hashlib
import requests,numpy as np
from PIL import Image
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OUT=HERE/'dist/data';CACHE=ROOT/'work/flood_depth/tiles';CACHE.mkdir(parents=True,exist_ok=True)
meta=json.loads((OUT/'flood_footprint.json').read_text());(south,west),(north,east)=meta['bounds'];z=15;n=2**z;layer='20191012typhoon19_abukuma_1014dansaizu'
def tx(lon):return int((lon+180)/360*n)
def ty(lat):return int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n)
x0,x1=tx(west),tx(east);y0,y1=ty(north),ty(south)
def download(pair):
 x,y=pair;path=CACHE/f'{x}_{y}.png';url=f'https://cyberjapandata.gsi.go.jp/xyz/{layer}/{z}/{x}/{y}.png'
 if path.exists():return x,y,200,path.read_bytes()
 for attempt in range(3):
  try:
   r=requests.get(url,timeout=25)
   if r.status_code==200:path.write_bytes(r.content);return x,y,200,r.content
   if r.status_code==404:return x,y,404,None
   r.raise_for_status()
  except requests.RequestException:
   if attempt==2:raise
mosaic=Image.new('RGBA',((x1-x0+1)*256,(y1-y0+1)*256),(113,121,125,90));counts={};manifest=[]
for x,y,status,body in ThreadPoolExecutor(8).map(download,[(x,y) for x in range(x0,x1+1) for y in range(y0,y1+1)]):
 counts[status]=counts.get(status,0)+1
 if body:
  a=np.array(Image.open(io.BytesIO(body)).convert('RGBA'));valid=a[:,:,3]>0;result=np.full_like(a,[113,121,125,90]);result[valid]=a[valid];mosaic.paste(Image.fromarray(result),((x-x0)*256,(y-y0)*256))
 manifest.append({'x':x,'y':y,'http_status':status,'sha256':hashlib.sha256(body).hexdigest() if body else None})
mosaic.save(OUT/'gsi_depth.png')
legend_url=f'https://maps.gsi.go.jp/legend/{layer}.png';r=requests.get(legend_url,timeout=25);r.raise_for_status();(OUT/'gsi_depth_legend.png').write_bytes(r.content)
def lat(y):return math.degrees(math.atan(math.sinh(math.pi*(1-2*y/n))))
out={'source':'Geospatial Information Authority of Japan (GSI)','layer_id':layer,'bounds':[[lat(y1+1),x0/n*360-180],[lat(y0),(x1+1)/n*360-180]],'study_bounds':meta['bounds'],'zoom':z,'max_native_zoom':15,'display_scale_m':[0,2,4,6,6.5],'top_scale_is_open_ended':True,'aerial_photography_date':'2019-10-13','information_cutoff':'2019-10-14 18:00 JST','source_page':'https://www.gsi.go.jp/BOUSAI/R1.taihuu19gou.html','tile_documentation':'https://maps.gsi.go.jp/development/ichiran.html','legend_url':legend_url,'tile_url':f'https://cyberjapandata.gsi.go.jp/xyz/{layer}/{{z}}/{{x}}/{{y}}.png','http_counts':counts,'tiles':manifest,'modification':'Original tile colours preserved; transparent and unavailable locations shown grey as unknown. Mosaic assembled from Web Mercator XYZ tiles.','limitations':['Estimated from event information, aerial photographs and terrain; not measurements at every location.','Unknown/transparent locations are not confirmed dry. Extent can omit flooding or include false inundation.','Not contemporaneous with the 13 October 05:42 JST SAR acquisition; not guaranteed maximum depth.','Colour map only: numeric field depths are not decoded, no flood duration, and crop-loss model remains unchanged.'],'mosaic_sha256':hashlib.sha256((OUT/'gsi_depth.png').read_bytes()).hexdigest()}
(OUT/'gsi_depth.json').write_text(json.dumps(out,indent=2));print(json.dumps({'tiles':counts,'dimensions':mosaic.size,'bytes':(OUT/'gsi_depth.png').stat().st_size},indent=2))
