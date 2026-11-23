"""Inference rules learned from the facts (path rule mining): a pattern of relations that the knowledge graph
keeps closing becomes a rule. Strength is how often it closes, confidence n/(n+1) over the cases seen — the same
evidence formula as each fact's. Nothing is listed: the rules change as papers are added.

Counting, not listing: two-step paths are counted per middle entity and closed paths found from each fact's two
ends through an index, so the cost grows with the number of facts, not with paths through hub entities."""
import math
from collections import Counter, defaultdict


def lower_bound(k: int, n: int, z: float = 1.96) -> float:
    """Wilson 95% lower bound of k/n: what the rate is at least, given how many cases back it."""
    if not n:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def mine_rules(facts: list[tuple[str, str, str]], min_support: int = 2, min_coverage: float = 0.01,
               min_lift: float = 2.0, min_strength: float = 0.1) -> list[dict]:
    """Rules from (predicate, subject, object) facts: P(a,b) → R(a,b), P(a,b) → R(b,a), and
    P(a,b) ∧ Q(b,c) → R(a,c). A rule is kept when its conclusion holds in at least min_support cases, those explain
    at least min_coverage of R's facts (head coverage, as in AMIE), the 95% lower bound of its rate is at least
    min_strength, and that bound is min_lift times above how often R holds between connected entities anyway —
    coincidences and rates resting on few cases fail one of these."""
    pairs: dict[str, set] = defaultdict(set)
    out_edges: dict[str, list] = defaultdict(list)           # a → [(P, b)]
    in_edges: dict[str, list] = defaultdict(list)            # c → [(Q, b)]
    between: dict[tuple, set] = defaultdict(set)              # (b, c) → relations from b to c
    p_in, q_out = defaultdict(Counter), defaultdict(Counter)   # b → facts of P ending at b / of Q starting at b
    for p, s, o in facts:
        if s == o or (s, o) in pairs[p]:
            continue
        pairs[p].add((s, o))
        out_edges[s].append((p, o))
        in_edges[o].append((p, s))
        between[(s, o)].add(p)
        p_in[o][p] += 1
        q_out[s][p] += 1

    def rule(name, text, closed, n, meaning):
        return {'name': name, 'text': text, 'stv': f"(STV {closed / n:.4f} {n / (n + 1):.4f})",
                'meaning': f"{meaning} — learned: holds in {closed} of {n} cases"}

    connected = len(set().union(*pairs.values())) or 1
    base = {R: len(rs) / connected for R, rs in pairs.items()}       # how often R holds between connected entities
    keep = lambda k, n, R: (k >= min_support and k >= min_coverage * len(pairs[R])
                            and n and lower_bound(k, n) >= max(min_strength, min_lift * base[R]))
    rules = []
    for P, ps in pairs.items():                                   # one premise, same pair or reversed
        same, back = Counter(), Counter()
        for s, o in ps:
            for R in between.get((s, o), ()):
                if R != P:
                    same[R] += 1
            for R in between.get((o, s), ()):
                back[R] += 1
        for R, k in same.items():
            if keep(k, len(ps), R):
                rules.append(rule(f"mined_{P}_{R}", f"(Implication (And ({P} $a $b)) ({R} $a $b))", k, len(ps),
                                  f"{P}(a, b) → {R}(a, b)"))
        for R, k in back.items():
            if keep(k, len(ps), R):
                rules.append(rule(f"mined_{P}_{R}_reversed", f"(Implication (And ({P} $a $b)) ({R} $b $a))", k,
                                  len(ps), f"{P}(a, b) → {R}(b, a)"))

    paths = Counter()                                             # P(a,b) ∧ Q(b,c) paths, counted per middle b
    for b in p_in.keys() & q_out.keys():
        for P, i in p_in[b].items():
            for Q, j in q_out[b].items():
                paths[(P, Q)] += i * j
    closed = Counter()                                            # those paths whose ends are an R fact: once per
    for (a, c), heads in list(between.items()):                  # pair, walked from the end with fewer edges
        found = Counter()
        outs, ins = out_edges.get(a, ()), in_edges.get(c, ())
        if len(outs) <= len(ins):
            for P, b in outs:
                if b != c:
                    for Q in between.get((b, c), ()):
                        found[(P, Q)] += 1
        else:
            for Q, b in ins:
                if b != a:
                    for P in between.get((a, b), ()):
                        found[(P, Q)] += 1
        for (P, Q), k in found.items():
            for R in heads:
                closed[(P, Q, R)] += k
    for (P, Q, R), k in closed.items():
        if keep(k, paths[(P, Q)], R):
            rules.append(rule(f"mined_{P}_{Q}_{R}", f"(Implication (And ({P} $a $b) ({Q} $b $c)) ({R} $a $c))",
                              k, paths[(P, Q)], f"{P}(a, b) ∧ {Q}(b, c) → {R}(a, c)"))
    return rules
