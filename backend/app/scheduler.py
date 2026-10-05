"""Critical-chain planning.

Pipeline (all rules are deterministic, see docs/DECISIONS.md):

1. ``downstream weight``  w(t) = aggressive duration of t plus the maximum
   weight of its logical successors.
2. A serial schedule generation scheme (Serial-SGS) resolves resource
   contention.  Tasks are picked in a precedence-feasible order produced by
   Kahn's algorithm with priority key ``(-w, task id)``: the task with the
   *longest downstream chain* goes first, ties broken lexicographically by
   id.  Each task is placed at the earliest time at which all predecessors
   are finished and every required resource has enough free capacity across
   the whole task.
3. A "tight graph" is built from:
   - every logical edge p->t whose finish equals t's start;
   - resource edges i->t where i occupies a resource that t needs, i
     finishes exactly when t starts, and without the capacity freed at that
     instant t could not have started.
4. The critical chain is the longest-duration path in the tight graph
   (aggressive durations).  Equal-length paths are broken deterministically
   by the lexicographically smallest tuple of task ids.  With no resource
   contention this is exactly the dependency critical path.
5. Project buffer PB = 0.5 * sum(conservative - aggressive) over the chain.
   Each longest non-critical path that enters the chain gets a feeding
   buffer FB computed by the same formula.

The heuristic is a longest-path-first greedy rule.  Networks where several
equal-resource tasks fan out and then merge are the known weak spot: the
rule always favors the long branch, so whenever favoring the *short*
branch would have permitted better interleaving, the produced chain is
longer than a time-optimal schedule.  It is stable (same input -> same
plan) and never shorter than the dependency critical path.
"""
from __future__ import annotations

import heapq
from datetime import datetime

from .buffers import buffer_basis as _buffer_basis, buffer_size as _buffer_size
from .timezones import UTC, get_tz
from .worktime import Calendar

EPS = 1e-9


# --------------------------- graph utilities ----------------------------- #

def _topo(tasks: dict[str, dict], order: list[str]) -> list[str]:
    indeg = {tid: len(tasks[tid]["preds"]) for tid in order}
    succ: dict[str, list[str]] = {tid: [] for tid in order}
    for tid in order:
        for p in tasks[tid]["preds"]:
            succ[p].append(tid)
    ready = [tid for tid in order if indeg[tid] == 0]
    heapq.heapify(ready)
    out: list[str] = []
    while ready:
        cur = heapq.heappop(ready)
        out.append(cur)
        for s in succ[cur]:
            indeg[s] -= 1
            if indeg[s] == 0:
                heapq.heappush(ready, s)
    return out  # validation already proved acyclic


def _downstream_weights(tasks, topo) -> dict[str, float]:
    succ: dict[str, list[str]] = {tid: [] for tid in topo}
    for tid in topo:
        for p in tasks[tid]["preds"]:
            succ[p].append(tid)
    w: dict[str, float] = {}
    for tid in reversed(topo):
        w[tid] = tasks[tid]["agg"] + max(
            (w[s] for s in succ[tid]), default=0.0)
    return w


def _priority_list(tasks, order, weights) -> list[str]:
    """Precedence-feasible list: Kahn with heap keyed by (-weight, id)."""
    indeg = {tid: len(tasks[tid]["preds"]) for tid in order}
    succ: dict[str, list[str]] = {tid: [] for tid in order}
    for tid in order:
        for p in tasks[tid]["preds"]:
            succ[p].append(tid)
    heap = [(-weights[tid], tid) for tid in order if indeg[tid] == 0]
    heapq.heapify(heap)
    out: list[str] = []
    while heap:
        _, cur = heapq.heappop(heap)
        out.append(cur)
        for s in succ[cur]:
            indeg[s] -= 1
            if indeg[s] == 0:
                heapq.heappush(heap, (-weights[s], s))
    return out


# --------------------------- serial SGS ---------------------------------- #

