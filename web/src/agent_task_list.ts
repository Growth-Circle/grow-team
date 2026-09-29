/*
    The old task list at #agent-jobs is gone. The Tasks screen lists
    tasks, and the Today screen lists the jobs that agents run now. A
    saved link to #agent-jobs opens the Today screen.

    The address is replaced, not added to the history. A Back button
    press then does not return to the old address, which would send the
    person to Today again.
*/

export let open = (): void => {
    window.location.replace("#today");
};

export function rewire_open(value: typeof open): void {
    open = value;
}
