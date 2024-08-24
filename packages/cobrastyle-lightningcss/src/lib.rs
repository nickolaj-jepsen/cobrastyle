use lightningcss::css_modules::Pattern;
use lightningcss::printer::PrinterOptions;
use lightningcss::stylesheet::{MinifyOptions, ParserOptions, StyleSheet, ToCssResult};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

#[pyclass(frozen, get_all)]
#[derive(Clone)]
struct CssModuleExport {
    name: String,
    composes: Vec<CssModuleReference>,
    is_referenced: bool,
}

impl From<lightningcss::css_modules::CssModuleExport> for CssModuleExport {
    fn from(exports: lightningcss::css_modules::CssModuleExport) -> Self {
        Self {
            name: exports.name,
            composes: exports
                .composes
                .into_iter()
                .map(|reference| reference.into())
                .collect(),
            is_referenced: exports.is_referenced,
        }
    }
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
enum CssModuleReference {
    Local { name: String },
    Global { name: String },
    Dependency { name: String, specifier: String },
}

impl From<lightningcss::css_modules::CssModuleReference> for CssModuleReference {
    fn from(reference: lightningcss::css_modules::CssModuleReference) -> Self {
        match reference {
            lightningcss::css_modules::CssModuleReference::Local { name } => Self::Local { name },
            lightningcss::css_modules::CssModuleReference::Global { name } => Self::Global { name },
            lightningcss::css_modules::CssModuleReference::Dependency { name, specifier } => {
                Self::Dependency { name, specifier }
            }
        }
    }
}

#[pyclass(frozen, get_all)]
#[derive(Clone)]
pub struct TransformResult {
    /// The transformed CSS code.
    code: String,
    /// CSS module exports, if enabled.
    exports: Option<HashMap<String, CssModuleExport>>,
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
        }
    }
}

#[pyfunction]
#[pyo3(
    signature = (
        filename,
        code,
        module = false,
        module_pattern = None,
        minify = true
    ),
)]
pub fn transform(
    filename: String,
    code: String,
    module: bool,
    module_pattern: Option<String>,
    minify: bool,
) -> PyResult<TransformResult> {
    let css_modules = if module {
        let pattern = if let Some(pattern) = module_pattern.as_ref() {
            Pattern::parse(pattern).map_err(|e| {
                PyValueError::new_err(format!("Parsing CSS module pattern failed: {}", e))
            })?
        } else {
            Default::default()
        };
        Some(lightningcss::css_modules::Config {
            pattern,
            ..Default::default()
        })
    } else {
        None
    };

    let mut stylesheet = StyleSheet::parse(
        &code,
        ParserOptions {
            filename,
            css_modules,
            ..Default::default()
        },
    )
    .map_err(|e| PyValueError::new_err(format!("Parsing stylesheet failed: {}", e)))?;

    stylesheet
        .minify(MinifyOptions {
            ..Default::default()
        })
        .map_err(|e| PyValueError::new_err(format!("Minifying stylesheet failed: {}", e)))?;

    let css = stylesheet
        .to_css(PrinterOptions {
            minify,
            ..Default::default()
        })
        .map_err(|e| PyValueError::new_err(format!("Printing stylesheet failed: {}", e)))?;

    Ok(TransformResult::from(css))
}

#[pymodule]
fn cobrastyle_lightningcss(m: &Bound<PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(transform, m)?)?;
    m.add_class::<TransformResult>()?;
    m.add_class::<CssModuleExport>()?;
    m.add_class::<CssModuleReference>()?;

    Ok(())
}
