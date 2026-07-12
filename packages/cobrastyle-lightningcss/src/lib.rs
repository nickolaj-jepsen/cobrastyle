use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Mutex;

use lightningcss::bundler::{BundleErrorKind, Bundler, ResolveResult, SourceProvider};
use lightningcss::css_modules::{self, Pattern};
use lightningcss::dependencies::{self, DependencyOptions};
use lightningcss::printer::PrinterOptions;
use lightningcss::rules::{CssRule, CssRuleList};
use lightningcss::selector::{Component, PseudoClass, Selector, SelectorList};
use lightningcss::stylesheet::{MinifyOptions, ParserOptions, StyleSheet, ToCssResult};
use lightningcss::targets::{Browsers, Targets};
use parcel_sourcemap::SourceMap;
use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

create_exception!(
    cobrastyle_lightningcss,
    TransformError,
    PyValueError,
    "Raised when a stylesheet cannot be parsed, minified or printed. \
     For errors with a source location, `filename`, `line` (1-based) and `column` are set."
);

#[pyclass(
    frozen,
    get_all,
    skip_from_py_object,
    module = "cobrastyle_lightningcss"
)]
#[derive(Clone, Debug)]
pub struct CssModuleExport {
    name: String,
    composes: Vec<CssModuleReference>,
    is_referenced: bool,
}

#[pymethods]
impl CssModuleExport {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

impl From<css_modules::CssModuleExport> for CssModuleExport {
    fn from(export: css_modules::CssModuleExport) -> Self {
        Self {
            name: export.name,
            composes: export.composes.into_iter().map(Into::into).collect(),
            is_referenced: export.is_referenced,
        }
    }
}

#[pyclass(frozen, eq, skip_from_py_object, module = "cobrastyle_lightningcss")]
#[derive(Clone, Debug, PartialEq)]
pub enum CssModuleReference {
    Local { name: String },
    Global { name: String },
    Dependency { name: String, specifier: String },
}

impl From<css_modules::CssModuleReference> for CssModuleReference {
    fn from(reference: css_modules::CssModuleReference) -> Self {
        match reference {
            css_modules::CssModuleReference::Local { name } => Self::Local { name },
            css_modules::CssModuleReference::Global { name } => Self::Global { name },
            css_modules::CssModuleReference::Dependency { name, specifier } => {
                Self::Dependency { name, specifier }
            }
        }
    }
}

// from_py_object: the Dependency enum's generated constructors extract this field type
#[pyclass(frozen, get_all, from_py_object, module = "cobrastyle_lightningcss")]
#[derive(Clone, Debug)]
pub struct SourceRange {
    file_path: String,
    start_line: u32,
    start_column: u32,
    end_line: u32,
    end_column: u32,
}

#[pymethods]
impl SourceRange {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

impl From<dependencies::SourceRange> for SourceRange {
    fn from(range: dependencies::SourceRange) -> Self {
        Self {
            file_path: range.file_path,
            start_line: range.start.line,
            start_column: range.start.column,
            end_line: range.end.line,
            end_column: range.end.column,
        }
    }
}

#[pyclass(frozen, skip_from_py_object, module = "cobrastyle_lightningcss")]
#[derive(Clone, Debug)]
pub enum Dependency {
    Url {
        url: String,
        placeholder: String,
        loc: SourceRange,
    },
    Import {
        url: String,
        placeholder: String,
        supports: Option<String>,
        media: Option<String>,
        loc: SourceRange,
    },
}

impl From<dependencies::Dependency> for Dependency {
    fn from(dependency: dependencies::Dependency) -> Self {
        match dependency {
            dependencies::Dependency::Url(url) => Self::Url {
                url: url.url,
                placeholder: url.placeholder,
                loc: url.loc.into(),
            },
            dependencies::Dependency::Import(import) => Self::Import {
                url: import.url,
                placeholder: import.placeholder,
                supports: import.supports,
                media: import.media,
                loc: import.loc.into(),
            },
        }
    }
}

#[pyclass(
    frozen,
    get_all,
    skip_from_py_object,
    module = "cobrastyle_lightningcss"
)]
#[derive(Clone, Debug)]
pub struct TransformResult {
    /// The transformed CSS code.
    code: String,
    /// CSS module exports, if enabled.
    exports: Option<HashMap<String, CssModuleExport>>,
    /// `url()` and `@import` dependencies, if analysis was enabled.
    dependencies: Option<Vec<Dependency>>,
    /// The source map as JSON, if requested.
    map: Option<String>,
}

