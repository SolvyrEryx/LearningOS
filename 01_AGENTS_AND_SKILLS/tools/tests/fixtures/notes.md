# Merge sort - synthetic lecture notes (test fixture)

## The recurrence

Splitting costs nothing, merging costs `n`, and there are two subproblems of size `n/2`:

    T(n) = 2T(n/2) + n

By case 2 of the master theorem, `a = 2`, `b = 2`, `f(n) = n`, and `n^log_b(a) = n`, so
`T(n) = Theta(n log n)`.

## What the exam actually asks

1. Derive the recurrence from the pseudocode, not from memory.
2. Say which case of the master theorem applies, and why.
3. State whether the sort is stable, and justify it from the merge step.

## A mistake worth remembering

Dropping the `n` in the merge step gives `T(n) = 2T(n/2)`, which solves to `Theta(n)` and is
wrong. The merge is linear; that is the whole point of the algorithm.
