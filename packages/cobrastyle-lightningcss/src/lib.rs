use std::collections::HashMap;

use lightningcss::css_modules::{self, Pattern};
use lightningcss::dependencies::{self, DependencyOptions};
use lightningcss::printer::PrinterOptions;
use lightningcss::stylesheet::{MinifyOptions, ParserOptions, StyleSheet, ToCssResult};
use lightningcss::targets::{Browsers, Targets};
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
        }
    }
}

fn transform_error(context: &str, error: impl std::fmt::Display) -> PyErr {
    TransformError::new_err(format!("{context}: {error}"))
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
) -> PyResult<TransformResult> {
    py.detach(move || {
        let browsers = match &targets {
            Some(queries) => Browsers::from_browserslist(queries)
                .map_err(|e| transform_error("Invalid browserslist targets", e))?,
            None => None,
        };
        let targets = Targets::from(browsers);

        let css_modules = module
            .then(|| -> PyResult<_> {
                let pattern = match module_pattern.as_deref() {
                    Some(pattern) => Pattern::parse(pattern)
                        .map_err(|e| transform_error("Invalid CSS module pattern", e))?,
                    None => Pattern::default(),
                };
                Ok(css_modules::Config {
                    pattern,
                    ..Default::default()
                })
            })
            .transpose()?;

        let mut stylesheet = StyleSheet::parse(
            &code,
            ParserOptions {
                filename,
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

        stylesheet
            .to_css(PrinterOptions {
                minify,
                targets,
                analyze_dependencies: analyze_dependencies
                    .then_some(DependencyOptions { remove_imports }),
                ..Default::default()
            })
            .map(TransformResult::from)
            .map_err(|e| transform_error("Failed to print stylesheet", e))
    })
}

#[pymodule]
fn cobrastyle_lightningcss(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(transform, m)?)?;
    m.add_class::<TransformResult>()?;
    m.add_class::<CssModuleExport>()?;
    m.add_class::<CssModuleReference>()?;
    m.add_class::<Dependency>()?;
    m.add_class::<SourceRange>()?;
    m.add("TransformError", m.py().get_type::<TransformError>())?;
    m.add("LIGHTNINGCSS_VERSION", env!("LIGHTNINGCSS_VERSION"))?;

    Ok(())
}