#[pymethods]
impl TransformResult {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

impl From<ToCssResult> for TransformResult {
    fn from(result: ToCssResult) -> Self {
        Self {
            code: result.code,
            exports: result.exports.map(|exports| {
                exports
                    .into_iter()
                    .map(|(name, export)| (name, export.into()))
                    .collect()
            }),
            dependencies: result
                .dependencies
                .map(|dependencies| dependencies.into_iter().map(Into::into).collect()),
            map: None,
        }
    }
}

#[pyclass(
    frozen,
    get_all,
    skip_from_py_object,
    module = "cobrastyle_lightningcss"
)]
#[derive(Clone, Debug)]
pub struct BundleResult {
    /// The bundled CSS code, with every non-external `@import` inlined.
    code: String,
    /// CSS module exports of the entry file, if enabled.
    exports: Option<HashMap<String, CssModuleExport>>,
    /// `url()` (and external `@import`) dependencies, if analysis was enabled.
    dependencies: Option<Vec<Dependency>>,
    /// Every file the provider read (the entry included), sorted.
    files: Vec<String>,
    /// The source map as JSON, if requested.
    map: Option<String>,
}

#[pymethods]
impl BundleResult {
    fn __repr__(&self) -> String {
        format!("{self:?}")
    }
}

fn transform_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    let message = format!("{context}: {error}");
    Python::attach(|py| {
        let err = TransformError::new_err(message);
        let value = err.value(py);
        value.setattr("filename", py.None()).ok();
        value.setattr("line", py.None()).ok();
        value.setattr("column", py.None()).ok();
        err
    })
}

/// A [`TransformError`] carrying the error's source location, both in the
/// message and as `filename`/`line`/`column` attributes (None when absent).
fn located_error<T: std::fmt::Display>(
    context: &str,
    error: &lightningcss::error::Error<T>,
) -> PyErr {
    // lightningcss locations are 0-based lines / 1-based columns; expose the editor convention
    let message = match &error.loc {
        Some(loc) => format!(
            "{context}: {} at {}:{}:{}",
            error.kind,
            loc.filename,
            loc.line + 1,
            loc.column
        ),
        None => format!("{context}: {}", error.kind),
    };
    Python::attach(|py| {
        let err = TransformError::new_err(message);
        let value = err.value(py);
        let loc = error.loc.as_ref();
        value
            .setattr("filename", loc.map(|l| l.filename.as_str()))
            .ok();
        value.setattr("line", loc.map(|l| l.line + 1)).ok();
        value.setattr("column", loc.map(|l| l.column)).ok();
        err
    })
}

fn minify_and_print(
    stylesheet: &mut StyleSheet<'_, '_>,
    targets: Targets,
    minify: bool,
    dependencies: Option<DependencyOptions>,
    map: Option<&mut SourceMap>,
) -> PyResult<TransformResult> {
    stylesheet
        .minify(MinifyOptions {
            targets,
            ..Default::default()
        })
        .map_err(|e| located_error("Failed to minify stylesheet", &e))?;

    stylesheet
        .to_css(PrinterOptions {
            minify,
            targets,
            analyze_dependencies: dependencies,
            source_map: map,
            ..Default::default()
        })
        .map(TransformResult::from)
        .map_err(|e| located_error("Failed to print stylesheet", &e))
}

fn browser_targets(targets: Option<&Vec<String>>) -> PyResult<Targets> {
    let browsers = match targets {
        Some(queries) => Browsers::from_browserslist(queries)
            .map_err(|e| transform_error("Invalid browserslist targets", e))?,
        None => None,
    };
    Ok(Targets::from(browsers))
}

fn css_modules_config(
    module: bool,
    module_pattern: Option<&str>,
) -> PyResult<Option<css_modules::Config<'_>>> {
    module
        .then(|| -> PyResult<_> {
            let pattern = match module_pattern {
                Some(pattern) => Pattern::parse(pattern)
                    .map_err(|e| transform_error("Invalid CSS module pattern", e))?,
                None => Pattern::default(),
            };
            Ok(css_modules::Config {
                pattern,
                ..Default::default()
            })
        })
        .transpose()
}

fn serialize_source_map(map: Option<SourceMap>) -> PyResult<Option<String>> {
    map.map(|mut map| {
        map.to_json(None)
            .map_err(|e| transform_error("Failed to serialize source map", e))
    })
    .transpose()
}

