"""Build a public-data parcel dashboard dataset, without synthetic field geometry."""
from pathlib import Path
import csv,hashlib,json,math,time
import numpy as np,rasterio,shapefile
from rasterio.features import geometry_mask,geometry_window
from rasterio.warp import transform_geom,reproject,Resampling,calculate_default_transform
from PIL import Image

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent;OUT=HERE/'dist/data';OUT.mkdir(parents=True,exist_ok=True)
shp=next((ROOT/'work/rice_loss/parcel_source').glob('*.shp'))
reader=shapefile.Reader(str(shp),encoding='cp932')
predpath=ROOT/'outputs/japan_rerun/mask.tif'
with rasterio.open(ROOT/'work/japan_adaptation/pre.tif') as ds:pre=ds.read()
with rasterio.open(ROOT/'work/japan_adaptation/post.tif') as ds:post=ds.read()
with np.errstate(divide='ignore',invalid='ignore'):
    pre=10*np.log10(pre);post=10*np.log10(post)
def ringarea(r):
    a=np.asarray(r);return abs(float(np.dot(a[:-1,0],a[1:,1])-np.dot(a[1:,0],a[:-1,1])))/2
def area(g):
    ps=[g['coordinates']] if g['type']=='Polygon' else g['coordinates']
    return sum(ringarea(p[0])-sum(ringarea(h) for h in p[1:]) for p in ps)

features=[];started=time.time()
with rasterio.open(predpath) as ds:
    pred=ds.read(1)
    for sr in reader.iterShapeRecords():
        rec=sr.record.as_dict()
        if rec['Kind']!='田' or not(140.32<=rec['CentroidX']<=140.44 and 37.35<=rec['CentroidY']<=37.44):continue
        geo=sr.shape.__geo_interface__
        projected=transform_geom('EPSG:6668',ds.crs,geo)
        area_ha=area(projected)/10000
        w=geometry_window(ds,[projected]);rr,cc=int(w.row_off),int(w.col_off);hh,ww=int(w.height),int(w.width)
        inside=geometry_mask([projected],(hh,ww),rasterio.windows.transform(w,ds.transform),invert=True,all_touched=False)
        pp=pred[rr:rr+hh,cc:cc+ww]
        valid=inside&(pp!=255);n=int(valid.sum());f=int(((pp==1)&valid).sum())
        fraction=f/n if n else None
        props=dict(id=str(rec['id']),area_ha=round(area_ha,7),boundary_year=int(rec['OpenYear']),edit_year=int(rec['EditYear']),
            lon=round(rec['CentroidX'],7),lat=round(rec['CentroidY'],7),
            valid_pixels=n,flood_pixels=f,flood_fraction=round(fraction,7) if n else None,
            flooded_area_ha=round(area_ha*fraction,7) if n else None,
            pre_vv_db=round(float(pre[0,rr:rr+hh,cc:cc+ww][valid].mean()),2) if n else None,
            post_vv_db=round(float(post[0,rr:rr+hh,cc:cc+ww][valid].mean()),2) if n else None,
            pre_vh_db=round(float(pre[1,rr:rr+hh,cc:cc+ww][valid].mean()),2) if n else None,
            post_vh_db=round(float(post[1,rr:rr+hh,cc:cc+ww][valid].mean()),2) if n else None)
        features.append(dict(type='Feature',properties=props,geometry=transform_geom('EPSG:6668','EPSG:4326',geo,precision=6)))
        if len(features)%3000==0:print('parcels',len(features),'seconds',round(time.time()-started,1),flush=True)
    # Georeferenced radar background from actual data, warped to Leaflet's Mercator grid.
    with rasterio.open(ROOT/'work/japan_trial/data/post.tif') as raw:
        src=10*np.log10(np.maximum(raw.read(1),1e-8))
        transform,width,height=calculate_default_transform(raw.crs,'EPSG:3857',raw.width,raw.height,*raw.bounds)
        arr=np.full((height,width),np.nan,dtype='float32')
        reproject(src,arr,src_transform=raw.transform,src_crs=raw.crs,dst_transform=transform,dst_crs='EPSG:3857',src_nodata=np.nan,dst_nodata=np.nan,resampling=Resampling.bilinear)
        gray=np.nan_to_num(np.clip((arr+25)/25,0,1)*255).astype('uint8')
        rgb=np.stack([gray,gray,gray,np.where(np.isfinite(arr),255,0).astype('uint8')],axis=-1)
        Image.fromarray(rgb).save(OUT/'radar_post.webp',quality=88)
        left,top=transform*(0,0);right,bottom=transform*(width,height)
        corners=transform_geom('EPSG:3857','EPSG:4326',{'type':'Polygon','coordinates':[[[left,bottom],[right,bottom],[right,top],[left,top],[left,bottom]]]})['coordinates'][0]
        radar_bounds=[[corners[0][1],corners[0][0]],[corners[2][1],corners[2][0]]]

