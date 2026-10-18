import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

const TITLE = "Evaluasi 4 jam";
const FIRST_TEXT =
    "# Evaluasi 4 jam\n\n<!-- note for the authors -->\n1. Check the blocked cards\n2. Send the summary\n";
const SECOND_TEXT = `${FIRST_TEXT}3. Close the day\n`;

// The text boxes take their value at once, because typing a long text one
// key at a time would only slow the test down.
async function fill(page: Page, selector: string, value: string): Promise<void> {
    await page.$eval(
        selector,
        (element, text) => {
            if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) {
                throw new TypeError("Expected a text field.");
            }
            element.focus();
            element.value = text;
            element.dispatchEvent(new Event("input", {bubbles: true}));
        },
        value,
    );
}

async function save_is_disabled(page: Page): Promise<boolean> {
    return await page.$eval(".playbook-save", (element) => {
        if (!(element instanceof HTMLButtonElement)) {
            throw new TypeError("Expected a button.");
        }
        return element.disabled;
    });
}

// The page of one playbook keeps its three buttons where they are,
// whatever it shows.
async function action_positions(page: Page): Promise<number[][]> {
    return await page.evaluate(() =>
        [".playbook-action-edit", ".playbook-action-history", ".playbook-action-archive"].map(
            (selector) => {
                const rect = document.querySelector(selector)?.getBoundingClientRect();
                if (!rect) {
                    throw new Error(`No element for ${selector}.`);
                }
                return [rect.x, rect.y, rect.width, rect.height].map((value) => Math.round(value));
            },
        ),
    );
}

async function test_create_edit_and_history(page: Page): Promise<void> {
    await common.log_in(page);
    await common.go_to_hash(page, "#playbooks");
    await page.waitForSelector("#playbooks-view .playbooks-page", {visible: true});

    // Create.
    await page.waitForSelector("#playbooks-view .playbooks-new", {visible: true});
    await page.click("#playbooks-view .playbooks-new");
    await page.waitForSelector(".playbook-editor", {visible: true});
    assert.ok(await save_is_disabled(page), "Save is off on an empty form.");
    await fill(page, ".playbook-title-input", TITLE);
    assert.ok(await save_is_disabled(page), "Save is off without text.");
    await fill(page, ".playbook-content-input", FIRST_TEXT);
    assert.ok(!(await save_is_disabled(page)), "Save is on with a title and text.");

    await page.click(".playbook-mode[data-mode='preview']");
    await page.waitForSelector(".playbook-preview h1", {visible: true});
    const preview = await common.get_text_from_selector(page, ".playbook-preview");
    assert.ok(!preview.includes("note for the authors"), "The preview hides HTML comments.");
    await page.click(".playbook-mode[data-mode='write']");

    await page.click(".playbook-save");
    await page.waitForSelector(".playbook-document", {visible: true});
    const document_html = await page.$eval(".playbook-document", (element) => element.innerHTML);
    assert.ok(document_html.includes("<h1"));
    assert.ok(document_html.includes("<ol>"));
    assert.ok(!document_html.includes("note for the authors"));
    assert.match(await common.get_text_from_selector(page, ".sj-page-header-title"), /Evaluasi/);
    const view_positions = await action_positions(page);

    // Edit: Save waits for a change, and a second revision is made.
    await page.click(".playbook-action-edit");
    await page.waitForSelector(".playbook-editor", {visible: true});
    assert.deepEqual(await action_positions(page), view_positions);
    assert.ok(await save_is_disabled(page), "Save is off until the text changes.");
    await fill(page, ".playbook-content-input", SECOND_TEXT);
    assert.ok(!(await save_is_disabled(page)), "Save is on after a change.");
    await fill(page, ".playbook-summary-input", "Add a closing step");
    await page.click(".playbook-save");
    await page.waitForSelector(".playbook-document", {visible: true});
    await page.waitForFunction(() =>
        document.querySelector(".playbook-document")?.textContent?.includes("Close the day"),
    );
    assert.match(
        await common.get_text_from_selector(page, ".sj-page-header-description"),
        /Revision 2/,
    );

    // History: both revisions, with the change of the second.
    await page.click(".playbook-action-history");
    await page.waitForSelector(".playbook-diff", {visible: true});
    assert.deepEqual(await action_positions(page), view_positions);
    const revisions = await page.$$eval(".playbook-revision", (elements) =>
        elements.map((element) => (element.textContent ?? "").replaceAll(/\s+/g, " ").trim()),
    );
    assert.equal(revisions.length, 2);
    assert.match(revisions[0]!, /Revision 2 Latest/);
    assert.match(revisions[0]!, /Add a closing step/);
    assert.match(revisions[1]!, /Revision 1/);
    const added = await page.$$eval(".playbook-diff-added .playbook-diff-text", (elements) =>
        elements.map((element) => element.textContent ?? ""),
    );
    assert.ok(added.some((line) => line.includes("Close the day")));
    await common.screenshot(page, "playbooks-history");

    // The list shows it, and the Mine tab does not.
    await common.go_to_hash(page, "#playbooks");
    await page.waitForSelector(".playbook-row", {visible: true});
    assert.match(await common.get_text_from_selector(page, ".playbook-row"), /Revision 2/);
    await common.go_to_hash(page, "#playbooks/mine");
    await page.waitForFunction(() =>
        document.querySelector(".playbooks-list-region")?.textContent?.includes("no playbooks"),
    );
}

await common.run_test(test_create_edit_and_history);
