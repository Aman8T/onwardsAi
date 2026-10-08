/**
 * CSS stub loader: makes `import './x.css'` resolve to an empty module.
 * Registered with `--import` flag for tsx.
 */

export async function resolve(specifier, context, nextResolve) {
  if (specifier.endsWith('.css')) {
    return {
      url: 'data:text/javascript,export%20default%20%22%22',
      shortCircuit: true,
    };
  }
  return nextResolve(specifier);
}

export async function load(url, context, nextLoad) {
  if (url.startsWith('data:')) {
    return {
      format: 'module',
      source: 'export default ""',
      shortCircuit: true,
    };
  }

  // Intercept .css file URLs
  if (url.endsWith('.css')) {
    return {
      format: 'module',
      source: 'export default ""',
      shortCircuit: true,
    };
  }

  return nextLoad(url);
}
