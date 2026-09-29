"""Trie (prefix tree), built from scratch.

A trie answers "does this exact word exist?" and "what words start with
this prefix?" in time proportional to the QUERY's length, not the number
of stored words -- the opposite trade-off from scanning a word list, where
cost grows with how many words there are. Compared throughout against a
plain Python list (the obviously-correct, obviously-slower reference) so
the shape of that trade-off is measured, not assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional


@dataclass
class _Node:
    children: Dict[str, "_Node"] = field(default_factory=dict)
    is_word: bool = False
    # Count of words in this node's subtree (itself, if is_word, plus all
    # descendants). Kept incrementally so autocomplete's word-count and
    # "how many words share this prefix" queries don't need a subtree walk.
    word_count: int = 0


class Trie:
    def __init__(self, words: Iterable[str] = ()):
        self._root = _Node()
        self._size = 0  # distinct words currently stored
        for w in words:
            self.insert(w)

    def __len__(self) -> int:
        return self._size

    def insert(self, word: str) -> bool:
        """Returns False if `word` was already present (no-op), True if newly added."""
        if not word:
            raise ValueError("cannot insert the empty string")
        node = self._root
        path = [node]
        for ch in word:
            if ch not in node.children:
                node.children[ch] = _Node()
            node = node.children[ch]
            path.append(node)
        if node.is_word:
            return False
        node.is_word = True
        self._size += 1
        for n in path:
            n.word_count += 1
        return True

    def _find_node(self, prefix: str) -> Optional[_Node]:
        node = self._root
        for ch in prefix:
            node = node.children.get(ch)
            if node is None:
                return None
        return node

    def __contains__(self, word: str) -> bool:
        node = self._find_node(word)
        return node is not None and node.is_word

    def starts_with(self, prefix: str) -> bool:
        """True iff at least one stored word has this prefix (prefix itself
        may or may not be a stored word)."""
        return self._find_node(prefix) is not None

    def count_with_prefix(self, prefix: str) -> int:
        node = self._find_node(prefix)
        return node.word_count if node else 0

    def words_with_prefix(self, prefix: str, limit: Optional[int] = None) -> List[str]:
        """All stored words starting with `prefix`, in sorted order (DFS
        visits children in a fixed, sorted order, so no separate sort is
        needed). `limit` stops the walk early rather than truncating a
        fully-built list, so it doesn't pay for words it never returns."""
        node = self._find_node(prefix)
        if node is None:
            return []
        out: List[str] = []

        if limit is not None and limit <= 0:
            return []

        def walk(n: _Node, path: str) -> bool:
            """Returns True if the caller should keep walking."""
            if n.is_word:
                out.append(path)
                if limit is not None and len(out) >= limit:
                    return False
            for ch in sorted(n.children):
                if not walk(n.children[ch], path + ch):
                    return False
            return True

        walk(node, prefix)
        return out

    def delete(self, word: str) -> bool:
        """Returns False if `word` was not present (no-op), True if removed.
        Prunes now-empty nodes along the path -- deleting the only word
        under a long unshared branch removes the whole branch, not just its
        is_word flag."""
        node = self._find_node(word)
        if node is None or not node.is_word:
            return False
        node.is_word = False
        self._size -= 1
        # Second pass, root to leaf, decrementing counts and pruning any
        # node that is now both not-a-word and childless.
        n = self._root
        n.word_count -= 1
        stack = [n]
        for ch in word:
            n = n.children[ch]
            n.word_count -= 1
            stack.append(n)
        # Leaf-to-root: a node can only be pruned once whatever's below it in
        # `word`'s path is already gone, so this must run in reverse of the
        # root-to-leaf order `stack` was built in.
        for parent, ch in reversed(list(zip(stack, word))):
            child = parent.children[ch]
            if not child.is_word and not child.children:
                del parent.children[ch]
        return True


class NaiveWordSet:
    """Reference model: a plain list, linear scan. Trivially correct --
    the point of comparing against it is complexity, not cleverness."""

    def __init__(self, words: Iterable[str] = ()):
        self._words: List[str] = []
        seen = set()
        for w in words:
            if w not in seen:
                seen.add(w)
                self._words.append(w)

    def __len__(self) -> int:
        return len(self._words)

    def __contains__(self, word: str) -> bool:
        return word in self._words

    def starts_with(self, prefix: str) -> bool:
        return any(w.startswith(prefix) for w in self._words)

    def words_with_prefix(self, prefix: str, limit: Optional[int] = None) -> List[str]:
        matches = sorted(w for w in self._words if w.startswith(prefix))
        return matches[:limit] if limit is not None else matches


# --- Demo ----------------------------------------------------------------------------


