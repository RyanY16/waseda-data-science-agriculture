const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');const c={};vm.createContext(c);vm.runInContext(fs.readFileSync('site/model.js','utf8'),c);const M=c.RiceMath;
const geo=JSON.parse(fs.readFileSync('site/data/fields.geojson')),fields=geo.features.map(f=>f.properties);const h=M.harvestSummary(fields,5,.25,.4787);
assert(Math.abs(h.before-h.after-h.loss)<1e-8);assert(Math.abs(h.assessedArea+h.unknownArea-h.area)<1e-7);assert(Math.abs(h.standingBefore-h.standingAfter-h.loss)<1e-8);assert(Math.abs(h.changePercent+100*h.loss/h.before)<1e-10);
const zero=M.harvestSummary(fields,5,0,.4787);assert.equal(zero.after,zero.before);assert.equal(zero.loss,0);
const all=M.harvestSummary(fields,5,1,.4787);assert(Math.abs(all.after-all.standingAfter)<1e-8);
const unknown=M.harvestSummary([{area_ha:1,flood_fraction:null}],5,.25,.4787);assert.equal(unknown.before,0);assert.equal(unknown.changePercent,null);assert.equal(unknown.unknownArea,1);
const mixed=M.harvestSummary([{area_ha:1,flood_fraction:1,flooded_area_ha:1},{area_ha:100,flood_fraction:null}],5,.5,.4);assert.equal(mixed.before,5);assert.equal(mixed.loss,1);assert.equal(mixed.after,4);assert.equal(mixed.changePercent,-20);
fs.writeFileSync('qa/harvest-math.json',JSON.stringify({checks:'passed',...h},null,2));console.log(JSON.stringify({checks:'passed',mappedHa:h.area,assessedHa:h.assessedArea,beforeTonnes:h.before,afterTonnes:h.after,lossTonnes:h.loss,changePercent:h.changePercent,floodedHa:h.floodArea}));
