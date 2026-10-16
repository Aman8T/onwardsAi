/**
 * How wide the screen is, said once for every Discover part that behaves differently on a phone: results are a grid on
 * wide screens and a swipe deck under 760 px, the role's details are a side panel or a bottom sheet, a popover is an
 * anchored card or a bottom sheet. `DiscoverScreen` provides it (from a media query, or forced by the dev gallery so a
 * phone can be looked at on a desktop); the parts read it with `useLayout()`.
 *
 * `frame` is where a bottom sheet is placed when the gallery shows a phone inside a frame; in the app it is null and
 * sheets go to the document body.
 */
import { createContext, useContext } from 'react';

export interface DiscoverLayout {
  narrow: boolean;
  frame: HTMLElement | null;
}

export const LayoutContext = createContext<DiscoverLayout>({ narrow: false, frame: null });

export const useLayout = (): DiscoverLayout => useContext(LayoutContext);
