"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const pending_channel_name = zrequire("pending_channel_name");
const room_create_modal = zrequire("room_create_modal");

const folders = [
    {id: 1, name: "Proyek"},
    {id: 2, name: "Klien"},
    {id: 3, name: "Tim"},
];

run_test("channel_form_hash", () => {
    const hash = (type, list = folders) => room_create_modal.channel_form_hash(type, list);

    assert.equal(hash("project"), "#channels/folders/1/new");
    assert.equal(hash("client"), "#channels/folders/2/new");
    assert.equal(hash("team"), "#channels/folders/3/new");
    // A workspace without the folders goes to the form with no folder.
    assert.equal(hash("client", [{id: 4, name: "Finance"}]), "#channels/new");
});

run_test("pending_channel_name", () => {
    assert.equal(pending_channel_name.take(), undefined);
    pending_channel_name.set("Zeta Test");
    assert.equal(pending_channel_name.take(), "Zeta Test");
    // The name is used once, so a later form does not inherit it.
    assert.equal(pending_channel_name.take(), undefined);
});
