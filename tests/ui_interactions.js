// UI interaction regression driver — run by tests/test_ui_interactions.py.
//
// Drives the real page (served by server.py, real engine) in headless Chromium and
// exercises the everyday interactions a user hits constantly: scroll positions that
// must survive a re-render, focus that must survive a commit, number entry
// (decimals, blanks, ranges), Escape/Delete semantics, comboboxes, renames,
// creation, undo, canvas gestures. Each scenario starts from a freshly seeded World
// and a reloaded page, and records its failed expectations; the Python side asserts
// every scenario came back clean.
//
//   node tests/ui_interactions.js <port> <world.json> [scenario ...]
//
// Prints one JSON object {scenario: {fails: [..], error?: str}} on the last line.
const { chromium } = require("playwright");
const fs = require("fs");

const [port, worldPath, ...only] = process.argv.slice(2);
const WORLD = JSON.parse(fs.readFileSync(worldPath, "utf8"));
const BASE = `http://127.0.0.1:${port}/`;

async function api(method, ...args) {
  const r = await fetch(BASE + "api/" + method, { method: "POST", body: JSON.stringify({ args }) });
  return (await r.json()).result;
}

const S = {};   // name -> async (t) => {}
function scenario(name, fn) { S[name] = fn; }

/* ---------------------------------------------------------------- helpers */
function makeT(page, fails) {
  const t = {
    page,
    ev: (f, a) => page.evaluate(f, a),
    expect(cond, msg) { if (!cond) fails.push(msg); },
    eq(a, b, msg) { if (JSON.stringify(a) !== JSON.stringify(b)) fails.push(`${msg}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); },
    // let queued edits land and the combobox blur timers run
    async idle() { await page.evaluate(() => editsSettled()); await page.waitForTimeout(180); await page.evaluate(() => editsSettled()); },
    async select(kind, id) { await page.evaluate(([k, i]) => select(k, i), [kind, id]); await t.idle(); },
    field(label) { return page.locator(`.insp-body [data-fk="${label}"]`).first(); },
    input(label) { return t.field(label).locator("input").first(); },
    async commit(locator, text) { await locator.click(); await locator.fill(text); await locator.press("Enter"); await t.idle(); },
    entity: (kind, id) => page.evaluate(([k, i]) => findEntity(k, i), [kind, id]),
    scrollTop: (sel) => page.evaluate((s) => { const e = document.querySelector(s); return e ? Math.round(e.scrollTop) : null; }, sel),
    setScroll: (sel, v) => page.evaluate(([s, v]) => { const e = document.querySelector(s); e.scrollTop = v === "max" ? e.scrollHeight : v; return Math.round(e.scrollTop); }, [sel, v]),
    active: () => page.evaluate(() => { const a = document.activeElement; if (!a || a === document.body) return null;
      const f = a.closest("[data-fk]"); return { tag: a.tagName, cls: a.className, fk: f ? f.dataset.fk : null, value: a.value }; }),
    toast: () => page.evaluate(() => { const t = document.querySelector("#toast"); return t && t.classList.contains("show") ? t.textContent : null; }),
  };
  return t;
}

/* ---------------------------------------------------------------- scroll */
scenario("inspector_scroll_kept_after_recipe_pick", async (t) => {
  await t.select("node", "mac_node_1");
  await t.page.click(".insp-body >> text=+ add recipe"); await t.idle();
  const before = await t.setScroll(".insp-body", "max");
  t.expect(before > 100, "inspector should be scrollable for this test (scrollTop " + before + ")");
  await t.page.locator(".recipe-opt").nth(3).click(); await t.idle();
  t.eq(await t.scrollTop(".insp-body"), before, "inspector scroll after picking a recipe");
  t.eq((await t.entity("node", "mac_node_1")).recipeIds.length, 2, "picked recipe bound");
});

scenario("inspector_scroll_kept_after_checkbox", async (t) => {
  await t.select("node", "mac_node_1");
  const before = await t.setScroll(".insp-body", "max");
  await t.page.locator(".insp-body .checkrow input").last().click(); await t.idle();
  t.eq(await t.scrollTop(".insp-body"), before, "inspector scroll after toggling a checkbox");
  t.eq((await t.entity("node", "mac_node_1")).assumeInputs, true, "checkbox committed");
});

scenario("inspector_scroll_resets_for_new_selection", async (t) => {
  await t.select("node", "mac_node_1");
  await t.setScroll(".insp-body", "max");
  await t.select("node", "mac_node_2");
  t.eq(await t.scrollTop(".insp-body"), 0, "a different entity opens at the top");
});

scenario("rail_scroll_kept_after_select", async (t) => {
  await t.ev(() => { railOpen.recipes = true; renderRailSections(); });
  const before = await t.setScroll(".rail-scroll", 1500);
  // click without letting the test harness scroll the leaf into view first
  await t.ev(() => [...document.querySelectorAll(".rail .leaf.clickable")].find(l => l.textContent.includes("Macerate 30")).click());
  await t.idle();
  t.eq(await t.scrollTop(".rail-scroll"), before, "rail scroll after selecting a recipe");
  t.eq(await t.ev(() => SEL), { kind: "recipe", id: "mac_30" }, "selection");
  // and after an edit to it: the selected leaf stays where it is on screen (the rail
  // may grow a status line above it; the browser's scroll anchoring absorbs that)
  const leafY = () => t.ev(() => Math.round(document.querySelector("#railSections .leaf.selected").getBoundingClientRect().top));
  const y0 = await leafY();
  await t.commit(t.input("cycle time"), "7");
  t.eq(await leafY(), y0, "selected leaf's on-screen position after editing it");
});

scenario("readout_scroll_kept_after_select", async (t) => {
  await t.page.setViewportSize({ width: 1400, height: 300 });
  await t.ev(() => runSimulation()); await t.idle();
  const before = await t.setScroll(".readout", 60);
  t.expect(before > 0, "readout should scroll for this test");
  await t.select("node", "mac_node_3");
  t.eq(await t.scrollTop(".readout"), before, "readout scroll after a selection");
});

scenario("recipe_finder_stays_open_with_filters_and_scroll", async (t) => {
  await t.select("node", "mac_node_1");
  await t.page.click(".insp-body >> text=+ add recipe"); await t.idle();
  const listBefore = await t.setScroll(".recipe-list", 120);
  await t.page.locator(".recipe-opt").nth(8).click(); await t.idle();
  t.expect(await t.page.locator(".recipe-list").count() === 1, "finder still open after a pick");
  t.eq(await t.scrollTop(".recipe-list"), listBefore, "recipe list scroll after a pick");
  // filter, then pick: the filter survives the re-render
  const filt = t.field("filter by output").locator("input");
  await filt.click(); await filt.fill("Ore 50"); await filt.press("Enter"); await t.idle();
  const shown = await t.page.locator(".recipe-opt").count();
  await t.page.locator(".recipe-opt").first().click(); await t.idle();
  t.eq(await t.field("filter by output").locator("input").inputValue(), "Ore 50", "output filter kept");
  t.expect(await t.page.locator(".recipe-opt").count() === shown - 1, "filtered list kept (minus the bound recipe)");
});

scenario("canvas_view_kept_on_select_and_edit", async (t) => {
  await t.ev(() => { view = { x: 123, y: 45, k: 0.8 }; applyView(); });
  await t.select("node", "mac_node_2");
  await t.page.locator(".insp-body .checkrow input").last().click(); await t.idle();
  t.eq(await t.ev(() => view), { x: 123, y: 45, k: 0.8 }, "canvas view");
});

/* ---------------------------------------------------------------- focus & concurrent edits */
scenario("tab_between_fields_keeps_both_values", async (t) => {
  await t.select("recipe", "mac_1");
  const qty = t.page.locator('.insp-body [data-fk="inputs[0]"] input.num').nth(0);
  await qty.click(); await qty.fill("3");
  await t.page.keyboard.press("Tab");
  await t.page.keyboard.type("0.25");
  await t.page.waitForTimeout(300);                  // the qty commit re-renders meanwhile
  const a = await t.active();
  t.eq(a && a.value, "0.25", "focus + typed text survive the re-render");
  await t.page.keyboard.press("Tab"); await t.idle();
  t.eq((await t.entity("recipe", "mac_1")).inputs[0], { itemId: "ore_1", quantity: 3, probability: 0.25 }, "both edits stored");
});

scenario("fast_consecutive_edits_all_land", async (t) => {
  await t.select("node", "mac_node_2");
  await t.ev(() => { const cbs = [...document.querySelectorAll(".insp-body .checkrow input")]; cbs[0].click(); cbs[1].click(); });
  await t.idle();
  const n = await t.entity("node", "mac_node_2");
  t.eq([n.maxPower, n.assumeInputs], [true, true], "two quick checkbox edits");
  // and two field commits back to back with stale closures
  await t.select("recipe", "mac_2");
  await t.ev(() => {
    const ins = [...document.querySelectorAll('.insp-body [data-fk="cycle time"] input, .insp-body [data-fk="outputs[0]"] input.num')];
    ins[0].value = "9"; ins[0].dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
    ins[1].value = "4"; ins[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
  });
  await t.idle();
  const r = await t.entity("recipe", "mac_2");
  t.eq([r.timeSeconds, r.outputs[0].quantity], [9, 4], "two quick field commits");
});

scenario("half_typed_value_survives_rerender", async (t) => {
  await t.select("recipe", "mac_3");
  const ct = t.input("cycle time");
  await ct.click(); await ct.fill(""); await t.page.keyboard.type("0.");
  // something else re-renders the inspector while the user is mid-number
  await t.ev(() => saveEntity("recipe", withField(findEntity("recipe", "mac_3"), "machineType", "x")));
  await t.idle();
  const a = await t.active();
  t.eq(a && [a.fk, a.value], ["cycle time", "0."], "focused field and its partial text");
  await t.page.keyboard.type("5"); await t.page.keyboard.press("Enter"); await t.idle();
  const r = await t.entity("recipe", "mac_3");
  t.eq([r.timeSeconds, r.machineType], [0.5, "x"], "both values");
});

scenario("set_button_focuses_its_new_input", async (t) => {
  await t.select("recipe", "mac_4");
  await t.ev(() => saveEntity("recipe", withoutField(findEntity("recipe", "mac_4"), "euPerTick"))); await t.idle();
  await t.field("energy draw").locator(".set-btn").click(); await t.idle();
  const a = await t.active();
  t.eq(a && [a.tag, a.fk], ["INPUT", "energy draw"], "focus moves into the new input");
  await t.page.keyboard.press("Backspace"); await t.page.keyboard.type("7"); await t.page.keyboard.press("Enter"); await t.idle();
  t.expect(await t.entity("recipe", "mac_4"), "Backspace did not delete the selected recipe");
  t.eq((await t.entity("recipe", "mac_4")).euPerTick, 7, "typed value stored");
});

scenario("backspace_after_inspector_button_does_not_delete", async (t) => {
  await t.select("node", "mac_node_5");
  await t.field("overclock").locator(".clear-btn").click(); await t.idle();
  await t.ev(() => document.activeElement && document.activeElement.blur && document.activeElement.blur());
  await t.page.keyboard.press("Backspace"); await t.idle();
  t.expect(await t.entity("node", "mac_node_5"), "node survives a Backspace after an inspector click");
  // ...but Delete right after clicking the node on the canvas still deletes it
  await t.ev(() => { view = { x: 300, y: 300, k: 1 }; applyView(); });
  await t.page.locator('.node[data-id="mac_node_5"] .nhead').click(); await t.idle();
  await t.page.keyboard.press("Delete"); await t.idle();
  t.eq(await t.entity("node", "mac_node_5"), null, "Delete after a canvas click deletes");
});

/* ---------------------------------------------------------------- numbers */
scenario("decimal_round_trip_per_tick_fields", async (t) => {
  await t.select("machineDef", "macerator");
  for (const [typed, perSec] of [["1.1", 22], ["0.35", 7], ["1.15", 23], ["2.05", 41], ["0.1", 2]]) {
    await t.commit(t.input("default energy draw"), typed);
    t.eq(await t.input("default energy draw").inputValue(), typed, "shown after typing " + typed);
    t.eq((await t.entity("machineDef", "macerator")).baseEnergyConsumption, perSec, "stored per second for " + typed);
  }
});

scenario("decimal_entry_forms", async (t) => {
  await t.select("recipe", "mac_5");
  const qty = () => t.page.locator('.insp-body [data-fk="outputs[0]"] input.num').nth(0);
  for (const [typed, want] of [["1,5", 1.5], [".5", 0.5], ["  2.25 ", 2.25], ["1e1", 10], ["0.1", 0.1]]) {
    await t.commit(qty(), typed);
    t.eq((await t.entity("recipe", "mac_5")).outputs[0].quantity, want, "quantity from " + JSON.stringify(typed));
  }
  t.eq(await qty().inputValue(), "0.1", "clean display");
});

scenario("clearing_optional_number_unsets_it", async (t) => {
  await t.select("recipe", "mac_6");
  await t.commit(t.input("energy draw"), "");
  t.expect(!("euPerTick" in (await t.entity("recipe", "mac_6"))), "euPerTick unset");
  t.expect(await t.field("energy draw").locator(".set-btn").count() === 1, "field shows + set");
  const prob = t.page.locator('.insp-body [data-fk="outputs[0]"] input.num').nth(1);
  await t.commit(prob, "");
  t.expect(!("probability" in (await t.entity("recipe", "mac_6")).outputs[0]), "probability unset");
  t.eq(await prob.getAttribute("placeholder"), "always", "blank probability reads as 'always'");
});

scenario("required_and_out_of_range_numbers_rejected", async (t) => {
  const cases = [
    ["recipe", "mac_7", () => t.input("cycle time"), "timeSeconds", ["", "0", "-3", "abc", "1..2"]],
    ["recipe", "mac_7", () => t.page.locator('.insp-body [data-fk="outputs[0]"] input.num').nth(1), "outputs.0.probability", ["5", "-0.1"]],
    ["recipe", "mac_7", () => t.page.locator('.insp-body [data-fk="outputs[0]"] input.num').nth(0), "outputs.0.quantity", ["0", "", "-1"]],
    ["node", "mac_node_7", () => t.input("parallel count"), "count", ["0", "2.5", "-1"]],
    ["node", "mac_node_7", () => t.input("overclock"), "overclock", ["0", "-2"]],
  ];
  for (const [kind, id, loc, path, bads] of cases) {
    await t.select(kind, id);
    const get = async () => path.split(".").reduce((o, k) => o == null ? o : o[k], await t.entity(kind, id));
    const orig = await get();
    for (const bad of bads) {
      await t.commit(loc(), bad);
      t.eq(await get(), orig, `${path} after typing ${JSON.stringify(bad)}`);
      t.eq(await loc().inputValue(), String(orig), `${path} field reverts after ${JSON.stringify(bad)}`);
      t.expect(await t.toast(), `a message explains why ${JSON.stringify(bad)} was rejected for ${path}`);
    }
  }
  // priorities are whole numbers in −128…127
  await t.select("pipe", "pipe_1");
  const pr = t.page.locator('.insp-body [data-fk="end-a"] [data-fk="extract priority"]');
  if (await pr.locator(".set-btn").count()) { await pr.locator(".set-btn").click(); await t.idle(); }
  for (const bad of ["200", "1.5"]) {
    await t.commit(pr.locator("input"), bad);
    t.expect(![200, 1.5].includes((await t.entity("pipe", "pipe_1")).a.extractPriority), "priority rejects " + bad);
  }
  await t.commit(pr.locator("input"), "-5");
  t.eq((await t.entity("pipe", "pipe_1")).a.extractPriority, -5, "negative priority accepted");
});

scenario("wheel_does_not_change_numbers", async (t) => {
  await t.select("recipe", "mac_8");
  const q = t.page.locator('.insp-body [data-fk="outputs[0]"] input.num').nth(0);
  await q.click(); await q.hover();
  await t.page.mouse.wheel(0, -100); await t.page.mouse.wheel(0, -100);
  await t.page.keyboard.press("Enter"); await t.idle();
  t.eq((await t.entity("recipe", "mac_8")).outputs[0].quantity, 2, "quantity after scrolling over it");
});

scenario("arrow_keys_step_numbers", async (t) => {
  await t.select("node", "mac_node_8");
  const oc = t.input("overclock");
  await oc.click(); await t.page.keyboard.press("ArrowUp"); await t.page.keyboard.press("ArrowUp"); await t.page.keyboard.press("Enter"); await t.idle();
  t.eq((await t.entity("node", "mac_node_8")).overclock, 1.5, "two ArrowUp steps of 0.25");
});

scenario("pipe_energy_rate_in_eu_per_tick", async (t) => {
  await t.select("pipe", "pipe_power");
  t.eq(await t.input("transfer rate").inputValue(), "256", "5120 EU/s shows as 256 EU/t");
  t.expect((await t.field("transfer rate").textContent()).includes("EU/t"), "unit label");
  await t.commit(t.input("transfer rate"), "100");
  t.eq((await t.entity("pipe", "pipe_power")).movementCapacity, 2000, "stored per second");
});

/* ---------------------------------------------------------------- keys */
scenario("escape_in_field_reverts_and_keeps_selection", async (t) => {
  await t.select("recipe", "mac_9");
  const ct = t.input("cycle time");
  await ct.click(); await ct.fill("99"); await ct.press("Escape"); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "recipe", id: "mac_9" }, "selection kept");
  t.eq((await t.entity("recipe", "mac_9")).timeSeconds, 5, "value not committed");
  t.eq(await t.input("cycle time").inputValue(), "5", "field reverted");
});

scenario("escape_in_combobox_keeps_selection", async (t) => {
  await t.select("recipe", "mac_9");
  const c = t.page.locator('.insp-body [data-fk="inputs[0]"] .combo input');
  await c.click(); await c.fill("Ore 7"); await c.press("Escape"); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "recipe", id: "mac_9" }, "selection kept");
  t.eq(await c.inputValue(), "Ore 9", "search abandoned, value label back");
  await t.page.keyboard.press("Escape");   // field still focused, list closed: blurs
  await t.ev(() => document.activeElement.blur());
  await t.page.keyboard.press("Escape"); await t.idle();
  t.eq(await t.ev(() => SEL), null, "Escape outside fields clears the selection");
});

scenario("combobox_abandoned_text_reverts_and_enter_picks", async (t) => {
  await t.select("recipe", "mac_10");
  const c = () => t.page.locator('.insp-body [data-fk="inputs[0]"] .combo input');
  await c().click(); await c().fill("zzz"); await t.page.locator(".insp-head").click(); await t.idle();
  t.eq(await c().inputValue(), "Ore 10", "typed-but-unpicked text reverts");
  await c().click(); await c().fill("Ore 77"); await c().press("Enter"); await t.idle();
  t.eq((await t.entity("recipe", "mac_10")).inputs[0].itemId, "ore_77", "Enter picks the first match");
  t.eq(await c().inputValue(), "Ore 77", "label shown");
});

scenario("combobox_list_stays_inside_the_panel", async (t) => {
  await t.select("recipe", "mac_1");
  await t.setScroll(".insp-body", "max");
  const c = t.page.locator('.insp-body [data-fk="outputs[0]"] .combo input');
  await c.click();
  const pop = await t.page.locator('.insp-body [data-fk="outputs[0]"] .combo-pop').boundingBox();
  const body = await t.page.locator(".insp-body").boundingBox();
  t.expect(pop && pop.y >= body.y - 1 && pop.y + pop.height <= body.y + body.height + 1, "dropdown fully visible inside the inspector");
});

scenario("add_another_combobox_keeps_focus", async (t) => {
  await t.select("item", "ore_20");
  await t.field("tag resource").locator(".set-btn").click(); await t.idle();
  const m = () => t.page.locator('.insp-body [placeholder="+ add member…"]');
  await m().click(); await t.page.keyboard.type("Ore 21"); await t.page.keyboard.press("Enter"); await t.idle();
  await t.page.keyboard.type("Ore 22"); await t.page.keyboard.press("Enter"); await t.idle();
  t.eq((await t.entity("item", "ore_20")).members, ["ore_21", "ore_22"], "two members added by typing");
  // placing machines from the palette twice in a row gives two distinct nodes
  const p = t.page.locator("#paletteCombo input");
  await p.click(); await t.page.keyboard.type("Generator"); await t.page.keyboard.press("Enter");
  await t.page.keyboard.type("Generator"); await t.page.keyboard.press("Enter"); await t.idle();
  t.eq(await t.ev(() => WORLD.nodes.filter(n => n.machineDefId === "generator").map(n => n.id).sort()), ["gen_1", "generator_1", "generator_2"], "two placements");
});

/* ---------------------------------------------------------------- rename / required text */
scenario("rename_validation", async (t) => {
  await t.select("item", "ore_5");
  const id = () => t.field("id").locator("input");
  await t.commit(id(), "");
  t.expect(await t.entity("item", "ore_5"), "empty id rejected (item kept)");
  t.eq(await t.entity("item", "item"), null, "no item renamed to 'item'");
  await t.commit(id(), "ore 6");
  t.expect(await t.entity("item", "ore_5"), "duplicate id rejected");
  await t.commit(id(), "Shiny Ore");
  t.eq(await t.ev(() => SEL), { kind: "item", id: "shiny_ore" }, "selection follows the rename");
  t.eq((await t.entity("recipe", "mac_5")).inputs[0].itemId, "shiny_ore", "references renamed");
});

scenario("item_name_required", async (t) => {
  await t.select("item", "ore_11");
  await t.commit(t.input("name"), "   ");
  t.eq((await t.entity("item", "ore_11")).name, "Ore 11", "blank name rejected");
  await t.commit(t.input("name"), " Gold ");
  t.eq((await t.entity("item", "ore_11")).name, "Gold", "trimmed name stored");
});

/* ---------------------------------------------------------------- create / undo */
scenario("create_item_is_visible_and_named_by_typing", async (t) => {
  await t.ev(() => { railQuery = "zzz"; renderRail(); });
  await t.page.locator('.rail-add[title="New item"]').click(); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "item", id: "new_item" }, "new item selected");
  t.expect(await t.page.locator("#railSections .leaf.selected").count() === 1, "visible in the opened, unfiltered list");
  const a = await t.active();
  t.eq(a && a.fk, "name", "caret in the name field");
  await t.page.keyboard.type("Copper Wire"); await t.page.keyboard.press("Enter"); await t.idle();
  t.eq((await t.entity("item", "new_item")).name, "Copper Wire", "typing names it");
});

scenario("undo_restores_and_drops_dead_selection", async (t) => {
  await t.select("recipe", "mac_12");
  await t.commit(t.input("cycle time"), "11");
  await t.page.locator("body").press("Control+z").catch(() => {});
  await t.ev(() => document.activeElement.blur()); await t.ev(() => undo()); await t.idle();
  t.eq((await t.entity("recipe", "mac_12")).timeSeconds, 5, "undo reverts the edit");
  await t.page.locator('.rail-add[title="New recipe"]').click(); await t.idle();
  await t.ev(() => document.activeElement.blur());
  await t.ev(() => undo()); await t.idle();
  t.eq(await t.ev(() => SEL), null, "undoing a create clears the selection");
  t.eq(await t.page.locator("#inspector").count(), 0, "no '(gone)' inspector left open");
});

/* ---------------------------------------------------------------- panels */
scenario("import_panel_survives_rerender", async (t) => {
  await t.page.locator(".rail-add[title^='Import']").click();
  await t.page.locator(".mi-body .seg button", { hasText: "Files" }).click();
  await t.page.locator(".mi-body textarea").fill("/some/path\n/other");
  await t.ev(() => select("node", "mac_node_1")); await t.idle();
  t.eq(await t.page.locator(".mi-body textarea").inputValue().catch(() => "(gone)"), "/some/path\n/other", "panel open, text kept");
});

scenario("pack_import_panel_keeps_path_and_reports_errors", async (t) => {
  await t.page.locator(".rail-add[title^='Import']").click();
  const pack = t.page.locator(".mi-body input.field-input");
  await pack.fill("/no/such/instance");
  await t.ev(() => select("node", "mac_node_1")); await t.idle();
  t.eq(await pack.inputValue().catch(() => "(gone)"), "/no/such/instance", "modpack path kept across re-render");
  await t.page.locator(".mi-body .mi-run").click(); await t.idle();
  const msg = await t.page.locator(".mi-msg").textContent();
  t.eq(/not a modpack instance/.test(msg), true, "a bad instance path is reported: " + msg);
  t.eq(await t.page.locator(".mi-msg.bad").count(), 1, "shown as an error");
  t.eq(await t.page.locator(".mi-body .mi-run").isDisabled(), false, "import button re-enabled");
});

scenario("rail_search_keeps_focus_across_render", async (t) => {
  const s = t.page.locator(".rail-search input");
  await s.click(); await t.page.keyboard.type("Ore 1");
  await t.ev(() => saveEntity("node", withField(findEntity("node", "mac_node_1"), "assumeInputs", true))); await t.idle();
  const a = await t.active();
  t.eq(a && [a.tag, a.value], ["INPUT", "Ore 1"], "search keeps focus and text");
  await t.page.keyboard.type("0"); await t.idle();
  t.eq(await s.inputValue(), "Ore 10", "typing continues");
});

scenario("project_dialog_escape_and_empty_path", async (t) => {
  await t.page.locator("text=⇄ Project").click();
  await t.page.locator("#projectDialog >> text=Load").click(); await t.idle();
  t.expect((await t.page.locator("#projectDialog .mi-msg").textContent()).includes("path"), "empty path explained");
  await t.page.keyboard.press("Escape"); await t.idle();
  t.eq(await t.page.locator("#projectDialog").count(), 0, "Escape closes the dialog");
});

scenario("run_and_save_commit_the_field_being_typed", async (t) => {
  await t.select("recipe", "mac_13");
  const ct = t.input("cycle time");
  await ct.click(); await ct.fill("13");
  await t.page.locator("#runBtn").click(); await t.idle();
  t.eq((await t.entity("recipe", "mac_13")).timeSeconds, 13, "Run committed the typed value");
  t.eq(await t.ev(() => STALE), false, "the run reflects it (not stale)");
  await t.select("recipe", "mac_14");
  await t.input("cycle time").click(); await t.input("cycle time").fill("14");
  await t.page.locator("#saveBtn").click(); await t.idle();
  const saved = await api("get_world");
  t.eq(saved.data.recipes.find(r => r.id === "mac_14").timeSeconds, 14, "Save committed the typed value");
  t.eq(await t.ev(() => DIRTY), false, "saved");
});

/* ---------------------------------------------------------------- canvas */
scenario("canvas_drag_wire_and_undo", async (t) => {
  await t.ev(() => { view = { x: 400, y: 200, k: 1 }; applyView(); });
  const h = await t.page.locator('.node[data-id="mac_node_1"] .nhead').boundingBox();
  await t.page.mouse.move(h.x + 20, h.y + 10); await t.page.mouse.down();
  await t.page.mouse.move(h.x + 80, h.y + 70, { steps: 6 }); await t.page.mouse.up(); await t.idle();
  t.eq([(await t.entity("node", "mac_node_1")).x, (await t.entity("node", "mac_node_1")).y], [60, 60], "dragged");
  await t.ev(() => undo()); await t.idle();
  t.eq((await t.entity("node", "mac_node_1")).x, 0, "undo moves it back");
  const before = (await t.ev(() => WORLD.pipes.length));
  const a = await t.page.locator('.node[data-id="mac_node_2"] .port.out').first().boundingBox();
  const b = await t.page.locator('.node[data-id="mac_node_9"] .port.in').first().boundingBox();
  await t.page.mouse.move(a.x + 5, a.y + 5); await t.page.mouse.down();
  await t.page.mouse.move(b.x + 5, b.y + 5, { steps: 8 }); await t.page.mouse.up(); await t.idle();
  t.eq(await t.ev(() => WORLD.pipes.length), before + 1, "a pipe was wired");
  t.eq((await t.ev(() => SEL)).kind, "pipe", "the new pipe is selected");
});

scenario("rail_pick_reveals_offscreen_node", async (t) => {
  await t.ev(() => { view = { x: -5000, y: -5000, k: 1 }; applyView(); });
  await t.ev(() => [...document.querySelectorAll(".rail .leaf.clickable")].find(l => l.querySelector(".leaf-id").textContent === "mac_node_12").click());
  await t.idle();
  const vis = await t.ev(() => { const r = document.querySelector('.node[data-id="mac_node_12"]').getBoundingClientRect(), c = document.querySelector("#canvas").getBoundingClientRect();
    return r.left >= c.left && r.right <= c.right && r.top >= c.top && r.bottom <= c.bottom; });
  t.expect(vis, "node scrolled into view");
});

scenario("shift_click_group", async (t) => {
  await t.ev(() => { view = { x: 300, y: 300, k: 1 }; applyView(); });
  await t.page.locator('.node[data-id="mac_node_3"] .nhead').click({ modifiers: ["Shift"] });
  await t.page.locator('.node[data-id="mac_node_4"] .nhead').click({ modifiers: ["Shift"] });
  await t.idle();
  t.eq(await t.ev(() => [...MULTI].sort()), ["mac_node_3", "mac_node_4"], "multi-selection");
  await t.page.locator("#groupBtn").click(); await t.idle();
  const sel = await t.ev(() => SEL);
  t.eq(sel && sel.kind, "system", "group selected");
  t.eq((await t.entity("system", sel.id)).memberNodeIds.sort(), ["mac_node_3", "mac_node_4"], "members");
});

/* ---------------------------------------------------------------- balance sheet */
async function openSheet(t) {
  await t.ev(() => runSimulation()); await t.idle();
  await t.page.locator("text=▤ Open balance sheet").click(); await t.idle();
}
const sheetRows = (t) => t.ev(() => [...document.querySelectorAll("#dock tr.row")].map(r => r.dataset.item || r.dataset.node));

scenario("balance_sheet_lists_every_resource", async (t) => {
  await openSheet(t);
  const ledger = await t.ev(() => Object.keys(REPORT.itemLedger).sort());
  t.expect(ledger.length > 10, "the test world touches more than 10 resources (" + ledger.length + ")");
  t.eq((await sheetRows(t)).sort(), ledger, "one row per ledger resource, none cut off");
  // the readout's "+N more" opens the sheet filtered to that list
  await t.ev(() => setDock({ open: false })); await t.idle();
  const more = t.page.locator(".readout .ro-more").first();
  if (await more.count()) {
    await more.click(); await t.idle();
    t.eq(await t.ev(() => DOCK.open), true, "+N more opens the sheet");
  }
});

scenario("balance_sheet_sort_search_filter_units", async (t) => {
  await openSheet(t);
  // sort by "made" (largest first), then toggle to ascending
  await t.page.locator("#dock th", { hasText: /^made/ }).click(); await t.idle();
  const made = () => t.ev(() => [...document.querySelectorAll("#dock tr.row")].map(r => REPORT.itemLedger[r.dataset.item].produced));
  let v = await made();
  t.expect(v.every((x, i) => i === 0 || v[i - 1] >= x), "sorted by made, descending");
  await t.page.locator("#dock th", { hasText: /^made/ }).click(); await t.idle();
  v = await made();
  t.expect(v.every((x, i) => i === 0 || v[i - 1] <= x), "second click: ascending");
  // search keeps focus while filtering
  const q = t.page.locator('#dock [data-fk="dock-q"]');
  await q.click(); await t.page.keyboard.type("ore 1");
  t.expect((await sheetRows(t)).every(id => id.startsWith("ore_1")), "search filters rows");
  const a = await t.active();
  t.eq(a && a.value, "ore 1", "search box keeps focus and text while the sheet re-renders");
  await q.press("Escape"); await t.idle();
  // category chip: hide energy
  await t.page.locator("#dock .fchip", { hasText: "energy" }).click(); await t.idle();
  t.expect(!(await sheetRows(t)).includes("electricity"), "energy hidden");
  // state view: products only
  await t.page.locator("#dock .fchip", { hasText: "products" }).click(); await t.idle();
  const prods = await t.ev(() => [...document.querySelectorAll("#dock tr.row")].every(r => REPORT.itemLedger[r.dataset.item].yield > 0));
  t.expect(prods, "products view shows only resources with yield");
  // units: /min is 60× /s
  await t.page.locator("#dock .fchip", { hasText: /^all/ }).click(); await t.idle();
  const cell = async () => t.ev(() => { const r = document.querySelector('#dock tr.row[data-item="ore_1"]'); return r ? r.children[2].title : null; });
  const perS = parseFloat(await cell());
  await t.page.locator("#dock .seg button", { hasText: "/min" }).click(); await t.idle();
  t.eq(parseFloat(await cell()), +(perS * 60).toPrecision(12), "made in /min");
});

scenario("balance_sheet_expand_and_select_maker", async (t) => {
  await openSheet(t);
  await t.page.locator('#dock tr.row[data-item="ore_2"]').click(); await t.idle();
  t.expect(await t.page.locator("#dock tr.detail").count() === 1, "row expands");
  const maker = t.page.locator("#dock tr.detail .det-row").first();
  await maker.click(); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "node", id: "mac_node_2" }, "clicking a maker selects that machine");
  // hovering a row outlines its makers / users on the canvas
  await t.page.locator('#dock tr.row[data-item="ore_3"]').hover();
  t.expect(await t.page.locator('.node.hl-make[data-id="mac_node_3"]').count() === 1, "maker outlined on hover");
  t.expect(await t.page.locator('.node.hl-use[data-id="mac_node_4"]').count() === 1, "user outlined on hover");
});

scenario("balance_sheet_keeps_place_and_persists", async (t) => {
  await t.page.setViewportSize({ width: 1400, height: 700 });
  await openSheet(t);
  const before = await t.setScroll("#dock .dock-body", 80);
  t.expect(before > 0, "sheet scrolls for this test");
  await t.select("recipe", "mac_3");
  await t.commit(t.input("cycle time"), "6");
  t.eq(await t.scrollTop("#dock .dock-body"), before, "sheet scroll survives a selection + edit");
  await t.page.locator("#dock .dock-tabs button", { hasText: "machines" }).click(); await t.idle();
  await t.page.reload(); await t.page.waitForFunction(() => BOOTED); await t.idle();
  t.eq(await t.ev(() => [DOCK.open, DOCK.tab]), [true, "machines"], "open state and tab persist across reloads");
  await t.ev(() => document.activeElement.blur && document.activeElement.blur());
  await t.page.keyboard.press("b"); await t.idle();
  t.eq(await t.ev(() => DOCK.open), false, "B toggles the sheet");
});

scenario("balance_sheet_machines_bottlenecks_export", async (t) => {
  await openSheet(t);
  await t.page.locator("#dock .dock-tabs button", { hasText: "machines" }).click(); await t.idle();
  t.eq((await sheetRows(t)).length, await t.ev(() => REPORT.nodeIds.length), "every machine listed");
  await t.page.locator('#dock tr.row[data-node="mac_node_6"]').click(); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "node", id: "mac_node_6" }, "clicking a machine selects it");
  await t.page.locator("#dock .dock-tabs button", { hasText: "bottlenecks" }).click(); await t.idle();
  t.eq(await t.page.locator("#dock tr.row").count(), await t.ev(() => REPORT.bottlenecks.length), "every bottleneck listed");
  const tbl = await t.ev(() => sheetTable());
  t.eq(tbl.length - 1, await t.ev(() => REPORT.bottlenecks.length), "export has one line per row + header");
});

/* ---------------------------------------------------------------- long lists */
scenario("long_allowed_recipes_list_is_capped_and_filterable", async (t) => {
  await t.select("machineDef", "macerator");          // 60 allowed recipes
  const f = t.page.locator('.insp-body [data-fk="allowed"]');
  const box = await f.locator(".chipset-box").boundingBox();
  t.expect(box && box.height <= 160, "60 chips sit in a capped scroll box (height " + (box && box.height) + ")");
  const fieldH = (await f.boundingBox()).height;
  t.expect(fieldH < 360, "the allowed-recipes field stays compact (" + fieldH + "px)");
  t.eq(await f.locator(".type-chip").allTextContents(), ["macerator×40×", "(no type)×20×"], "summary by machine type");
  // filter keeps focus as it narrows; "remove shown" drops just the matches
  const q = f.locator(".chipset-head input");
  await q.click(); await t.page.keyboard.type("Macerate 1");
  t.eq(await f.locator(".chipset-n").textContent(), "11 / 60", "filter count");
  t.eq((await t.active()).value, "Macerate 1", "filter keeps focus and text");
  await f.locator(".chipset-head button").click(); await t.idle();
  const left = (await t.entity("machineDef", "macerator")).allowedRecipeIds;
  t.eq(left.length, 49, "11 filtered recipes removed");
  t.expect(!left.includes("mac_1") && !left.includes("mac_15") && left.includes("mac_2"), "only the matches removed");
  t.eq(await t.page.locator('.insp-body [data-fk="allowed"] .chipset-head input').inputValue(), "Macerate 1", "filter survives the re-render");
  // a whole machine type at once
  await t.page.locator('.insp-body [placeholder="+ allow a whole machine type…"]').click();
  await t.page.keyboard.type("macerator"); await t.page.keyboard.press("Enter"); await t.idle();
  const ids = (await t.entity("machineDef", "macerator")).allowedRecipeIds;
  t.eq(ids.length, 60, "the 11 removed (all macerator-type) re-allowed in one pick");
  // a type block can be removed in one click
  await t.page.locator('.insp-body .type-chip', { hasText: "(no type)" }).locator(".chip-x").click(); await t.idle();
  t.eq((await t.entity("machineDef", "macerator")).allowedRecipeIds.length, 40, "untyped block removed");
});

scenario("long_tag_member_list_is_capped", async (t) => {
  await t.ev(() => applyEdits([{ op: "upsert", kind: "item", entity: { ...findEntity("item", "ore_0"), isTag: true, members: WORLD.items.slice(1, 101).map(i => i.id) } }]));
  await t.select("item", "ore_0");
  const box = await t.page.locator(".insp-body .chipset-box").boundingBox();
  t.expect(box && box.height <= 160, "100 members sit in a capped scroll box");
  t.expect(await t.page.locator('.insp-body [placeholder="+ add member…"]').isVisible(), "add-member box still reachable");
});

scenario("recipe_machine_type_suggests_and_lists_runners", async (t) => {
  await t.select("recipe", "mac_3");
  t.eq(await t.ev(() => [...document.querySelectorAll("#dl-machine-types option")].map(o => o.value)), ["macerator"], "existing types suggested");
  t.eq(await t.field("run by").locator("button.chip").allTextContents(), ["Electric Macerator"], "run by lists the def");
  await t.field("run by").locator("button.chip").click(); await t.idle();
  t.eq(await t.ev(() => SEL), { kind: "machineDef", id: "macerator" }, "clicking it opens the def");
});

scenario("balance_sheet_under_supplied_vs_limiting", async (t) => {
  // A quarry makes 0.1 ore/s; the macerator needs 1/s but has no power, so power
  // (not ore) is what holds it: ore is under-supplied but not limiting.
  await api("set_world", {
    items: [{ id: "silver_ore", name: "Silver Ore", category: "solid" }, { id: "silver_dust", name: "Silver Dust", category: "solid" }, { id: "power", name: "Power", category: "energy" }],
    recipes: [{ id: "mine", name: "Mine", timeSeconds: 10, outputs: [{ itemId: "silver_ore", quantity: 1 }] },
      { id: "grind", name: "Grind", timeSeconds: 1, euPerTick: 2, inputs: [{ itemId: "silver_ore", quantity: 1 }], outputs: [{ itemId: "silver_dust", quantity: 1 }] }],
    machineDefs: [{ id: "quarry", name: "Quarry" }, { id: "mac", name: "Macerator", energySource: "electric", sourceItemId: "power", baseEnergyConsumption: 40 }, { id: "gen", name: "Generator" }],
    nodes: [{ id: "q1", machineDefId: "quarry", recipeIds: ["mine"], x: 0, y: 0 }, { id: "m1", machineDefId: "mac", recipeIds: ["grind"], x: 300, y: 0 }, { id: "g1", machineDefId: "gen", x: 300, y: 200 }],
    pipes: [{ id: "p1", category: "solid", movementCapacity: 16, a: { nodeId: "q1", role: "out", port: "solid:out" }, b: { nodeId: "m1", role: "in", port: "solid:in" } },
      { id: "p2", category: "energy", movementCapacity: 5120, a: { nodeId: "g1", role: "out", port: "energy:out" }, b: { nodeId: "m1", role: "in", port: "energy:in" } }],
    pipeNodes: [], systems: [],
  });
  await t.page.reload(); await t.page.waitForFunction(() => BOOTED); await t.idle();
  await openSheet(t);
  const flags = (id) => t.page.locator(`#dock tr.row[data-item="${id}"] .flag`).allTextContents();
  t.eq(await flags("silver_ore"), ["under-supplied", "product"], "ore: capacity deficit, not the binding input (its unused trickle is surplus)");
  t.eq(await flags("power"), ["limiting", "under-supplied"], "power: binding input and in deficit");
  await t.page.locator('#dock tr.row[data-item="silver_ore"]').click(); await t.idle();
  const user = await t.page.locator("#dock tr.detail .det-row").last().textContent();
  t.expect(/stalled · held by Power/.test(user), "the user says what actually holds it: " + user);
  t.expect(await t.page.locator("#dock .fchip", { hasText: /^limiting/ }).getAttribute("title"), "filter chips explain themselves");
});

/* ---------------------------------------------------------------- runner */
(async () => {
  const browser = await chromium.launch();
  const results = {};
  const names = only.length ? only : Object.keys(S);
  for (const name of names) {
    const page = await browser.newPage({ viewport: { width: 1400, height: 800 } });
    const fails = [], errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    try {
      await api("set_world", WORLD);
      await page.goto(BASE);
      await page.waitForFunction(() => typeof BOOTED !== "undefined" && BOOTED);
      await page.waitForTimeout(150);
      await S[name](makeT(page, fails));
    } catch (e) {
      fails.push("threw: " + String(e).split("\n")[0]);
    }
    for (const e of errors) fails.push("page error: " + e);
    results[name] = { fails };
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify(results));
})().catch((e) => { console.error(e); process.exit(1); });
