/*
 * The little WebGL2 the cursor light needs: a context on a transparent, premultiplied canvas, one program,
 * its uniform locations, and a clean release. No WebGL1 fallback: without WebGL2 nothing is drawn.
 */

/** The two constants used from `EXT_disjoint_timer_query_webgl2`, which the DOM lib does not declare. */
export interface TimerQueryExt {
  TIME_ELAPSED_EXT: number;
  GPU_DISJOINT_EXT: number;
}

export interface LightGL<U extends string> {
  gl: WebGL2RenderingContext;
  program: WebGLProgram;
  uniforms: Record<U, WebGLUniformLocation | null>;
  /** Present when the driver exposes GPU timer queries (dev measurements only). */
  timer: TimerQueryExt | null;
  dispose(): void;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    if (import.meta.env.DEV) console.error('cursor light: shader failed to compile', gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

/**
 * Creates the context and program. `powerPreference: 'low-power'` keeps laptops on the integrated GPU, which
 * this shader is sized for; `antialias: false` because the output has no edges to smooth; premultiplied alpha
 * so the compositor blends the canvas over the ambient field with plain source-over.
 */
export function createLightGL<U extends string>(
  canvas: HTMLCanvasElement,
  vert: string,
  frag: string,
  uniformNames: readonly U[],
  withTimer: boolean,
): LightGL<U> | null {
  const gl = canvas.getContext('webgl2', {
    alpha: true,
    premultipliedAlpha: true,
    antialias: false,
    depth: false,
    stencil: false,
    powerPreference: 'low-power',
    preserveDrawingBuffer: false,
  });
  if (!gl) return null;

  const vs = compile(gl, gl.VERTEX_SHADER, vert);
  const fs = compile(gl, gl.FRAGMENT_SHADER, frag);
  const program = gl.createProgram();
  if (!vs || !fs || !program) return null;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  gl.deleteShader(vs);
  gl.deleteShader(fs);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    if (import.meta.env.DEV) console.error('cursor light: program failed to link', gl.getProgramInfoLog(program));
    gl.deleteProgram(program);
    return null;
  }

  gl.useProgram(program);
  gl.disable(gl.DEPTH_TEST);
  gl.disable(gl.BLEND); // the shader writes premultiplied colour straight into a cleared buffer
  gl.bindVertexArray(gl.createVertexArray()); // the vertex shader positions the triangle from gl_VertexID
  gl.clearColor(0, 0, 0, 0);

  const uniforms = {} as Record<U, WebGLUniformLocation | null>;
  for (const name of uniformNames) uniforms[name] = gl.getUniformLocation(program, name);

  const timer = withTimer ? (gl.getExtension('EXT_disjoint_timer_query_webgl2') as TimerQueryExt | null) : null;

  return {
    gl,
    program,
    uniforms,
    timer,
    dispose() {
      gl.deleteProgram(program);
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    },
  };
}
