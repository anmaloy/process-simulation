const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const handlers = {};
class JList { constructor(a){this.a=a;} forEach(f){this.a.forEach(x=>f(x));} }
class JMap { constructor(m){this.m=m;} forEach(f){for(const [k,v] of this.m) f(k,v);} values(){return new JList([...this.m.values()]);} }
const rl = s => ({ toString: () => s });
const jobj = o => ({ toString: () => JSON.stringify(o) });
const recipe = (id, json, extra={}) => Object.assign({ removed:false, json: jobj(json), getOrCreateId: () => rl(id) }, extra);
const recipeEvent = {
  originalRecipes: new JMap(new Map([
    ['a', recipe('modern_industrialization:materials/iron/macerator/ore', {type:'modern_industrialization:macerator', eu:2, duration:100, item_inputs:[{tag:'c:ores/iron',amount:1}], item_outputs:[{item:'modern_industrialization:iron_dust',amount:2}]})],
    ['b', recipe('create:crushing/removed', {type:'create:crushing'}, {removed:true})],
    ['c', recipe('broken:failed', {type:'x:y'})],
  ])),
  addedRecipes: new JList([recipe('statech:kernite_dust', {type:'modern_industrialization:electrolyzer', eu:16, duration:600, item_inputs:[{tag:'c:dusts/kernite',amount:9}], item_outputs:[{item:'modern_industrialization:sodium_dust',amount:2}]})]),
};
const tagEvent = { tags: new JMap(new Map([[rl('c:dusts/kernite'), { getObjectIds: () => new JList([rl('modern_industrialization:kernite_dust')]) }], [rl('c:bad'), { getObjectIds: () => { throw new Error('boom'); } }]])) };
const finalIds = ['modern_industrialization:materials/iron/macerator/ore', 'statech:kernite_dust', 'mi:code_injected'];
let written = null;
const ctx = {
  console: { info: m => console.log('INFO', m), error: m => console.log('ERROR', m) },
  ServerEvents: {
    tags: (t, f) => (handlers['tags:'+t] = f), recipes: f => (handlers.recipes = f), afterRecipes: f => (handlers.after = f),
  },
  JsonIO: { write: (p, o) => { written = { p, o: JSON.parse(JSON.stringify(o)) }; } },
  Item: { getTypeList: () => new JList(['modern_industrialization:kernite_dust']), of: id => ({ getHoverName: () => ({ getString: () => 'Kernite Dust' }) }) },
  Fluid: { getTypes: () => new JList(['minecraft:water', 'minecraft:flowing_water']), of: id => ({ getHoverName: () => ({ getString: () => 'Water' }) }) },
};
vm.createContext(ctx); vm.runInContext(src, ctx);
handlers['tags:item'](tagEvent); handlers['tags:fluid']({ tags: new JMap(new Map()) });
handlers.recipes(recipeEvent);
handlers.after({ forEachRecipe: (filter, f) => finalIds.forEach(id => f({ getOrCreateId: () => rl(id) })) });
fs.writeFileSync(process.argv[3], JSON.stringify(written.o, null, 1));
console.log('path', written.p, 'recipes', Object.keys(written.o.recipes), 'missing', written.o.missingFromExport, 'tags', JSON.stringify(written.o.itemTags), JSON.stringify(written.o.fluidNames));
