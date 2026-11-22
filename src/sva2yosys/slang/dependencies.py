"""Which parameters can influence the monitor of one concurrent assertion.

A static, conservative dependency analysis over the elaborated design. The monitor generated for
an assertion is a function of its lowered property, clock, ``disable iff`` condition and enabling
condition, and of the widths and signedness of the signals it reads. The set returned by
:func:`assertion_dependencies` contains every parameter, type parameter and genvar the assertion
can depend on; a parameter outside it provably cannot change the monitor. Whatever the analysis
cannot classify makes the result ``None`` (unknown: every visible parameter counts), never a
smaller set. Contract: ``tests/functional_tests/slang_frontend/CONTRACT.md`` (point 28).

Three sources feed the set, because none alone is complete:

1. the bound expression tree of the assertion (and of the procedural block around it, and of the
   bodies of the user functions it calls), which expands named properties and sequences;
2. a scan of **every identifier of the assertion's syntax**, each resolved by scope lookup at the
   position of the assertion, because constants Slang folds (``##N``, ``[*K]``, ``(W)'(x)``,
   ``$bits(T)``, a function called with a constant) vanish from the bound tree;
3. the closure over each referenced symbol: the syntax of its declared type (typedef and enum
   chains, packed and unpacked dimensions, non-ANSI port redeclarations), the syntax and body of a
   module-local function, property, sequence or ``let``, the default clocking and the default
   ``disable iff`` that apply.

The closure stops at a parameter (the rewrite compares its value and implicit type, so what it was
derived from does not matter) but follows the syntax of its explicit type, and stops at a type
parameter (recorded by name). A parameter whose name does not resolve to itself at the assertion
(hidden by an inner declaration) is reported apart (:attr:`Dependencies.hidden`) and the directive
is rejected, since the visible parameters cannot key the monitor on it; a compilation-unit parameter
hidden that way, which no instantiation can override, makes the result unknown.

Soundness holes and their guards are listed next to the code that handles them; the common
principle is that anything not understood raises :class:`_UnknownError`, which turns the result into
``None``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import pyslang
from pyslang import ast, syntax


def _maybe[T](value: T) -> T | None:
    """``value`` typed as possibly absent: Slang returns null for a missing syntax, scope or
    symbol although its stubs promise a value."""
    return value


class _Scoped(Protocol):
    """What the identifier scan needs of a scope (an instance body, generate block, function,
    property, ... all have it, though pyslang does not derive them from ``ast.Scope``)."""

    def lookupName(  # noqa: N802
        self,
        name: str,
        location: ast.LookupLocation = ...,
        flags: ast.LookupFlags = ...,
    ) -> ast.Symbol: ...


type EnclosingScope = (
    ast.InstanceBodySymbol
    | ast.GenerateBlockSymbol
    | ast.GenerateBlockArraySymbol
    | ast.CheckerInstanceBodySymbol
    | ast.InstanceArraySymbol
)
"""The scope symbols the exporter walks through on its way to an assertion."""


class _UnknownError(Exception):
    """Raised when a reference cannot be classified: the result is then ``None``."""


# The syntax kinds that open a scope; the default clocking and default disable declarations are
# attributed to the nearest one.
_SCOPE_SYNTAX = frozenset(
    {
        "ModuleDeclaration",
        "InterfaceDeclaration",
        "ProgramDeclaration",
        "CheckerDeclaration",
        "PackageDeclaration",
        "ClassDeclaration",
        "GenerateBlock",
        "FunctionDeclaration",
    }
)

# A syntax parent that groups the declarations of several symbols (a module header) and must not
# be scanned as part of one declared symbol.
_CONTAINER_SYNTAX = ("Module", "Generate", "Compilation", "Interface", "Program", "Package")
_CONTAINER_SYNTAX_EXACT = ("Class", "Checker")


type _Span = tuple[int, int, int]
"""A source range as ``(buffer, start offset, end offset)``."""


def _span(source: pyslang.SourceRange) -> _Span:
    return (source.start.buffer.id, source.start.offset, source.end.offset)


def _off_path(block: syntax.SyntaxNode, statement: syntax.SyntaxNode) -> Callable[[_Span], bool]:
    """A test for source ranges inside a statement of ``block`` that is not on the path from
    ``block`` to ``statement``.

    Guard (an enclosing ``if``/``case``/``begin``): what a procedural assertion depends on in its
    block is the block's event control and the conditions on the path to it (the selectors, case
    items, predicates, and the declarations they read); the other statements of the block are
    not part of the monitor. A statement that cannot be located stays on the path (over-
    approximation), so the test only ever removes provably unrelated statements.
    """
    root = _span(block.sourceRange)
    on_path: set[tuple[_Span, str]] = set()
    node: syntax.SyntaxNode | None = statement
    while node is not None:
        on_path.add((_span(node.sourceRange), node.kind.name))
        if _span(node.sourceRange) == root:
            break
        node = _maybe(node.parent)
    off: list[_Span] = []

    def visit(n: object) -> None:
        if (
            isinstance(n, syntax.SyntaxNode)
            and n.kind.name.endswith("Statement")
            and (_span(n.sourceRange), n.kind.name) not in on_path
        ):
            off.append(_span(n.sourceRange))

    block.visit(visit)

    def inside(span: _Span) -> bool:
        return any(o[0] == span[0] and o[1] <= span[1] and span[2] <= o[2] for o in off)

    return inside


@dataclass
class _Walk:
    """The state of one analysis: the names found, the symbols already processed."""

    site: ast.Symbol
    deps: set[str] = field(default_factory=set)
    seen: set[tuple[str, str, int, int]] = field(default_factory=set)
    tokens: set[str] = field(default_factory=set)  # every identifier spelling scanned
    hidden: set[str] = field(default_factory=set)  # dependencies hidden at the assertion

    # --- sources 1 and 2 ---------------------------------------------------------------

    def bound(
        self, node: ast.Statement | ast.Symbol, skip: Callable[[_Span], bool] | None = None
    ) -> None:
        """Source 1: every symbol referenced in the bound tree below ``node``, except in the
        source ranges for which ``skip`` is true."""

        def visit(n: object) -> None:
            if skip is not None and isinstance(n, ast.Expression) and skip(_span(n.sourceRange)):
                return
            if isinstance(n, ast.HierarchicalValueExpression):
                # Guard: a hierarchical or interface reference reaches another instance, whose
                # parameters no lexical name keys.
                raise _UnknownError(f"hierarchical reference {n.symbol.name}")
            if isinstance(n, ast.NamedValueExpression):
                self.symbol(n.symbol)
            elif isinstance(n, ast.CallExpression):
                # Guard: a system function adds only its arguments (visited as children); a
                # user function brings its declaration; any other callee is not understood.
                if not n.isSystemCall:
                    if isinstance(n.subroutine, ast.SubroutineSymbol):
                        self.symbol(n.subroutine)
                    else:
                        raise _UnknownError("call of an unknown subroutine")
            elif isinstance(n, ast.AssertionInstanceExpression):
                self.symbol(n.symbol)

        node.visit(visit)

    def scan(
        self,
        node: syntax.SyntaxNode | None,
        scope: _Scoped,
        location: ast.LookupLocation | None = None,
        skip: Callable[[_Span], bool] | None = None,
    ) -> None:
        """Source 2: resolve every identifier of the syntax ``node`` in ``scope``.

        Guard (shadowing): the lookup is made at ``location`` (just before the assertion or
        declaration), so a declaration that follows it cannot capture a name, and one that
        precedes it hides the outer one exactly as in elaboration. Names that do not resolve are
        struct members, labels, formal names, local variables of a sequence and the like; a
        parameter visible at the position normally resolves, so ignoring the rest cannot lose one;
        but a lookup by spelling can fail for a name that is not a plain identifier (an escaped
        name such as P-x), so every scanned spelling is also kept in ``tokens`` and matched by name
        against the visible parameters at the end (``assertion_dependencies``). A name that is only
        a member name or a local variable of the same spelling can only add a name: the result
        is a superset, which is sound.
        """
        if node is None:
            return
        where = ast.LookupLocation.max if location is None else location
        names: list[str] = []

        def visit(x: object) -> None:
            if skip is not None and isinstance(x, syntax.SyntaxNode) and skip(_span(x.sourceRange)):
                return
            if isinstance(x, (syntax.IdentifierNameSyntax, syntax.IdentifierSelectNameSyntax)):
                names.append(x.identifier.valueText)
            elif isinstance(x, syntax.ClassNameSyntax):
                # Guard: a class type brings the class's own parameters.
                raise _UnknownError(f"class type {x.identifier.valueText}")

        node.visit(visit)
        self.tokens.update(names)
        for name in names:
            found = _maybe(scope.lookupName(name, where))
            if found is not None and found.name != name:
                # Guard: Slang re-lexes the string, so an escaped name (``\P-x``) can resolve
                # to a different symbol (``P``); a lookup that does not return the name asked
                # for is not trusted.
                raise _UnknownError(f"{name} resolved to {found.name}")
            self.symbol(found)

    # --- source 3 ----------------------------------------------------------------------

    def symbol(self, sym: ast.Symbol | None) -> None:
        """Add ``sym`` and what it brings in (the closure)."""
        if sym is None:
            return
        key = (sym.kind.name, sym.name, sym.location.buffer.id, sym.location.offset)
        if key in self.seen:
            return
        self.seen.add(key)
        kind = sym.kind
        if isinstance(sym, ast.TransparentMemberSymbol):
            self.symbol(sym.wrapped)
            return
        if kind in (ast.SymbolKind.Package, ast.SymbolKind.Unknown, ast.SymbolKind.Genvar):
            # A genvar declaration carries no value: the iteration's value is the implicit
            # parameter of the generate block, found as a Parameter.
            return
        parent = _maybe(sym.parentScope)
        if parent is None:
            raise _UnknownError(f"{sym.name} has no scope")
        is_global = _maybe(parent.containingInstance) is None
        if kind in (ast.SymbolKind.Parameter, ast.SymbolKind.TypeParameter):
            if is_global and _inside(sym, "PackageDeclaration"):
                return  # a constant of the design: cannot vary between elaborations
            if not is_global and _instance_differs(parent, self.site):
                raise _UnknownError(f"{sym.name} belongs to another instance")
            # Guard (derived constants): stop at the parameter, the rewrite compares its value
            # and implicit type, so what it was derived from is not recorded. A
            # compilation-unit parameter is recorded too: it is not among the visible names,
            # which makes the result unknown.
            # Guard (shadowing): the dependency is the NAME, evaluated at the assertion; if that
            # name resolves there to another declaration (an inner block redeclares ``D``), the
            # name would select the wrong parameter and the one really used is not among the
            # visible ones, so no comparison can see it: it is reported as hidden (the directive
            # is rejected). A compilation-unit parameter cannot be overridden: unknown.
            if not self._visible_as_itself(sym):
                if is_global:
                    raise _UnknownError(f"{sym.name} is shadowed at the assertion")
                self.hidden.add(sym.name)
                return
            self.deps.add(sym.name)
            if isinstance(sym, ast.ParameterSymbol):
                # Guard (explicit type): the value and implicit type are compared, but an
                # explicit type (``logic [W-1:0] LP``) can depend on other parameters that no
                # comparison of LP sees (``$bits(LP)``): its syntax is followed.
                self.scan(
                    _maybe(sym.declaredType.typeSyntax), parent, ast.LookupLocation.before(sym)
                )
            return
        if is_global:
            # Guard: a package or $unit function, property, typedef or variable cannot depend on
            # a module parameter; the arguments of a call are visited as children of the call.
            return
        if _instance_differs(parent, self.site):
            raise _UnknownError(f"{sym.name} belongs to another instance")
        self.declared(sym, kind, parent)

    def _visible_as_itself(self, sym: ast.Symbol) -> bool:
        """Whether the name of the parameter ``sym`` resolves to ``sym`` itself at the assertion
        (a name that does not resolve at all is left to the visible-names check of the caller)."""
        scope = _maybe(self.site.parentScope)
        if scope is None:
            return True
        found = _maybe(scope.lookupName(sym.name, ast.LookupLocation.before(self.site)))
        if found is None:
            return True
        if isinstance(found, ast.TransparentMemberSymbol):
            found = found.wrapped
        same = (
            found.kind == sym.kind
            and found.location.buffer.id == sym.location.buffer.id
            and found.location.offset == sym.location.offset
        )
        return same

    def declared(self, sym: ast.Symbol, kind: ast.SymbolKind, scope: ast.Scope) -> None:
        """The closure over a symbol that lives in the instance (not a parameter)."""
        before = ast.LookupLocation.before(sym)
        if kind in _VALUES:
            if kind == ast.SymbolKind.ClassProperty:
                raise _UnknownError("class property")
            self.scan(_maybe(sym.declaredType.typeSyntax), scope, before)
            node = _maybe(sym.syntax)
            if node is not None:
                # Guard (a port declared in two places; unpacked dimensions): the declaration's
                # own syntax and the declaration that groups it (``input [W-1:0] v``, ``logic
                # [W-1:0] a, b [N]``) are both scanned.
                self.scan(node, scope, before)
                grouping = _maybe(node.parent)
                if grouping is not None and not _is_container(grouping.kind.name):
                    self.scan(grouping, scope, before)
            if isinstance(sym, ast.PortSymbol):
                # Guard (non-ANSI ``input v; logic [W-1:0] v;``): the port is typed through its
                # internal symbol, the declaration of the same name in the scope.
                self.symbol(_maybe(sym.internalSymbol))
        elif isinstance(sym, ast.TypeAliasType):
            node = _maybe(sym.syntax)
            if node is not None and node.kind.name == "TypeAssignment":
                # a ``parameter type T``: Slang represents it as an alias of the assignment
                if self._visible_as_itself(sym):
                    self.deps.add(sym.name)
                else:
                    self.hidden.add(sym.name)  # as for a parameter (shadowing)
            else:
                self.scan(node, scope, before)
        elif isinstance(sym, ast.EnumValueSymbol):
            self.scan(_maybe(sym.type.syntax), scope, before)
            self.scan(_maybe(sym.syntax), scope, before)
        elif isinstance(sym, ast.SubroutineSymbol):
            if _inside(sym, "ClassDeclaration"):
                raise _UnknownError("class method")
            # Guard (function called with a folded argument): the body is scanned by syntax
            # (a folded call has no bound body) and, when present, by its bound tree.
            self.scan(_maybe(sym.syntax), sym)
            body = _maybe(sym.body)
            if body is not None:
                self.bound(body)
        elif isinstance(sym, (ast.PropertySymbol, ast.SequenceSymbol, ast.LetDeclSymbol)):
            self.scan(_maybe(sym.syntax), sym)
        elif isinstance(sym, ast.ClockingBlockSymbol):
            self.scan(_maybe(sym.syntax), scope, before)
        else:
            # Guard: an instance, interface port, modport, checker instance, array of instances
            # and any kind a later Slang adds are not understood.
            raise _UnknownError(f"unhandled symbol {kind.name} {sym.name}")


_VALUES = frozenset(
    {
        ast.SymbolKind.Variable,
        ast.SymbolKind.Net,
        ast.SymbolKind.FormalArgument,
        ast.SymbolKind.Port,
        ast.SymbolKind.LocalAssertionVar,
        ast.SymbolKind.AssertionPort,
        ast.SymbolKind.Field,
        ast.SymbolKind.ClassProperty,
    }
)


def _is_container(syntax_kind: str) -> bool:
    """Whether a syntax parent groups many declarations (a module header, a class): it must not
    be scanned as part of one declared symbol."""
    return syntax_kind in _CONTAINER_SYNTAX_EXACT or any(
        syntax_kind.startswith(prefix) and "Declaration" in syntax_kind
        for prefix in _CONTAINER_SYNTAX
    )


def _inside(sym: ast.Symbol, declaration: str) -> bool:
    """Whether ``sym`` is declared inside a ``declaration`` syntax (a package, a class): Slang
    does not say what symbol a scope is, the syntax does."""
    node = _maybe(sym.syntax)
    while node is not None:
        if node.kind.name == declaration:
            return True
        node = _maybe(node.parent)
    return False


def _instance_differs(scope: ast.Scope, site: ast.Symbol) -> bool:
    """Whether ``scope`` belongs to an instance other than the one that contains ``site``."""
    mine = _maybe(site.parentScope.containingInstance)
    theirs = _maybe(scope.containingInstance)
    if theirs is None or mine is None:
        return False
    return theirs.hierarchicalPath != mine.hierarchicalPath


def _scopes(
    enclosing: Sequence[EnclosingScope],
) -> list[ast.InstanceBodySymbol | ast.GenerateBlockSymbol]:
    """The scopes of ``enclosing`` that can declare a default clocking or default disable.

    Raises:
        _UnknownError: The assertion is inside a checker, whose actual arguments come from the
            instantiation site.
    """
    scopes: list[ast.InstanceBodySymbol | ast.GenerateBlockSymbol] = []
    for scope in enclosing:
        if isinstance(scope, ast.CheckerInstanceBodySymbol):
            raise _UnknownError("assertion inside a checker")
        if isinstance(scope, (ast.InstanceBodySymbol, ast.GenerateBlockSymbol)):
            scopes.append(scope)
    return scopes


DeclarationCache = dict[tuple[int, int, str], list[syntax.SyntaxNode]]
"""The default clocking and default disable declarations of each scope of one export, found once:
a module with thousands of assertions must not be walked again for each of them."""

_DEFAULTS = frozenset({"ClockingDeclaration", "DefaultDisableDeclaration"})


def _own_declarations(
    owner: ast.InstanceBodySymbol | ast.GenerateBlockSymbol,
    wanted: frozenset[str],
    cache: DeclarationCache | None = None,
) -> list[syntax.SyntaxNode]:
    """The syntax nodes of kind ``wanted`` (a subset of the two default declarations) declared
    directly in ``owner``'s scope (not in a nested module, generate block, function or class).
    The scope is walked once, for both kinds, and remembered in ``cache`` when one is given."""
    key = (owner.location.buffer.id, owner.location.offset, owner.kind.name)
    if cache is not None and key in cache:
        everything = cache[key]
    else:
        everything = _walk_declarations(owner, _DEFAULTS)
        if cache is not None:
            cache[key] = everything
    return [node for node in everything if node.kind.name in wanted]


def _walk_declarations(
    owner: ast.InstanceBodySymbol | ast.GenerateBlockSymbol, wanted: frozenset[str]
) -> list[syntax.SyntaxNode]:
    """One walk of ``owner``'s syntax for the nodes of kind ``wanted`` declared directly in it."""
    root = _maybe(owner.syntax)
    found: list[syntax.SyntaxNode] = []
    if root is None:
        return found

    def visit(n: object) -> None:
        if not isinstance(n, syntax.SyntaxNode) or n.kind.name not in wanted:
            return
        parent = _maybe(n.parent)
        while parent is not None and parent.kind.name not in _SCOPE_SYNTAX:
            parent = _maybe(parent.parent)
        if parent is not None and parent.sourceRange.start == root.sourceRange.start:
            found.append(n)

    root.visit(visit)
    return found


