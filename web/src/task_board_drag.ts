// The arithmetic of dragging a card, kept apart from the DOM so the
// line that shows where a card will land and the drop itself read from
// the same numbers.

export type CardBox = {top: number; height: number};

// The slot is the number of cards the pointer has passed the middle
// of. Slot 0 is above the first card and slot `boxes.length` is below
// the last one, which is also the only slot of an empty column.
export function drop_slot(boxes: CardBox[], pointer_y: number): number {
    const slot = boxes.findIndex((box) => pointer_y < box.top + box.height / 2);
    return slot === -1 ? boxes.length : slot;
}

// Where the insertion line sits, measured from the top of the column's
// card area. It goes in the middle of the gap between two cards, so it
// does not move the cards it points between.
export function indicator_offset(
    boxes: CardBox[],
    slot: number,
    container_top: number,
    gap: number,
): number {
    const next = boxes[slot];
    if (next !== undefined) {
        return next.top - gap / 2 - container_top;
    }
    const last = boxes.at(-1);
    if (last === undefined) {
        return 0;
    }
    return last.top + last.height + gap / 2 - container_top;
}

export function is_inside(
    rect: {left: number; right: number; top: number; bottom: number},
    x: number,
    y: number,
): boolean {
    return x >= rect.left && x < rect.right && y >= rect.top && y < rect.bottom;
}

// How far to scroll for a pointer at `pointer` in an area that runs
// from `start` to `end`. The step grows from nothing at the edge of the
// hot zone to `max_step` at the edge of the area, and its sign says
// which way to scroll.
export function scroll_step(
    pointer: number,
    start: number,
    end: number,
    zone: number,
    max_step: number,
): number {
    if (pointer < start || pointer > end) {
        return 0;
    }
    if (pointer < start + zone) {
        return -Math.ceil(max_step * ((start + zone - pointer) / zone));
    }
    if (pointer > end - zone) {
        return Math.ceil(max_step * ((pointer - (end - zone)) / zone));
    }
    return 0;
}
