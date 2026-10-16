/** The cities Discover knows, for the picker. Empty until they arrive (or if the list is unavailable). */
import { useEffect, useState } from 'react';
import type { DiscoverCity } from '../../shared/discover';
import { getCities } from './api';

export function useCities(): DiscoverCity[] {
  const [cities, setCities] = useState<DiscoverCity[]>([]);
  useEffect(() => {
    let live = true;
    getCities()
      .then((c) => live && setCities(c))
      .catch(() => {
        /* the picker says the list is unavailable; the screen still works */
      });
    return () => {
      live = false;
    };
  }, []);
  return cities;
}

/**
 * The city a resume's location names, as the picker lists it ("Bengaluru, Karnataka" is Bengaluru). The server reads
 * places with a longer alias list; when this finds none, the caller shows the resume's own words instead.
 */
export function cityFromLocation(location: string | undefined, cities: readonly DiscoverCity[]): string | undefined {
  if (!location) return undefined;
  const text = location.toLowerCase();
  return cities.find((c) => text.includes(c.name.toLowerCase()))?.name;
}
