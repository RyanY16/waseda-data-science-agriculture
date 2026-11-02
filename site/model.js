(function(root){
  'use strict';
  function loss(field,yieldTHa,unharvested,damage){
    if(!Number.isFinite(yieldTHa)||!Number.isFinite(unharvested)||!Number.isFinite(damage)||yieldTHa<0||unharvested<0||unharvested>1||damage<0||damage>1)throw new Error('Invalid scenario assumptions');
    if(field.flood_fraction===null)return null;
    return field.flooded_area_ha*yieldTHa*unharvested*damage;
  }
  function status(p){return p.flood_fraction===null?'unknown':p.flood_fraction>0?'flooded':'clear';}
  function aggregate(fields,yieldTHa,unharvested,damage){return fields.reduce((s,p)=>{s.count++;s.area+=p.area_ha;if(p.flood_fraction===null){s.unknown++;s.unknownArea+=p.area_ha;}else{s.floodArea+=p.flooded_area_ha;s.loss+=loss(p,yieldTHa,unharvested,damage);if(p.flood_fraction>0)s.flagged++;else s.clear++;}return s;},{count:0,area:0,floodArea:0,loss:0,flagged:0,clear:0,unknown:0,unknownArea:0});}
  function harvestSummary(fields,yieldTHa,unharvested,damage){
    const s=aggregate(fields,yieldTHa,unharvested,damage);
    const assessedArea=fields.reduce((sum,p)=>sum+(p.flood_fraction===null?0:p.area_ha),0);
    const before=assessedArea*yieldTHa,standingBefore=before*unharvested;
    return {...s,assessedArea,before,after:Math.max(0,before-s.loss),changePercent:before>0?-100*s.loss/before:null,standingBefore,standingAfter:Math.max(0,standingBefore-s.loss)};
  }
  root.RiceMath=Object.freeze({loss,status,aggregate,harvestSummary});
})(globalThis);
