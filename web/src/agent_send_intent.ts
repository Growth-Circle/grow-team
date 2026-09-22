import {new_client_key} from "./agent_ui_state.ts";
import {current_user, realm} from "./state_data.ts";

// A visit changes on navigation, even when the user returns to the same destination.
let visit_token = new_client_key();
let draft_revision = 0;

export type SendAuthority = Readonly<{
    visit_token: string;
    draft_revision: number;
    draft_id: string;
    destination: string;
    content: string;
    send_key: string;
    actor: string;
}>;

function actor(): string {
    return `${realm?.realm_url ?? ""}:${current_user?.user_id ?? ""}`;
}

export function change_draft(): void {
    draft_revision += 1;
}

export function change_visit(): void {
    visit_token = new_client_key();
    change_draft();
}

export function current_visit_token(): string {
    return visit_token;
}

export function capture(draft_id: string, destination: string, content: string): SendAuthority {
    return Object.freeze({
        visit_token,
        draft_revision,
        draft_id,
        destination,
        content,
        send_key: new_client_key(),
        actor: actor(),
    });
}

export function is_current(intent: SendAuthority, destination: string, content: string): boolean {
    return (
        intent.visit_token === visit_token &&
        intent.draft_revision === draft_revision &&
        intent.destination === destination &&
        intent.content === content &&
        intent.actor === actor()
    );
}
