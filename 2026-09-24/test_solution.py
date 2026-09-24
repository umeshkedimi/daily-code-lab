import os
import random
import statistics
import subprocess
import sys
import threading

import pytest

from solution import EmptyRingError, HashRing, ModuloSharder, RendezvousHasher, stable_hash64

KEYS = [f"user:{i}" for i in range(20_000)]
NODES = [f"node-{i}" for i in range(10)]


def ring_of(nodes, vnodes=128, **kw):
    r = HashRing(vnodes=vnodes, **kw)
    r.add_nodes(nodes)
    return r


def assignment(fn, keys=KEYS):
    return {k: fn(k) for k in keys}


def small_ring_hash(positions):
    """Deterministic hash for hand-checkable rings: labels/keys map to given positions."""
    return lambda data: positions[data.decode()]


# --- basics -----------------------------------------------------------------------------


def test_empty_ring_raises_on_lookup():
    r = HashRing()
    with pytest.raises(EmptyRingError):
        r.get_node("k")
    with pytest.raises(EmptyRingError):
        r.get_nodes("k", 2)


def test_single_node_owns_every_key():
    r = ring_of(["only"])
    assert {r.get_node(k) for k in KEYS[:500]} == {"only"}
    assert r.ownership() == {"only": 1.0}


def test_membership_errors():
    r = ring_of(["a"])
    with pytest.raises(ValueError):
        r.add_node("a")
    with pytest.raises(KeyError):
        r.remove_node("missing")
    with pytest.raises(ValueError):
        r.add_node("b", weight=0)
    with pytest.raises(ValueError):
        HashRing(vnodes=0)


def test_str_and_bytes_keys_are_equivalent():
    r = ring_of(NODES)
    assert all(r.get_node(k) == r.get_node(k.encode()) for k in KEYS[:200])


# --- exact behavior on a hand-checkable ring ------------------------------------------------
# 8-bit ring (256 positions); one vnode each: a@50, b@150, c@250.

POS = {"a#0": 50, "b#0": 150, "c#0": 250, "k0": 0, "k10": 10, "k50": 50, "k51": 51, "k150": 150, "k250": 250, "k251": 251, "k255": 255, "k60": 60}


@pytest.fixture
def tiny():
    return ring_of(["a", "b", "c"], vnodes=1, hash_fn=small_ring_hash(POS), bits=8)


@pytest.mark.parametrize(
    "key, expected",
    [
        ("k10", "a"),  # first vnode clockwise is a@50
        ("k50", "a"),  # exactly ON a vnode: that node owns the arc (prev, pos]
        ("k51", "b"),  # one past it: next vnode
        ("k150", "b"),
        ("k250", "c"),
        ("k251", "a"),  # past the last vnode: wraps to the first
        ("k255", "a"),
        ("k0", "a"),
    ],
)
def test_lookup_boundaries_and_wraparound(tiny, key, expected):
    assert tiny.get_node(key) == expected


def test_arcs_are_exact_and_sum_to_the_ring_size(tiny):
    # a owns (250-256, 50] = 56 positions (the wrapped arc); b and c own 100 each
    assert tiny.arcs() == {"a": 56, "b": 100, "c": 100}
    assert sum(tiny.arcs().values()) == 256


def test_replicas_walk_clockwise_over_distinct_nodes(tiny):
    assert tiny.get_nodes("k60", 3) == ["b", "c", "a"]  # 60 -> b@150 -> c@250 -> wrap a@50
    assert tiny.get_nodes("k60", 2) == ["b", "c"]
    assert tiny.get_nodes("k60", 0) == []
    assert tiny.get_nodes("k60", -2) == []
    assert tiny.get_nodes("k60", 99) == ["b", "c", "a"]  # asking for more than exist returns all


def test_colliding_vnodes_are_resolved_deterministically_and_removable():
    same = lambda data: 100  # every label and key hashes to the same position
    r1, r2 = HashRing(vnodes=1, hash_fn=same, bits=8), HashRing(vnodes=1, hash_fn=same, bits=8)
    r1.add_node("b"), r1.add_node("a")
    r2.add_node("a"), r2.add_node("b")
    assert r1.get_node("x") == r2.get_node("x") == "a"  # tie broken by name, not insertion order
    assert r1.get_nodes("x", 2) == ["a", "b"]
    assert r1.arcs() == {"a": 256, "b": 0}
    r1.remove_node("a")
    r1.check_invariants()
    assert r1.get_node("x") == "b"  # removing one colliding entry must not remove the other's


# --- determinism ---------------------------------------------------------------------------