/// A [`SourceProvider`] error carrying the Python exception it wraps, so the
/// original exception (type, traceback and all) can be re-raised after the
/// bundler unwinds — including `BaseException`s like `KeyboardInterrupt`.
#[derive(Debug)]
struct ProviderError(PyErr);

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for ProviderError {}

/// scheme:, protocol-relative, or same-document fragment — kept as an external `@import`.
/// Must agree with `_EXTERNAL_URL` in cobrastyle's build.py, which re-classifies the
/// surviving external imports; a specifier this accepts but the regex rejects becomes
/// a spurious `BuildError`.
fn is_external(specifier: &str) -> bool {
    if specifier.starts_with("//") || specifier.starts_with('#') {
        return true;
    }
    match specifier.split_once(':') {
        Some((scheme, _)) => {
            scheme
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_alphabetic())
                && scheme
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '.' | '-'))
        }
        None => false,
    }
}

/// Bridges the bundler's file access to a Python object exposing
/// `read(path) -> str` and `resolve(specifier, from_path) -> str`.
///
/// The bundler borrows every source for its whole run, so contents are
/// leaked as they are read and reclaimed in `Drop` — the same arena trick
/// as lightningcss's own `FileProvider`.
struct PySourceProvider {
    provider: Py<PyAny>,
    sources: Mutex<Vec<*mut str>>,
    files: Mutex<Vec<String>>,
}

// SAFETY: the raw pointers are only ever created from leaked boxes owned by
// this struct and freed in Drop; `Py<PyAny>` is itself Send + Sync.
unsafe impl Send for PySourceProvider {}
unsafe impl Sync for PySourceProvider {}

fn utf8_path(path: &Path) -> Result<&str, ProviderError> {
    path.to_str().ok_or_else(|| {
        ProviderError(TransformError::new_err(format!(
            "Non-UTF-8 stylesheet path: {}",
            path.display()
        )))
    })
}

impl PySourceProvider {
    /// Call a provider method, re-attaching to the interpreter first: the
    /// bundler loads files from rayon worker threads.
    fn call(&self, method: &str, args: (&str, Option<&str>)) -> Result<String, ProviderError> {
        Python::attach(|py| {
            let provider = self.provider.bind(py);
            let result = match args {
                (first, Some(second)) => provider.call_method1(method, (first, second)),
                (first, None) => provider.call_method1(method, (first,)),
            };
            result
                .and_then(|value| value.extract::<String>())
                .map_err(ProviderError)
        })
    }
}

impl SourceProvider for PySourceProvider {
    type Error = ProviderError;

    fn read<'a>(&'a self, file: &Path) -> Result<&'a str, Self::Error> {
        let path = utf8_path(file)?;
        let content = self.call("read", (path, None))?;
        self.files.lock().unwrap().push(path.to_owned());
        let leaked: *mut str = Box::leak(content.into_boxed_str());
        self.sources.lock().unwrap().push(leaked);
        // SAFETY: freed in Drop, which cannot run before 'a ends
        Ok(unsafe { &*leaked })
    }

    fn resolve(
        &self,
        specifier: &str,
        originating_file: &Path,
    ) -> Result<ResolveResult, Self::Error> {
        if is_external(specifier) {
            return Ok(ResolveResult::External(specifier.to_owned()));
        }
        let path = self.call("resolve", (specifier, Some(utf8_path(originating_file)?)))?;
        Ok(ResolveResult::File(path.into()))
    }
}

impl Drop for PySourceProvider {
    fn drop(&mut self) {
        for pointer in self.sources.lock().unwrap().drain(..) {
            // SAFETY: created by Box::leak in read() and never freed elsewhere
            drop(unsafe { Box::from_raw(pointer) });
        }
    }
}

/// The source indices of the bundled files the provider calls global.
///
/// `sources` is indexed by the `source_index` every rule's `loc` carries, which is
/// what the printer scopes idents by — so this set is all the rule walk needs.
fn global_sources(provider: &Py<PyAny>, sources: &[String]) -> PyResult<HashSet<u32>> {
    Python::attach(|py| {
        let provider = provider.bind(py);
        let mut globals = HashSet::new();
        for (index, source) in (0u32..).zip(sources) {
            if provider
                .call_method1("is_global", (source.as_str(),))?
                .extract::<bool>()?
            {
                globals.insert(index);
            }
        }
        Ok(globals)
    })
}

