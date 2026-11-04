"""Conservative Python AST impact analyzer with explicit coverage outcomes."""

import ast
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from app.control_plane.models import (
    CallSite,
    Confidence,
    ConfidenceBasis,
    Coverage,
    DependencyMatch,
    ImpactEvidence,
    NormalizedChange,
)
from app.repository_intelligence.inventory import MAX_ANALYSIS_FILE_BYTES
from app.repository_intelligence.models import InventoryResult, RepositoryRef, RepositoryWorkspace
from app.repository_intelligence.workspace import _walk_entries


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _normalize_package(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _ecosystem_matches(dependency_ecosystem: str, target_ecosystem: str | None) -> bool:
    if not target_ecosystem:
        return True
    aliases = {
        "pypi": {"pypi", "python", "pip"},
        "npm": {"npm", "javascript", "typescript", "node"},
    }
    return target_ecosystem.lower() in aliases.get(dependency_ecosystem, {dependency_ecosystem})


UNRESOLVED_RECEIVER_SCORE = 0.7


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return None


def _expand_alias(value: str, aliases: dict[str, str]) -> str:
    """Expand the longest known alias prefix (`self.client.send` resolves via `self.client`)."""
    parts = value.split(".")
    for length in range(len(parts), 0, -1):
        expanded = aliases.get(".".join(parts[:length]))
        if expanded:
            return ".".join([expanded, *parts[length:]])
    return value


def _normalized_module(value: str) -> str:
    return re.sub(r"[-_.]", "", value.lower())


def _module_plausibly_provides(symbol: str, package: str) -> bool:
    """Reject symbols that resolve through an import of an unrelated module.

    A bare symbol (no import root, for example via a star import) is kept. Distribution and
    import names often differ (`beautifulsoup4` / `bs4`), so this is a heuristic that only
    rejects when the import root shares no text with the declared package.
    """
    root = _normalized_module(symbol.split(".")[0])
    declared = _normalized_module(package)
    return not root or root in declared or declared in root


def _specifiers_disjoint(declared: str, affected_range: str) -> bool:
    """True when a declared requirement cannot select any version in the affected range.

    Each bound and a version just above it are probed, so a false True needs a range too narrow
    to contain any of them. Anything unparseable counts as overlapping, which keeps the match.
    """
    try:
        requirement = SpecifierSet(declared)
        affected = SpecifierSet(affected_range)
        probes = [
            version
            for specifier in [*requirement, *affected]
            for version in (Version(specifier.version), Version(f"{specifier.version}.1"))
        ]
    except (InvalidSpecifier, InvalidVersion):
        return False
    return not any(version in requirement and version in affected for version in probes)


def _package_change_applies(change: NormalizedChange, matches: list[DependencyMatch]) -> bool:
    """A declared dependency proves impact only when the package itself is what changed.

    For a symbol, endpoint, or field, the package only says where the target lives; repositories
    can declare it and never use the changed surface, so those matches stay as evidence.
    """
    declared = {_normalize_package(match.package) for match in matches}
    return any(
        target.kind == "sdk_package"
        and _normalize_package(target.package or target.name) in declared
        for target in change.targets
    )


def _endpoint_matches(value: str, endpoint: str) -> bool:
    if value == endpoint:
        return True
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and parsed.path == endpoint


@dataclass
class PythonFileIndex:
    aliases: dict[str, str] = field(default_factory=dict)
    import_roots: set[str] = field(default_factory=set)
    calls: list[tuple[ast.AST, str]] = field(default_factory=list)
    references: list[tuple[ast.AST, str]] = field(default_factory=list)
    string_arguments: list[tuple[ast.AST, str, str | None]] = field(default_factory=list)
    fields: list[tuple[ast.AST, str, str | None]] = field(default_factory=list)


class _PythonIndexer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.index = PythonFileIndex()
        self._imported: set[str] = set()
        self._local_definitions: set[str] = set()
        self._call_functions: set[int] = set()

    def visit_Module(self, node: ast.Module) -> None:
        # A locally defined base-less class owns its name; calls through it are not the SDK.
        self._local_definitions = {
            item.name
            for item in ast.walk(node)
            if isinstance(item, ast.ClassDef) and not item.bases
        }
        self.generic_visit(node)

    def _resolve(self, dotted: str | None) -> str | None:
        if not dotted:
            return None
        head = dotted.split(".")[0]
        if head in self._local_definitions and head not in self._imported:
            return None
        return _expand_alias(dotted, self.index.aliases)

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            name = item.asname or item.name.split(".")[0]
            self._imported.add(name)
            self.index.import_roots.add(item.name.split(".")[0])
            self.index.aliases[name] = item.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.index.import_roots.add(node.module.split(".")[0])
            for item in node.names:
                name = item.asname or item.name
                self._imported.add(name)
                self.index.aliases[name] = f"{node.module}.{item.name}"

    def _bind(self, target: ast.AST, value: str | None) -> None:
        name = _dotted_name(target) if isinstance(target, ast.Name | ast.Attribute) else None
        if name and value:
            self.index.aliases[name] = self._resolve(value) or value

    def visit_Assign(self, node: ast.Assign) -> None:
        value = _dotted_name(node.value)
        for target in node.targets:
            self._bind(target, value)
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            self.index.string_arguments.append((node.value, node.value.value, None))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        value = _dotted_name(node.value) if node.value else None
        self._bind(node.target, value or _dotted_name(node.annotation))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        for argument in arguments:
            if argument.annotation is not None:
                self._bind(ast.Name(id=argument.arg), _dotted_name(argument.annotation))
        returned = [
            _dotted_name(item.value)
            for item in ast.walk(node)
            if isinstance(item, ast.Return) and isinstance(item.value, ast.Call)
        ]
        factory = next((name for name in returned if name), None)
        if factory is None and node.returns is not None:
            factory = _dotted_name(node.returns)
        if factory:
            self._bind(ast.Name(id=node.name), factory)
        self.generic_visit(node)

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _visit_with(self, node: ast.With | ast.AsyncWith) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self._bind(item.optional_vars, _dotted_name(item.context_expr))
        self.generic_visit(node)

    visit_With = _visit_with
    visit_AsyncWith = _visit_with

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        dotted = _dotted_name(function)
        if (
            isinstance(function, ast.Call)
            and isinstance(function.func, ast.Name)
            and function.func.id == "getattr"
            and len(function.args) >= 2
            and isinstance(function.args[1], ast.Constant)
            and isinstance(function.args[1].value, str)
        ):
            receiver = _dotted_name(function.args[0])
            dotted = f"{receiver}.{function.args[1].value}" if receiver else None
        self._call_functions.add(id(function))
        expanded = self._resolve(dotted)
        if expanded:
            self.index.calls.append((node, expanded))
        for argument in [*node.args, *[keyword.value for keyword in node.keywords]]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                self.index.string_arguments.append((argument, argument.value, expanded))
        for keyword in node.keywords:
            if keyword.arg:
                self.index.fields.append((keyword, keyword.arg, expanded))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            if id(node) not in self._call_functions:
                expanded = self._resolve(_dotted_name(node))
                if expanded:
                    self.index.references.append((node, expanded))
            self.index.fields.append((node, node.attr, None))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            self.index.fields.append((key, key.value, None))
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.index.string_arguments.append((node, node.value, None))

    def visit_Dict(self, node: ast.Dict) -> None:
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.index.fields.append((key, key.value, None))
        self.generic_visit(node)


class PythonAstImpactAnalyzer:
    analyzer_id = "python-ast"
    analyzer_version = "1.1.0"

    def analyze(
        self,
        repository: RepositoryRef,
        workspace: RepositoryWorkspace,
        inventory: InventoryResult,
        change: NormalizedChange,
    ) -> ImpactEvidence:
        assessment_id = _stable_id(
            "impact", repository.id, change.id, workspace.content_digest, self.analyzer_version
        )
        dependency_matches = self._dependency_matches(inventory, change)
        call_sites: list[CallSite] = []
        parse_failures = 0
        python_files = 0
        excluded_files = inventory.files_excluded
        root = Path(workspace.root)

        for relative, entry_type, value in sorted(_walk_entries(root), key=lambda item: item[0]):
            if entry_type != "file" or value.suffix.lower() not in {".py", ".pyi"}:
                continue
            python_files += 1
            if value.stat(follow_symlinks=False).st_size > MAX_ANALYSIS_FILE_BYTES:
                excluded_files += 1
                continue
            try:
                tree = ast.parse(value.read_text(encoding="utf-8"), filename=relative)
            except (OSError, UnicodeDecodeError, SyntaxError):
                parse_failures += 1
                continue
            indexer = _PythonIndexer()
            indexer.visit(tree)
            call_sites.extend(self._match_file(relative, indexer.index, change, assessment_id))

        call_sites = sorted(
            {item.id: item for item in call_sites}.values(),
            key=lambda item: (item.path, item.start_line, item.end_line, item.id),
        )
        observed_languages = {item.language for item in inventory.languages}
        unsupported_languages = sorted(observed_languages - {"Python"})
        limitations = [
            "Dynamic dispatch, reflection, generated code, and computed strings are not resolved."
        ]
        if unsupported_languages:
            limitations.append(
                "No semantic analyzer is available for: " + ", ".join(unsupported_languages)
            )
        if parse_failures:
            limitations.append(f"{parse_failures} Python file(s) could not be parsed.")
        if excluded_files:
            limitations.append(f"{excluded_files} file(s) exceeded analysis limits.")
        if inventory.symlinks_excluded:
            limitations.append(f"{inventory.symlinks_excluded} symbolic link(s) were not analyzed.")
        if inventory.warnings:
            limitations.append(
                f"Repository inventory reported {len(inventory.warnings)} warning(s)."
            )
        coverage_excluded = excluded_files + inventory.symlinks_excluded
        full_coverage = bool(python_files) and not (
            unsupported_languages or parse_failures or coverage_excluded or inventory.warnings
        )
        coverage = Coverage(
            supported=full_coverage,
            languages=sorted(observed_languages),
            files_considered=python_files,
            files_excluded=coverage_excluded,
            parse_failures=parse_failures,
            limitations=limitations,
        )

        deterministic_findings = bool(call_sites) or _package_change_applies(
            change, dependency_matches
        )
        if deterministic_findings:
            conclusion = "affected"
            summary = "Deterministic repository evidence matches this provider change."
            score = 1.0 if full_coverage else 0.9
        elif not python_files:
            conclusion = "unsupported"
            summary = "No supported Python source files were available for analysis."
            score = 1.0
        elif not full_coverage:
            conclusion = "uncertain"
            summary = "No match was found, but analyzer coverage is incomplete."
            score = 0.5
        else:
            conclusion = "unaffected"
            summary = "No target match was found under complete supported Python coverage."
            score = 1.0
        unresolved = limitations[1:] if not full_coverage else []
        return ImpactEvidence(
            assessment_id=assessment_id,
            conclusion=conclusion,
            summary=summary,
            dependency_matches=dependency_matches,
            call_sites=call_sites,
            coverage=coverage,
            confidence=Confidence(
                score=score,
                basis=ConfidenceBasis.deterministic,
                reasons=["Python AST and manifest/lockfile evidence only."],
                unresolved=unresolved,
            ),
        )

    def _dependency_matches(
        self, inventory: InventoryResult, change: NormalizedChange
    ) -> list[DependencyMatch]:
        targets = [
            target for target in change.targets if target.kind == "sdk_package" or target.package
        ]
        matches = []
        for dependency in inventory.dependencies:
            package = _normalize_package(dependency.package)
            matching_target = next(
                (
                    target
                    for target in targets
                    if package == _normalize_package(target.package or target.name)
                    and _ecosystem_matches(dependency.ecosystem, target.ecosystem)
                ),
                None,
            )
            if not matching_target:
                continue
            version_evidence = self._version_evidence(dependency, matching_target)
            if version_evidence is None:
                continue
            method = dependency.detection_method
            if method not in {"manifest", "lockfile", "import"}:
                method = "other"
            matches.append(
                DependencyMatch(
                    ecosystem=dependency.ecosystem,
                    package=dependency.package,
                    resolved_version=dependency.resolved_version,
                    source_path=dependency.source_path,
                    detection_method=method,
                    evidence=(
                        f"Declared provider dependency in {dependency.source_path}. "
                        f"{version_evidence}"
                    ),
                )
            )
        return matches

    def _version_evidence(self, dependency, target) -> str | None:
        affected_range = target.version_scope.affected_range if target.version_scope else None
        if not affected_range:
            return "The change does not declare an affected version range."
        if not dependency.resolved_version:
            if (
                dependency.ecosystem == "pypi"
                and dependency.declared_specifier
                and _specifiers_disjoint(dependency.declared_specifier, affected_range)
            ):
                return None
            return f"Installed version is unresolved; declared affected range is {affected_range}."
        if dependency.ecosystem != "pypi":
            return (
                f"Installed version is {dependency.resolved_version}; range {affected_range} "
                "cannot be evaluated for this ecosystem."
            )
        try:
            applies = Version(dependency.resolved_version) in SpecifierSet(affected_range)
        except (InvalidSpecifier, InvalidVersion):
            return (
                f"Installed version is {dependency.resolved_version}; range {affected_range} "
                "could not be evaluated."
            )
        if not applies:
            return None
        return (
            f"Resolved version {dependency.resolved_version} is within affected range "
            f"{affected_range}."
        )

    def _match_file(
        self,
        path: str,
        index: PythonFileIndex,
        change: NormalizedChange,
        assessment_id: str,
    ) -> list[CallSite]:
        matches = []
        for target in change.targets:
            if target.kind in {"symbol", "type"}:
                for node, symbol in [*index.calls, *index.references]:
                    if symbol != target.name and not symbol.endswith(f".{target.name}"):
                        continue
                    # Only an import can prove a symbol belongs to another package. A local
                    # receiver such as self.driver has no root module to compare, so it stays a
                    # lower-confidence match instead of being dropped.
                    resolved = symbol.split(".")[0] in index.import_roots
                    if (
                        target.package
                        and symbol != target.name
                        and resolved
                        and not _module_plausibly_provides(symbol, target.package)
                    ):
                        continue
                    certain = resolved or not target.package
                    matches.append(
                        self._call_site(
                            assessment_id,
                            path,
                            node,
                            symbol,
                            target.name,
                            f"AST call resolves to changed {target.kind} {target.name}."
                            if certain
                            else f"Method name matches changed {target.kind} {target.name}; "
                            "the receiver type is not statically resolved.",
                            score=1.0 if certain else UNRESOLVED_RECEIVER_SCORE,
                        )
                    )
            elif target.kind == "endpoint":
                for node, value, symbol in index.string_arguments:
                    if _endpoint_matches(value, target.name):
                        matches.append(
                            self._call_site(
                                assessment_id,
                                path,
                                node,
                                symbol,
                                target.name,
                                f"Exact endpoint constant matches {target.name}.",
                            )
                        )
            elif target.kind == "field":
                for node, value, symbol in index.fields:
                    if value == target.name:
                        matches.append(
                            self._call_site(
                                assessment_id,
                                path,
                                node,
                                symbol,
                                target.name,
                                f"Static request field matches {target.name}.",
                            )
                        )
        return matches

    def _call_site(
        self,
        assessment_id: str,
        path: str,
        node: ast.AST,
        symbol: str | None,
        target: str,
        reason: str,
        *,
        score: float = 1.0,
    ) -> CallSite:
        start_line = node.lineno
        end_line = getattr(node, "end_lineno", start_line)
        return CallSite(
            id=_stable_id(
                "callsite", assessment_id, path, str(start_line), str(end_line), target, reason
            ),
            path=path,
            start_line=start_line,
            end_line=end_line,
            language="Python",
            symbol=symbol,
            target=target,
            detection_method="ast",
            reason=reason,
            confidence=Confidence(
                score=score,
                basis=ConfidenceBasis.deterministic,
                reasons=["Parsed from a static Python AST node."],
            ),
        )


class MultiLanguageImpactAnalyzer:
    """Python semantic analysis plus conservative JavaScript/TypeScript evidence.

    The lexical analyzer may prove a positive exact match, but it never certifies
    a JavaScript or TypeScript repository as unaffected. Dynamic imports, aliases,
    computed properties, generated clients, and type-only resolution remain
    explicitly outside its coverage.
    """

    analyzer_id = "multilanguage-static"
    analyzer_version = "1.1.0"
    script_extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

    def __init__(self) -> None:
        self.python = PythonAstImpactAnalyzer()

    def analyze(
        self,
        repository: RepositoryRef,
        workspace: RepositoryWorkspace,
        inventory: InventoryResult,
        change: NormalizedChange,
    ) -> ImpactEvidence:
        assessment_id = _stable_id(
            "impact", repository.id, change.id, workspace.content_digest, self.analyzer_version
        )
        python_result = self.python.analyze(repository, workspace, inventory, change)
        call_sites = list(python_result.call_sites)
        parse_failures = python_result.coverage.parse_failures
        script_files = 0
        script_excluded = 0
        root = Path(workspace.root)

        for relative, entry_type, value in sorted(_walk_entries(root), key=lambda item: item[0]):
            if entry_type != "file" or value.suffix.lower() not in self.script_extensions:
                continue
            script_files += 1
            if value.stat(follow_symlinks=False).st_size > MAX_ANALYSIS_FILE_BYTES:
                script_excluded += 1
                continue
            try:
                content = value.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                parse_failures += 1
                continue
            call_sites.extend(self._match_script(relative, content, change, assessment_id))

        call_sites = sorted(
            {item.id: item for item in call_sites}.values(),
            key=lambda item: (item.path, item.start_line, item.end_line, item.id),
        )
        dependency_matches = self.python._dependency_matches(inventory, change)
        observed_languages = {item.language for item in inventory.languages}
        supported_languages = {"Python", "JavaScript", "TypeScript"}
        unsupported_languages = sorted(observed_languages - supported_languages)
        python_files = python_result.coverage.files_considered
        exclusions = (
            inventory.files_excluded + inventory.symlinks_excluded + script_excluded
        )
        limitations = [
            "Python uses AST evidence; JavaScript and TypeScript use exact lexical evidence only.",
            "JavaScript/TypeScript negative results are not authoritative because aliases, "
            "computed properties, and generated code are unresolved.",
        ]
        if unsupported_languages:
            limitations.append(
                "No static analyzer is available for: " + ", ".join(unsupported_languages)
            )
        if parse_failures:
            limitations.append(
                f"{parse_failures} supported source file(s) could not be read or parsed."
            )
        if exclusions:
            limitations.append(f"{exclusions} file(s) were excluded by repository policy.")
        if inventory.warnings:
            limitations.append(
                f"Repository inventory reported {len(inventory.warnings)} warning(s)."
            )

        # Complete negative coverage is currently possible only for Python-only repositories.
        complete_negative_coverage = bool(python_files) and not (
            script_files
            or unsupported_languages
            or parse_failures
            or exclusions
            or inventory.warnings
        )
        coverage = Coverage(
            supported=complete_negative_coverage,
            languages=sorted(observed_languages),
            files_considered=python_files + script_files,
            files_excluded=exclusions,
            parse_failures=parse_failures,
            limitations=limitations,
        )
        if call_sites or _package_change_applies(change, dependency_matches):
            conclusion = "affected"
            summary = "Deterministic repository evidence matches this provider change."
            score = 1.0 if not script_files else 0.9
        elif not python_files and not script_files:
            conclusion = "unsupported"
            summary = "No supported Python, JavaScript, or TypeScript source files were found."
            score = 1.0
        elif complete_negative_coverage:
            conclusion = "unaffected"
            summary = "No target match was found under complete supported Python coverage."
            score = 1.0
        else:
            conclusion = "uncertain"
            summary = (
                "No match was found, but analyzer coverage cannot prove the repository unaffected."
            )
            score = 0.5
        return ImpactEvidence(
            assessment_id=assessment_id,
            conclusion=conclusion,
            summary=summary,
            dependency_matches=dependency_matches,
            call_sites=call_sites,
            coverage=coverage,
            confidence=Confidence(
                score=score,
                basis=ConfidenceBasis.deterministic,
                reasons=["Static AST, lexical, and manifest/lockfile evidence only."],
                unresolved=[] if complete_negative_coverage else limitations[1:],
            ),
        )

    def _match_script(
        self,
        path: str,
        content: str,
        change: NormalizedChange,
        assessment_id: str,
    ) -> list[CallSite]:
        matches: list[CallSite] = []
        language = "TypeScript" if Path(path).suffix.lower() in {".ts", ".tsx"} else "JavaScript"
        # Comments never execute; string bodies are masked too where a string cannot be evidence.
        code = _mask_script(content, strings=False)
        bare_code = _mask_script(content, strings=True)
        for target in change.targets:
            patterns: list[tuple[re.Pattern[str], str | None, str, str]] = []
            escaped = re.escape(target.name)
            if target.kind in {"symbol", "type"}:
                patterns.extend(self._symbol_patterns(target.name, target.kind, bare_code, code))
            elif target.kind == "endpoint":
                patterns.append(
                    (
                        re.compile(rf"(['\"`])(?:https?://[^'\"`/\s]+)?{escaped}\1"),
                        None,
                        f"Exact endpoint string matches {target.name}.",
                        "code",
                    )
                )
            elif target.kind == "field":
                patterns.append(
                    (
                        re.compile(rf"(?:\b{escaped}\b|(['\"]){escaped}\1)\s*:"),
                        None,
                        f"Static object field matches {target.name}.",
                        "code",
                    )
                )
                patterns.append(
                    (
                        re.compile(rf"\??\.\s*{escaped}\b(?!\s*\()"),
                        None,
                        f"Property access matches {target.name}.",
                        "bare",
                    )
                )
                patterns.append(
                    (
                        re.compile(rf"\[\s*(['\"]){escaped}\1\s*\]"),
                        None,
                        f"Subscript access matches {target.name}.",
                        "code",
                    )
                )
            for pattern, symbol, reason, source in patterns:
                text = code if source == "code" else bare_code
                for match in pattern.finditer(text):
                    line = content.count("\n", 0, match.start()) + 1
                    matches.append(
                        CallSite(
                            id=_stable_id(
                                "callsite", assessment_id, path, str(line), target.name, reason
                            ),
                            path=path,
                            start_line=line,
                            end_line=line,
                            language=language,
                            symbol=symbol,
                            target=target.name,
                            detection_method="text_heuristic",
                            reason=reason,
                            confidence=Confidence(
                                score=0.85,
                                basis=ConfidenceBasis.deterministic,
                                reasons=[
                                    "Matched an exact bounded lexical form without executing "
                                    "repository code."
                                ],
                                unresolved=["The lexical match is not type-resolved."],
                            ),
                        )
                    )
        return matches

    @staticmethod
    def _symbol_patterns(
        name: str, kind: str, bare_code: str, code: str
    ) -> list[tuple[re.Pattern[str], str | None, str, str]]:
        reason = f"Exact lexical call matches changed {kind} {name}."
        parts = name.split(".")
        member = re.escape(parts[-1])
        if len(parts) == 1:
            return [(re.compile(rf"\b{member}\s*\("), name, reason, "bare")]
        owner = parts[-2]
        receivers = {owner}
        # Aliased imports (`Client as Api`) and simple re-binding (`const Api = Client`).
        for _ in range(2):
            names = "|".join(re.escape(item) for item in receivers)
            receivers |= set(re.findall(rf"\b(?:{names})\s+as\s+([\w$]+)", bare_code))
            receivers |= set(
                re.findall(rf"\b(?:const|let|var)\s+([\w$]+)\s*=\s*(?:{names})\s*[;\n]", bare_code)
            )
        names = "|".join(re.escape(item) for item in sorted(receivers))
        # Instances created from any known receiver: `x = new Client()`, `this.x = new Client()`.
        instances = set(re.findall(rf"([\w$]+)\s*=\s*(?:await\s+)?new\s+(?:{names})\b", bare_code))
        holders = "|".join(re.escape(item) for item in sorted(receivers | instances))
        patterns: list[tuple[re.Pattern[str], str | None, str, str]] = [
            (
                re.compile(
                    rf"\b(?:{holders})\s*(?:\([^()]*\))?\s*\??\.\s*{member}\s*\(",
                    re.IGNORECASE,
                ),
                name,
                reason,
                "bare",
            ),
            (
                re.compile(
                    rf"\b(?:{holders})\s*\[\s*(['\"]){member}\1\s*\]\s*\(", re.IGNORECASE
                ),
                name,
                reason,
                "code",
            ),
        ]
        # Destructured methods: `const { send } = new Client(); send(...)`.
        for group in re.findall(
            rf"\b(?:const|let|var)\s*\{{([^}}]*)\}}\s*=\s*(?:await\s+)?(?:new\s+)?"
            rf"(?:{'|'.join(re.escape(item) for item in sorted(receivers | instances))})\b",
            bare_code,
        ):
            for entry in group.split(","):
                source, _, local = entry.partition(":")
                if source.strip() == parts[-1]:
                    local_name = re.escape((local or source).strip())
                    patterns.append(
                        (re.compile(rf"(?<![\w$.]){local_name}\s*\("), name, reason, "bare")
                    )
        return patterns


def _mask_script(content: str, *, strings: bool) -> str:
    """Blank out comments (and optionally quoted string bodies), keeping offsets and newlines."""
    output: list[str] = []
    index, length = 0, len(content)
    while index < length:
        char = content[index]
        pair = content[index : index + 2]
        if pair == "//":
            end = content.find("\n", index)
            end = length if end == -1 else end
            output.append(" " * (end - index))
            index = end
        elif pair == "/*":
            end = content.find("*/", index + 2)
            end = length if end == -1 else end + 2
            output.append("".join(c if c == "\n" else " " for c in content[index:end]))
            index = end
        elif char in {"'", '"', "`"}:
            end = index + 1
            while end < length and content[end] != char:
                end += 2 if content[end] == "\\" else 1
                if char != "`" and end < length and content[end] == "\n":
                    break
            end = min(end + 1, length)
            literal = content[index:end]
            if strings and char != "`":
                literal = char + "".join(" " if c != "\n" else c for c in literal[1:-1]) + char
            output.append(literal)
            index = end
        else:
            output.append(char)
            index += 1
    return "".join(output)
