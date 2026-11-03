"""Render the original held-out 0/1 label grid and dashboard predictions on identical support."""
import argparse
import json
from pathlib import Path
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject, Resampling, transform_bounds

p = argparse.ArgumentParser()
for key in ('labels', 'regions', 'predictions'):
    p.add_argument(key, type=Path)
args = p.parse_args()
out = Path(__file__).resolve().parents[1] / 'site/data'
with rasterio.open(args.labels) as labels, rasterio.open(args.regions) as regions, rasterio.open(args.predictions) as predictions:
    assert all((s.transform, s.crs, s.shape) == (labels.transform, labels.crs, labels.shape) for s in (regions, predictions))
    y, z, pred = (s.read(1) for s in (labels, regions, predictions))
    valid = (z == 3) & np.isin(y, [0,1]) & np.isin(pred, [0,1])
    rows, cols = np.where(valid)
    win = rasterio.windows.Window(cols.min(), rows.min(), cols.max()-cols.min()+1, rows.max()-rows.min()+1)
    ys, xs = win.toslices()
    src = labels.window_transform(win)
    transform, w, h = calculate_default_transform(labels.crs, 'EPSG:3857', int(win.width), int(win.height), *labels.window_bounds(win))
    palette = np.array([[239,232,209,255],[19,117,189,255],[146,153,157,255]], np.uint8)
    for name, grid in [('test_labels', y), ('test_prediction', pred)]:
        a = np.where(valid, grid, 2)[ys,xs].astype(np.uint8)
        b = np.full((h,w),2,np.uint8)
        reproject(a,b,src_transform=src,src_crs=labels.crs,dst_transform=transform,dst_crs='EPSG:3857',resampling=Resampling.nearest)
        Image.fromarray(palette[b]).save(out / (name+'.png'))
    w_,s,e,n = transform_bounds('EPSG:3857','EPSG:4326',*array_bounds(h,w,transform))
    tp, fp, fn, tn = [int((valid & (y==a) & (pred==b)).sum()) for a,b in [(1,1),(0,1),(1,0),(0,0)]]
    summary = dict(bounds=[[s,w_],[n,e]], source='Igarashi & Wakabayashi (2023), Figure 2, digitized approximate labels', partition='Southern spatial holdout; latitude < 37.260° N; same 2019 event', tp=tp,fp=fp,fn=fn,tn=tn, pixels=tp+fp+fn+tn, iou=tp/(tp+fp+fn), f1=2*tp/(2*tp+fp+fn))
    (out/'test_comparison.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(summary)
