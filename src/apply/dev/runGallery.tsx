/**
 * The run screens' part of the `/dev/apply` gallery (one application, by stage). This is the shell's stub; package FE2
 * replaces it with every stage and stop reason from its own fixtures (`runFixtures.ts`), keeping these two exports:
 *
 *   RUN_SCREENS   [id, label] pairs the gallery's picker lists after its own screens
 *   RunGallery    renders one of them
 */
import { ApplicationSummaryView } from '../ApplicationScreen';
import type { Navigate } from '../route';
import { DETAIL } from './fixtures';

export const RUN_SCREENS: readonly (readonly [string, string])[] = [['run-stub', 'One application (stub until FE2)']];

export function RunGallery({ screen, navigate }: { screen: string; navigate: Navigate }) {
  if (screen !== 'run-stub') return null;
  return <ApplicationSummaryView detail={DETAIL} navigate={navigate} />;
}
