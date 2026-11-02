"""Package the unchanged classifier raster, across all land in the locked study region."""
from pathlib import Path
import json,hashlib,math
import numpy as np,rasterio
from rasterio.warp import transform_geom,transform_bounds,reproject,Resampling
from rasterio.features import geometry_window,geometry_mask
from rasterio.transform import from_bounds
from PIL import Image
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];OUT=HERE/'dist/data'
g=json.loads((OUT/'fields.geojson').read_text());coords=[]
for f in g['features']:
 c=f['geometry']['coordinates'];polys=[c] if f['geometry']['type']=='Polygon' else c
 for poly in polys:
  for ring in poly:coords.extend(ring)
a=np.array(coords);west,south=a.min(0);east,north=a.max(0);dx=(east-west)*.025;dy=(north-south)*.025;west-=dx;east+=dx;south-=dy;north+=dy
poly={'type':'Polygon','coordinates':[[[west,south],[east,south],[east,north],[west,north],[west,south]]]}
source=ROOT/'outputs/japan_rerun/mask.tif'
with rasterio.open(source) as ds:
 projected=transform_geom('EPSG:4326',ds.crs,poly);window=geometry_window(ds,[projected],boundless=True);t=ds.window_transform(window)
 data=ds.read(1,window=window,boundless=True,fill_value=255)
 inside=geometry_mask([projected],out_shape=data.shape,transform=t,invert=True)
 cell_ha=abs(ds.transform.a*ds.transform.e)/10000
 counts={name:int(np.sum(inside & test)) for name,test in [('flood',data==1),('not_detected',data==0),('unknown',data==255)]}
 assert sum(counts.values())==int(inside.sum())
 left,bottom,right,top=transform_bounds('EPSG:4326','EPSG:3857',west,south,east,north)
 pixel=10/math.cos(math.radians((north+south)/2));w=math.ceil((right-left)/pixel);h=math.ceil((top-bottom)/pixel)
 warped=np.full((h,w),255,dtype='uint8');dst=from_bounds(left,bottom,right,top,w,h)
 reproject(data,warped,src_transform=t,src_crs=ds.crs,dst_transform=dst,dst_crs='EPSG:3857',src_nodata=255,dst_nodata=255,resampling=Resampling.nearest)
 rgba=np.zeros((h,w,4),dtype='uint8');rgba[warped==1]=[19,117,189,210];rgba[warped==255]=[145,149,151,120]
 Image.fromarray(rgba).save(OUT/'flood_footprint.png')
 meta={'bounds':[[south,west],[north,east]],'source':'Unchanged Japanese Random Forest mask; 13 October 2019 retrospective detection','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'pixel_counts':counts,'area_ha':{k:v*cell_ha for k,v in counts.items()},'pixel_area_ha':cell_ha,'footprint_dimensions':[w,h],'method':'Area totals count native 10 m UTM pixel centres inside the padded crop-study rectangle. Footprint is nearest-neighbour reprojected to Web Mercator for display. Covers all land, not just paddies.','limitations':['Not an independently validated flood boundary; classification errors and permanent water may be present.','Grey means unknown or outside raster coverage; transparent means no flood detected, not proven dry.','All-land footprint hectares differ from area-weighted parcel flooded hectares.']}
(OUT/'flood_footprint.json').write_text(json.dumps(meta,indent=2));print(json.dumps(meta,indent=2))