/// Wrap every selector coming from a global source file in the CSS modules `:global()`
/// pseudo-class, so its class names print verbatim and stay out of the module's exports.
///
/// Scoping is a print-time decision keyed on each rule's `source_index`, and `:global()`
/// is how the printer is already told to skip it (it takes its `css_module` out for the
/// wrapped selector, which is also what keeps the names unexported). Reaching that from
/// the AST is what lets one bundle mix scoped and unscoped files, which the bundler's
/// single `ParserOptions` cannot express.
///
/// Selector-level only: `@keyframes` names, `animation`, grid/container names and custom
/// idents inside a global file still hash. They hash consistently within the file, so CSS
/// that only references its own names still works; a *module* naming a global's keyframe
/// does not.
fn globalize_rules(rules: &mut CssRuleList<'_>, globals: &HashSet<u32>) {
    for rule in &mut rules.0 {
        match rule {
            CssRule::Style(style) => {
                if globals.contains(&style.loc.source_index) {
                    globalize_selectors(&mut style.selectors);
                }
                globalize_rules(&mut style.rules, globals);
            }
            CssRule::Nesting(nesting) => {
                if globals.contains(&nesting.style.loc.source_index) {
                    globalize_selectors(&mut nesting.style.selectors);
                }
                globalize_rules(&mut nesting.style.rules, globals);
            }
            CssRule::Scope(scope) => {
                if globals.contains(&scope.loc.source_index) {
                    for selectors in [&mut scope.scope_start, &mut scope.scope_end]
                        .into_iter()
                        .flatten()
                    {
                        globalize_selectors(selectors);
                    }
                }
                globalize_rules(&mut scope.rules, globals);
            }
            CssRule::Media(media) => globalize_rules(&mut media.rules, globals),
            CssRule::Supports(supports) => globalize_rules(&mut supports.rules, globals),
            CssRule::LayerBlock(layer) => globalize_rules(&mut layer.rules, globals),
            CssRule::Container(container) => globalize_rules(&mut container.rules, globals),
            CssRule::MozDocument(document) => globalize_rules(&mut document.rules, globals),
            CssRule::StartingStyle(starting) => globalize_rules(&mut starting.rules, globals),
            _ => {}
        }
    }
}

fn globalize_selectors(selectors: &mut SelectorList<'_>) {
    for selector in &mut selectors.0 {
        // An explicit `:local()`/`:global()` is the author overriding the file's default —
        // wrapping it would silently swallow `:local()`, the only way back into scoping
        if selector.iter_raw_match_order().any(is_module_pseudo_class) {
            continue;
        }
        *selector = Selector::from(Component::NonTSPseudoClass(PseudoClass::Global {
            selector: Box::new(selector.clone()),
        }));
    }
}

fn is_module_pseudo_class(component: &Component<'_>) -> bool {
    matches!(
        component,
        Component::NonTSPseudoClass(PseudoClass::Local { .. } | PseudoClass::Global { .. })
    )
}

/// Parse, minify and print a stylesheet, optionally as a CSS module.
///
/// # Errors
///
/// Raises `TransformError` if the stylesheet, module pattern or browserslist
/// targets cannot be parsed, or if minifying or printing fails.
#[pyfunction]
#[pyo3(
    signature = (
        filename,
        code,
        *,
        module = false,
        module_pattern = None,
        minify = false,
        targets = None,
        analyze_dependencies = false,
        remove_imports = false,
        source_map = false,
    ),
)]
#[allow(clippy::fn_params_excessive_bools, clippy::too_many_arguments)]
pub fn transform(
    py: Python<'_>,
    filename: String,
    code: String,
    module: bool,
    module_pattern: Option<String>,
    minify: bool,
    targets: Option<Vec<String>>,
    analyze_dependencies: bool,
    remove_imports: bool,
    source_map: bool,
) -> PyResult<TransformResult> {
    py.detach(move || {
        let targets = browser_targets(targets.as_ref())?;
        let css_modules = css_modules_config(module, module_pattern.as_deref())?;

        let mut stylesheet = StyleSheet::parse(
            &code,
            ParserOptions {
                filename: filename.clone(),
                css_modules,
                ..Default::default()
            },
        )
        .map_err(|e| located_error("Failed to parse stylesheet", &e))?;

        let mut map = source_map.then(|| SourceMap::new("/"));
        if let Some(map) = &mut map {
            // The printer's mappings point at pre-registered source indices;
            // unlike the bundler it registers nothing itself.
            let index = map.add_source(&filename);
            map.set_source_content(index as usize, &code)
                .map_err(|e| transform_error("Failed to embed source content", e))?;
        }
        let mut result = minify_and_print(
            &mut stylesheet,
            targets,
            minify,
            analyze_dependencies.then_some(DependencyOptions { remove_imports }),
            map.as_mut(),
        )?;

        result.map = serialize_source_map(map)?;
        Ok(result)
    })
}

