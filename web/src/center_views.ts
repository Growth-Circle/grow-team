import $ from "jquery";
import assert from "minimalistic-assert";

import * as room_header from "./room_header.ts";
import * as room_topics_column from "./room_topics_column.ts";

// Registry for the views that can fill the main column: the Sanji
// screens (Today, Needs you, Agents, ...) and the Zulip views that
// predate them (Recent, Inbox, the task board). Each view registers
// once, so no view has to know the names of its siblings to hide
// them.
//
// A narrow to a message feed does not go through `show`, because
// message_view.ts keeps its own Filter-based entry point. It calls
// `hide_all` and then shows the feed and the room header itself.

export type CenterView = {
    id: string;
    hash: string;
    show: (args: string[]) => void;
    hide: () => void;
    title: () => string;
};

const views = new Map<string, CenterView>();
let current_id: string | undefined;

export function register(view: CenterView): void {
    assert(!views.has(view.id), `center_views: "${view.id}" is already registered`);
    views.set(view.id, view);
}

// Hides every registered view except `id`, and records `id` as the
// view on screen. A view that is already visible keeps its state:
// Inbox and the task board skip a full render, and an open task
// card stays open.
export function hide_others(id: string | undefined): void {
    for (const [view_id, view] of views) {
        if (view_id !== id) {
            view.hide();
        }
    }
    $("#room-header, #room-topics-column").hide();
    if (id !== undefined) {
        // A center view replaces the message feed. The room header and
        // topic column are not in `views`, so they need their own
        // teardown. `hide_all` skips it: a narrow then draws or hides
        // them itself.
        room_header.hide();
        room_topics_column.hide();
    }
    current_id = id;
}

export function hide_all(): void {
    hide_others(undefined);
}

export function show(id: string, args: string[] = []): void {
    const view = views.get(id);
    assert(view !== undefined, `center_views: "${id}" is not registered`);
    // Record the new view before it renders: the view's own show()
    // sets the browser tab title, which reads current_title().
    hide_others(id);
    view.show(args);
}

export function current(): string | undefined {
    return current_id;
}

export function is_any_visible(): boolean {
    return current_id !== undefined;
}

export function current_title(): string | undefined {
    if (current_id === undefined) {
        return undefined;
    }
    return views.get(current_id)?.title();
}
