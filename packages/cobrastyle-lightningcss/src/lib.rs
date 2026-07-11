use std::collections::HashMap;
use std::path::Path;
use std::sync::Mutex;

use lightningcss::bundler::{Bundler, ResolveResult, SourceProvider};
use lightningcss::css_modules::{self, Pattern};
use lightningcss::dependencies::{self, DependencyOptions};
use lightningcss::printer::PrinterOptions;
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
    "Raised when a stylesheet cannot be parsed, minified or printed."
);

#[pyclass(frozen, get_all, skip_from_py_object)]
#[derive(Clone, Debug)]
pub struct CssModuleExport {
    name: String,
    composes: Vec<CssModuleReference>,
    is_referenced: bool,
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

#[pyclass(frozen, eq, skip_from_py_object)]
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
#[pyclass(frozen, get_all, from_py_object)]
#[derive(Clone, Debug)]
pub struct SourceRange {
    file_path: String,
    start_line: u32,
    start_column: u32,
    end_line: u32,
    end_column: u32,
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

#[pyclass(frozen, skip_from_py_object)]
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

#[pyclass(frozen, get_all, skip_from_py_object)]
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

#[pyclass(frozen, get_all, skip_from_py_object)]
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

fn transform_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    TransformError::new_err(format!("{context}: {error}"))
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

/// A [`SourceProvider`] error carrying the message of the Python exception it wraps.
#[derive(Debug)]
struct ProviderError(String);

impl std::fmt::Display for ProviderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ProviderError {}

/// scheme:, protocol-relative, or same-document fragment — kept as an external `@import`
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
    path.to_str()
        .ok_or_else(|| ProviderError(format!("Non-UTF-8 stylesheet path: {}", path.display())))
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
                .map_err(|error| ProviderError(error.to_string()))
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
        .map_err(|e| transform_error("Failed to parse stylesheet", e))?;

        stylesheet
            .minify(MinifyOptions {
                targets,
                ..Default::default()
            })
            .map_err(|e| transform_error("Failed to minify stylesheet", e))?;

        let mut map = source_map.then(|| SourceMap::new("/"));
        if let Some(map) = &mut map {
            // The printer's mappings point at pre-registered source indices;
            // unlike the bundler it registers nothing itself.
            let index = map.add_source(&filename);
            map.set_source_content(index as usize, &code)
                .map_err(|e| transform_error("Failed to embed source content", e))?;
        }
        let mut result: TransformResult = stylesheet
            .to_css(PrinterOptions {
                minify,
                targets,
                analyze_dependencies: analyze_dependencies
                    .then_some(DependencyOptions { remove_imports }),
                source_map: map.as_mut(),
                ..Default::default()
            })
            .map(TransformResult::from)
            .map_err(|e| transform_error("Failed to print stylesheet", e))?;

        result.map = serialize_source_map(map)?;
        Ok(result)
    })
}

/// Bundle a stylesheet and its `@import`s into one, reading every file
/// through `provider` (an object with ``read(path) -> str`` and
/// ``resolve(specifier, from_path) -> str`` methods).
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
                .map_err(|e| transform_error("Failed to bundle stylesheet", e))?
        };

        stylesheet
            .minify(MinifyOptions {
                targets,
                ..Default::default()
            })
            .map_err(|e| transform_error("Failed to minify stylesheet", e))?;

        let result: TransformResult = stylesheet
            .to_css(PrinterOptions {
                minify,
                targets,
                analyze_dependencies: analyze_dependencies.then_some(DependencyOptions {
                    remove_imports: false,
                }),
                source_map: map.as_mut(),
                ..Default::default()
            })
            .map(TransformResult::from)
            .map_err(|e| transform_error("Failed to print stylesheet", e))?;

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
