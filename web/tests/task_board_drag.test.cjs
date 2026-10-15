"use strict";

const assert = require("node:assert/strict");

const {zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const {drop_slot, indicator_offset, is_inside, scroll_step} = zrequire("task_board_drag");

// Three cards 100 tall with a 10 gap, in a column whose card area
// starts at y = 200.
const container_top = 200;
const gap = 10;
const boxes = [
    {top: 200, height: 100},
    {top: 310, height: 100},
    {top: 420, height: 100},
];

run_test("the slot counts the cards the pointer has passed the middle of", () => {
    assert.equal(drop_slot(boxes, 150), 0);
    assert.equal(drop_slot(boxes, 249), 0);
    assert.equal(drop_slot(boxes, 250), 1);
    assert.equal(drop_slot(boxes, 305), 1);
    assert.equal(drop_slot(boxes, 400), 2);
    assert.equal(drop_slot(boxes, 470), 3);
    assert.equal(drop_slot(boxes, 900), 3);
});

run_test("an empty column has one slot", () => {
    assert.equal(drop_slot([], 0), 0);
    assert.equal(drop_slot([], 500), 0);
    assert.equal(indicator_offset([], 0, container_top, gap), 0);
});

run_test("the line sits in the gap the slot points at", () => {
    // Before the first card, between two cards, after the last card.
    assert.equal(indicator_offset(boxes, 0, container_top, gap), -5);
    assert.equal(indicator_offset(boxes, 1, container_top, gap), 105);
    assert.equal(indicator_offset(boxes, 2, container_top, gap), 215);
    assert.equal(indicator_offset(boxes, 3, container_top, gap), 325);
});

run_test("the line and the drop agree for every pointer position", () => {
    for (let pointer_y = 190; pointer_y <= 540; pointer_y += 5) {
        const slot = drop_slot(boxes, pointer_y);
        const line_y = container_top + indicator_offset(boxes, slot, container_top, gap);
        const cards_above = boxes.filter((box) => box.top + box.height <= line_y).length;
        assert.equal(cards_above, slot, `pointer at ${pointer_y}`);
    }
});

run_test("a point on the edge of a column counts as inside on the near edge only", () => {
    const rect = {left: 10, right: 110, top: 20, bottom: 220};
    assert.ok(is_inside(rect, 10, 20));
    assert.ok(is_inside(rect, 109, 219));
    assert.ok(!is_inside(rect, 110, 100));
    assert.ok(!is_inside(rect, 50, 220));
    assert.ok(!is_inside(rect, 9, 100));
});

run_test("the scroll step follows how close the pointer is to an edge", () => {
    // An area from 100 to 700 with a 50 zone and a top speed of 20.
    assert.equal(scroll_step(400, 100, 700, 50, 20), 0);
    assert.equal(scroll_step(150, 100, 700, 50, 20), 0);
    assert.equal(scroll_step(125, 100, 700, 50, 20), -10);
    assert.equal(scroll_step(100, 100, 700, 50, 20), -20);
    assert.equal(scroll_step(675, 100, 700, 50, 20), 10);
    assert.equal(scroll_step(700, 100, 700, 50, 20), 20);
    // Outside the area nothing scrolls.
    assert.equal(scroll_step(50, 100, 700, 50, 20), 0);
    assert.equal(scroll_step(750, 100, 700, 50, 20), 0);
});
