"""The free names of a function, flow-sensitively: a name is free when some
path through the body loads it before the function has bound it (its
arguments count as bound from the start). A name bound on one branch and
read after the branch is free: that read is an UnboundLocalError on the
other path. Comprehensions, lambdas and nested defs keep their own locals;
a nested def's free names are the enclosing function's (it may run at any
time after its definition). Used by the engine pins (tests/test_engine_run.py)
to check that a part reads nothing of its old enclosing frame (TJ-1988,
TJ-1994)."""
import ast


def free_names(func):
    """The names `func` (an ast.FunctionDef) reads from outside its body."""
    args = func.args
    bound = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    bound |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    return _Walk(bound).run(func.body).free


def _leaves(stmts):
    """True when the list cannot fall through to what follows it."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
        return True
    if isinstance(last, ast.If):
        return _leaves(last.body) and _leaves(last.orelse)
    return False


def _arg_names(a):
    out = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs}
    return out | {x.arg for x in (a.vararg, a.kwarg) if x is not None}


class _Walk:
    def __init__(self, assigned):
        self.assigned = set(assigned)
        self.free = set()

    def fork(self):
        w = _Walk(self.assigned)
        w.free = self.free          # one free set, shared by every path
        return w

    def run(self, stmts):
        for s in stmts:
            self.stmt(s)
        return self

    # --- expressions: loads at this scope; inner scopes get their own locals

    def expr(self, e):
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            local = set()
            for g in e.generators:
                self.scoped(g.iter, local)
                local |= {n.id for n in ast.walk(g.target) if isinstance(n, ast.Name)}
                for c in g.ifs:
                    self.scoped(c, local)
            parts = [e.key, e.value] if isinstance(e, ast.DictComp) else [e.elt]
            for part in parts:
                self.scoped(part, local)
            return
        if isinstance(e, ast.Lambda):
            for d in e.args.defaults + [x for x in e.args.kw_defaults if x]:
                self.expr(d)
            self.scoped(e.body, _arg_names(e.args))
            return
        if isinstance(e, ast.Name):
            if isinstance(e.ctx, ast.Load) and e.id not in self.assigned:
                self.free.add(e.id)
            return
        if isinstance(e, ast.NamedExpr):
            self.expr(e.value)
            self.assigned.add(e.target.id)
            return
        for child in ast.iter_child_nodes(e):
            self.expr(child)

    def scoped(self, e, local):
        saved = self.assigned
        self.assigned = saved | local
        self.expr(e)
        self.assigned = saved

    def target(self, t):
        for n in ast.walk(t):
            if isinstance(n, ast.Name):
                if isinstance(n.ctx, ast.Store):
                    self.assigned.add(n.id)
                elif n.id not in self.assigned:
                    self.free.add(n.id)

    # --- statements

    def stmt(self, s):
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in s.args.defaults + [x for x in s.args.kw_defaults if x] + s.decorator_list:
                self.expr(d)
            nonlocals = {n for x in ast.walk(s) if isinstance(x, ast.Nonlocal) for n in x.names}
            own = {n.id for n in ast.walk(s) if isinstance(n, ast.Name)
                   and isinstance(n.ctx, ast.Store)} - nonlocals
            inner = _Walk(self.assigned | _arg_names(s.args) | own | {s.name})
            inner.run(s.body)
            self.free |= inner.free | {n for n in nonlocals if n not in self.assigned}
            self.assigned.add(s.name)
            return
        if isinstance(s, ast.ClassDef):
            raise ValueError("a class statement inside the function")
        if isinstance(s, ast.If):
            self.expr(s.test)
            a = self.fork().run(s.body)
            b = self.fork().run(s.orelse)
            if _leaves(s.body) and _leaves(s.orelse):
                self.assigned = a.assigned | b.assigned
            elif _leaves(s.body):
                self.assigned = b.assigned
            elif _leaves(s.orelse):
                self.assigned = a.assigned
            else:
                self.assigned = a.assigned & b.assigned
            return
        if isinstance(s, (ast.For, ast.AsyncFor)):
            self.expr(s.iter)
            a = self.fork()
            a.target(s.target)
            a.run(s.body)
            b = self.fork().run(s.orelse)
            if s.orelse:
                self.assigned |= a.assigned & b.assigned
            return
        if isinstance(s, ast.While):
            self.expr(s.test)
            self.fork().run(s.body)
            self.fork().run(s.orelse)
            return
        if isinstance(s, ast.Try):
            a = self.fork().run(s.body)
            after = set(a.assigned)
            for h in s.handlers:
                hh = self.fork()
                if h.name:
                    hh.assigned.add(h.name)
                if h.type:
                    hh.expr(h.type)
                hh.run(h.body)
                if not _leaves(h.body):
                    after &= hh.assigned
            if s.orelse:
                o = a.fork().run(s.orelse)
                after |= o.assigned & a.assigned
            self.assigned = after
            self.run(s.finalbody)
            return
        if isinstance(s, (ast.With, ast.AsyncWith)):
            for item in s.items:
                self.expr(item.context_expr)
                if item.optional_vars is not None:
                    self.target(item.optional_vars)
            self.run(s.body)
            return
        if isinstance(s, ast.Assign):
            self.expr(s.value)
            for t in s.targets:
                self.target(t)
            return
        if isinstance(s, ast.AnnAssign):
            if s.value is not None:
                self.expr(s.value)
            self.expr(s.annotation)
            if s.value is not None:
                self.target(s.target)
            return
        if isinstance(s, ast.AugAssign):
            self.expr(s.value)
            if isinstance(s.target, ast.Name):
                if s.target.id not in self.assigned:
                    self.free.add(s.target.id)
                self.assigned.add(s.target.id)
            else:
                self.expr(s.target)
            return
        if isinstance(s, ast.Delete):
            for t in s.targets:
                if isinstance(t, ast.Name):
                    self.assigned.discard(t.id)
                else:
                    self.expr(t)
            return
        if isinstance(s, (ast.Import, ast.ImportFrom)):
            self.assigned |= {(a.asname or a.name).split(".")[0] for a in s.names}
            return
        if isinstance(s, (ast.Nonlocal, ast.Global)):
            self.free |= {n for n in s.names if n not in self.assigned}
            return
        for child in ast.iter_child_nodes(s):
            if isinstance(child, ast.expr):
                self.expr(child)
