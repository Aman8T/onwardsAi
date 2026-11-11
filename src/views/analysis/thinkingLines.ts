/** Every line the Analysis screen may show while it works. Edit freely. */
export type ThinkingType = 'verb' | 'tip' | 'quote' | 'stat';
export type ThinkingPhase = 'starting' | 'reading' | 'merge' | 'score' | 'rank';

export interface ThinkingLine {
  type: ThinkingType;
  text: string;
  /** Show only during this step. Left out, the line may show at any step. */
  when?: ThinkingPhase;
}

export const THINKING_LINES: ThinkingLine[] = [
  // verbs: what is really happening now
  { type: 'verb', when: 'starting', text: 'Waking one reader per source…' },
  { type: 'verb', when: 'starting', text: 'Lining up the sources to be read…' },
  { type: 'verb', when: 'reading', text: 'Reading {source}, citing as it goes…' },
  { type: 'verb', when: 'reading', text: 'Collecting receipts from {source}…' },
  { type: 'verb', when: 'reading', text: 'Asking {source} what you actually shipped…' },
  { type: 'verb', when: 'merge', text: 'Connecting skills to the evidence under them…' },
  { type: 'verb', when: 'merge', text: 'Untangling one skill said three different ways…' },
  { type: 'verb', when: 'score', text: 'Scoring your domains. In code, not by feel…' },
  { type: 'verb', when: 'score', text: 'Weighing what you shipped over what you listed…' },
  { type: 'verb', when: 'rank', text: 'Measuring the distance to each next role…' },
  { type: 'verb', when: 'rank', text: 'Ranking the roles within reach…' },

  // tips: true of the product today
  { type: 'tip', text: 'Every score on your graph opens to the lines it came from.' },
  { type: 'tip', text: 'A number on your resume is a question in an interview. Practice asks it first.' },
  { type: 'tip', text: 'Paste a posting into Match to see each requirement against your evidence.' },
  { type: 'tip', text: 'Skills your GitHub shows and your resume skips get their own shelf.' },

  // quotes: only Leafout's own rules, which are ours to quote
  { type: 'quote', text: '“No claim without a cited fact.” Leafout’s first rule.' },
  { type: 'quote', text: '“Code computes. Models classify and phrase.” The second.' },

  // stats: filled from this run only. A missing value skips the line.
  { type: 'stat', text: '{done} of {sources} sources read so far.' },
  { type: 'stat', when: 'merge', text: '{chars} characters of your graph written so far.' },
];
