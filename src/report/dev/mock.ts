/**
 * A scripted `ReportClient` for the dev page. It keeps sharing and the choices in memory, so the checkboxes, the quote
 * switches, "Make a link", "Make a new link" and "Stop sharing" really work on the page; `mode` decides how the server
 * behaves:
 *
 *   stored     answers after a short wait
 *   hang-load  `GET` never answers (the sheet's loading state)
 *   fail-load  `GET` is refused
 *   hang-put   `PUT` never answers (the saving state)
 *   fail-put   `PUT` is refused with the server's sentence (the draft goes back and the sentence shows)
 */
import type { ReportChoices } from '../../../shared/report';
import { ApiError } from '../../lib/api';
import type { ReportClient } from '../api';
import { DEFAULTS, viewFor } from './fixtures';
import type { ViewOptions } from './fixtures';

export type MockMode = 'stored' | 'hang-load' | 'fail-load' | 'hang-put' | 'fail-put';

const WAIT_MS = 350;

const never = <T>() => new Promise<T>(() => {});
const later = <T>(value: () => T): Promise<T> => new Promise((resolve) => window.setTimeout(() => resolve(value()), WAIT_MS));

export function mockClient(start: { shared: boolean; choices?: ReportChoices }, mode: MockMode = 'stored', opts: ViewOptions = {}): ReportClient {
  let shared = start.shared;
  let choices = start.choices ?? DEFAULTS;
  return {
    get: () => {
      if (mode === 'hang-load') return never();
      if (mode === 'fail-load') return Promise.reject(new ApiError("We couldn't reach Leafout. Check your connection and try again.", 0));
      return later(() => viewFor(shared, choices, opts));
    },
    put: (update) => {
      if (mode === 'hang-put') return never();
      if (mode === 'fail-put') {
        return Promise.reject(new ApiError("You've changed this a lot just now. Please wait a moment and try again.", 429));
      }
      shared = update.shared;
      choices = update.choices;
      return later(() => viewFor(shared, choices, opts));
    },
    newLink: () => {
      if (!shared) return Promise.reject(new ApiError('The link is off, so there is nothing to replace. Make a link first.', 400));
      return later(() => viewFor(shared, choices, opts));
    },
  };
}
