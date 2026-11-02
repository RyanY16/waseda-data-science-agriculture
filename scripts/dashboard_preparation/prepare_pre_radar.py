"""Render the real pre-event VV crop on the same Mercator grid as the post-event backdrop."""
from pathlib import Path
import numpy as np,rasterio
from rasterio.warp import calculate_default_transform,reproject,Resampling
from PIL import Image
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(__file__).resolve().parent/'dist/data/radar_pre.webp'
with rasterio.open(ROOT/'work/japan_trial/data/post.tif') as post,rasterio.open(ROOT/'work/japan_adaptation/pre.tif') as pre:
    transform,width,height=calculate_default_transform(post.crs,'EPSG:3857',post.width,post.height,*post.bounds)
    arr=np.full((height,width),np.nan,dtype='float32')
    with np.errstate(divide='ignore',invalid='ignore'):src=10*np.log10(np.maximum(pre.read(1),1e-8))
    reproject(src,arr,src_transform=pre.transform,src_crs=pre.crs,dst_transform=transform,dst_crs='EPSG:3857',src_nodata=np.nan,dst_nodata=np.nan,resampling=Resampling.bilinear)
    gray=np.nan_to_num(np.clip((arr+25)/25,0,1)*255).astype('uint8')
    Image.fromarray(np.stack([gray,gray,gray,np.where(np.isfinite(arr),255,0).astype('uint8')],axis=-1)).save(OUT,quality=88)
    print('Saved pre-event radar:',OUT.name,width,height)