def test_ring_depends_only_on_the_node_set_not_insertion_order():
    forward = ring_of(NODES)
    shuffled = HashRing(vnodes=128)
    order = NODES[:]
    random.Random(7).shuffle(order)
    for n in order:
        shuffled.add_node(n)
    assert forward._ring == shuffled._ring
    assert assignment(forward.get_node) == assignment(shuffled.get_node)


def test_bulk_add_equals_sequential_add():
    looped = HashRing(vnodes=64)
    for n in NODES:
        looped.add_node(n)
    assert looped._ring == ring_of(NODES, vnodes=64)._ring


def test_bulk_add_is_all_or_nothing():
    r = ring_of(["a", "b"], vnodes=16)
    snapshot = (list(r._ring[0]), list(r._ring[1]), r._ring[2])
    with pytest.raises(ValueError):
        r.add_nodes(["c", "d", "a"])  # 'a' already present -> the whole batch is rejected
    with pytest.raises(ValueError):
        r.add_nodes(["c", ("d", 0)])  # invalid weight
    with pytest.raises(ValueError):
        r.add_nodes(["c", "c"])  # duplicate within the batch
    assert r.nodes == ["a", "b"] and r._ring == snapshot


def _mapping_in_subprocess(hashseed: str) -> str:
    code = (
        "from solution import HashRing\n"
        "r = HashRing(vnodes=64); r.add_nodes([f'node-{i}' for i in range(8)])\n"
        "print(','.join(r.get_node(f'user:{i}') for i in range(500)))\n"
        "print(hash('user:1'))\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=os.path.dirname(__file__) or ".", timeout=60, check=True).stdout.splitlines()
    return out[0], out[1]


def test_mapping_is_identical_across_processes_despite_per_process_str_hash_salt():
    runs = [_mapping_in_subprocess(seed) for seed in ("1", "2", "12345")]
    # control: the premise -- Python's own str hash really does differ between these processes...
    assert len({hash_value for _, hash_value in runs}) == 3
    # ...yet the ring assignment does not, because it uses a stable hash.
    assert len({mapping for mapping, _ in runs}) == 1


# --- minimal disruption (the point of the whole structure) ---------------------------------------


def test_adding_a_node_moves_keys_only_onto_the_new_node():
    before = ring_of(NODES)
    after = ring_of(NODES + ["node-10"])
    a, b = assignment(before.get_node), assignment(after.get_node)
    moved = [k for k in KEYS if a[k] != b[k]]
    assert {b[k] for k in moved} == {"node-10"}  # nothing shuffles between the existing nodes
    assert 0.05 < len(moved) / len(KEYS) < 0.14  # ideal 1/11 = 9.1%


def test_removing_a_node_moves_exactly_that_nodes_keys_and_nothing_else():
    before = ring_of(NODES)
    after = ring_of([n for n in NODES if n != "node-3"])
    a, b = assignment(before.get_node), assignment(after.get_node)
    moved = {k for k in KEYS if a[k] != b[k]}
    assert moved == {k for k in KEYS if a[k] == "node-3"}  # every survivor keeps every key it had


def test_remove_then_readd_restores_the_identical_mapping():
    r = ring_of(NODES)
    original = assignment(r.get_node)
    r.remove_node("node-4")
    r.add_node("node-4")
    assert assignment(r.get_node) == original


def test_control_modulo_sharding_reshuffles_almost_everything():
    """If this stopped being large, the movement metric above wouldn't discriminate anything."""
    a = assignment(ModuloSharder(NODES).get_node)
    b = assignment(ModuloSharder(NODES + ["node-10"]).get_node)
    moved = sum(a[k] != b[k] for k in KEYS) / len(KEYS)
    assert moved > 0.85  # theory: N/(N+1) = 90.9%


def test_rendezvous_baseline_is_also_minimally_disruptive():
    a = assignment(RendezvousHasher(NODES).get_node)
    b = assignment(RendezvousHasher(NODES + ["node-10"]).get_node)
    assert {b[k] for k in KEYS if a[k] != b[k]} == {"node-10"}
    c = assignment(RendezvousHasher([n for n in NODES if n != "node-3"]).get_node)
    assert {k for k in KEYS if a[k] != c[k]} == {k for k in KEYS if a[k] == "node-3"}


# --- balance ---------------------------------------------------------------------------------------


def _mean_cv(vnodes, trials=20, n=10):
    cvs = []
    for t in range(trials):
        shares = list(ring_of([f"t{t}-node-{i}" for i in range(n)], vnodes).ownership().values())
        cvs.append(statistics.pstdev(shares) / statistics.mean(shares))
    return statistics.mean(cvs)


@pytest.mark.parametrize("vnodes", [10, 100, 500])
def test_load_imbalance_matches_the_beta_distribution_theory(vnodes):
    """A node's ring share with V random points is Beta(V, (N-1)V), whose
    coefficient of variation is sqrt((N-1)/(N*V+1))."""
    n = 10
    theory = ((n - 1) / (n * vnodes + 1)) ** 0.5
    assert _mean_cv(vnodes) == pytest.approx(theory, rel=0.25)


def test_more_virtual_nodes_means_more_even_load():
    cvs = [_mean_cv(v, trials=10) for v in (1, 16, 256)]
    assert cvs[0] > cvs[1] > cvs[2]
    assert cvs[0] > 0.5 and cvs[2] < 0.15  # one point per node is badly skewed; 256 is tolerable


def test_analytic_ownership_agrees_with_where_keys_actually_land():
    """Two independent computations of the same quantity: exact arc arithmetic
    vs. routing 50,000 real keys."""
    r = ring_of(NODES, vnodes=64)
    sample = [f"probe:{i}" for i in range(50_000)]
    counts = {n: 0 for n in NODES}
    for k in sample:
        counts[r.get_node(k)] += 1
    for node, share in r.ownership().items():
        assert counts[node] / len(sample) == pytest.approx(share, abs=0.006)


def test_weighted_node_takes_a_proportional_share():
    r = HashRing(vnodes=256)
    r.add_nodes([("a", 1), ("b", 1), ("c", 3)])
    r.check_invariants()
    own = r.ownership()
    assert own["c"] == pytest.approx(0.6, abs=0.08)  # 3 of 5 weight units; unweighted it would be 0.33
    assert own["a"] == pytest.approx(0.2, abs=0.06)


# --- replication preference lists ----------------------------------------------------------------------


def test_replica_lists_are_distinct_sized_correctly_and_start_at_the_owner():
    r = ring_of(NODES, vnodes=64)
    for k in KEYS[:2000]:
        reps = r.get_nodes(k, 3)
        assert len(reps) == 3 and len(set(reps)) == 3
        assert reps[0] == r.get_node(k)
    assert sorted(r.get_nodes("x", 50)) == sorted(NODES)


def test_removing_a_node_only_deletes_it_from_replica_lists_and_appends_one():
    r = ring_of(NODES, vnodes=64)
    old = {k: r.get_nodes(k, 3) for k in KEYS[:5000]}
    r.remove_node("node-6")
    for k, before in old.items():
        after = r.get_nodes(k, 3)
        if "node-6" in before:
            assert after[:2] == [n for n in before if n != "node-6"]  # order of the survivors is preserved
        else:
            assert after == before  # untouched lists stay untouched


# --- concurrency: lock-free readers over immutable snapshots ---------------------------------------------


@pytest.fixture
def frequent_thread_switches():
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-5)
    yield
    sys.setswitchinterval(old)