def demo() -> None:
    import random
    import time

    words = [
        "cat", "car", "care", "cart", "card", "careful", "carbon", "dog", "do", "done",
        "door", "dorm", "apple", "app", "apply", "application", "banana", "band", "bandana",
    ]
    print("1) Basic behaviour on a small, hand-checkable word list")
    t = Trie(words)
    print(f"   len={len(t)} | 'car' in t: {'car' in t} | 'ca' in t: {'ca' in t} (prefix, not a word itself)")
    print(f"   starts_with('car'): {t.starts_with('car')} | starts_with('xyz'): {t.starts_with('xyz')}")
    print(f"   words_with_prefix('car'): {t.words_with_prefix('car')}")
    print(f"   count_with_prefix('do'): {t.count_with_prefix('do')} -> {t.words_with_prefix('do')}")
    removed = t.delete("car")
    print(f"   delete('car') -> {removed} | 'car' in t now: {'car' in t} | 'care' in t still: {'care' in t} "
          f"| words_with_prefix('car') now: {t.words_with_prefix('car')}")

    print("\n2) Differential test against a naive word list: 200 random insert/delete/query sequences")
    rng = random.Random(0)
    alphabet = "abc"
    mismatches = 0
    for _ in range(200):
        trie: Trie = Trie()
        naive: NaiveWordSet = NaiveWordSet()
        for _ in range(40):
            w = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 4)))
            op = rng.choice(["insert", "insert", "delete", "contains", "prefix"])
            if op == "insert":
                naive_had = w in naive._words
                t_inserted = trie.insert(w)
                if t_inserted == naive_had:  # insert() should return True iff it's genuinely new
                    mismatches += 1
                if not naive_had:
                    naive._words.append(w)
            elif op == "delete":
                t_removed = trie.delete(w)
                n_removed = w in naive._words
                if n_removed:
                    naive._words.remove(w)
                if t_removed != n_removed:
                    mismatches += 1
            elif op == "contains":
                if (w in trie) != (w in naive):
                    mismatches += 1
            else:
                p = w[: rng.randint(1, len(w))]
                if sorted(trie.words_with_prefix(p)) != sorted(naive.words_with_prefix(p)):
                    mismatches += 1
        if len(trie) != len(naive):
            mismatches += 1
    print(f"   mismatches across 200 randomized trials: {mismatches}")

    print("\n3) Why the shape matters: query cost vs number of stored words (trie should stay ~flat, list should grow)")
    print(f"   {'stored words':>13} {'trie: contains (s)':>19} {'list: contains (s)':>19} {'list/trie':>10}")
    rng = random.Random(1)
    all_words = [f"user{n}" for n in range(200_000)]  # fixed length -> isolates "how many words" from "how long is the word"
    queries = [f"user{rng.randint(0, 199_999)}" for _ in range(20_000)]
    for n in (5_000, 50_000, 200_000):
        subset = all_words[:n]
        trie = Trie(subset)
        naive_list = list(subset)  # a plain list, not even wrapped -- the most direct "no structure" baseline

        t0 = time.perf_counter()
        for q in queries:
            q in trie
        trie_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        for q in queries:
            q in naive_list
        list_time = time.perf_counter() - t0

        print(f"   {n:>13,} {trie_time:>19.4f} {list_time:>19.4f} {list_time / trie_time:>9.1f}x")
    print("   (word length is fixed across all three sizes, so the trie's near-constant time isolates the effect of")
    print("    'how many words are stored' from 'how long is the query' -- see (4) for the other axis)")

    print("\n4) The OTHER axis: trie cost scales with query LENGTH, not with how many words are stored")
    print(f"   {'query length':>13} {'trie: contains (us/call)':>25}")
    long_words = ["x" * 2000 + str(n) for n in range(2000)]  # 2000 distinct long words, shared long prefix
    trie = Trie(long_words)
    for length in (10, 100, 1000, 2000):
        probe = "x" * length
        t0 = time.perf_counter()
        for _ in range(20_000):
            trie.starts_with(probe)
        elapsed = (time.perf_counter() - t0) / 20_000 * 1e6
        print(f"   {length:>13,} {elapsed:>25.3f}")

    print("\n5) Autocomplete-style ranking: top-k shortest completions, using count_with_prefix to skip empty branches")
    dictionary = ["do", "dog", "dogma", "dogmatic", "dot", "dote", "doting", "download", "downtown", "downtown2"]
    ac: Trie = Trie(dictionary)
    prefix = "do"
    completions = sorted(ac.words_with_prefix(prefix), key=len)[:3]
    print(f"   top-3 shortest completions of '{prefix}': {completions} (out of {ac.count_with_prefix(prefix)} total)")


if __name__ == "__main__":
    demo()