def _schedule(tasks, resources, order, priority):
    start: dict[str, float] = {}
    finish: dict[str, float] = {}
    # resource id -> list of (start, finish, amount, task_id) kept sorted
    usage: dict[str, list[tuple[float, float, float, str]]] = {
        rid: [] for rid in resources}

    def feasible(j, t):
        d = tasks[j]["agg"]
        if d <= EPS:
            return True  # zero-duration task occupies no resource time
        end = t + d
        for rid, need in tasks[j]["demands"].items():
            # Capacity is a *cumulative* constraint: sum every interval
            # overlapping [t, end) (half-open), not pairwise comparisons.
            load = 0.0
            for (s, f, amount, _i) in usage[rid]:
                if s < end - EPS and f > t + EPS:
                    load += amount
            if load + need > resources[rid]["capacity"] + EPS:
                return False
        return True

    for j in priority:
        earliest = max((finish[p] for p in tasks[j]["preds"]), default=0.0)
        if tasks[j]["agg"] <= EPS:
            t = earliest
        else:
            # Feasibility can only change at a scheduled interval finish, so
            # those finishes (plus the predecessor-dictated earliest time)
            # are a sufficient set of candidate starts.
            candidates = {earliest}
            for rid in tasks[j]["demands"]:
                for (_s, f, _amount, _i) in usage[rid]:
                    if f > earliest + EPS:
                        candidates.add(f)
            t = min(c for c in candidates if feasible(j, c))
        start[j], finish[j] = t, t + tasks[j]["agg"]
        for rid, need in tasks[j]["demands"].items():
            usage[rid].append((t, finish[j], need, j))
            usage[rid].sort(key=lambda x: (x[0], x[1], x[3]))
    return start, finish, usage


# --------------------------- tight graph --------------------------------- #

def _tight_graph(tasks, resources, start, finish, usage):
    """Return adjacency {task: [tight predecessors]} (sorted id lists)."""
    tight: dict[str, set[str]] = {tid: set() for tid in start}

    # logical tight edges
    for j in start:
        for p in tasks[j]["preds"]:
            if abs(finish[p] - start[j]) <= EPS:
                tight[j].add(p)

    # resource tight edges: at every start boundary t of a task j needing
    # resource r, look at the holders active just before t.  If their load
    # plus j's demand exceeds capacity, every holder freeing capacity
    # exactly at t is a tight predecessor.
    for rid, intervals in usage.items():
        if not intervals:
            continue
        cap = resources[rid]["capacity"]
        for (sj, fj, need_j, j) in intervals:
            active_before = [
                (si, fi, i) for (si, fi, amount_i, i) in intervals
                if i != j and si < sj - EPS and fi >= sj - EPS
            ]
            if sum(tasks[i]["demands"][rid]
                   for _, _, i in active_before) + need_j > cap + EPS:
                for (_si, fi, i) in active_before:
                    if abs(fi - sj) <= EPS:
                        tight[j].add(i)

    return {tid: sorted(preds) for tid, preds in tight.items()}


def _longest_chains(nodes, tight_preds, tasks):
    """Longest duration path ending at each node, over the given node set.

    Returns (dist, chain) where chain[node] is the deterministic tuple of
    ids along the longest path ending at node (node included).  Ties on
    length are broken by the lexicographically smallest id tuple.
    """
    dist: dict[str, float] = {}
    chain: dict[str, tuple[str, ...]] = {}

    def visit(v):
        if v in dist:
            return
        best_d = 0.0
        best_tuple: tuple[str, ...] = ()
        for p in tight_preds.get(v, []):
            if p not in nodes:
                continue
            visit(p)
            cand_tuple = chain[p]
            cand_d = dist[p]
            if cand_d > best_d + EPS or (
                    abs(cand_d - best_d) <= EPS
                    and (not best_tuple or cand_tuple < best_tuple)):
                best_d, best_tuple = cand_d, cand_tuple
        dist[v] = best_d + tasks[v]["agg"]
        chain[v] = best_tuple + (v,)

    for v in nodes:
        visit(v)
    return dist, chain


# --------------------------- public plan --------------------------------- #

