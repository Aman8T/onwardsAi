/**
 * Portfolio parity check: verifies that rendered PortfolioView components
 * correctly display domain names and scores matching the graph snapshot.
 *
 * Renders RIYA + all test fixtures in both skins and modes, with graph
 * variants and hidden domains. Parses data-pf-domain/data-pf-score attributes
 * and asserts correctness.
 *
 * Run: npm run check:portfolio
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { register } from 'node:module';

// Register CSS stub loader IMMEDIATELY before any other imports
register('./css-stub.mjs', { parentURL: import.meta.url });

// Now we can safely import modules that use CSS
async function main() {
  const React = await import('react');
  const { renderToStaticMarkup } = await import('react-dom/server');
  const { BLOCK_VARIANTS } = await import('../shared/portfolio');
  const { PortfolioView } = await import('../src/portfolio/PortfolioView');
  const { SkillGraph } = await import('../src/portfolio/graph/SkillGraph');
  const { PressSkillsIndex } = await import('../src/portfolio/graph/PressSkillsIndex');
  const { PressView } = await import('../src/portfolio/press/PressView');
  const { RIYA } = await import('../src/portfolio/dev/samples');

  // Shims for motion/react under Node
  if (!globalThis.matchMedia) {
    (globalThis as any).matchMedia = () => ({
      matches: false,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
    });
  }

  // Create a minimal DOM environment shim
  const mockElement = {
    addEventListener: () => {},
    removeEventListener: () => {},
    setAttribute: () => {},
    getAttribute: () => {},
    removeAttribute: () => {},
    appendChild: () => {},
    removeChild: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
  };

  if (typeof window === 'undefined') {
    (globalThis as any).window = {
      ...globalThis,
      matchMedia: (globalThis as any).matchMedia,
      addEventListener: () => {},
      removeEventListener: () => {},
      document: {
        createElement: () => mockElement,
        querySelector: () => null,
        querySelectorAll: () => [],
      },
    };
    (globalThis as any).document = (globalThis as any).window.document;
  }

  // Utility to decode HTML entities
  function decodeHtmlEntities(str: string): string {
    const entities: { [key: string]: string } = {
      '&amp;': '&',
      '&lt;': '<',
      '&gt;': '>',
      '&quot;': '"',
      '&#39;': "'",
      '&apos;': "'",
    };
    return str.replace(/&[a-z]+;/g, (match) => entities[match] || match);
  }

  type GraphDomain = any;
  type GraphSnapshot = any;
  type Portfolio = any;
  type PortfolioContent = any;
  type PortfolioSkin = 'standard' | 'press';
  type PortfolioSpec = any;
  type ViewMode = 'owner' | 'public';

  /* ─────────────────────────────────────────────────────────────────── */

  interface RenderTestCase {
    portfolioId: string;
    variant: string;
    skin: PortfolioSkin;
    mode: ViewMode;
    hiddenDomainId: string | null;
  }

  interface DomainData {
    name: string;
    score: number;
  }

  interface ParsedResult {
    domains: DomainData[];
    tableCount: number;
  }

  /* ─────────────────────────────────────────────────────────────────── */

  /**
   * Parse HTML to extract data-pf-domain and data-pf-score from all elements.
   */
  function parseRenderedDomains(html: string): ParsedResult {
    const domains = new Map<string, number>();
    let tableCount = 0;

    const domainRegex = /data-pf-domain="([^"]+)"[^>]*data-pf-score="(\d+)"/g;
    let match;

    while ((match = domainRegex.exec(html)) !== null) {
      const name = decodeHtmlEntities(match[1]);
      const score = parseInt(match[2], 10);
      domains.set(name, score);
    }

    tableCount = (html.match(/<table/g) || []).length;

    return {
      domains: Array.from(domains, ([name, score]) => ({ name, score })),
      tableCount,
    };
  }

  /**
   * Get the list of shown domains (snapshot domains minus hidden ones).
   */
  function getShownDomains(graph: GraphSnapshot, hiddenDomainIds: string[]): GraphDomain[] {
    return graph.domains.filter((d: any) => !hiddenDomainIds.includes(d.id));
  }

  /**
   * Render PortfolioView and verify rendered domains match expected domains.
   */
  function testRender(testCase: RenderTestCase, portfolio: Portfolio): {
    pass: boolean;
    message: string;
  } {
    const { portfolioId, variant, skin, mode, hiddenDomainId } = testCase;
    const { spec, content, graph } = portfolio;

    // Modify spec for this test case
    const modifiedSpec: PortfolioSpec = {
      ...spec,
      blocks: spec.blocks.map((b: any) =>
        b.kind === 'graph'
          ? {
              ...b,
              variant,
            }
          : b
      ),
      graph: {
        ...spec.graph,
        hiddenDomainIds: hiddenDomainId ? [hiddenDomainId] : [],
      },
    };

    const shownDomains = getShownDomains(graph, modifiedSpec.graph.hiddenDomainIds);
    // Components use d.name for data-pf-domain attribute, not d.short
    const expectedDomains = new Map(shownDomains.map((d: any) => [d.name, d.score]));

    // Render PortfolioView
    let html = '';
    try {
      const renderGraph = (args: any) => React.default.createElement(SkillGraph, args);
      const renderPress = (args: any) =>
        React.default.createElement(PressView, {
          ...args,
          renderIndex: (indexArgs: any) =>
            React.default.createElement(PressSkillsIndex, indexArgs),
        });

      const element = React.default.createElement(PortfolioView, {
        spec: modifiedSpec,
        content,
        graph,
        mode,
        skin,
        renderGraph,
        renderPress,
      });

      html = renderToStaticMarkup(element);
    } catch (err) {
      return {
        pass: false,
        message: `Render failed: ${err instanceof Error ? err.message : String(err)}`,
      };
    }

    // Parse rendered HTML
    const parsed = parseRenderedDomains(html);

    // Validate
    const issues: string[] = [];

    if (parsed.tableCount === 0) {
      issues.push('No data tables found in rendered output');
    }

    if (parsed.domains.length !== expectedDomains.size) {
      const expectedNames = Array.from(expectedDomains.keys()).join(', ');
      const parsedNames = parsed.domains.map(d => d.name).join(', ');
      issues.push(
        `Domain count mismatch: expected ${expectedDomains.size} [${expectedNames}], got ${parsed.domains.length} [${parsedNames}]`
      );
    }

    for (const { name, score } of parsed.domains) {
      if (!expectedDomains.has(name)) {
        issues.push(`Unexpected domain in output: "${name}" (score: ${score})`);
      } else {
        const expectedScore = expectedDomains.get(name);
        if (score !== expectedScore) {
          issues.push(
            `Score mismatch for "${name}": expected ${expectedScore}, got ${score}`
          );
        }
      }
    }

    for (const hiddenId of modifiedSpec.graph.hiddenDomainIds) {
      const hiddenDomain = graph.domains.find((d: any) => d.id === hiddenId);
      if (hiddenDomain) {
        const hiddenName = hiddenDomain.short || hiddenDomain.name;
        if (parsed.domains.some((d) => d.name === hiddenName)) {
          issues.push(`Hidden domain "${hiddenName}" appeared in output`);
        }
      }
    }

    if (issues.length > 0) {
      return {
        pass: false,
        message: issues.join('; '),
      };
    }

    return { pass: true, message: '' };
  }

  /* ─────────────────────────────────────────────────────────────────── */

  /**
   * Load portfolio JSON files from backend/tests/data/portfolio/.
   */
  function loadPortfolios(): Map<string, Portfolio> {
    const portfolios = new Map<string, Portfolio>();

    portfolios.set('RIYA', RIYA);

    const dataDir = join(process.cwd(), 'backend/tests/data/portfolio');
    if (existsSync(dataDir)) {
      for (const file of readdirSync(dataDir)) {
        if (file.endsWith('.json')) {
          try {
            const content = readFileSync(join(dataDir, file), 'utf-8');
            const data = JSON.parse(content);
            const id = file.replace(/\.json$/, '');
            portfolios.set(id, data);
          } catch (err) {
            console.error(`Failed to load ${file}:`, err);
          }
        }
      }
    }

    return portfolios;
  }

  /* ─────────────────────────────────────────────────────────────────── */

  const portfolios = loadPortfolios();
  const graphVariants = BLOCK_VARIANTS.graph as readonly string[];

  let totalTests = 0;
  let passedTests = 0;
  const failures: string[] = [];

  for (const [portfolioId, portfolio] of portfolios) {
    const { graph } = portfolio;
    if (!graph || !graph.domains || graph.domains.length === 0) {
      continue;
    }

    const domainToHide = graph.domains[0];

    for (const variant of graphVariants) {
      for (const skin of ['standard', 'press'] as const) {
        for (const mode of ['owner', 'public'] as const) {
          const testCase: RenderTestCase = {
            portfolioId,
            variant,
            skin,
            mode,
            hiddenDomainId: domainToHide.id,
          };

          totalTests++;
          const result = testRender(testCase, portfolio);

          if (result.pass) {
            passedTests++;
            const line = `✓ ${portfolioId} skin=${skin} mode=${mode} variant=${variant} (hiding ${domainToHide.id})`;
            console.log(line);
          } else {
            const line = `✗ ${portfolioId} skin=${skin} mode=${mode} variant=${variant} (hiding ${domainToHide.id})`;
            console.log(line);
            failures.push(`${line}\n  ${result.message}`);
          }

          const testCaseNoHide: RenderTestCase = {
            portfolioId,
            variant,
            skin,
            mode,
            hiddenDomainId: null,
          };

          totalTests++;
          const resultNoHide = testRender(testCaseNoHide, portfolio);

          if (resultNoHide.pass) {
            passedTests++;
            const line = `✓ ${portfolioId} skin=${skin} mode=${mode} variant=${variant}`;
            console.log(line);
          } else {
            const line = `✗ ${portfolioId} skin=${skin} mode=${mode} variant=${variant}`;
            console.log(line);
            failures.push(`${line}\n  ${resultNoHide.message}`);
          }
        }
      }
    }
  }

  console.log(
    `\nResults: ${passedTests}/${totalTests} passed, ${totalTests - passedTests} failed`
  );

  if (failures.length > 0) {
    console.log('\nFailures:');
    failures.forEach((f) => console.log(f));
    process.exit(1);
  }

  process.exit(0);
}

main().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