def _has_explicit_disable(statement: ast.ConcurrentAssertionStatement) -> bool:
    """Whether the property spec of the statement itself has a ``disable iff``."""
    found = False

    def visit(n: object) -> None:
        nonlocal found
        if isinstance(n, syntax.SyntaxNode) and n.kind.name == "DisableIff":
            found = True

    statement.syntax.visit(visit)
    return found


@dataclass(frozen=True)
class Dependencies:
    """The result of :func:`assertion_dependencies`."""

    names: frozenset[str] | None
    """The dependency names (``None``: unknown, every visible parameter counts)."""
    hidden: frozenset[str]
    """Parameters and type parameters the assertion depends on that its position hides behind
    another declaration of the same name. They are not among the visible names, so no comparison
    of the visible parameters can key the monitor on them; the directive cannot be lowered."""


def assertion_dependencies(
    statement: ast.ConcurrentAssertionStatement,
    block: ast.Symbol,
    visible: frozenset[str],
    enclosing: Sequence[EnclosingScope],
    cache: DeclarationCache | None = None,
) -> Dependencies:
    """The names of the parameters, type parameters and genvars (among ``visible``, the names of
    ``sva2yosys_parameters``) that the monitor of ``statement`` can depend on; ``None`` when the
    analysis cannot classify some reference; and the hidden dependencies (see
    :class:`Dependencies`).

    Args:
        statement: The concurrent assertion.
        block: The symbol that contains it (an implicit or user procedural block), used to
            resolve names.
        visible: The parameter names visible at the assertion.
        enclosing: The scope symbols around ``block``, innermost first, up to and including its
            instance (or checker) body; the default clocking and default disable are looked up
            in them (Slang does not reach the scope symbol from a scope).
        cache: Memory of the scope walks, shared by the assertions of one export.
    """
    walk = _Walk(block)
    try:
        scope = block.parentScope
        scopes = _scopes(enclosing)
        before = ast.LookupLocation.before(block)
        # the assertion, then the procedural block around it (its event control and the
        # conditions on the path to the assertion: Guard, an enclosing if/case)
        walk.scan(statement.syntax, scope, before)
        walk.bound(statement)
        if block.syntax.kind != syntax.SyntaxKind.ConcurrentAssertionMember:
            unrelated = _off_path(block.syntax, statement.syntax)
            walk.scan(block.syntax, scope, before, unrelated)
            walk.bound(block, unrelated)
        # Guard: the default clocking applies to an assertion without a clock, the default
        # disable iff to one without its own; they are scanned in every enclosing scope (a
        # superset when an enclosing scope's declaration does not apply).
        wanted = {"ClockingDeclaration"}
        if not _has_explicit_disable(statement):
            wanted.add("DefaultDisableDeclaration")
        for owner in scopes:
            for declaration in _own_declarations(owner, frozenset(wanted), cache):
                walk.scan(declaration, owner)
        # Guard (names the lookup cannot resolve, such as an escaped parameter name): every
        # identifier spelling found in the scanned syntax that is a visible parameter is a
        # dependency, resolved or not.
        walk.deps |= walk.tokens & visible
        # Guard: a name the exporter does not list as visible (a compilation-unit parameter)
        # is not keyed by the rewrite.
        if not walk.deps <= visible:
            return Dependencies(None, frozenset(walk.hidden))
        return Dependencies(frozenset(walk.deps), frozenset(walk.hidden))
    except _UnknownError:
        return Dependencies(None, frozenset(walk.hidden))


__all__ = ["Dependencies", "EnclosingScope", "assertion_dependencies", "pyslang"]
