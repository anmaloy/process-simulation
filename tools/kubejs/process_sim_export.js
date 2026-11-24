// priority: -100000
// -----------------------------------------------------------------------------
// Process Simulation — modpack recipe exporter (KubeJS 7 / NeoForge 1.21)
//
// Writes the pack's FINAL recipe set — after every mod, datapack and KubeJS
// script has run — plus resolved item/fluid tags and display names to
//
//     <game dir>/local/process_sim/pack_export.json
//
// so Process Simulation's "Import pack" sees exactly what the game sees:
// script-added recipes, removed/replaced recipes, materials registered by
// startup scripts, unified outputs. It only READS game data and writes that one
// file. Installed by Process Simulation into local/kubejs/local_server_scripts
// (outside the pack's own kubejs folder, so pack updates leave it alone).
//
// Use: load any world (or run /reload in one). Look for
//     [process-sim] exported N recipes ...
// in logs/kubejs/server.log. Delete this file once exported if you like; the
// export costs a few seconds per world load / reload.
// -----------------------------------------------------------------------------

const PSIM_OUT = 'local/process_sim/pack_export.json';
const PSIM_FORMAT = 'process-sim-pack-export';

// The recipe/tag events are captured, not read on the spot: other scripts may
// run after this one, so everything is read once the game has finished
// applying them (afterRecipes).
let psimRecipes = null;
const psimTagEvents = { item: null, fluid: null };

ServerEvents.tags('item', (event) => {
    psimTagEvents.item = event;
});
ServerEvents.tags('fluid', (event) => {
    psimTagEvents.fluid = event;
});
ServerEvents.recipes((event) => {
    psimRecipes = event;
});

function psimTry(label, fn) {
    try {
        return fn();
    } catch (err) {
        console.error('[process-sim] ' + label + ' failed: ' + err);
        return null;
    }
}

function psimTags(event) {
    const out = {};
    if (!event) return out;
    event.tags.forEach((id, wrapper) => {
        psimTry('tag ' + id, () => {
            const ids = [];
            wrapper.getObjectIds().forEach((rl) => ids.push(String(rl)));
            if (ids.length) out[String(id)] = ids;
        });
    });
    return out;
}

function psimRecipeJson(out, recipe) {
    if (recipe.removed) return;
    const json = recipe.json;
    if (!json) return;
    out[String(recipe.getOrCreateId())] = String(json.toString());
}

ServerEvents.afterRecipes((event) => {
    if (!psimRecipes) {
        console.error('[process-sim] recipe event was not captured; nothing exported');
        return;
    }
    const recipes = {};
    psimTry('original recipes', () =>
        psimRecipes.originalRecipes.values().forEach((r) =>
            psimTry('recipe', () => psimRecipeJson(recipes, r))
        )
    );
    psimTry('added recipes', () =>
        psimRecipes.addedRecipes.forEach((r) =>
            psimTry('recipe', () => psimRecipeJson(recipes, r))
        )
    );

    // The ids the game actually loaded: drops anything that failed to parse,
    // and counts recipes that exist in game but never passed through JSON.
    const finalIds = {};
    let finalCount = 0;
    psimTry('final recipe ids', () =>
        event.forEachRecipe({}, (r) => {
            finalIds[String(r.getOrCreateId())] = true;
            finalCount++;
        })
    );
    let missing = 0;
    if (finalCount > 0) {
        Object.keys(recipes).forEach((id) => {
            if (!finalIds[id]) delete recipes[id];
        });
        Object.keys(finalIds).forEach((id) => {
            if (!(id in recipes)) missing++;
        });
    }

    const itemNames = {};
    psimTry('item names', () =>
        Item.getTypeList().forEach((id) =>
            psimTry('item name ' + id, () => {
                itemNames[String(id)] = String(Item.of(id).getHoverName().getString());
            })
        )
    );
    const fluidNames = {};
    psimTry('fluid names', () =>
        Fluid.getTypes().forEach((id) => {
            if (String(id).indexOf('flowing') >= 0) return;
            psimTry('fluid name ' + id, () => {
                fluidNames[String(id)] = String(Fluid.of(id).getHoverName().getString());
            });
        })
    );

    const out = {
        format: PSIM_FORMAT,
        version: 1,
        exportedAt: new Date().toISOString(),
        recipeCount: Object.keys(recipes).length,
        finalRecipeCount: finalCount,
        missingFromExport: missing,
        recipes: recipes,
        itemTags: psimTags(psimTagEvents.item),
        fluidTags: psimTags(psimTagEvents.fluid),
        itemNames: itemNames,
        fluidNames: fluidNames,
    };
    const wrote = psimTry('write ' + PSIM_OUT, () => {
        JsonIO.write(PSIM_OUT, out);
        return true;
    });
    if (wrote) {
        console.info(
            '[process-sim] exported ' + out.recipeCount + ' recipes (' +
            finalCount + ' loaded in game, ' + missing + ' not exportable), ' +
            Object.keys(out.itemTags).length + ' item tags, ' +
            Object.keys(itemNames).length + ' item names to ' + PSIM_OUT
        );
    }
});
