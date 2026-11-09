/**
 * A scripted `CardClient` for the dev page. It keeps the settings in memory, so the switch, the checkboxes and the menus
 * really work on the page; `mode` decides how the server behaves:
 *
 *   stored     answers after a short wait
 *   hang-load  `GET` never answers (the sheet's loading state)
 *   fail-load  `GET` is refused
 *   hang-put   `PUT` never answers (the saving state)
 *   fail-put   `PUT` is refused with the server's sentence (the draft goes back and the sentence shows)
 */
import type { CardSettings } from '../../../shared/card';
import { ApiError } from '../../lib/api';
import type { CardClient } from '../api';
import { OFF, viewFor } from './fixtures';
import type { ViewOptions } from './fixtures';

export type MockMode = 'stored' | 'hang-load' | 'fail-load' | 'hang-put' | 'fail-put';

const WAIT_MS = 350;

const never = <T>() => new Promise<T>(() => {});
const later = <T>(value: () => T): Promise<T> => new Promise((resolve) => window.setTimeout(() => resolve(value()), WAIT_MS));

export function mockClient(start: CardSettings = OFF, mode: MockMode = 'stored', opts: ViewOptions = {}): CardClient {
  let settings = start;
  return {
    get: () => {
      if (mode === 'hang-load') return never();
      if (mode === 'fail-load') return Promise.reject(new ApiError("We couldn't reach Leafout. Check your connection and try again.", 0));
      return later(() => viewFor(settings, opts));
    },
    put: (update) => {
      if (mode === 'hang-put') return never();
      if (mode === 'fail-put') {
        return Promise.reject(new ApiError("You've changed this a lot just now. Please wait a moment and try again.", 429));
      }
      settings = { shared: update.shared, skills: update.skills, roleId: update.roleId, theme: update.theme, line: update.line };
      return later(() => viewFor(settings, opts));
    },
    newLink: () => later(() => viewFor(settings, opts)),
  };
}