collection=dict(type='FeatureCollection',features=features)
(OUT/'fields.geojson').write_text(json.dumps(collection,separators=(',',':'),ensure_ascii=False,allow_nan=False))
p=[f['properties'] for f in features]
totals=dict(fields=len(p),paddy_area_ha=sum(r['area_ha'] for r in p),
    sampled_fields=sum(r['valid_pixels']>0 for r in p),unknown_fields=sum(r['valid_pixels']==0 for r in p),
    flood_detected_fields=sum((r['flood_fraction'] or 0)>0 for r in p),
    no_flood_detected_fields=sum(r['flood_fraction']==0 for r in p),
    predicted_flooded_area_ha=sum(r['flooded_area_ha'] or 0 for r in p),
    unknown_area_ha=sum(r['area_ha'] for r in p if r['valid_pixels']==0))
lookup=list(csv.DictReader(open(ROOT/'outputs/rice_loss_scenarios/damage_lookup.csv')))
scenarios=[dict(name=r['name'],depth_cm=r['depth_cm'],duration_days=r['duration_days'],damage_fraction=next(float(x['mean_loss_fraction']) for x in lookup if float(x['duration_days'])==r['duration_days'] and float(x['depth_cm'])==r['depth_cm'])) for r in json.loads((ROOT/'outputs/rice_loss_scenarios/assumptions.json').read_text())['damage_scenarios']]
meta=dict(title='Koriyama · Hagibis 2019',boundary_year=2021,pre_date='2019-10-07 05:43 JST',post_date='2019-10-13 05:42 JST',
    totals=totals,scenarios=scenarios,radar_bounds=radar_bounds,
    default_yield_t_ha=5,default_unharvested_fraction=.25,
    source_urls=dict(parcels='https://www.geospatial.jp/ckan/dataset/agri-poly-2021-07/resource/c007ede4-046c-4f00-b2c2-1bf96e359996',
      maff='https://www.maff.go.jp/j/tokei/porigon/',radar='https://planetarycomputer.microsoft.com/dataset/sentinel-1-rtc',
      damage='https://doi.org/10.5194/nhess-26-925-2026',basemap='https://maps.gsi.go.jp/development/ichiran.html'),
    methodology='MAFF-derived 2021 paddy parcels (Kind=田), selected by centroid in the existing Koriyama AOI. Flood fraction = positive frozen Japanese RF pixels / valid 10 m pixel centers inside parcel. Flooded hectares = projected vector parcel hectares × sampled fraction. Parcels with no pixel centers are unknown, not dry.',
    caveats=['Boundaries are from 2021, radar from 2019. Paddy classification does not verify rice planted or standing in 2019.',
      'Parcel areas are computed geometry, not surveyed cadastral area. Ten-metre mixed pixels and alignment affect small parcels.',
      'No independent Japanese flood accuracy has been established. Any positive sampled pixel marks a parcel as some flood detected.',
      'Depth, duration, growth stage, normal yield and harvest fraction are assumptions. Damage lookup trained in Thailand/Myanmar; not validated in Japan.',
      'Totals use the parcel domain and vector-area-weighted flood fractions; they intentionally differ from the earlier 2016 coarse-grid result of 743.13 ha.',
      'No flood probability, real-time monitoring, yield-loss accuracy or quality-loss estimate is supplied.'])
(OUT/'metadata.json').write_text(json.dumps(meta,indent=2,ensure_ascii=False))
with (OUT/'fields.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(p[0]));writer.writeheader();writer.writerows(p)
provenance=dict(parcel_archive_sha256=hashlib.sha256((ROOT/'work/rice_loss/koriyama_fields_2021.zip').read_bytes()).hexdigest(),
    flood_prediction_sha256=hashlib.sha256(predpath.read_bytes()).hexdigest(),
    data_sha256=hashlib.sha256((OUT/'fields.geojson').read_bytes()).hexdigest(),
    preparation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    attribution='MAFF Fude Polygon Data, transformed to geographic shapefile and municipal attributes by AIGID, via G Spatial Information Center. Modified Copernicus Sentinel-1 (2019), Microsoft Planetary Computer processing. Damage lookup: Bill-Weilandt et al. (2026), CC BY 4.0.')
(OUT/'provenance.json').write_text(json.dumps(provenance,indent=2))
assert len(features)==11074
assert all(r['flood_fraction'] is None or 0<=r['flood_fraction']<=1 for r in p)
assert math.isclose(totals['fields'],totals['flood_detected_fields']+totals['no_flood_detected_fields']+totals['unknown_fields'])
print(json.dumps(totals,indent=2),flush=True)