def test_readers_see_consistent_snapshots_while_membership_changes(frequent_thread_switches):
    ring = ring_of(["base"], vnodes=32)
    universe = {"base"} | {f"tmp-{i}" for i in range(15)}
    stop, errors = threading.Event(), []

    def reader():
        probe = KEYS[:40]
        while not stop.is_set():
            for k in probe:
                try:
                    assert ring.get_node(k) in universe
                    reps = ring.get_nodes(k, 3)
                    assert reps and len(set(reps)) == len(reps) and set(reps) <= universe
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for _ in range(3):
        for i in range(15):
            ring.add_node(f"tmp-{i}")
        for i in range(15):
            ring.remove_node(f"tmp-{i}")
    stop.set()
    for t in readers:
        t.join()
    assert errors == []
    ring.check_invariants()
    assert ring.nodes == ["base"]


def test_lookups_read_only_the_snapshot_not_writer_side_state():
    """get_nodes once derived its result size from the writer-side dict, which can be
    momentarily ahead of/behind the snapshot. Emptying that dict must not change lookups."""
    r = ring_of(NODES, vnodes=32)
    expected = (r.get_node("k"), r.get_nodes("k", 4))
    r._weights.clear()
    assert (r.get_node("k"), r.get_nodes("k", 4)) == expected


def test_stable_hash_is_deterministic_and_well_spread():
    assert stable_hash64(b"x") == stable_hash64(b"x")
    top_bits = {stable_hash64(f"k{i}".encode()) >> 60 for i in range(2000)}
    assert len(top_bits) == 16  # all 16 top-nibble buckets hit: no gross clustering
