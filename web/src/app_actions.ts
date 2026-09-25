import * as permissions from "./permissions.ts";

// Registry of named actions that the command palette and other
// launch points can run by name, so a caller does not have to import
// the module that does the work.
//
// `permission` only keeps an action that the user cannot use out of
// list(). The server still checks every request.

export type AppAction = {
    name: string;
    label: string;
    permission?: string;
    run: () => void;
};

const actions = new Map<string, AppAction>();

export function register(action: AppAction): void {
    actions.set(action.name, action);
}

function is_allowed(action: AppAction): boolean {
    return action.permission === undefined || permissions.can(action.permission);
}

export function list(): AppAction[] {
    return [...actions.values()].filter((action) => is_allowed(action));
}

export function run(name: string): void {
    const action = actions.get(name);
    if (action !== undefined && is_allowed(action)) {
        action.run();
    }
}