def build_plan(validated: dict, project_start: datetime | None = None) -> dict:
    tasks = validated["tasks"]
    resources = validated["resources"]
    order = validated["task_order"]

    tz_name = validated.get("timezone")
    tz = get_tz(tz_name) if tz_name else UTC
    cal = Calendar(tz)

    if project_start is None:
        if validated.get("start_time"):
            project_start = cal.parse_dt(validated["start_time"])
        else:
            project_start = datetime.now(UTC)
    project_start = cal.parse_dt(project_start)

    topo = _topo(tasks, order)
    weights = _downstream_weights(tasks, topo)
    priority = _priority_list(tasks, order, weights)

    start, finish, usage = _schedule(tasks, resources, order, priority)
    tight = _tight_graph(tasks, resources, start, finish, usage)
    dist, chain = _longest_chains(set(order), tight, tasks)

    # critical chain: longest chain overall, tie -> smallest id tuple
    best: tuple[float, tuple[str, ...]] | None = None
    for tid in order:
        cand = (dist[tid], chain[tid])
        if best is None or cand[0] > best[0] + EPS or (
                abs(cand[0] - best[0]) <= EPS and cand[1] < best[1]):
            best = cand
    assert best is not None
    cc_ids = list(best[1])
    cc_set = set(cc_ids)
    makespan = best[0]

    resource_edges = sorted({(p, t) for t, ps in tight.items() for p in ps
                             if p not in set(tasks[t]["preds"])})

    # feeding chains: a feeding buffer sits at the *end* of every non-CC
    # path that crosses into the critical chain (logically or via a
    # resource edge).  Classic Goldratt placement: the buffer starts right
    # after the feeding chain's last task; any gap between buffer end and
    # the CC node is natural schedule slack (shown on the Gantt).
    all_preds: dict[str, set[str]] = {tid: set(tasks[tid]["preds"])
                                      for tid in order}
    for a, b in resource_edges:
        all_preds[b].add(a)

    non_cc = set(order) - cc_set
    if non_cc:
        feed_preds = {tid: sorted(p for p in all_preds[tid] if p in non_cc)
                      for tid in non_cc}
        _d2, nchain = _longest_chains(non_cc, feed_preds, tasks)
        feeders: dict[tuple[str, ...], str] = {}
        for v in cc_ids:
            for p in sorted(all_preds[v]):
                if p in non_cc and _d2[p] > EPS:
                    feeders.setdefault(nchain[p], v)
        feeding = []
        seen_tuples: set[tuple[str, ...]] = set()
        ordered = sorted(
            feeders.items(),
            key=lambda kv: (cc_ids.index(kv[1]),
                            -sum(tasks[t]["agg"] for t in kv[0]), kv[0]))
        for idx, (tup, feeds) in enumerate(ordered, start=1):
            if tup in seen_tuples:
                continue
            seen_tuples.add(tup)
            basis = _buffer_basis(tup, tasks)
            size = _buffer_size(basis)
            end_day = finish[tup[-1]]
            feeding.append({
                "id": f"FB{idx}",
                "chain": list(tup),
                "feeds_task": feeds,
                "start_day": end_day - size,
                "end_day": end_day,
                "size_days": size,
                "basis": basis,
            })
    else:
        feeding = []

    pb_basis = _buffer_basis(cc_ids, tasks)
    pb_size = _buffer_size(pb_basis)

    scheduled = {}
    for tid in order:
        scheduled[tid] = {
            "start_day": start[tid],
            "finish_day": finish[tid],
            "start_at": cal.work_add(project_start, start[tid]).isoformat(),
            "finish_at": cal.work_add(project_start, finish[tid]).isoformat(),
            "on_critical_chain": tid in cc_set,
        }

    return {
        "project_start": project_start.isoformat(),
        "timezone": tz_name,
        "makespan_days": makespan,
        "critical_chain": cc_ids,
        "project_buffer": {
            "start_day": makespan,
            "end_day": makespan + pb_size,
            "size_days": pb_size,
            "basis": pb_basis,
        },
        "feeding_buffers": feeding,
        "schedule": scheduled,
        "priority_list": priority,
        "weights": {tid: weights[tid] for tid in order},
        "resource_edges": [list(e) for e in resource_edges],
        "thresholds": validated["thresholds"],
    }
