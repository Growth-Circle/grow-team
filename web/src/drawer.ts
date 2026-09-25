import $ from "jquery";

import render_drawer from "../templates/drawer.hbs";

import * as modals from "./modals.ts";

/*

The Sanji drawer is a panel on the right side of the window. This
module makes the frame of the drawer: the scrim, the header, the body
that scrolls, and the footer. The caller gives the content in $body,
and the footer buttons in actions.

The drawer uses modals.ts for Esc, for a click on the scrim, and for
one overlay at a time. drawer.hbs has the same class names as a
dialog_widget.ts modal, thus modals.open() handles the drawer as a
modal. A new drawer or a new modal closes the drawer that is open.

*/

export type DrawerAction = {
    label: string;
    // A primary button has the accent color. Other buttons are plain.
    primary?: boolean;
    on_click: () => void;
};

export type DrawerOpenOptions = {
    // The mono label in the header, for example "TUGAS · #12".
    kind_label: string;
    $body: JQuery;
    actions?: DrawerAction[];
    on_close?: () => void;
};

let drawer_id_counter = 0;
// The open drawer, from on_show until on_hidden.
let open_drawer_id: string | undefined;
// modals.close() has no effect before the open animation ends. These
// two values keep a close() call that comes before that time.
let open_drawer_shown = false;
let close_when_shown = false;
// The element that had focus before the drawer opened. Focus goes
// back to it when the drawer closes.
let $trigger: JQuery<Element> | undefined;

export function is_open(): boolean {
    return open_drawer_id !== undefined;
}

export function close(): void {
    if (open_drawer_id === undefined) {
        return;
    }
    if (!open_drawer_shown) {
        close_when_shown = true;
        return;
    }
    modals.close(open_drawer_id);
}

function build_action_button(action: DrawerAction): JQuery {
    return $("<button>")
        .attr("type", "button")
        .addClass("sj-drawer__action")
        .toggleClass("sj-drawer__action--primary", action.primary === true)
        .text(action.label)
        .on("click", () => {
            action.on_click();
        });
}

export function open(opts: DrawerOpenOptions): void {
    drawer_id_counter += 1;
    const drawer_id = `sj_drawer_${drawer_id_counter}`;
    // When a drawer opens from an open drawer, keep the first trigger.
    // The focused element is then in the old drawer, and the old
    // drawer goes out of the page.
    const $previous_trigger = $trigger ?? $(document.activeElement ?? document.body);

    const $drawer = $(render_drawer({drawer_unique_id: drawer_id, kind_label: opts.kind_label}));
    $("body").append($drawer);
    $drawer.find(".sj-drawer__body").append(opts.$body);
    const $footer = $drawer.find(".sj-drawer__footer");
    for (const action of opts.actions ?? []) {
        const $button = build_action_button(action);
        $footer.append($button);
    }

    modals.open(drawer_id, {
        autoremove: true,
        on_show() {
            open_drawer_id = drawer_id;
            open_drawer_shown = false;
            close_when_shown = false;
            $trigger = $previous_trigger;
            $drawer.find(".sj-drawer__close").trigger("focus");
        },
        on_shown() {
            open_drawer_shown = true;
            if (close_when_shown) {
                close();
            }
        },
        on_hidden() {
            open_drawer_id = undefined;
            open_drawer_shown = false;
            close_when_shown = false;
            opts.on_close?.();
            // The trigger can be out of the page, for example when
            // its list shows again with new elements.
            if ($trigger?.[0]?.isConnected) {
                $trigger.trigger("focus");
            }
            $trigger = undefined;
        },
    });
}