/// Bundle a stylesheet and its `@import`s into one, reading every file
/// through `provider` (an object with ``read(path) -> str``,
/// ``resolve(specifier, from_path) -> str`` and ``is_global(path) -> bool``
/// methods).
///
/// Files the provider calls global are emitted unscoped, even when the bundle
/// compiles as a module: their selectors print verbatim and are not exported.
///
/// # Errors
///
/// Raises `TransformError` if any file cannot be read, parsed or resolved,
/// on unsupported `@import` conditions, or if minifying or printing fails.
#[pyfunction]
#[pyo3(
    signature = (
        filename,
        provider,
        *,
        module = false,
        module_pattern = None,
        minify = false,
        targets = None,
        analyze_dependencies = false,
        source_map = false,
    ),
)]
// missing_panics_doc: only a poisoned internal mutex can panic
#[allow(
    clippy::fn_params_excessive_bools,
    clippy::too_many_arguments,
    clippy::missing_panics_doc
)]
pub fn bundle(
    py: Python<'_>,
    filename: String,
    provider: Py<PyAny>,
    module: bool,
    module_pattern: Option<String>,
    minify: bool,
    targets: Option<Vec<String>>,
    analyze_dependencies: bool,
    source_map: bool,
) -> PyResult<BundleResult> {
    py.detach(move || {
        let targets = browser_targets(targets.as_ref())?;
        let css_modules = css_modules_config(module, module_pattern.as_deref())?;
        let provider = PySourceProvider {
            provider,
            sources: Mutex::new(Vec::new()),
            files: Mutex::new(Vec::new()),
        };
        let mut map = source_map.then(|| SourceMap::new("/"));

        // Scoped so the bundler's borrow of the map ends before printing
        let mut stylesheet = {
            let mut bundler = Bundler::new(
                &provider,
                map.as_mut(),
                ParserOptions {
                    filename: filename.clone(),
                    css_modules,
                    ..Default::default()
                },
            );
            bundler
                .bundle(Path::new(&filename))
                .map_err(|error| match error.kind {
                    // Provider failures re-raise the original Python exception — a
                    // KeyboardInterrupt must escape as itself, not as a catchable TransformError
                    BundleErrorKind::ResolverError(ProviderError(err)) => err,
                    kind => located_error(
                        "Failed to bundle stylesheet",
                        &lightningcss::error::Error {
                            kind,
                            loc: error.loc,
                        },
                    ),
                })?
        };

        // Before minify(), which merges and reorders rules; the wrapper travels with the selector
        let globals = global_sources(&provider.provider, &stylesheet.sources)?;
        if !globals.is_empty() {
            globalize_rules(&mut stylesheet.rules, &globals);
        }

        let result = minify_and_print(
            &mut stylesheet,
            targets,
            minify,
            analyze_dependencies.then_some(DependencyOptions {
                remove_imports: false,
            }),
            map.as_mut(),
        )?;

        let mut files = std::mem::take(&mut *provider.files.lock().unwrap());
        files.sort();
        Ok(BundleResult {
            code: result.code,
            exports: result.exports,
            dependencies: result.dependencies,
            files,
            map: serialize_source_map(map)?,
        })
    })
}

#[pymodule]
fn cobrastyle_lightningcss(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(transform, m)?)?;
    m.add_function(wrap_pyfunction!(bundle, m)?)?;
    m.add_class::<TransformResult>()?;
    m.add_class::<BundleResult>()?;
    m.add_class::<CssModuleExport>()?;
    m.add_class::<CssModuleReference>()?;
    m.add_class::<Dependency>()?;
    m.add_class::<SourceRange>()?;
    m.add("TransformError", m.py().get_type::<TransformError>())?;
    m.add("LIGHTNINGCSS_VERSION", env!("LIGHTNINGCSS_VERSION"))?;

    Ok(())
}
